"""Portfolio risk gate — position limit, concentration, and sector exposure checks.

Spec ref: Architecture.md §9.5
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RiskGateInputs:
    current_position_count: int
    max_concurrent_positions: int = 5
    target_usd: float = 0.0
    portfolio_value_usd: float = 5000.0
    max_position_pct: float = 0.20
    sector: str | None = None
    current_sector_exposure: dict[str, float] | None = None  # sector -> current exposure %
    max_sector_pct: float = 0.40  # 40% per sector


@dataclass(frozen=True)
class RiskGateResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)


def evaluate_risk_gate(x: RiskGateInputs) -> RiskGateResult:
    """Check position limits, concentration, sector exposure."""
    reasons: list[str] = []

    # Position count limit
    if x.current_position_count >= x.max_concurrent_positions:
        reasons.append(
            f"Position limit: {x.current_position_count}/{x.max_concurrent_positions}"
        )

    # Single position concentration
    position_pct = (
        x.target_usd / x.portfolio_value_usd if x.portfolio_value_usd > 0 else 0.0
    )
    if position_pct > x.max_position_pct:
        reasons.append(
            f"Position concentration: {position_pct:.1%} > {x.max_position_pct:.1%}"
        )

    # Sector exposure
    if x.sector and x.current_sector_exposure is not None:
        current = x.current_sector_exposure.get(x.sector, 0.0)
        if current + position_pct > x.max_sector_pct:
            reasons.append(
                f"Sector exposure: {x.sector} would be {current + position_pct:.1%}"
            )

    return RiskGateResult(passed=len(reasons) == 0, reasons=reasons)
