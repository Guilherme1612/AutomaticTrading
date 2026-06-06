---
phase: full-code-review
reviewed: 2026-05-28T12:59:00Z
depth: standard
files_reviewed: 25
files_reviewed_list:
  - pmacs/engines/pricing.py
  - pmacs/engines/state_machine.py
  - pmacs/engines/flywheel_health.py
  - pmacs/engines/failure_diagnostic.py
  - pmacs/nervous/orchestrator.py
  - pmacs/nervous/api.py
  - pmacs/execution/adapter.py
  - pmacs/execution/service.py
  - pmacs/mutation/daemon.py
  - pmacs/mutation/candidate_generator.py
  - pmacs/mutation/promotion.py
  - pmacs/cortex/kill_switch.py
  - pmacs/cortex/health.py
  - pmacs/cortex/boot_detector.py
  - pmacs/cortex/crash_loop_detector.py
  - pmacs/cortex/self_check.py
  - pmacs/cortex/stop_loss_daemon.py
  - pmacs/cortex/sleep_watch.py
  - pmacs/cortex/flywheel_monitor.py
  - pmacs/cortex/disk_monitor.py
  - pmacs/agents/base.py
  - pmacs/config.py
  - pmacs/constants.py
  - pmacs/schemas/contracts.py
  - pmacs/schemas/system.py
  - pmacs/schemas/flywheel.py
findings:
  critical: 3
  warning: 10
  info: 7
  total: 20
status: issues_found
---

# Code Review Report

**Reviewed:** 2026-05-28T12:59:00Z
**Depth:** standard
**Files Reviewed:** 25
**Status:** issues_found

## Summary

Reviewed the PMACS trading system core: engines, orchestration, execution pipeline, cortex daemons, mutation engine, agent base, schemas, and configuration. The codebase is well-structured overall, with clear spec references, proper audit logging, and strong safety mechanisms (kill switch, TOTP gating, catastrophe-net stops).

Three critical issues found:
1. Duplicate `KillSwitchState` enum with conflicting values across two files
2. `FlywheelHealthSnapshot` naming collision -- two different types with same name and different fields
3. Holding mutations outside `state_machine.py` violate Architecture.md anti-pattern (fields are set directly in orchestrator)

Ten warnings include: `load_config()` called at module import time (pricing.py), schema field mismatches, unguarded Pydantic model mutations, missing error_code on flywheel_monitor WARN event, stop-loss daemon creating classes inside a loop, and self_check stale detection logic error.

## Critical Issues

### CR-01: Duplicate KillSwitchState enum with conflicting values

**File:** `pmacs/schemas/system.py:21` and `pmacs/cortex/kill_switch.py:29`
**Issue:** Two separate `KillSwitchState` enums exist with different state values. `system.py` defines `DISENGAGED` / `ENGAGED`. `kill_switch.py` defines `ARMED` / `ENGAGED`. The actual database uses `ARMED` (from `kill_switch.py`). Any code importing from `schemas/system.py` and checking for `DISENGAGED` will never match, since the DB stores `ARMED`. This can cause the kill switch state check to silently fail to recognize the disarmed state.

**Fix:**
Remove the duplicate from `schemas/system.py` and import from `cortex/kill_switch.py`:
```python
# In schemas/system.py, remove class KillSwitchState entirely.
# All consumers should import from pmacs.cortex.kill_switch import KillSwitchState.
```
If both must exist, unify the values. The canonical states are `ARMED` / `ENGAGED` per `kill_switch.py` and `Architecture.md`.

### CR-02: FlywheelHealthSnapshot naming collision -- two different types

**File:** `pmacs/engines/flywheel_health.py:20` and `pmacs/schemas/flywheel.py:7`
**Issue:** `engines/flywheel_health.py` defines a dataclass `FlywheelHealthSnapshot` with fields `pending_reviews`, `lessons_count`. `schemas/flywheel.py` defines a Pydantic model `FlywheelHealthSnapshot` with fields `max_drawdown_pct`, `cycles_since_calibration`. Neither contains all fields needed by consumers. The orchestrator uses the dataclass version and accesses `snap.pending_reviews` / `snap.lessons_count`. The `flywheel_monitor.py` uses the Pydantic version and accesses `max_drawdown_pct` / `cycles_since_calibration`. If any code imports the wrong one, it will get `AttributeError` at runtime.

**Fix:**
Unify into a single Pydantic model in `schemas/flywheel.py` containing all fields:
```python
class FlywheelHealthSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    rolling_brier: float | None = None
    rolling_sharpe: float | None = None
    max_drawdown_pct: float | None = None
    calibration_gap: float | None = None
    cycles_since_calibration: int = 0
    active_mutations: int = 0
    pending_reviews: int = 0
    lessons_count: int = 0
```
Then remove the dataclass from `engines/flywheel_health.py` and import from schemas.

### CR-03: Holding fields mutated outside state_machine.py

**File:** `pmacs/nervous/orchestrator.py:1672-1676`
**Issue:** The orchestrator directly mutates `holding.entry_price_usd`, `holding.stop_price_usd`, `holding.verdict`, and `holding.conviction_score` outside the state machine. While `Architecture.md section 16.1` only explicitly forbids `holding.state = "ABORTED_LLM"`, the spirit of the spec is that state-machine-managed holdings should transition atomically. These direct mutations happen after `transition()` is called, meaning the audit log records the transition without these values. The holding is also updated again at line 1753 (`holding.entry_price_usd = fill_price`) inside the execution block.

**Fix:**
Create a dedicated method in `state_machine.py` for setting execution fields that also logs to audit:
```python
def set_execution_fields(
    holding: Holding,
    entry_price: float,
    stop_price: float,
    verdict: str,
    conviction_score: float,
    cycle_id: str,
    op_seq: int,
    audit_path: Path | None = None,
) -> Holding:
    """Set execution-related fields on a holding with audit trail."""
    object.__setattr__(holding, "entry_price_usd", entry_price)
    object.__setattr__(holding, "stop_price_usd", stop_price)
    # ... audit log ...
    return holding
```

## Warnings

### WR-01: load_config() called at module import time

**File:** `pmacs/engines/pricing.py:29`
**Issue:** `_load_pricing_config()` is called at module import time. If any config file is missing or malformed, this silently falls back to hardcoded defaults at import time -- before the operator can see any error. Additionally, `load_config()` reads and parses multiple TOML/JSON files on every import. This also means test mocking is harder.

**Fix:** Use lazy initialization:
```python
_MIN_EV: float | None = None
# ...
def _get_min_ev() -> float:
    global _MIN_EV
    if _MIN_EV is None:
        _MIN_EV = _load_pricing_config()[0]
    return _MIN_EV
```

### WR-02: FlywheelHealthSnapshot fields mismatch with schema

**File:** `pmacs/cortex/flywheel_monitor.py:93-100`
**Issue:** The `FlywheelMonitor.get_health()` constructs a `FlywheelHealthSnapshot` with `max_drawdown_pct` and `cycles_since_calibration`, but the dataclass in `engines/flywheel_health.py` used by the orchestrator does not have these fields. Conversely, the dataclass has `pending_reviews` and `lessons_count` that the Pydantic model lacks. This means the flywheel monitor and the orchestrator are seeing different shapes of the same concept.

**Fix:** Same as CR-02 -- unify into a single schema.

### WR-03: Holding model mutation on frozen=False Pydantic model

**File:** `pmacs/engines/state_machine.py:71`
**Issue:** The `Holding` model uses `model_config = ConfigDict(frozen=False)` to allow state machine transitions, but this means any code can mutate any field at any time. The `_sync_price_aliases` and `_check_probabilities` validators will NOT re-run when fields are mutated post-construction via direct assignment. This means `entry_price_usd` / `entry_price` aliases can get out of sync after direct mutation.

**Fix:** Either make the model truly immutable and use `model_copy(update={...})` in the state machine, or add an explicit sync method that must be called after mutation.

### WR-04: Orchestrator uses _current_price as instance variable without initialization

**File:** `pmacs/nervous/orchestrator.py:1259` and `pmacs/nervous/orchestrator.py:1456`
**Issue:** `self._current_price` is set at line 1259 inside `_step_13d_personas` and read at line 1456 in `_step_13h_l_decision` via `getattr(self, '_current_price', 0.0)`. The `getattr` with default masks the fact that this field is never declared in `__init__`. If `_step_13d_personas` is skipped (e.g., checkpoint resume skips it), `_step_13h_l_decision` will silently use `0.0` as the current price, which would produce a zero-share sizing.

**Fix:** Initialize `self._current_price: float = 0.0` in `__init__` alongside the other state fields.

### WR-05: self_check stale detection logic error

**File:** `pmacs/cortex/self_check.py:164-170`
**Issue:** When the first health check fails, `last_success_time` is set to `now` (the current time), making `stale_seconds = now - last_success_time = 0`. On the next failed check, `stale_seconds` will be the interval between checks (e.g., 60s), not the time since the last successful check. This means the kill switch will never trigger unless checks fail for `stale_threshold / check_interval` iterations, which is 2 full iterations instead of the intended 120 seconds of continuous failure.

**Fix:**
```python
if healthy:
    last_success_time = time.time()
else:
    now = time.time()
    if last_success_time is None:
        # First check failed -- start timer from this moment
        last_success_time = now  # This is correct
    # stale_seconds now = now - last_success_time
    # After 2 failed checks (60s apart), stale_seconds = 60, not 120
    # To fix: track last_success_time correctly
```
Actually, re-examining: after the 2nd failure, `stale_seconds = now - last_success_time` where `last_success_time` was set to `now` of the 1st failure. So after 2 failures at 60s interval, stale = 60s. After 3 failures, stale = 120s. The threshold is 120s. So it takes 3 failures (180s wall clock) to trigger, not 2 (120s). This is a minor but meaningful delay in safety-critical code.

**Fix:** Initialize `last_success_time = time.time()` at startup instead of `None`, so the stale calculation starts from the process start, not from the first failure:
```python
last_success_time: float = time.time()
```

### WR-06: Stop-loss daemon defines class inside loop

**File:** `pmacs/cortex/stop_loss_daemon.py:245-246`
**Issue:** `_HoldingProxy` is defined inside the `while True` loop and inside the `for row in rows` iteration. This creates a new class object on every iteration of every loop cycle. While functionally harmless, it is wasteful and makes debugging harder (class identity changes each iteration).

**Fix:** Move the class definition outside the loop, at module level:
```python
class _HoldingProxy:
    __slots__ = ("id", "ticker", "stop_price_usd",
                 "trailing_stop_price_usd", "trailing_stop_armed")
```

### WR-07: Flywheel monitor missing error_code on WARN log

**File:** `pmacs/cortex/flywheel_monitor.py:130-142`
**Issue:** The `FLYWHEEL_UNHEALTHY` log event at level `WARN` does not include an `error_code` parameter. Per `Architecture.md section 16.15`: "Missing error_code on WARN+ debug events -- every WARN+ has a canonical code." This violates the anti-pattern check.

**Fix:**
```python
log_debug(
    "FLYWHEEL_UNHEALTHY",
    payload={...},
    level="WARN",
    error_code="FLYWHEEL_UNHEALTHY",
    msg=...,
)
```

### WR-08: check_promotion_gates silently re-maps mode keys

**File:** `pmacs/engines/flywheel_health.py:196-200`
**Issue:** When `key` (e.g., `SHADOW_PAPER_to_PAPER_VALIDATED`) is not found, it falls back to `PAPER_to_PAPER_VALIDATED`. If neither exists, line 201 `thresholds = PROMOTION_THRESHOLDS[key]` will raise `KeyError` with the original key, which is confusing since the user sees the fallback was attempted. The fallback should be explicit and handle the case where neither key exists.

**Fix:**
```python
if key not in PROMOTION_THRESHOLDS:
    alt_key = f"PAPER_to_{target_mode}"
    key = alt_key
if key not in PROMOTION_THRESHOLDS:
    raise ValueError(f"No promotion thresholds found for {current_mode} -> {target_mode}")
thresholds = PROMOTION_THRESHOLDS[key]
```

### WR-09: orchestrator creates new event loop per symbol execution

**File:** `pmacs/nervous/orchestrator.py:1746-1747`
**Issue:** `_asyncio.new_event_loop()` is created and closed for every symbol that reaches execution. This is inefficient and can fail if called from within an existing event loop (e.g., if the orchestrator is ever run inside an async context). The `run_until_complete` call is also blocking.

**Fix:** Consider reusing a single event loop for all symbol executions, or using `asyncio.run()` for cleaner lifecycle management. Alternatively, make the orchestrator itself async.

### WR-10: crash_loop_detector.record_restart does not ensure table exists before INSERT

**File:** `pmacs/cortex/crash_loop_detector.py:53-54`
**Issue:** `record_restart()` calls `_ensure_table(conn)` but the INSERT at line 57 targets `process_state` which is a DIFFERENT table (defined in `sqlite.py`). If `process_state` does not exist yet (first run), this INSERT will fail with `sqlite3.OperationalError`. The `_ensure_table` only creates `process_restarts`, not `process_state`.

**Fix:** Either add the `process_state` DDL to `_ensure_table`, or wrap the second INSERT in a try/except for `OperationalError`:
```python
try:
    conn.execute(
        "INSERT INTO process_state ...",
        ...
    )
except sqlite3.OperationalError:
    # process_state table may not exist yet; skip state update
    pass
```

## Info

### IN-01: pricing.py module-level config load may mask config errors

**File:** `pmacs/engines/pricing.py:26`
**Issue:** The bare `except Exception: return (0.01, 0.10, 0.15)` silently swallows all config errors including typos in config files. This makes debugging misconfiguration very difficult.

**Fix:** Log the error before falling back:
```python
except Exception as exc:
    log_debug("PRICING_CONFIG_FALLBACK", payload={"error": str(exc)}, level="WARN",
              error_code="CONFIG_LOAD_FAILED", msg=f"Pricing config load failed, using defaults: {exc}")
    return (0.01, 0.10, 0.15)
```

### IN-02: api.py uses module-level mutable globals for state

**File:** `pmacs/nervous/api.py:33-35`
**Issue:** `_publisher`, `_session_mgr`, `_heartbeat_dir` are module-level mutable globals. While `configure()` exists for testing, the globals make concurrent access unsafe and testing requires careful cleanup.

**Fix:** Consider using FastAPI's dependency injection (`Depends()`) or an application state object.

### IN-03: api.py TOTP audit uses relative path

**File:** `pmacs/nervous/api.py:136`
**Issue:** `audit_path = Path("logs/audit.log")` uses a relative path that depends on the working directory at runtime. This will fail silently if the process is started from a different directory.

**Fix:** Use the canonical audit path from config:
```python
from pmacs.config import AUDIT_LOG_PATH
audit_path = AUDIT_LOG_PATH
```

### IN-04: config.py MODEL_REGISTRY_TYPE candidates field uses mutable default

**File:** `pmacs/config.py:128`
**Issue:** `candidates: dict = field(default_factory=dict)` is correct (uses `field(default_factory=...)`), but `backends`, `personas`, and `candidates` are all typed as plain `dict` without key/value types. This makes it easy to pass incorrect data.

**Fix:** Consider using typed dicts or stricter validation.

### IN-05: stop_loss_daemon uses `pytz` instead of `zoneinfo`

**File:** `pmacs/cortex/stop_loss_daemon.py:41`
**Issue:** `pytz.timezone("US/Eastern")` is used instead of the standard library `zoneinfo.ZoneInfo("US/Eastern")`. The `boot_detector.py` correctly uses `zoneinfo`. `pytz` is a third-party dependency that could be removed.

**Fix:** Replace with:
```python
from zoneinfo import ZoneInfo
eastern = ZoneInfo("US/Eastern")
now = datetime.now(eastern)
```

### IN-06: Multiple files open SQLite connections without WAL mode

**File:** `pmacs/nervous/orchestrator.py:645-655`, `pmacs/cortex/boot_detector.py:44-45`
**Issue:** Several files open SQLite connections without setting `PRAGMA journal_mode=WAL`, while `kill_switch.py` and `crash_loop_detector.py` do set it. Inconsistent WAL usage can cause locking issues when multiple processes access the same database.

**Fix:** Standardize: always set WAL mode when opening SQLite connections, or use a shared connection factory.

### IN-07: flywheel_health.py demotion uses _MODE_DEMOTION_ORDER.index() inside loop

**File:** `pmacs/engines/flywheel_health.py:311`
**Issue:** `_MODE_DEMOTION_ORDER.index(mode)` is called inside the loop iteration that already found `current_mode == mode`. The index call is redundant -- `idx` is already known from `enumerate`.

**Fix:**
```python
for idx, mode in enumerate(_MODE_DEMOTION_ORDER):
    if current_mode == mode:
        demoted_mode = _MODE_DEMOTION_ORDER[idx + 1] if idx + 1 < len(_MODE_DEMOTION_ORDER) else "PAPER"
        key = f"{mode}_to_{demoted_mode}"
        break
```

---

_Reviewed: 2026-05-28T12:59:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
