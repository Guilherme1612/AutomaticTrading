"""Pricing engine — EV computation.

Spec ref: Architecture.md §9.4
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvInputs:
    p_up: float
    p_down: float
    target_gain_pct: float
    stop_loss_pct: float


@dataclass(frozen=True)
class EvResult:
    expected_value_pct: float
    ev_multiple: float  # EV / stop_loss
    is_positive: bool


def compute_ev(x: EvInputs) -> EvResult:
    """EV = p_up * gain - p_down * loss. ev_multiple = EV / stop_loss."""
    ev = x.p_up * x.target_gain_pct - x.p_down * x.stop_loss_pct
    ev_multiple = ev / x.stop_loss_pct if x.stop_loss_pct > 0 else 0.0
    return EvResult(expected_value_pct=ev, ev_multiple=ev_multiple, is_positive=ev > 0)
