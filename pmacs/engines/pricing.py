"""Pricing engine -- EV computation with volatility-based targets.

Spec ref: Architecture.md §9.4

Computes expected value from arbitrated probabilities and volatility-adjusted
target gain / stop loss percentages.  The catastrophe-net stop is capped at
15 % (Source.md non-negotiable).  When ATR data is unavailable the engine
falls back to config/risk.toml [pricing] defaults.
"""
from __future__ import annotations

from dataclasses import dataclass


def _load_pricing_config() -> tuple[float, float, float]:
    """Load pricing thresholds from config/risk.toml via typed config loader."""
    try:
        from pmacs.config import load_config
        cfg = load_config()
        return (
            cfg.risk.minimum_ev_pct,
            cfg.risk.default_target_gain_pct,
            cfg.risk.default_stop_loss_pct,
        )
    except (FileNotFoundError, KeyError, TypeError, AttributeError):
        return (0.01, 0.10, 0.15)


_MIN_EV, _TARGET_GAIN, _STOP_LOSS = _load_pricing_config()
MIN_EV_THRESHOLD: float = _MIN_EV
DEFAULT_TARGET_GAIN_PCT: float = _TARGET_GAIN
DEFAULT_STOP_LOSS_PCT: float = _STOP_LOSS
MAX_STOP_LOSS_PCT: float = 0.15     # hard cap (Source.md §5, non-negotiable)


@dataclass(frozen=True)
class EvInputs:
    p_up: float
    p_down: float
    target_gain_pct: float = DEFAULT_TARGET_GAIN_PCT
    stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT
    atr_pct: float | None = None   # ATR as % of price, if available
    current_price: float = 0.0  # must be set by caller; 0.0 causes safe zero-share sizing
    cycle_id: str = ""  # Architecture.md §1.11: required on audit-emitting functions


@dataclass(frozen=True)
class EvResult:
    expected_value_pct: float
    ev_multiple: float    # EV / MIN_EV_THRESHOLD
    is_positive: bool
    target_gain_pct: float   # resolved target gain
    stop_loss_pct: float     # resolved stop loss


def compute_target_and_stop(atr_pct: float | None = None) -> tuple[float, float]:
    """Compute target gain and stop loss from ATR.

    If ATR is unavailable or non-positive, returns defaults.
    Stop loss is capped at MAX_STOP_LOSS_PCT (catastrophe-net).

    Formulas (Arch §9.4):
        target_gain_pct = max(0.05, 1.5 * atr_pct)
        stop_loss_pct   = min(MAX_STOP_LOSS_PCT, max(0.10, 2.0 * atr_pct))
    """
    if atr_pct is None or atr_pct <= 0:
        return DEFAULT_TARGET_GAIN_PCT, DEFAULT_STOP_LOSS_PCT

    target = max(0.05, 1.5 * atr_pct)
    stop = min(MAX_STOP_LOSS_PCT, max(0.10, 2.0 * atr_pct))
    return round(target, 4), round(stop, 4)


def compute_ev(x: EvInputs) -> EvResult:
    """Compute expected value with volatility-adjusted targets.

    EV = p_up * target_gain - p_down * stop_loss
    ev_multiple = EV / MIN_EV_THRESHOLD

    Convention: ev_multiple > 1.0 means EV exceeds the minimum threshold
    (trade-worthy).  ev_multiple < 1.0 means marginal.
    """
    target_gain, stop_loss = compute_target_and_stop(x.atr_pct)

    # Allow callers to override with explicit values when they are not the
    # defaults (e.g. a previous layer already resolved them).
    using_explicit = (
        x.target_gain_pct != DEFAULT_TARGET_GAIN_PCT
        or x.stop_loss_pct != DEFAULT_STOP_LOSS_PCT
    )
    if using_explicit:
        target_gain = x.target_gain_pct
        stop_loss = x.stop_loss_pct

    ev = x.p_up * target_gain - x.p_down * stop_loss
    ev_multiple = ev / MIN_EV_THRESHOLD if MIN_EV_THRESHOLD > 0 else 0.0

    from pmacs.logsys import log_debug
    log_debug(
        "PRICING_EV_COMPUTED",
        payload={
            "p_up": x.p_up,
            "p_down": x.p_down,
            "target_gain_pct": target_gain,
            "stop_loss_pct": stop_loss,
            "atr_pct": x.atr_pct,
            "ev": round(ev, 6),
            "ev_multiple": round(ev_multiple, 4),
            "is_positive": ev > 0,
        },
        level="INFO",
        cycle_id=x.cycle_id or None,
        msg=f"EV computed: {ev:.6f} (multiple: {ev_multiple:.4f})",
    )

    return EvResult(
        expected_value_pct=round(ev, 6),
        ev_multiple=round(ev_multiple, 4),
        is_positive=ev > 0,
        target_gain_pct=target_gain,
        stop_loss_pct=stop_loss,
    )
