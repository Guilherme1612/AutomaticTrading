"""InsiderActivity sanity validator — persona-specific checks (Agents.md §9).

Checks:
- Transaction dates within 90-day window
- amount_usd > 0 for all transactions
- CLUSTER_BUY only valid if >= 3 OPEN_MARKET_BUY transactions
- NO_SIGNAL/INSUFFICIENT_DATA implies near-uniform probabilities (±0.1 of 0.33)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult


class InsiderActivitySanity(BaseSanityValidator):
    """Sanity validator for InsiderActivity persona output."""

    def _persona_checks(
        self, output: dict[str, Any], evidence: list[Any]
    ) -> SanityResult:
        transactions = output.get("transactions", [])

        # amount_usd > 0
        for i, tx in enumerate(transactions):
            amt = tx.get("amount_usd", 0)
            if amt <= 0:
                return SanityResult(
                    passed=False,
                    reason=f"transaction[{i}] amount_usd {amt} <= 0",
                )

        # Transaction dates within 90-day window
        now = datetime.now()
        for i, tx in enumerate(transactions):
            date_str = tx.get("date", "")
            try:
                tx_date = datetime.fromisoformat(date_str)
                if abs((now - tx_date).days) > 90:
                    return SanityResult(
                        passed=False,
                        reason=f"transaction[{i}] date {date_str} outside 90-day window",
                    )
            except (ValueError, TypeError):
                return SanityResult(
                    passed=False,
                    reason=f"transaction[{i}] has invalid date: {date_str}",
                )

        # CLUSTER_BUY requires >= 3 OPEN_MARKET_BUY
        signal = output.get("signal", "")
        if signal == "CLUSTER_BUY":
            buy_count = sum(
                1 for tx in transactions
                if tx.get("transaction_type") == "OPEN_MARKET_BUY"
            )
            if buy_count < 3:
                return SanityResult(
                    passed=False,
                    reason=f"CLUSTER_BUY signal but only {buy_count} OPEN_MARKET_BUY transactions",
                )

        # NO_SIGNAL or INSUFFICIENT_DATA implies near-uniform probabilities
        if signal in ("NO_SIGNAL", "INSUFFICIENT_DATA"):
            p_up = output.get("p_up", 0.0)
            p_flat = output.get("p_flat", 0.0)
            p_down = output.get("p_down", 0.0)
            uniform_min = 0.23  # 0.33 - 0.10
            uniform_max = 0.43  # 0.33 + 0.10
            for label, val in [("p_up", p_up), ("p_flat", p_flat), ("p_down", p_down)]:
                if val < uniform_min or val > uniform_max:
                    return SanityResult(
                        passed=False,
                        reason=f"{signal} but {label}={val} outside near-uniform range [{uniform_min}, {uniform_max}]",
                    )

        return SanityResult(passed=True)
