"""PMACS configuration loader.

Loads all config/*.toml + config/model_registry.json.
Resolves paths relative to the project root (directory containing pyproject.toml).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


def _project_root() -> Path:
    """Find project root by walking up from this file to find pyproject.toml."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    raise FileNotFoundError("Cannot find project root (pyproject.toml)")


PROJECT_ROOT = _project_root()
CONFIG_DIR = PROJECT_ROOT / "config"

# Operator runtime overrides (gitignored config/runtime_state.json). Lives next
# to model_registry.json so the loader finds it cheaply, but gitignored so
# `git pull` / `git checkout` / fresh clones cannot silently undo the
# operator's last explicit choice. See tests/unit/test_runtime_state_persistence
# for the contract. Spec: §16 (operator-local config layer).
RUNTIME_STATE_PATH = CONFIG_DIR / "runtime_state.json"


def data_dir() -> Path:
    """Return the PMACS data directory.

    Resolves in this order:
    1. PMACS_DATA_DIR environment variable
    2. <PROJECT_ROOT>/data  (default for cloned repos)
    """
    return Path(os.environ.get("PMACS_DATA_DIR", str(PROJECT_ROOT / "data")))


# Module-level convenience constants (evaluated once at import).
# For function default parameters, use None sentinel and call data_dir() inside.
DATA_DIR = data_dir()
DB_PATH = DATA_DIR / "pmacs.db"
DUCKDB_PATH = DATA_DIR / "pmacs_analytics.duckdb"
HEARTBEAT_DIR = DATA_DIR / "heartbeats"
AUDIT_LOG_PATH = DATA_DIR / "audit.log"
PID_DIR = DATA_DIR / "pids"


@dataclass(frozen=True)
class ResourcesConfig:
    ram_gb: int = 64
    cpu_cores: int = 10
    gpu: str = "apple_m1_max"
    backend: str = "llama_server"
    gguf_path: str = ""
    threads: int = 8
    parallel_slots: int = 3
    ctx_size: int = 32768
    quantization: str = "UD-Q4_K_XL"
    phase1_seconds_per_symbol: int = 270
    debate_wave_seconds_per_symbol: int = 180
    daily_llm_seconds_total: int = 21600
    stop_loss_check_interval_seconds: int = 1800
    quote_freshness_max_seconds: int = 60
    crash_loop_max_restarts: int = 5
    catastrophe_stop_pct: float = 0.15


@dataclass(frozen=True)
class RiskConfig:
    max_single_position_pct: float = 0.20
    max_concurrent_positions: int = 5
    daily_loss_pct: float = 0.05
    rolling_5d_loss_pct: float = 0.10
    reconciliation_tolerance_usd: float = 100.0
    reconciliation_tolerance_pct: float = 0.05
    minimum_ev_pct: float = 0.01
    half_kelly: bool = True
    correlation_floor: float = 0.30
    max_position_usd: float = 1000.0
    starting_capital_usd: float = 5000.0
    default_target_gain_pct: float = 0.10
    default_stop_loss_pct: float = 0.15


@dataclass(frozen=True)
class CrucibleConfig:
    seconds_per_attack: int = 90
    max_cycles: int = 2
    temperature: float = 0.1
    default_verdict: str = "NO_TRADE"
    no_trade_threshold: float = 0.6


@dataclass(frozen=True)
class MutationConfig:
    min_paper_cycles: int = 50
    p_value_threshold: float = 0.05
    cohens_d_threshold: float = 0.20
    min_sample_size: int = 20
    probation_cycles: int = 30
    auto_rollback_window: int = 50
    max_ab_tests: int = 3


@dataclass(frozen=True)
class SourceCriticality:
    criticality: str
    staleness_budget_seconds: int


@dataclass(frozen=True)
class ModelBackend:
    url: str
    default_model: str
    structured_output: str
    api_key_ref: str = ""
    base_url: str = ""


@dataclass(frozen=True)
class ModelRegistry:
    backends: dict[str, ModelBackend]
    active: str
    personas: dict[str, str | None]
    candidates: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PMACSConfig:
    resources: ResourcesConfig
    risk: RiskConfig
    crucible: CrucibleConfig
    mutation: MutationConfig
    model_registry: ModelRegistry
    source_criticality: dict[str, SourceCriticality]
    model_hashes: dict[str, str]


def _load_toml(filename: str) -> dict:
    path = CONFIG_DIR / filename
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def _load_json(filename: str) -> dict:
    path = CONFIG_DIR / filename
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def _parse_resources(data: dict) -> ResourcesConfig:
    hw = data.get("hardware", {})
    rt = data.get("runtime", {})
    bu = data.get("budgets", {})
    sl = data.get("stop_loss", {})
    cl = data.get("crash_loop", {})
    cn = data.get("catastrophe_net", {})
    return ResourcesConfig(
        ram_gb=hw.get("ram_gb", 64),
        cpu_cores=hw.get("cpu_cores", 10),
        gpu=hw.get("gpu", "apple_m1_max"),
        backend=rt.get("backend", "llama_server"),
        gguf_path=rt.get("gguf_path", ""),
        threads=rt.get("threads", 8),
        parallel_slots=rt.get("parallel_slots", 3),
        ctx_size=rt.get("ctx_size", 32768),
        quantization=rt.get("quantization", "UD-Q4_K_XL"),
        phase1_seconds_per_symbol=bu.get("phase1_seconds_per_symbol", 270),
        debate_wave_seconds_per_symbol=bu.get("debate_wave_seconds_per_symbol", 180),
        daily_llm_seconds_total=bu.get("daily_llm_seconds_total", 21600),
        stop_loss_check_interval_seconds=sl.get("intraday_check_interval_seconds", 1800),
        quote_freshness_max_seconds=sl.get("quote_freshness_max_seconds", 60),
        crash_loop_max_restarts=cl.get("max_restarts_per_minute", 5),
        catastrophe_stop_pct=cn.get("catastrophe_stop_pct", 0.15),
    )


def _parse_risk(data: dict) -> RiskConfig:
    pos = data.get("position", {})
    ks = data.get("kill_switch", {})
    ev = data.get("ev", {})
    sz = data.get("sizing", {})
    cap = data.get("capital", {})
    pr = data.get("pricing", {})
    return RiskConfig(
        max_single_position_pct=pos.get("max_single_position_pct", 0.20),
        max_concurrent_positions=pos.get("max_concurrent_positions", 5),
        daily_loss_pct=ks.get("daily_loss_pct", 0.05),
        rolling_5d_loss_pct=ks.get("rolling_5d_loss_pct", 0.10),
        reconciliation_tolerance_usd=ks.get("reconciliation_tolerance_usd", 100.0),
        reconciliation_tolerance_pct=ks.get("reconciliation_tolerance_pct", 0.05),
        minimum_ev_pct=ev.get("minimum_ev_pct", 0.01),
        half_kelly=sz.get("half_kelly", True),
        correlation_floor=sz.get("correlation_floor", 0.30),
        max_position_usd=sz.get("max_position_usd", 1000.0),
        starting_capital_usd=cap.get("starting_usd", 5000.0),
        default_target_gain_pct=pr.get("default_target_gain_pct", 0.10),
        default_stop_loss_pct=pr.get("default_stop_loss_pct", 0.15),
    )


def _parse_crucible(data: dict) -> CrucibleConfig:
    tb = data.get("time_budget", {})
    df = data.get("defaults", {})
    sv = data.get("severity", {})
    return CrucibleConfig(
        seconds_per_attack=tb.get("seconds_per_attack", 90),
        max_cycles=tb.get("max_cycles", 2),
        temperature=df.get("temperature", 0.1),
        default_verdict=df.get("default_verdict", "NO_TRADE"),
        no_trade_threshold=sv.get("no_trade_threshold", 0.6),
    )


def _parse_mutation(data: dict) -> MutationConfig:
    act = data.get("activation", {})
    rec = data.get("recommendation", {})
    prob = data.get("probation", {})
    ar = data.get("auto_rollback", {})
    con = data.get("concurrent", {})
    return MutationConfig(
        min_paper_cycles=act.get("min_paper_cycles", 50),
        p_value_threshold=rec.get("p_value_threshold", 0.05),
        cohens_d_threshold=rec.get("cohens_d_threshold", 0.20),
        min_sample_size=rec.get("min_sample_size", 20),
        probation_cycles=prob.get("cycles", 30),
        auto_rollback_window=ar.get("window_cycles", 50),
        max_ab_tests=con.get("max_ab_tests", 3),
    )


def _load_runtime_state() -> dict:
    """Read config/runtime_state.json (gitignored operator-local override).

    Returns {} on missing file, corrupt JSON, or schema mismatch.
    Never raises — load_config() must work even if the file is garbage,
    because the override is advisory; the registry is the source of truth.

    spec_ref: §16 (operator-local config layer)
    """
    if not RUNTIME_STATE_PATH.exists():
        return {}
    try:
        data = json.loads(RUNTIME_STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _parse_model_registry(data: dict) -> ModelRegistry:
    backends = {}
    for name, bd in data.get("backends", {}).items():
        backends[name] = ModelBackend(
            url=bd.get("url", ""),
            default_model=bd.get("default_model", ""),
            structured_output=bd.get("structured_output", ""),
            api_key_ref=bd.get("api_key_ref", ""),
            base_url=bd.get("base_url", ""),
        )
    return ModelRegistry(
        backends=backends,
        active=data.get("active", "llama_server"),
        personas=data.get("personas", {}),
        candidates=data.get("candidates", {}),
    )


def _parse_source_criticality(data: dict) -> dict[str, SourceCriticality]:
    result = {}
    for source_name, source_data in data.get("sources", {}).items():
        result[source_name] = SourceCriticality(
            criticality=source_data.get("criticality", "IMPORTANT"),
            staleness_budget_seconds=source_data.get("staleness_budget_seconds", 86400),
        )
    return result


def _parse_model_hashes(data: dict) -> dict[str, str]:
    return dict(data.get("gguf", {}))


def load_config() -> PMACSConfig:
    """Load all PMACS configuration files.

    Returns a frozen PMACSConfig dataclass with typed access to all settings.

    Runtime override: if config/runtime_state.json (gitignored) carries an
    ``active_backend`` key, it overrides model_registry.json's ``active``
    AFTER parsing. Validation: the override must be a non-empty string AND
    a key of ``backends`` (typos fall through silently — see
    tests/unit/test_runtime_state_persistence.py). This way VCS operations
    (``git pull``, ``git checkout``, fresh clone) cannot silently undo the
    operator's last explicit choice.
    """
    resources_data = _load_toml("resources.toml")
    risk_data = _load_toml("risk.toml")
    crucible_data = _load_toml("crucible.toml")
    mutation_data = _load_toml("mutation.toml")
    model_registry_data = _load_json("model_registry.json")
    source_criticality_data = _load_toml("source_criticality.toml")
    model_hashes_data = _load_toml("model_hashes.toml")

    # Apply runtime_state.json override (advisory; never raises).
    runtime_state = _load_runtime_state()
    override_backend = runtime_state.get("active_backend")
    if (
        isinstance(override_backend, str)
        and override_backend
        and override_backend in model_registry_data.get("backends", {})
    ):
        model_registry_data = {**model_registry_data, "active": override_backend}

    return PMACSConfig(
        resources=_parse_resources(resources_data),
        risk=_parse_risk(risk_data),
        crucible=_parse_crucible(crucible_data),
        mutation=_parse_mutation(mutation_data),
        model_registry=_parse_model_registry(model_registry_data),
        source_criticality=_parse_source_criticality(source_criticality_data),
        model_hashes=_parse_model_hashes(model_hashes_data),
    )
