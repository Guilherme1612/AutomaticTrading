"""Unit tests for pmacs.execution.service — UDS execution service with adapter."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
from pathlib import Path

import pytest

from pmacs.execution.service import ExecutionService
from pmacs.execution.signing import generate_keypair, sign_bytes
from pmacs.schemas.trade import TradeDirection, TradePlan

# macOS AF_UNIX path limit is ~104 chars; tmp_path can exceed this.
# Use /tmp directly with short unique names.
_UDS_COUNTER = 0


def _make_plan_bytes(
    plan_id: str = "tp-001",
    ticker: str = "AAPL",
    direction: str = "BUY",
    quantity: int = 10,
    price_usd: float = 150.0,
    cycle_id: str = "cycle-test",
) -> bytes:
    """Create valid TradePlan JSON bytes for testing."""
    plan = TradePlan(
        id=plan_id,
        ticker=ticker,
        direction=TradeDirection(direction),
        quantity=quantity,
        price_usd=price_usd,
        cycle_id=cycle_id,
    )
    return plan.model_dump_json().encode("utf-8")


def _short_tmp_dir() -> Path:
    """Return a short-lived temp dir under /tmp."""
    global _UDS_COUNTER
    _UDS_COUNTER += 1
    p = Path(f"/tmp/pmacs_test_{os.getpid()}_{_UDS_COUNTER}")
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture()
def keypair() -> tuple[bytes, bytes]:
    """Generate a fresh Ed25519 keypair for each test."""
    return generate_keypair()


@pytest.fixture()
def uds_paths() -> tuple[Path, Path]:
    """Return (sock_path, audit_dir) with short paths under /tmp."""
    base = _short_tmp_dir()
    return base / "e.sock", base / "a"


@pytest.fixture(autouse=True)
def _cleanup_uds_paths(uds_paths: tuple[Path, Path]) -> None:
    """Clean up temp dir after test."""
    yield
    base = uds_paths[0].parent
    if base.exists():
        for f in base.iterdir():
            try:
                f.unlink()
            except OSError:
                pass
        try:
            base.rmdir()
        except OSError:
            pass


async def _start_server(
    sock_path: Path,
    audit_dir: Path,
    public_key: bytes,
) -> ExecutionService:
    """Create and start an ExecutionService, returning it for cleanup."""
    svc = ExecutionService(
        sock_path=sock_path,
        public_key=public_key,
        audit_dir=audit_dir,
    )
    await svc.start()
    return svc


class TestAcceptedFlow:
    """Valid signed payload -> ACCEPTED."""

    @pytest.mark.asyncio()
    async def test_sign_and_send_accepted(
        self,
        keypair: tuple[bytes, bytes],
        uds_paths: tuple[Path, Path],
    ) -> None:
        priv, pub = keypair
        sock_path, audit_dir = uds_paths
        svc = await _start_server(sock_path, audit_dir, pub)
        try:
            plan_bytes = _make_plan_bytes(
                plan_id="tp-001", ticker="AAPL", quantity=10, price_usd=150.0
            )
            result = await ExecutionService.sign_and_send(sock_path, plan_bytes, priv)
            assert result["status"] == "ACCEPTED"
            assert "fill" in result
            # MockAdapter fills with plan data via service's population logic
            assert result["fill"]["price"] == 150.0
            assert result["fill"]["qty"] == 10
            assert result["fill"]["ticker"] == "AAPL"
            assert "timestamp" in result["fill"]
            assert "stop_order_id" in result
        finally:
            await svc.stop()


class TestTamperedPayload:
    """Payload modified after signing -> REJECTED."""

    @pytest.mark.asyncio()
    async def test_tampered_payload_rejected(
        self,
        keypair: tuple[bytes, bytes],
        uds_paths: tuple[Path, Path],
    ) -> None:
        priv, pub = keypair
        sock_path, audit_dir = uds_paths
        svc = await _start_server(sock_path, audit_dir, pub)
        try:
            plan_bytes = _make_plan_bytes(plan_id="tp-002", ticker="MSFT", direction="SELL")
            signature = sign_bytes(plan_bytes, priv)

            # Derive public key bytes
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

            priv_obj = Ed25519PrivateKey.from_private_bytes(priv)
            pub_bytes = priv_obj.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )

            # Send tampered payload with valid signature of original
            tampered_message = json.dumps({
                "payload": base64.b64encode(b'TAMPERED_PAYLOAD').decode("ascii"),
                "signature": base64.b64encode(signature).decode("ascii"),
                "public_key": base64.b64encode(pub_bytes).decode("ascii"),
            }).encode("utf-8")

            reader, writer = await asyncio.open_unix_connection(str(sock_path))
            writer.write(tampered_message)
            await writer.drain()
            response_data = await reader.read(65_536)
            writer.close()
            await writer.wait_closed()

            result = json.loads(response_data.decode("utf-8"))
            assert result["status"] == "REJECTED"
            assert result["reason"] == "INVALID_SIGNATURE"
        finally:
            await svc.stop()


class TestWrongKey:
    """Signature from a different key -> REJECTED."""

    @pytest.mark.asyncio()
    async def test_wrong_key_rejected(
        self,
        keypair: tuple[bytes, bytes],
        uds_paths: tuple[Path, Path],
    ) -> None:
        priv_server, pub_server = keypair
        sock_path, audit_dir = uds_paths
        svc = await _start_server(sock_path, audit_dir, pub_server)
        try:
            # Use a different keypair to sign
            priv_wrong, _ = generate_keypair()
            plan_bytes = _make_plan_bytes(plan_id="tp-003", ticker="GOOG")

            # sign_and_send with the wrong private key
            result = await ExecutionService.sign_and_send(
                sock_path, plan_bytes, priv_wrong,
            )
            assert result["status"] == "REJECTED"
            assert result["reason"] == "INVALID_SIGNATURE"
        finally:
            await svc.stop()


class TestServerLifecycle:
    """Server start/stop and socket file management."""

    @pytest.mark.asyncio()
    async def test_socket_file_created_on_start(
        self,
        keypair: tuple[bytes, bytes],
        uds_paths: tuple[Path, Path],
    ) -> None:
        priv, pub = keypair
        sock_path, audit_dir = uds_paths
        assert not sock_path.exists()

        svc = await _start_server(sock_path, audit_dir, pub)
        assert sock_path.exists()

        await svc.stop()
        assert not sock_path.exists()

    @pytest.mark.asyncio()
    async def test_stop_idempotent(
        self,
        keypair: tuple[bytes, bytes],
        uds_paths: tuple[Path, Path],
    ) -> None:
        priv, pub = keypair
        sock_path, audit_dir = uds_paths
        svc = await _start_server(sock_path, audit_dir, pub)

        await svc.stop()
        # Second stop should not raise
        await svc.stop()

    @pytest.mark.asyncio()
    async def test_removes_stale_socket_on_start(
        self,
        keypair: tuple[bytes, bytes],
        uds_paths: tuple[Path, Path],
    ) -> None:
        priv, pub = keypair
        sock_path, audit_dir = uds_paths

        # Create a stale socket file
        sock_path.parent.mkdir(parents=True, exist_ok=True)
        sock_path.touch()

        svc = await _start_server(sock_path, audit_dir, pub)
        assert sock_path.exists()  # Now it's the real socket
        await svc.stop()


class TestAuditLogging:
    """Verify audit entries are written on each request."""

    @pytest.mark.asyncio()
    async def test_audit_entry_on_accepted(
        self,
        keypair: tuple[bytes, bytes],
        uds_paths: tuple[Path, Path],
    ) -> None:
        priv, pub = keypair
        sock_path, audit_dir = uds_paths
        svc = await _start_server(sock_path, audit_dir, pub)
        try:
            plan_bytes = _make_plan_bytes(plan_id="tp-audit", ticker="TSLA")
            await ExecutionService.sign_and_send(sock_path, plan_bytes, priv)

            audit_log = audit_dir / "exec_audit.log"
            assert audit_log.exists()
            content = audit_log.read_text()
            assert "EXEC_TRADE_ACCEPTED" in content
            assert "ticker" in content
        finally:
            await svc.stop()

    @pytest.mark.asyncio()
    async def test_audit_entry_on_rejected(
        self,
        keypair: tuple[bytes, bytes],
        uds_paths: tuple[Path, Path],
    ) -> None:
        priv, pub = keypair
        sock_path, audit_dir = uds_paths
        svc = await _start_server(sock_path, audit_dir, pub)
        try:
            priv_wrong, _ = generate_keypair()
            plan_bytes = _make_plan_bytes(plan_id="tp-audit-rej")
            await ExecutionService.sign_and_send(sock_path, plan_bytes, priv_wrong)

            audit_log = audit_dir / "exec_audit.log"
            content = audit_log.read_text()
            assert "EXEC_SERVICE_RECEIVE" in content
            assert '"signature_valid":false' in content
        finally:
            await svc.stop()
