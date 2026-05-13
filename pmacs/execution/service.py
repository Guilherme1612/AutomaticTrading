"""Execution service — UDS server for signed trade plan submission (Architecture.md §4.5).

Receives signed TradePlan payloads via Unix Domain Socket, verifies Ed25519
signatures, submits orders through a BrokerAdapter, polls for fills, places
catastrophe-net stops, and returns fill results.

Production path: /var/db/pmacs/exec.sock
Dev/test path: /tmp/pmacs_exec_test.sock
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pmacs.execution.adapter import BrokerAdapter, MockAdapter
from pmacs.execution.catastrophe_net import compute_catastrophe_stop
from pmacs.execution.signing import sign_bytes, verify_signature
from pmacs.logsys.debug_log import log_debug
from pmacs.schemas.trade import TradePlan, TradeResult
from pmacs.storage.audit import AuditWriter

logger = logging.getLogger(__name__)


class ExecutionService:
    """Unix Domain Socket server that accepts and verifies signed TradePlans.

    Protocol (Architecture.md §4.5):
        Client sends JSON:
            {"payload": "<b64-plan-bytes>", "signature": "<b64-sig>", "public_key": "<b64-pub>"}
        Server verifies Ed25519 signature.
        Valid  -> {"status": "ACCEPTED", "fill": {...}, "stop_order_id": "..."}
        Invalid -> {"status": "REJECTED", "reason": "INVALID_SIGNATURE"}
    """

    def __init__(
        self,
        sock_path: Path,
        public_key: bytes,
        audit_dir: Path,
        adapter: BrokerAdapter | None = None,
    ) -> None:
        self._sock_path = Path(sock_path)
        self._public_key = public_key
        self._audit_path = audit_dir / "exec_audit.log"
        self._audit_dir = audit_dir
        self._server: asyncio.Server | None = None
        self._audit: AuditWriter | None = None
        self._adapter: BrokerAdapter = adapter or MockAdapter()

    async def start(self) -> None:
        """Start listening on the UDS."""
        # Ensure socket file doesn't linger from a previous run
        if self._sock_path.exists():
            self._sock_path.unlink()

        self._sock_path.parent.mkdir(parents=True, exist_ok=True)
        self._audit_dir.mkdir(parents=True, exist_ok=True)
        self._audit = AuditWriter(self._audit_path)

        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self._sock_path),
        )
        logger.info("ExecutionService listening on %s", self._sock_path)

    async def stop(self) -> None:
        """Shut down the server and clean up."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._sock_path.exists():
            self._sock_path.unlink()
        if self._audit is not None:
            self._audit.close()
            self._audit = None
        logger.info("ExecutionService stopped")

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Read a signed message, verify, submit via adapter, respond, log audit."""
        try:
            data = await reader.read(1_048_576)  # 1 MiB max
            if not data:
                return

            message = json.loads(data.decode("utf-8"))
            payload_b64: str = message["payload"]
            signature_b64: str = message["signature"]
            public_key_b64: str = message["public_key"]

            payload_bytes = base64.b64decode(payload_b64)
            signature_bytes = base64.b64decode(signature_b64)
            client_pub = base64.b64decode(public_key_b64)

            # Verify against the server's trusted public key
            valid = (
                client_pub == self._public_key
                and verify_signature(payload_bytes, signature_bytes, client_pub)
            )

            if not valid:
                response = {
                    "status": "REJECTED",
                    "reason": "INVALID_SIGNATURE",
                }
                self._audit_signature_result(valid=False, payload_size=len(payload_bytes))
                writer.write(json.dumps(response).encode("utf-8"))
                await writer.drain()
                return

            # Parse payload as TradePlan
            try:
                plan = TradePlan.model_validate_json(payload_bytes)
            except Exception as exc:
                logger.error("Invalid TradePlan payload: %s", exc)
                response = {
                    "status": "REJECTED",
                    "reason": f"INVALID_PLAN: {exc}",
                }
                self._audit_signature_result(valid=True, payload_size=len(payload_bytes))
                writer.write(json.dumps(response).encode("utf-8"))
                await writer.drain()
                return

            # Submit order via adapter
            broker_order_id = await self._adapter.submit_order(plan)

            # Poll for fill
            fill = await self._adapter.poll_fill(broker_order_id)

            # Populate fill fields from plan if adapter returned defaults
            if fill.ticker == "UNKNOWN" or not fill.ticker:
                fill = TradeResult(
                    id=fill.id,
                    trade_plan_id=plan.id,
                    ticker=plan.ticker,
                    direction=plan.direction,
                    filled_quantity=plan.quantity,
                    filled_price_usd=plan.price_usd,
                    status=fill.status,
                    broker_order_id=fill.broker_order_id,
                    filled_at=fill.filled_at,
                )

            # Place catastrophe-net stop after successful fill
            stop_order_id: str | None = None
            if fill.status in ("FILLED", "PARTIAL") and fill.filled_quantity > 0:
                stop_order_id = await self._place_catastrophe_stop(plan, fill)

            response = {
                "status": "ACCEPTED",
                "fill": {
                    "price": fill.filled_price_usd,
                    "qty": fill.filled_quantity,
                    "ticker": fill.ticker,
                    "direction": fill.direction.value,
                    "broker_order_id": fill.broker_order_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                "stop_order_id": stop_order_id,
            }

            # Audit log
            self._audit_trade_accepted(plan, fill, stop_order_id)

            writer.write(json.dumps(response).encode("utf-8"))
            await writer.drain()

        except Exception as exc:
            logger.exception("Error handling client: %s", exc)
            error_resp = json.dumps({
                "status": "REJECTED",
                "reason": "INTERNAL_ERROR",
            }).encode("utf-8")
            writer.write(error_resp)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def _place_catastrophe_stop(
        self, plan: TradePlan, fill: TradeResult
    ) -> str | None:
        """Place catastrophe-net stop after fill. Returns stop_order_id or None.

        On failure: log CRITICAL, continue (position still valid).
        Architecture.md §16.7: broker gets ONLY catastrophe-net (15% below entry).
        """
        try:
            stop_price = compute_catastrophe_stop(fill.filled_price_usd)
            stop_order_id = await self._adapter.place_stop_order(
                ticker=plan.ticker,
                stop_price=stop_price,
                qty=fill.filled_quantity,
            )
            log_debug(
                "CATASTROPHE_NET_PLACED",
                payload={
                    "ticker": plan.ticker,
                    "stop_price": stop_price,
                    "stop_order_id": stop_order_id,
                    "broker_order_id": fill.broker_order_id,
                },
                level="INFO",
                cycle_id=plan.cycle_id,
                msg=f"Catastrophe-net placed: {plan.ticker} stop={stop_price}",
            )
            return stop_order_id
        except Exception as exc:
            logger.critical(
                "Catastrophe-net stop FAILED for %s: %s (position still open, broker_order_id=%s)",
                plan.ticker,
                exc,
                fill.broker_order_id,
            )
            log_debug(
                "CATASTROPHE_NET_FAILED",
                payload={
                    "ticker": plan.ticker,
                    "error": str(exc),
                    "broker_order_id": fill.broker_order_id,
                },
                level="CRITICAL",
                error_code="CATASTROPHE_NET_FAILED",
                cycle_id=plan.cycle_id,
                msg=f"Catastrophe-net stop FAILED: {plan.ticker} error={exc}",
            )
            return None

    def _audit_signature_result(self, *, valid: bool, payload_size: int) -> None:
        """Write signature verification result to audit log."""
        if self._audit is not None:
            self._audit.append(
                event_type="EXEC_SERVICE_RECEIVE",
                payload={
                    "signature_valid": valid,
                    "payload_size": payload_size,
                },
            )

    def _audit_trade_accepted(
        self,
        plan: TradePlan,
        fill: TradeResult,
        stop_order_id: str | None,
    ) -> None:
        """Write trade acceptance to audit log."""
        if self._audit is not None:
            self._audit.append(
                event_type="EXEC_TRADE_ACCEPTED",
                payload={
                    "plan_id": plan.id,
                    "ticker": plan.ticker,
                    "direction": plan.direction.value,
                    "filled_qty": fill.filled_quantity,
                    "filled_price": fill.filled_price_usd,
                    "broker_order_id": fill.broker_order_id,
                    "stop_order_id": stop_order_id,
                    "cycle_id": plan.cycle_id,
                },
            )

    @staticmethod
    async def sign_and_send(
        sock_path: Path,
        plan_bytes: bytes,
        private_key: bytes,
    ) -> dict:
        """Client-side helper: sign a trade plan and send it over UDS.

        Returns the parsed server response dict.
        """
        signature = sign_bytes(plan_bytes, private_key)

        # Derive public key from private key for the message envelope
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        priv_obj = Ed25519PrivateKey.from_private_bytes(private_key)
        pub_bytes = priv_obj.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

        message = json.dumps({
            "payload": base64.b64encode(plan_bytes).decode("ascii"),
            "signature": base64.b64encode(signature).decode("ascii"),
            "public_key": base64.b64encode(pub_bytes).decode("ascii"),
        }).encode("utf-8")

        reader, writer = await asyncio.open_unix_connection(str(sock_path))
        try:
            writer.write(message)
            await writer.drain()
            response_data = await reader.read(65_536)
            return json.loads(response_data.decode("utf-8"))  # type: ignore[no-any-return]
        finally:
            writer.close()
            await writer.wait_closed()
