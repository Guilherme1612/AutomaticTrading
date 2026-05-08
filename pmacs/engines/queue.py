"""Queue engine — composes the cycle queue from universe + gatekeeper results.

Spec ref: Architecture.md §9, Agents.md §4, cycle orchestration §14

Priority bands:
  Band 1: pinned + admitted
  Band 2: admitted with catalysts pending (future: check catalyst table)
  Band 3: admitted, no catalysts
  Band 4: admitted with flags (limited history, low ADV)

Rejected tickers are excluded entirely.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pmacs.schemas.queue import PriorityBand, QueueItem


def compose_queue(
    universe_tickers: list[str],
    *,
    pinned_tickers: list[str],
    cycle_id: str,
    gatekeeper_results: dict[str, Any],
    catalyst_pending: set[str] | None = None,
) -> list[QueueItem]:
    """Compose the cycle queue from universe + gatekeeper results.

    Args:
        universe_tickers: Full universe of tickers.
        pinned_tickers: Operator-pinned tickers (highest priority).
        cycle_id: Current cycle identifier.
        gatekeeper_results: Dict of ticker -> GatekeeperResult.
        catalyst_pending: Set of tickers with pending catalysts (future use).

    Returns:
        Sorted list of QueueItem, ordered by priority band (1 first).
    """
    catalyst_pending = catalyst_pending or set()
    pinned_set = set(pinned_tickers)
    items: list[QueueItem] = []
    now = datetime.now(timezone.utc).isoformat()

    for ticker in universe_tickers:
        gk = gatekeeper_results.get(ticker)
        if gk is None or not gk.admitted:
            continue

        is_pinned = ticker in pinned_set
        has_catalyst = ticker in catalyst_pending
        has_flags = bool(gk.flags)

        if is_pinned:
            band = PriorityBand.P1_HIGHEST
        elif has_catalyst:
            band = PriorityBand.P2_HIGH
        elif has_flags:
            band = PriorityBand.P4_LOW
        else:
            band = PriorityBand.P3_NORMAL

        items.append(
            QueueItem(
                cycle_id=cycle_id,
                ticker=ticker,
                priority_band=band,
                pinned=is_pinned,
                enqueued_at=now,
            )
        )

    items.sort(key=lambda q: q.priority_band)
    return items
