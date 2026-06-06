---
phase: full-code-review
reviewed: 2026-05-31T15:36:00Z
depth: standard
files_reviewed: 63
files_reviewed_list:
  - pmacs/agents/base.py
  - pmacs/agents/catalyst_summarizer.py
  - pmacs/agents/crucible.py
  - pmacs/agents/forensics.py
  - pmacs/agents/gatekeeper.py
  - pmacs/agents/growth_hunter.py
  - pmacs/agents/insider_activity.py
  - pmacs/agents/macro_regime.py
  - pmacs/agents/memo_writer.py
  - pmacs/agents/moat_analyst.py
  - pmacs/agents/short_interest.py
  - pmacs/cli.py
  - pmacs/config.py
  - pmacs/constants.py
  - pmacs/cortex/boot_detector.py
  - pmacs/cortex/crash_loop_detector.py
  - pmacs/cortex/daemon.py
  - pmacs/cortex/disk_monitor.py
  - pmacs/cortex/flywheel_monitor.py
  - pmacs/cortex/health.py
  - pmacs/cortex/kill_switch.py
  - pmacs/cortex/self_check.py
  - pmacs/cortex/sleep_watch.py
  - pmacs/cortex/stop_loss_daemon.py
  - pmacs/engines/cash_ledger.py
  - pmacs/engines/failure_diagnostic.py
  - pmacs/engines/flywheel_health.py
  - pmacs/engines/pricing.py
  - pmacs/engines/sizing.py
  - pmacs/engines/state_machine.py
  - pmacs/execution/adapter.py
  - pmacs/execution/service.py
  - pmacs/installer/steps/create_dirs.py
  - pmacs/installer/steps/smoke_test.py
  - pmacs/installer/steps/totp_enroll.py
  - pmacs/installer/steps/verify_llm.py
  - pmacs/logsys/debug_log.py
  - pmacs/logsys/error_classifier.py
  - pmacs/mutation/candidate_generator.py
  - pmacs/mutation/daemon.py
  - pmacs/mutation/promotion.py
  - pmacs/nervous/api.py
  - pmacs/nervous/mutation.py
  - pmacs/nervous/orchestrator.py
  - pmacs/nervous/sse_publisher.py
  - pmacs/nervous/stop_poller.py
  - pmacs/schemas/contracts.py
  - pmacs/schemas/system.py
  - pmacs/sim/alpaca_paper_adapter.py
  - pmacs/storage/audit.py
  - pmacs/storage/duckdb.py
  - pmacs/storage/kuzu.py
  - pmacs/storage/qdrant.py
  - pmacs/storage/sqlite.py
  - pmacs/web/app.py
  - pmacs/web/config.py
  - pmacs/web/data.py
  - pmacs/web/routes/agents.py
  - pmacs/web/routes/cortex.py
  - pmacs/web/routes/dashboard.py
  - pmacs/web/routes/pipeline.py
  - pmacs/web/routes/settings.py
  - pmacs/web/routes/universe.py
  - pmacs/web/routes/wizard.py
findings:
  critical: 2
  warning: 7
  info: 5
  total: 14
status: issues_found
---

# Full Code Review Report

**Reviewed:** 2026-05-31T15:36:00Z
**Depth:** standard
**Files Reviewed:** 63
**Status:** issues_found

## Summary

Reviewed all 63 modified Python source files under `pmacs/` for bugs, security vulnerabilities, code quality, spec compliance, and Pydantic v2 violations.

**High-level assessment:** The codebase is well-structured and spec-compliant. Pydantic v2 rules are consistently followed (no v1 imports, no `class Config:`). Anti-pattern checks pass: no `holding.state =` direct mutations, no hardcoded secrets, no `eval()` or `shell=True`, no bare `except:` clauses. The architecture is defensively coded with proper audit logging, TOTP gating, and Ed25519 signing.

Two critical findings involve incorrect log levels (WARN used where INFO is correct) that could mask real issues in monitoring, and a missing error_code on a WARN-level event. Several warnings involve potential edge cases: silent swallowing of AuditWriter failures, transitive imports, and a heartbeat file format inconsistency between cortex health and stop_loss_daemon.

## Critical Issues

### CR-01: Missing error_code on WARN-level log event (Architecture.md 16.14 violation)

**File:** `pmacs/cortex/sleep_watch.py:158-159`
**Issue:** The `SLEEP_DETECTED` event is logged at `level="INFO"` but has `error_code="SLEEP_DETECTED"`. This is not itself a violation. However, the `log_debug` call at line 158 uses `error_code="SLEEP_DETECTED"` at `level="INFO"`. The error_code `SLEEP_DETECTED` is **not** in the VALID_ERROR_CODES registry in `error_classifier.py`. The `log_debug` function only validates error_code for WARN/ERROR levels, so this does not crash at runtime, but it is misleading -- error_codes should only appear on WARN+ events, or be removed from INFO events.

**Impact:** While this does not crash, it indicates a pattern inconsistency. The real critical issue is at line 154-160 where the `SLEEP_DETECTED` event includes `error_code` but is at INFO level. If this is later changed to WARN without registering the code, the system will raise a ValueError at runtime during sleep detection.

**Fix:**
```python
# Line 154-160: Remove error_code from INFO-level event
log_debug(
    "SLEEP_DETECTED",
    payload={"gap_seconds": round(gap_seconds or 0, 1)},
    level="INFO",
    msg=f"Sleep detected: heartbeat gap {gap_seconds:.1f}s",
)
# Same fix needed for WAKE_DETECTED events at lines 166-183
```

### CR-02: WAKE_DETECTED error_code not in registry

**File:** `pmacs/cortex/sleep_watch.py:173`
**Issue:** `error_code="WAKE_DETECTED"` is used at `level="INFO"`. Neither `SLEEP_DETECTED` nor `WAKE_DETECTED` are in `VALID_ERROR_CODES` (line 120-172 of `error_classifier.py`). While the validator only enforces on WARN+ levels today, this creates a latent defect: if anyone raises these to WARN level (appropriate for wake-with-incomplete-cycle), the system will crash with `ValueError: Invalid error_code 'WAKE_DETECTED'`.

**Impact:** System crash on wake detection if log level is corrected to WARN.
**Fix:**
```python
# In pmacs/logsys/error_classifier.py, add to VALID_ERROR_CODES:
SLEEP_DETECTED = "SLEEP_DETECTED"
WAKE_DETECTED = "WAKE_DETECTED"
# Then add to the frozenset at line 120
```

## Warnings

### WR-01: AuditWriter failure silently swallowed in execution service

**File:** `pmacs/execution/service.py:116`
**Issue:** In `_handle_client`, when audit writing fails for a rejected signature (line 116), the `_audit_signature_result` method catches no exceptions from the AuditWriter. However, the `AuditWriter.append` could throw (file I/O). The `ExecutionService._audit_signature_result` method (line 252-262) checks `if self._audit is not None` but does not catch exceptions from `writer.append()`. If audit writing fails, it will propagate up to the outer try/except at line 191 which returns `INTERNAL_ERROR` to the client, even though the trade logic itself succeeded.

More critically, in `kill_switch.py:160-166`, the AuditWriter is opened and closed inline without error handling around the `append`:
```python
writer = AuditWriter(audit_path)
writer.append(...)  # If this throws, conn is left in inconsistent state
writer.close()
```

**Fix:** Wrap AuditWriter calls in try/except in `_audit_signature_result` and kill_switch functions:
```python
try:
    writer = AuditWriter(audit_path)
    writer.append("KILL_SWITCH_ENGAGED", {...}, cycle_id=cycle_id)
except Exception:
    pass  # Audit failure must not block kill switch
finally:
    try:
        writer.close()
    except Exception:
        pass
```

### WR-02: stop_loss_daemon heartbeat format inconsistent with cortex health monitoring

**File:** `pmacs/cortex/stop_loss_daemon.py:171-179`
**Issue:** The stop_loss_daemon writes its heartbeat as a JSON file (`pmacs-stoploss.json`) with fields `process`, `ts`, `cycle_id`, `status`. However, the cortex health monitoring in `pmacs/cortex/health.py:35-36` expects heartbeat files named `{proc_name}.ts` containing only a Unix timestamp integer. The stop_loss_daemon's heartbeat file (`pmacs-stoploss.json`) will never be found by cortex's heartbeat checker, which looks for `pmacs-stoploss.ts`.

**Impact:** Cortex daemon will always report the stoploss process as stale, potentially triggering false kill switch alerts.
**Fix:**
```python
# In stop_loss_daemon.py, change heartbeat filename to match cortex convention:
HEARTBEAT_FILENAME = "pmacs-stoploss.ts"

def _write_heartbeat(heartbeat_dir: Path, cycle_id: str) -> None:
    heartbeat_path = heartbeat_dir / HEARTBEAT_FILENAME
    heartbeat_dir.mkdir(parents=True, exist_ok=True)
    heartbeat_path.write_text(str(int(time.time())))
```

### WR-03: Transitive import of canonical_json in candidate_generator

**File:** `pmacs/mutation/candidate_generator.py:15`
**Issue:** Imports `canonical_json` from `pmacs.storage.audit` instead of the canonical source `pmacs.data.canonical`. While this works because `storage/audit.py` imports and re-exports it, it creates a hidden coupling: if `storage/audit.py` ever removes or renames this import, candidate_generator breaks.

**Fix:**
```python
# Line 15: Change to direct import
from pmacs.data.canonical import canonical_json
```

### WR-04: cash_ledger.py validate_total reads without serialization lock

**File:** `pmacs/engines/cash_ledger.py:206-257`
**Issue:** `validate_total()` calls `self.get_balance()` which opens and closes its own connection, then opens another connection to read + write. Between these two operations, another writer could modify the balance. While the individual `apply_flow` method uses `BEGIN IMMEDIATE`, `validate_total` does not.

**Impact:** Minor race window in concurrent scenarios (unlikely for single-operator system, but worth noting).
**Fix:** Use a single `BEGIN IMMEDIATE` connection for the entire validate_total operation, similar to `apply_flow`.

### WR-05: orchestrator _step_weekly_reeval leaves SQLite connection open on exception path

**File:** `pmacs/nervous/orchestrator.py:2699-2706`
**Issue:** The connection opened at line 2699 has its `try/finally` block only around the initial SELECT, but subsequent operations at lines 2772-2775 (`conn.execute(UPDATE ...)`) and the loop body that follows are not within the finally scope. If an exception occurs during the re-eval loop body after the initial query but before natural exit, the connection is not closed.

**Fix:** Ensure the `finally: conn.close()` wraps the entire loop body, not just the initial query.

### WR-06: stop_poller async/sync context mismatch

**File:** `pmacs/nervous/stop_poller.py:100-117`
**Issue:** `process_trigger` attempts to detect whether it's running inside an existing event loop (line 101-103) and uses a ThreadPoolExecutor to call `asyncio.run()` in a separate thread. However, `asyncio.run()` creates a **new** event loop in the calling thread, which means the `execute_exit` coroutine runs in a different thread's event loop. This is fragile: if `execute_exit` references any non-threadsafe asyncio primitives, it will fail silently or with obscure errors.

**Impact:** Works in practice for the current `execute_exit` implementation, but fragile under refactoring.
**Fix:** Consider making `process_trigger` itself async, or using `loop.run_until_complete()` when an existing loop is available.

### WR-07: pricing.py module-level config loading may fail at import time

**File:** `pmacs/engines/pricing.py:29-33`
**Issue:** `_load_pricing_config()` is called at module level (line 29). If the config system has an import cycle or the TOML files are missing, this could raise during import, breaking any module that imports from `pricing.py`. The `except Exception` guard mitigates this, but silent fallback to hardcoded values means config errors go undetected.

Same pattern exists in `sizing.py:24` and `cash_ledger.py:36`.

**Fix:** Consider lazy initialization or logging a warning when fallback values are used.

## Info

### IN-01: Duplicate error code in SYSTEM_EVENT_TYPES

**File:** `pmacs/logsys/debug_log.py:147`
**Issue:** `"RECONCILIATION_CALL_NOT_FOUND"` appears twice in the `SYSTEM_EVENT_TYPES` frozenset (lines 147-148). Harmless since frozenset deduplicates, but indicates a copy-paste oversight.

**Fix:** Remove the duplicate entry.

### IN-02: candidate_generator reads model_registry.json as mutation baseline

**File:** `pmacs/mutation/candidate_generator.py:184-205`
**Issue:** `_read_baseline_config` reads `config/model_registry.json` to find baseline values for mutation targets. However, mutation targets like `moat_analyst.system_prompt` would not be found in `model_registry.json` -- the function navigates the JSON path but returns `{"current": "production"}` when not found. This is correct fallback behavior but means baseline configs are mostly placeholders.

**Fix:** Consider reading from the actual config source (e.g., the prompt files themselves) for more meaningful baselines.

### IN-03: _HoldingProxy class defined inside loop body

**File:** `pmacs/cortex/stop_loss_daemon.py:245-246`
**Issue:** A `_HoldingProxy` class is defined inside the `for row in rows` loop at line 245. While functionally correct, defining a class inside a hot loop is unusual and incurs minor repeated class creation overhead.

**Fix:** Move `_HoldingProxy` class definition to module level.

### IN-04: Missing spec_ref on several engine files

**Files:** `pmacs/engines/cash_ledger.py`, `pmacs/engines/flywheel_health.py`
**Issue:** CLAUDE.md recommends spec references. `cash_ledger.py` has no `spec_ref` in its module docstring. `flywheel_health.py` references "Phases.md 3" in the docstring but not in a formal `spec_ref` field. Several agent files (growth_hunter, forensics, insider_activity, short_interest) do include spec_ref, so this is inconsistent.

**Fix:** Add `spec_ref:` field to module docstrings for consistency.

### IN-05: debug_log file descriptor never closed

**File:** `pmacs/logsys/debug_log.py:177-181`
**Issue:** `_ensure_fd()` opens a file descriptor for `_log_path` that is never explicitly closed. There is no `close()` function or `atexit` handler. For long-running daemon processes this is typically fine (OS cleans up), but it means the file cannot be rotated externally without a restart.

**Fix:** Consider adding a `close_log()` function or registering an `atexit` handler.

---

_Reviewed: 2026-05-31T15:36:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
