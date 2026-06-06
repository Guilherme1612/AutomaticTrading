"""Queue engine — composes the cycle queue from universe + gatekeeper results.

Spec ref: Architecture.md §9, Agents.md §4, cycle orchestration §14

Priority bands:
  Band 1: pinned + admitted
  Band 2: admitted with catalysts pending OR prior-cycle BUY/STRONG_BUY conviction
  Band 3: admitted, no catalysts, no prior conviction signal
  Band 4: admitted with flags (limited history, low ADV)

Prior-cycle conviction promotion: tickers that scored BUY (>= 0.3) or
STRONG_BUY (>= 0.6) in the most recent cycle are promoted to P2 if they
would otherwise land in P3. This ensures high-conviction names get re-evaluated
first each cycle rather than in arbitrary universe order.

Rejected tickers are excluded entirely.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pmacs.schemas.queue import PriorityBand, QueueItem

# Conviction threshold for prior-cycle promotion to P2.
# Matches the BUY threshold in conviction.py / pipeline.py.
PRIOR_CONVICTION_PROMOTE_THRESHOLD: float = 0.3


def compose_queue(
    universe_tickers: list[str],
    *,
    pinned_tickers: list[str],
    cycle_id: str,
    gatekeeper_results: dict[str, Any],
    catalyst_pending: set[str] | None = None,
    universe_priority: dict[str, int] | None = None,
    prior_conviction: dict[str, float] | None = None,
) -> list[QueueItem]:
    """Compose the cycle queue from universe + gatekeeper results.

    Args:
        universe_tickers: Full universe of tickers.
        pinned_tickers: Operator-pinned tickers (highest priority).
        cycle_id: Current cycle identifier.
        gatekeeper_results: Dict of ticker -> GatekeeperResult.
        catalyst_pending: Set of tickers with pending catalysts (future use).
        universe_priority: Optional per-ticker priority override (1 = pin).
        prior_conviction: Optional dict of ticker -> conviction_score from the
            previous cycle. Tickers above PRIOR_CONVICTION_PROMOTE_THRESHOLD are
            promoted to P2 (same band as catalyst tickers) if not already P1.

    Returns:
        Sorted list of QueueItem, ordered by priority band (1 first).
    """
    catalyst_pending = catalyst_pending or set()
    universe_priority = universe_priority or {}
    prior_conviction = prior_conviction or {}
    pinned_set = set(pinned_tickers)
    items: list[QueueItem] = []
    now = datetime.now(timezone.utc).isoformat()

    for ticker in universe_tickers:
        gk = gatekeeper_results.get(ticker)
        if gk is None or not gk.admitted:
            continue

        # Universe pinned_priority=1 is equivalent to operator pin (P1 band)
        is_pinned = ticker in pinned_set or (universe_priority.get(ticker) or 999) <= 1
        has_catalyst = ticker in catalyst_pending
        has_flags = bool(gk.flags)
        has_prior_conviction = (
            prior_conviction.get(ticker, 0.0) >= PRIOR_CONVICTION_PROMOTE_THRESHOLD
        )

        if is_pinned:
            band = PriorityBand.P1_HIGHEST
        elif has_catalyst or has_prior_conviction:
            # Catalyst tickers and prior high-conviction tickers share P2.
            # Prior conviction promotion: re-examine high-conviction names first
            # so the operator sees updated verdicts on the most promising names
            # before working through lower-interest tickers.
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
