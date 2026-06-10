"""Sizing engine — position sizing with Kelly criterion, bootstrap haircuts.

Spec ref: Architecture.md §9.3
"""

from __future__ import annotations

from dataclasses import dataclass, field

BOOTSTRAP_HAIRCUT: dict[int, float] = {0: 0.50, 1: 0.65, 2: 0.80, 3: 0.90}
LIMITED_HISTORY_HAIRCUT: float = 0.50


def _load_max_position() -> float:
    """Load max position from config, fallback to constant."""
    try:
        from pmacs.config import load_config
        return load_config().risk.max_position_usd
    except (FileNotFoundError, KeyError, TypeError, AttributeError):
        from pmacs.constants import MAX_POSITION_USD
        return MAX_POSITION_USD


MAX_POSITION_USD: float = _load_max_position()  # Architecture.md §2: hard cap at $1,000


@dataclass(frozen=True)
class SizingInputs:
    p_up: float
    p_down: float
    target_gain_pct: float
    stop_loss_pct: float
    matured_sources_used: int
    is_limited_history: bool
    portfolio_correlations: list[float] = field(default_factory=list)
    max_position_pct: float = 0.20  # 20%
    portfolio_value_usd: float = 5000.0
    current_price: float = 1.0
    is_bootstrap: bool = False


@dataclass(frozen=True)
class SizingResult:
    target_usd: float
    target_shares: float
    applied_haircuts: dict[str, float] = field(default_factory=dict)
    abort_reason: str | None = None


def compute_kelly(
    p_up: float, p_down: float, target_gain_pct: float, stop_loss_pct: float
) -> float:
    """Full Kelly fraction. f = (p_up * gain - p_down * loss) / loss."""
    if stop_loss_pct == 0:
        return 0.0
    return (p_up * target_gain_pct - p_down * stop_loss_pct) / stop_loss_pct


def size_position(x: SizingInputs) -> SizingResult:
    """Half-Kelly with bootstrap + limited-history haircuts + correlation factor."""
    kelly_fraction = compute_kelly(x.p_up, x.p_down, x.target_gain_pct, x.stop_loss_pct)

    if kelly_fraction <= 0:
        # IMP-4: During bootstrap paper trading, use a minimal 5% position floor
        # even when Kelly is negative/zero. This generates trade data needed for
        # Sharpe/drawdown/win-rate calculations required for mode promotion.
        if x.is_bootstrap:
            min_pct = 0.05  # 5% of portfolio = $250 on $5K
            target_usd = min_pct * x.portfolio_value_usd
            target_usd = min(target_usd, MAX_POSITION_USD)
            target_shares = target_usd / x.current_price if x.current_price > 0 else 0.0
            return SizingResult(
                target_usd=target_usd,
                target_shares=target_shares,
                applied_haircuts={"bootstrap_floor": min_pct},
            )
        return SizingResult(
            target_usd=0.0,
            target_shares=0.0,
            applied_haircuts={},
            abort_reason="NEGATIVE_KELLY_NO_EDGE",
        )

    safety_kelly = kelly_fraction * 0.5  # half-Kelly

    # Correlation factor: reduce size if highly correlated with existing positions.
    # Floor at 0.30 so a single high-correlation holding cannot block all new entries —
    # valid, uncorrelated theses should still get meaningful allocation even when the
    # portfolio happens to hold a related name.
    if x.portfolio_correlations:
        correlation_factor = max(0.30, 1.0 - max(x.portfolio_correlations))
    else:
        correlation_factor = 1.0

    # Bootstrap haircut based on number of matured sources (0-3 mature → haircut; 4+ → 1.0)
    n_mature = min(x.matured_sources_used, 4)
    bootstrap_factor = BOOTSTRAP_HAIRCUT.get(n_mature, 1.0)

    # Limited history haircut
    limited_factor = LIMITED_HISTORY_HAIRCUT if x.is_limited_history else 1.0

    target_pct = safety_kelly * correlation_factor * bootstrap_factor * limited_factor
    target_pct = min(target_pct, x.max_position_pct)

    target_usd = target_pct * x.portfolio_value_usd
    target_usd = min(target_usd, MAX_POSITION_USD)  # Architecture.md §9.3: $1000 hard cap
    target_shares = target_usd / x.current_price if x.current_price > 0 else 0.0

    return SizingResult(
        target_usd=target_usd,
        target_shares=target_shares,
        applied_haircuts={
            "bootstrap": bootstrap_factor,
            "limited_history": limited_factor,
            "correlation": correlation_factor,
        },
    )
