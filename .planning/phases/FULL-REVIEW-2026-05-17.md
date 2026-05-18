---
phase: full-codebase
reviewed: 2026-05-17T18:04:00Z
depth: standard
files_reviewed: 42
files_reviewed_list:
  - pmacs/agents/base.py
  - pmacs/engines/arbitration.py
  - pmacs/engines/state_machine.py
  - pmacs/engines/conviction.py
  - pmacs/engines/sizing.py
  - pmacs/engines/reconciliation.py
  - pmacs/engines/lessons.py
  - pmacs/engines/flywheel_health.py
  - pmacs/engines/fundamental_routing.py
  - pmacs/engines/portfolio_risk_gate.py
  - pmacs/engines/stop_loss_monitor.py
  - pmacs/engines/trailing_stop.py
  - pmacs/execution/service.py
  - pmacs/execution/alpaca_paper.py
  - pmacs/execution/signing.py
  - pmacs/execution/catastrophe_net.py
  - pmacs/execution/adapter.py
  - pmacs/storage/sqlite.py
  - pmacs/storage/kuzu.py
  - pmacs/storage/qdrant.py
  - pmacs/storage/consistency.py
  - pmacs/storage/audit.py
  - pmacs/storage/keychain.py
  - pmacs/nervous/orchestrator.py
  - pmacs/nervous/auth.py
  - pmacs/web/app.py
  - pmacs/web/data.py
  - pmacs/web/routes/settings.py
  - pmacs/web/routes/cortex.py
  - pmacs/web/sse_client.py
  - pmacs/constants.py
  - pmacs/cortex/kill_switch.py
  - pmacs/cortex/stop_loss_daemon.py
  - pmacs/cortex/flywheel_monitor.py
  - pmacs/mutation/candidate_generator.py
  - pmacs/logsys/error_classifier.py
  - pmacs/logsys/replay.py
  - pmacs/schemas/contracts.py
  - pmacs/schemas/flywheel.py
  - pmacs/schemas/trade.py
  - pmacs/sim/alpaca_paper_adapter.py
  - pmacs/installer/wizard.py
findings:
  critical: 3
  warning: 9
  info: 7
  total: 19
status: issues_found
---

# Full Codebase Code Review Report

**Reviewed:** 2026-05-17T18:04:00Z
**Depth:** standard
**Files Reviewed:** 42
**Status:** issues_found

## Summary

Full review of all source files under `pmacs/`. Found 3 critical issues (import errors that will crash at runtime, undefined variable reference) and 9 warnings (logic bugs, missing state transitions, data loss risks, TOTP bypass). The codebase is well-structured overall with strong spec alignment, but several bugs will prevent correct runtime behavior.

## Critical Issues

### CR-01: Broken import -- `pmacs.data.keychain` does not exist

**File:** `pmacs/web/routes/settings.py:142` and `pmacs/web/routes/settings.py:216`
**Issue:** Two lazy imports reference `from pmacs.data.keychain import get_api_key`, but the `keychain` module only exists at `pmacs/storage/keychain.py`, not `pmacs/data/keychain.py`. This will raise `ModuleNotFoundError` at runtime when TOTP verification is attempted during mutation promote or rollback operations. The mutation TOTP gate is a Non-Negotiable (#5) security requirement.
**Fix:**
```python
# Lines 142 and 216: Change
from pmacs.data.keychain import get_api_key
# To:
from pmacs.storage.keychain import get_api_key
```

### CR-02: Undefined `logger` variable in reconciliation engine

**File:** `pmacs/engines/reconciliation.py:37,47`
**Issue:** `load_tolerance_from_config()` calls `logger.debug(...)` on lines 37 and 47, but `logger` is never imported or defined. The module only imports `log_debug` from `pmacs.logsys.debug_log` -- it has no `import logging; logger = logging.getLogger(__name__)` statement. This will raise `NameError` at runtime when the config file is missing or the TOML parser is unavailable.
**Fix:**
```python
# Either add at module level:
import logging
logger = logging.getLogger(__name__)

# Or replace logger.debug calls with log_debug:
log_debug(
    "RECONCILIATION_CONFIG_MISSING",
    payload={"path": str(path)},
    level="DEBUG",
    msg=f"Config file not found at {path}, using default tolerances",
)
```

### CR-03: Undefined `logger` variable in KuzuDB adapter

**File:** `pmacs/storage/kuzu.py:144`
**Issue:** `_init_schema()` catches DDL exceptions and calls `logger.debug("KuzuDB DDL skip: %s", exc)`, but `logger` is never imported. The module only imports `log_debug` from `pmacs.logsys`. This will crash with `NameError` whenever a DDL statement targets an already-existing table (which is the common case after first init).
**Fix:**
```python
# Add at module level (after other imports):
import logging
logger = logging.getLogger(__name__)

# Or replace logger.debug with log_debug:
log_debug(
    "KUZU_DDL_SKIP",
    payload={"ddl": ddl[:80], "error": str(exc)},
    level="DEBUG",
    msg=f"KuzuDB DDL skip: {exc}",
)
```

## Warnings

### WR-01: Trailing stop loses price when already armed -- returns 0.0

**File:** `pmacs/engines/trailing_stop.py:64-65`
**Issue:** `maybe_arm_trailing()` returns `TrailingStopState(armed=True, trailing_stop_price=0.0)` when `is_armed` is already True. The docstring says "preserves existing trailing price" but the code hardcodes `0.0` instead. The caller is expected to separately call `maybe_ratchet_trailing()` to update, but if the caller only calls `maybe_arm_trailing()` (as is the case in the orchestrator's per-symbol pipeline), the trailing stop price is silently reset to zero, which would never trigger a trailing stop exit and could cause unlimited losses.
**Fix:**
```python
def maybe_arm_trailing(
    entry_price, current_price, stop_loss_price, atr_20, is_armed,
    current_trailing_price: float = 0.0,  # Add this parameter
) -> TrailingStopState:
    if is_armed:
        return TrailingStopState(armed=True, trailing_stop_price=current_trailing_price)
    # ... rest unchanged
```

### WR-02: TOTP bypass allowed in mutation promote/rollback endpoints

**File:** `pmacs/web/routes/settings.py:149-151` and `pmacs/web/routes/settings.py:223-224`
**Issue:** The `mutation_promote` and `mutation_rollback` endpoints have a bare `except Exception: pass` after TOTP verification. If TOTP verification fails for any reason (import error, misconfigured secret, network issue), the endpoint silently skips TOTP enforcement and proceeds with the mutation. This violates Non-Negotiable #5 ("ALL mutations require operator TOTP"). The `mutation_reject` endpoint (line 179) has no TOTP gate at all, which is acceptable for rejection, but the promote/rollback bypass is a security risk.
**Fix:**
```python
# Lines 149-151 and 223-224: Replace bare except with:
except Exception as exc:
    # TOTP not configured -- DENY mutation, do not bypass
    logger.warning("TOTP verification failed: %s", exc)
    return JSONResponse(
        {"ok": False, "error": "TOTP verification unavailable -- cannot proceed"},
        status_code=500,
    )
```

### WR-03: Missing INTERRUPT state in holding state transitions

**File:** `pmacs/schemas/contracts.py:95`
**Issue:** `VALID_TRANSITIONS` maps `HALTED` to only `{HoldingState.CANDIDATE}`, but `INTERRUPTED` is a terminal state (line 53) and several non-terminal states can transition to `INTERRUPTED`. However, `INTERRUPTED` itself has no entry in `VALID_TRANSITIONS` -- meaning if code attempts to transition *from* `INTERRUPTED` (e.g., to recover an interrupted holding), it will fail. More critically, there is no transition path *out* of `RESOLUTION_TIMEOUT` -- it is listed as a state but has no entry in `VALID_TRANSITIONS`, so `is_valid_transition(RESOLUTION_TIMEOUT, any)` returns False, which is correct since it is terminal. But `HALTED -> CANDIDATE` seems incomplete -- HALTED should probably also allow transition to `INTERRUPTED` if the cycle is killed while halted.
**Fix:** Verify with spec whether `HALTED` needs additional transitions. At minimum, document the intentional restriction.

### WR-04: Stop-loss daemon connection leak on exception path

**File:** `pmacs/cortex/stop_loss_daemon.py:207-297`
**Issue:** In `run_stop_loss_loop()`, the SQLite connection is opened at line 208 inside a `try` block, but the `conn.close()` at line 273 is inside the same `try` block. If an exception occurs between lines 208 and 273 (e.g., during the `for row in rows` iteration), the `except` at line 289 catches it but does not close the connection. The connection is leaked. This accumulates over time in a long-running daemon.
**Fix:**
```python
conn = None
try:
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(...).fetchall()
    for row in rows:
        # ... processing
except Exception as exc:
    # error handling
finally:
    if conn:
        conn.close()
```

### WR-05: Qdrant upsert uses lossy UUID truncation (32 bits)

**File:** `pmacs/storage/qdrant.py:176`
**Issue:** The Qdrant upsert generates a numeric ID via `uuid.uuid5(uuid.NAMESPACE_URL, id).int >> 96`, which right-shifts a 128-bit UUID by 96 bits, keeping only the lowest 32 bits. This means two different string IDs have a collision probability of ~1 in 4 billion per upsert (birthday paradox: ~1 in 65K after 65K upserts). While the `retrieve` method at line 229 uses the same formula, any collision silently overwrites the previous point. For a system with thesis/memo/lesson embeddings, this is a data integrity risk.
**Fix:**
```python
# Use full UUID as int (128 bits) instead of truncating to 32 bits:
id=uuid.uuid5(uuid.NAMESPACE_URL, id).int,  # Full 128-bit int
```
Or use the UUID object directly if Qdrant supports it.

### WR-06: AuditWriter.append silently overwrites cycle_id in payload

**File:** `pmacs/storage/audit.py:56-57`
**Issue:** `append()` creates a shallow copy with `{**payload, "cycle_id": cycle_id}`. If the caller passes a payload that already contains a `cycle_id` key, it will be silently overwritten with the value from the `cycle_id` parameter. This is a minor data integrity concern -- if the caller's `cycle_id` differs from the parameter, the audit log records the parameter value, not the original payload value.
**Fix:** Document this behavior, or warn/raise if payload already contains `cycle_id`.

### WR-07: Schema `datetime.utcnow` is deprecated in Python 3.12+

**File:** `pmacs/schemas/contracts.py:114,141,142`, `pmacs/schemas/trade.py:40`, `pmacs/schemas/data.py:65`, `pmacs/schemas/catalysts.py:44`
**Issue:** Multiple Pydantic models use `default_factory=datetime.utcnow` which is deprecated since Python 3.12 and will be removed in a future version. The replacement is `datetime.now(timezone.utc)`.
**Fix:**
```python
from datetime import datetime, timezone
created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

### WR-08: Flywheel health snapshot in orchestrator uses hardcoded zeros

**File:** `pmacs/nervous/orchestrator.py:518-522`
**Issue:** `_step_flywheel_health()` calls `snapshot_health(rolling_brier_avg=0.0, rolling_sharpe=0.0, calibration_gap=0.0)` with all zeros instead of reading real metrics from DuckDB/SQLite. The `FlywheelMonitor` class in `cortex/flywheel_monitor.py` exists and can read real metrics, but the orchestrator does not use it. This means flywheel health is always recorded as "all zeros" in the cycle debug log, making the data meaningless for post-cycle analysis.
**Fix:**
```python
def _step_flywheel_health(self, cycle_id: str) -> None:
    from pmacs.cortex.flywheel_monitor import FlywheelMonitor
    duckdb_path = Path(str(self._db_path)).parent / "pmacs_analytics.duckdb"
    monitor = FlywheelMonitor(db_path=self._db_path, duckdb_path=duckdb_path)
    snap = monitor.get_health()
    # use snap.rolling_brier, snap.rolling_sharpe, etc.
```

### WR-09: `_step_dispatch` table is partially dead code

**File:** `pmacs/nervous/orchestrator.py:140-145`
**Issue:** The `_step_dispatch` dict maps steps 0, 1, 4, 5 to handler methods, but `run_cycle()` calls these steps directly (lines 252-271) rather than through the dispatch table. The dispatch table is never consulted for these steps, making it misleading dead code.
**Fix:** Either remove the dispatch table or refactor `run_cycle` to use it consistently.

## Info

### IN-01: `mutation_candidates` view always returns NULL for `trending_direction`

**File:** `pmacs/web/data.py:461` referencing `pmacs/storage/sqlite.py:186`
**Issue:** The SQL query selects `trending_direction` from the `mutation_candidates` view, but the view definition always returns `NULL` for this column. The `trending_direction` field will always be `None` in the UI.
**Fix:** Either implement the trending direction computation or remove the column from the query.

### IN-02: Stop-loss daemon defines `_HoldingProxy` class inside loop body

**File:** `pmacs/cortex/stop_loss_daemon.py:244-245`
**Issue:** The `_HoldingProxy` class is defined inside the `for row in rows` loop, creating a new class object on every iteration. While functionally correct, this is inefficient and makes the code harder to test.
**Fix:** Move the class definition outside the loop or use `types.SimpleNamespace`.

### IN-03: `get_debug_events` loads entire JSONL file into memory

**File:** `pmacs/web/data.py:556-583`
**Issue:** Reads the entire debug JSONL file into memory, parses every line, then returns the last 200 entries. For a long-running system, this could consume significant memory.
**Fix:** Use a bounded deque while iterating, or seek to the end of the file.

### IN-04: `get_risk_metrics` creates new DuckDB adapter per call without closing

**File:** `pmacs/web/data.py:147-168`
**Issue:** Creates a new `DuckDBAdapter` on every call. If DuckDB uses file handles, this could leak.
**Fix:** Use a shared adapter instance or ensure proper cleanup.

### IN-05: Re-export modules use wildcard imports

**File:** `pmacs/execution/alpaca_paper.py`, `pmacs/stop_loss_daemon.py`
**Issue:** Re-export shims use `from X import *` which can mask import errors.
**Fix:** Prefer explicit re-exports.

### IN-06: `flywheel_monitor.py` references non-existent column `calibrated`

**File:** `pmacs/cortex/flywheel_monitor.py:79`
**Issue:** The SQL query references `calibrated = 1` but the `cycles` table schema does not define a `calibrated` column. The query raises `OperationalError`, caught silently, always returning `cycles_since = 0`.
**Fix:** Add the `calibrated` column to the `cycles` table or remove the query.

### IN-07: Web routes mutation promote writes to `mutation_log` without dimension/target

**File:** `pmacs/web/routes/settings.py:167-172`
**Issue:** The INSERT into `mutation_log` selects `dimension` and `target` from `mutation_proposals`, but if the proposal row is not found (race condition between the UPDATE and INSERT), the INSERT will insert NULL for `dimension` and `target`, violating the NOT NULL constraint on `mutation_log.dimension` and `mutation_log.target`.
**Fix:** Wrap the UPDATE + INSERT in a transaction, or use the proposal data from a prior SELECT.

---

_Reviewed: 2026-05-17T18:04:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
