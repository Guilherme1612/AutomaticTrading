"""PMACS constants — CI-tested anti-pattern thresholds and key values.

Do not edit casually. Values derived from spec/Source.md, spec/Architecture.md.
"""

from __future__ import annotations

# -- Position limits (Architecture.md §9.3, Source.md §1) --
MAX_SINGLE_POSITION_PCT: float = 0.20
MAX_CONCURRENT_POSITIONS: int = 5
MAX_POSITION_USD: float = 1_000.0  # 20% of $5K paper capital
PAPER_CAPITAL_USD: float = 5_000.0
CATASTROPHE_NET_PCT: float = 0.15

# -- Conviction thresholds (Source.md §7.2, Architecture.md §9.2) --
CONVICTION_STRONG_BUY: float = 0.6
CONVICTION_BUY: float = 0.3
# SKIP is anything < CONVICTION_BUY

# -- Kill switch triggers (Architecture.md §13) --
KILL_SWITCH_DAILY_LOSS_PCT: float = 0.05
KILL_SWITCH_ROLLING_5D_LOSS_PCT: float = 0.10

# -- Reconciliation (Architecture.md §13) --
RECONCILIATION_TOLERANCE_USD: float = 100.0
RECONCILIATION_TOLERANCE_PCT: float = 0.05

# -- EV minimum (Architecture.md §9) --
MINIMUM_EV_PCT: float = 0.01

# -- Sizing (Architecture.md §9.3) --
HALF_KELLY: bool = True
CORRELATION_FLOOR: float = 0.30

# -- Crucible (Architecture.md §16) --
CRUCIBLE_TIME_BUDGET_SECONDS: int = 90
CRUCIBLE_MAX_CYCLES: int = 2
CRUCIBLE_SEVERITY_NO_TRADE: float = 0.6

# -- Mutation Engine (Architecture.md §10, Agents.md §17) --
MUTATION_ACTIVATION_CYCLES: int = 50
MUTATION_STAT_SIG_P: float = 0.05
MUTATION_STAT_SIG_COHENS_D: float = 0.20
MUTATION_STAT_SIG_MIN_N: int = 20
MUTATION_PROBATION_CYCLES: int = 30
MUTATION_AUTO_ROLLBACK_WINDOW: int = 50
MUTATION_MAX_CONCURRENT_AB: int = 3

# -- Mode promotion gates (Phases.md §3) --
PROMOTION_THRESHOLDS: dict[str, dict[str, float | int]] = {
    "SHADOW_PAPER_to_PAPER_VALIDATED": {
        "min_cycles": 90,
        "min_trades": 200,
        "max_brier": 0.30,
        "min_sharpe": 0.0,
        "max_drawdown_pct": 15.0,
    },
    "PAPER_to_PAPER_VALIDATED": {
        "min_cycles": 90,
        "min_trades": 200,
        "max_brier": 0.30,
        "min_sharpe": 0.0,
        "max_drawdown_pct": 15.0,
    },
    "PAPER_VALIDATED_to_LIVE_EARLY": {
        "min_cycles": 90,
        "min_trades": 200,
        "max_brier": 0.28,
        "min_sharpe": 0.5,
        "max_drawdown_pct": 12.0,
    },
    "LIVE_EARLY_to_LIVE_STANDARD": {
        "min_cycles": 90,
        "min_trades": 200,
        "max_brier": 0.27,
        "min_sharpe": 0.7,
        "max_drawdown_pct": 10.0,
    },
    "LIVE_STANDARD_to_LIVE_EXPANDED": {
        "min_cycles": 120,
        "min_trades": 300,
        "max_brier": 0.25,
        "min_sharpe": 0.8,
        "max_drawdown_pct": 8.0,
    },
}

# -- Mode demotion gates (Phases.md §3.5) --
DEMOTION_THRESHOLDS: dict[str, dict[str, float]] = {
    "LIVE_EXPANDED_to_LIVE_STANDARD": {
        "window": 20,
        "max_sharpe": 0.0,        # Sharpe < 0 triggers
        "max_drawdown_pct": 12.0,  # drawdown > 12% triggers
    },
    "LIVE_STANDARD_to_LIVE_EARLY": {
        "window": 20,
        "max_sharpe": 0.0,
        "max_drawdown_pct": 14.0,
    },
    "LIVE_EARLY_to_PAPER_VALIDATED": {
        "window": 20,
        "max_sharpe": 0.0,
        "max_drawdown_pct": 16.0,
    },
    "PAPER_VALIDATED_to_PAPER": {
        "window": 30,
        "max_brier": 0.32,        # Brier > 0.32 triggers
        "min_sharpe": -0.3,       # Sharpe < -0.3 triggers
    },
}

DEMOTE_COOLDOWN_CYCLES: int = 10  # 10-cycle observation after demotion (§3.5 step 7)

# -- Persona temperatures (Source.md, Agents.md) --
TEMP_ANALYSIS: float = 0.2
TEMP_CRUCIBLE: float = 0.1
TEMP_MEMO_WRITER: float = 0.3

# -- Crash loop detection (Architecture.md §4.7) --
CRASH_LOOP_MAX_RESTARTS: int = 5
CRASH_LOOP_WINDOW_SECONDS: int = 60

# -- Audit chain (Architecture.md §5.1) --
AUDIT_GENESIS_PREV_SHA: str = "0" * 64
AUDIT_FLOAT_DECIMALS: int = 10

# -- FX convention (Architecture.md §16.8) --
FX_CONVENTION = "usd_per_eur"  # NEVER use eur_per_usd

# -- Logging (Architecture.md §5) --
LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARN", "ERROR")

# -- Source criticality (Architecture.md §6) --
SOURCE_CRITICALITY_LEVELS: tuple[str, ...] = ("CRITICAL", "IMPORTANT", "NICE_TO_HAVE")

# -- Bootstrap (Source.md §4) --
PROCEED_BOOTSTRAP_LOW_CONFIDENCE: str = "PROCEED_BOOTSTRAP_LOW_CONFIDENCE"
