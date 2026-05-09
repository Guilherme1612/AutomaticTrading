"""Stop event poller -- processes PENDING stop events from SQLite.

Runs during RTH, polls stop_events table for PENDING triggers every 10 seconds.
For each trigger:
1. Constructs a TradePlan (side=SELL).
2. Calls execute_exit to cancel catastrophe-net and submit SELL.
3. Updates status to SUBMITTED then FILLED.
4. Calls state_machine.transition for appropriate exit state.

Architecture.md Section 4.4: nervous orchestrates stop execution.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from pmacs.engines.state_machine import transition
from pmacs.execution.catastrophe_net import execute_exit
from pmacs.logsys import log_debug
from pmacs.schemas.contracts import Holding, HoldingState
from pmacs.schemas.stop_loss import StopEventStatus, StopType


POLL_INTERVAL_S = 10


class StopEventPoller:
    """Polls SQLite for PENDING stop events and processes them."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def poll_pending(self) -> list[dict]:
        """Query PENDING stop events from SQLite.

        Returns:
            List of dicts representing pending stop_events rows.
        """
        conn = sqlite3.connect(str(self._db_path))
        try:
            rows = conn.execute(
                "SELECT id, holding_id, ticker, stop_type, trigger_price_usd, "
                "stop_price_usd, detected_at, cycle_id, stop_type_category "
                "FROM stop_events WHERE status = ?",
                (StopEventStatus.PENDING.value,),
            ).fetchall()

            results = []
            for row in rows:
                results.append({
                    "id": row[0],
                    "holding_id": row[1],
                    "ticker": row[2],
                    "stop_type": row[3],
                    "trigger_price_usd": row[4],
                    "stop_price_usd": row[5],
                    "detected_at": row[6],
                    "cycle_id": row[7] or "",
                    "stop_type_category": row[8] or "FIXED",
                })
            return results
        finally:
            conn.close()

    def process_trigger(
        self,
        trigger: dict,
        execution_service=None,
        holding: Holding | None = None,
        cycle_id: str = "",
    ) -> None:
        """Process a single stop trigger.

        Sequence:
        1. Update status to SUBMITTED.
        2. Call execute_exit (cancel catastrophe-net, submit SELL).
        3. Update status to FILLED.
        4. Transition holding state machine.

        Args:
            trigger: Dict from poll_pending() with stop event fields.
            execution_service: Optional execution service for broker calls.
            holding: The Holding model to transition (required for state transition).
            cycle_id: Cycle ID for audit trail.
        """
        trigger_id = trigger["id"]
        holding_id = trigger["holding_id"]
        ticker = trigger["ticker"]
        stop_type = trigger["stop_type"]
        trig_cycle_id = trigger.get("cycle_id") or cycle_id or "stop-poll"

        # Step 1: Update status to SUBMITTED
        self._update_status(trigger_id, StopEventStatus.SUBMITTED)

        # Step 2: Execute exit (cancel catastrophe-net, submit SELL, audit)
        if holding is not None:
            execute_exit(
                holding=holding,
                exit_reason=stop_type,
                cycle_id=trig_cycle_id,
            )

        # Step 3: Update status to FILLED
        self._update_status(trigger_id, StopEventStatus.FILLED)

        # Step 4: Transition holding state
        if holding is not None and trig_cycle_id:
            target_state = self._determine_exit_state(stop_type)
            transition(
                holding=holding,
                new_state=target_state,
                reason=f"Stop trigger: {stop_type}",
                cycle_id=trig_cycle_id,
                op_seq=0,
            )

        log_debug(
            "STOP_EVENT_PROCESSED",
            payload={
                "trigger_id": trigger_id,
                "holding_id": holding_id,
                "stop_type": stop_type,
                "status": "FILLED",
            },
            level="INFO",
            cycle_id=trig_cycle_id,
            msg=f"Stop event processed: {ticker} {stop_type}",
        )

    def _update_status(self, trigger_id: int, status: StopEventStatus) -> None:
        """Update stop_events status for a given trigger."""
        conn = sqlite3.connect(str(self._db_path))
        try:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE stop_events SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, now, trigger_id),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _determine_exit_state(stop_type: str) -> HoldingState:
        """Determine the target state based on stop type.

        FIXED_STOP / CATASTROPHE_NET -> STOPPED_OUT
        TRAILING_STOP -> EXIT_TRAILING_STOP
        """
        if stop_type in ("TRAILING_STOP",):
            return HoldingState.EXIT_TRAILING_STOP
        return HoldingState.STOPPED_OUT

    def run_poll_loop(
        self,
        execution_service=None,
        holding_lookup: dict[str, Holding] | None = None,
        interval_s: int = POLL_INTERVAL_S,
    ) -> None:
        """Run the poll loop during RTH only.

        Args:
            execution_service: Optional execution service for broker calls.
            holding_lookup: Dict mapping holding_id -> Holding model.
            interval_s: Seconds between polls (default 10).
        """
        from pmacs.stop_loss_daemon import is_rth

        while True:
            if is_rth():
                pending = self.poll_pending()
                for trigger in pending:
                    holding = None
                    if holding_lookup:
                        holding = holding_lookup.get(trigger["holding_id"])
                    self.process_trigger(
                        trigger=trigger,
                        execution_service=execution_service,
                        holding=holding,
                    )
            time.sleep(interval_s)
