"""Paper-ledger reconciliation engine (Architecture.md §9).

Compares PMACS internal paper ledger totals against broker-reported
positions.  Pure deterministic math -- no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pmacs.logsys.debug_log import log_debug

# Default config path (relative to project root)
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "risk.toml"


@dataclass
class ToleranceConfig:
    """Tolerance thresholds loaded from config/risk.toml."""
    tolerance_usd: float = 100.0
    tolerance_pct: float = 5.0


def load_tolerance_from_config(config_path: str | Path | None = None) -> ToleranceConfig:
    """Load reconciliation tolerance from config/risk.toml.

    The config file uses a fraction (0.05 = 5%).  The reconciliation
    engine works with percentages (5.0 = 5%).  This function converts
    automatically.

    Falls back to defaults if the file or keys are missing.
    """
    path = Path(config_path) if config_path is not None else _DEFAULT_CONFIG_PATH

    if not path.is_file():
        log_debug(
            "RECONCILIATION_CONFIG_MISSING",
            payload={"path": str(path)},
            level="DEBUG",
            msg=f"Config file not found at {path}, using default tolerances",
        )
        return ToleranceConfig()

    try:
        import tomllib
    except ImportError:
        # Python < 3.11 fallback
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            log_debug(
                "RECONCILIATION_NO_TOML_PARSER",
                payload={},
                level="DEBUG",
                msg="No TOML parser available, using default tolerances",
            )
            return ToleranceConfig()

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception as exc:
        log_debug(
            "RECONCILIATION_CONFIG_PARSE_FAILED",
            payload={"path": str(path), "error": str(exc)},
            level="WARN",
            error_code="RECONCILIATION_FAILED",
            msg=f"Failed to parse {path}: {exc}",
        )
        return ToleranceConfig()

    kill_switch = data.get("kill_switch", {})

    # Config stores tolerance_pct as a fraction (0.05), convert to percentage (5.0)
    raw_pct = kill_switch.get("reconciliation_tolerance_pct", 0.05)
    pct = raw_pct * 100.0

    usd = float(kill_switch.get("reconciliation_tolerance_usd", 100.0))

    return ToleranceConfig(tolerance_usd=usd, tolerance_pct=pct)


@dataclass
class ReconciliationResult:
    """Output of a single reconciliation pass."""

    matched: bool
    pmacs_position_value: float
    broker_position_value: float
    difference_usd: float
    difference_pct: float
    requires_action: bool


def reconcile_paper_ledger(
    ledger_total: float,
    broker_total: float,
    tolerance_usd: float = 100.0,
    tolerance_pct: float = 5.0,
) -> ReconciliationResult:
    """Reconcile PMACS paper ledger with broker positions.

    A match is declared when *both* the absolute and percentage differences
    are within their respective tolerances.
    """
    diff = abs(ledger_total - broker_total)
    diff_pct = (diff / ledger_total * 100) if ledger_total > 0 else 0.0
    matched = diff <= tolerance_usd and diff_pct <= tolerance_pct

    return ReconciliationResult(
        matched=matched,
        pmacs_position_value=ledger_total,
        broker_position_value=broker_total,
        difference_usd=round(diff, 2),
        difference_pct=round(diff_pct, 2),
        requires_action=not matched,
    )
