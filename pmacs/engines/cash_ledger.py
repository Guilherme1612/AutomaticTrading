"""Cash ledger engine — paper account balance tracking (Architecture.md §9).

Tracks all cash flows in the paper trading account:
- Starting capital ($5,000 from Source.md)
- Trade fills (buy debits, sell credits)
- Dividend credits
- Fee debits

Writes snapshot rows to the ``paper_account`` table (Architecture.md §8.5)
whose canonical schema is defined in ``pmacs/storage/sqlite.py``.

Invariants:
- cash_usd >= 0 (no margin in paper mode)
- total_value_usd = cash_usd + positions_value_usd
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pmacs.logsys import log_debug

STARTING_CAPITAL = 5000.00  # Source.md §2


@dataclass(frozen=True)
class CashFlow:
    """A single cash movement in the paper account."""

    flow_type: str  # "TRADE_BUY", "TRADE_SELL", "DIVIDEND", "FEE"
    amount_usd: float  # positive = credit, negative = debit
    reference_id: str  # trade_id or dividend_id
    description: str = ""


class CashLedger:
    """Append-only ledger for the paper-trading account.

    Each balance change inserts a new snapshot row into ``paper_account``
    so the full history is retained.  The latest row is the current state.
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def seed(self, cycle_id: str = "") -> float:
        """Insert the starting-capital snapshot if the ledger is empty.

        Returns the starting balance ($5,000).
        Idempotent — safe to call on every boot.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM paper_account"
            ).fetchone()
            if row[0] == 0:
                now = self._now()
                conn.execute(
                    "INSERT INTO paper_account (snapshot_at, cash_usd, positions_value_usd, total_value_usd) "
                    "VALUES (?, ?, ?, ?)",
                    (now, STARTING_CAPITAL, 0.0, STARTING_CAPITAL),
                )
                conn.commit()
                log_debug(
                    "CASH_LEDGER_SEED",
                    payload={"starting_capital": STARTING_CAPITAL},
                    level="INFO",
                    cycle_id=cycle_id,
                    msg=f"Ledger seeded with starting capital ${STARTING_CAPITAL:.2f}",
                )
            return STARTING_CAPITAL
        finally:
            conn.close()

    def get_balance(self) -> float:
        """Return current cash balance (latest snapshot row)."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT cash_usd FROM paper_account ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return float(row[0]) if row else STARTING_CAPITAL
        finally:
            conn.close()

    def get_snapshot(self) -> dict[str, float]:
        """Return the latest full snapshot {cash, positions_value, total_value}."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT cash_usd, positions_value_usd, total_value_usd "
                "FROM paper_account ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return {
                    "cash_usd": STARTING_CAPITAL,
                    "positions_value_usd": 0.0,
                    "total_value_usd": STARTING_CAPITAL,
                }
            return {
                "cash_usd": float(row[0]),
                "positions_value_usd": float(row[1]),
                "total_value_usd": float(row[2]),
            }
        finally:
            conn.close()

    def apply_flow(self, flow: CashFlow, cycle_id: str = "") -> float:
        """Apply a cash flow, insert a new snapshot, and return the new balance.

        If the flow would push cash below zero, the balance is floored at 0
        (paper trading has no margin).
        """
        conn = self._connect()
        try:
            # Read latest snapshot
            row = conn.execute(
                "SELECT cash_usd, positions_value_usd, total_value_usd "
                "FROM paper_account ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                cash = STARTING_CAPITAL
                pos_val = 0.0
            else:
                cash = float(row[0])
                pos_val = float(row[1])

            new_cash = round(cash + flow.amount_usd, 2)

            if new_cash < 0:
                log_debug(
                    "CASH_LEDGER_NEGATIVE",
                    payload={
                        "flow_type": flow.flow_type,
                        "amount": flow.amount_usd,
                        "balance": cash,
                    },
                    level="ERROR",
                    error_code="LEDGER_CONSTRAINT",
                    cycle_id=cycle_id,
                    msg=f"Cash flow would result in negative balance: "
                        f"{cash:.2f} + {flow.amount_usd:.2f}",
                )
                new_cash = 0.0  # Floor at zero for paper trading

            new_total = round(new_cash + pos_val, 2)
            now = self._now()
            conn.execute(
                "INSERT INTO paper_account (snapshot_at, cash_usd, positions_value_usd, total_value_usd) "
                "VALUES (?, ?, ?, ?)",
                (now, new_cash, pos_val, new_total),
            )
            conn.commit()

            log_debug(
                "CASH_LEDGER_FLOW",
                payload={
                    "flow_type": flow.flow_type,
                    "amount_usd": flow.amount_usd,
                    "previous_balance": cash,
                    "new_balance": new_cash,
                    "reference_id": flow.reference_id,
                },
                level="INFO",
                cycle_id=cycle_id,
                msg=f"Cash flow applied: {flow.flow_type} {flow.amount_usd:.2f} "
                    f"(balance: {cash:.2f} -> {new_cash:.2f})",
            )
            return new_cash
        finally:
            conn.close()

    def validate_total(
        self,
        position_values: dict[str, float],
        cycle_id: str = "",
    ) -> bool:
        """Recompute and persist total_value_usd = cash + sum(position values).

        Inserts a new snapshot row with the corrected total.
        Returns True if the recomputed total matches the stored total
        (before correction), False if a drift was detected.
        """
        cash = self.get_balance()
        positions_total = round(sum(position_values.values()), 2)
        total = round(cash + positions_total, 2)

        conn = self._connect()
        try:
            # Read current total for drift detection
            row = conn.execute(
                "SELECT total_value_usd FROM paper_account ORDER BY id DESC LIMIT 1"
            ).fetchone()
            stored_total = float(row[0]) if row else total

            now = self._now()
            conn.execute(
                "INSERT INTO paper_account (snapshot_at, cash_usd, positions_value_usd, total_value_usd) "
                "VALUES (?, ?, ?, ?)",
                (now, cash, positions_total, total),
            )
            conn.commit()
        finally:
            conn.close()

        drift = abs(stored_total - total) > 0.01
        log_debug(
            "CASH_LEDGER_VALIDATE",
            payload={
                "cash": cash,
                "positions_total": positions_total,
                "total_value": total,
                "stored_total": stored_total,
                "drift_detected": drift,
                "position_count": len(position_values),
            },
            level="WARN" if drift else "DEBUG",
            error_code="LEDGER_CONSTRAINT" if drift else None,
            cycle_id=cycle_id,
            msg=f"Total value validated: {cash:.2f} cash + {positions_total:.2f} "
                f"positions = {total:.2f}"
                + (" [DRIFT DETECTED]" if drift else ""),
        )
        return not drift
