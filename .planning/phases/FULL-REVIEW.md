---
phase: FULL-PROJECT
reviewed: 2026-05-13T23:02:00Z
depth: standard
files_reviewed: 48
files_reviewed_list:
  - pmacs/mutation/ab_runner.py
  - pmacs/mutation/candidate_generator.py
  - pmacs/mutation/daemon.py
  - pmacs/mutation/promotion.py
  - pmacs/mutation/rollback.py
  - pmacs/mutation/stat_test.py
  - pmacs/web/data.py
  - pmacs/web/app.py
  - pmacs/web/sse_client.py
  - pmacs/web/config.py
  - pmacs/web/routes/dashboard.py
  - pmacs/web/routes/settings.py
  - pmacs/web/routes/pipeline.py
  - pmacs/web/routes/cortex.py
  - pmacs/web/routes/debug.py
  - pmacs/web/routes/universe.py
  - pmacs/web/routes/agents.py
  - pmacs/web/routes/wizard.py
  - pmacs/web/static/app.js
  - pmacs/web/templates/base.html
  - pmacs/web/templates/dashboard.html
  - pmacs/web/templates/settings.html
  - pmacs/web/templates/pipeline.html
  - pmacs/engines/state_machine.py
  - pmacs/engines/sizing.py
  - pmacs/engines/arbitration.py
  - pmacs/engines/portfolio_risk_gate.py
  - pmacs/engines/conviction.py
  - pmacs/engines/stop_loss_monitor.py
  - pmacs/engines/failure_diagnostic.py
  - pmacs/storage/audit.py
  - pmacs/execution/signing.py
  - pmacs/cortex/totp.py
  - pmacs/nervous/auth.py
  - pmacs/data/canonical.py
  - pmacs/constants.py
  - pmacs/logsys/dead_letter.py
  - pmacs/schemas/currency.py
  - pmacs/cortex/health.py
  - pmacs/cortex/boot_detector.py
  - pmacs/cortex/clock_monitor.py
  - pmacs/cortex/disk_monitor.py
  - pmacs/cortex/self_check.py
  - pmacs/cortex/daemon.py
  - pmacs/cortex/crash_loop_detector.py
  - pmacs/data/gateway.py
  - pmacs/data/staleness.py
  - pmacs/data/universe.py
findings:
  critical: 1
  warning: 9
  info: 8
  total: 18
status: issues_found
---

# Full Project Code Review Report

**Reviewed:** 2026-05-13T23:02:00Z
**Depth:** standard
**Files Reviewed:** 48
**Status:** issues_found

## Summary

Comprehensive review of all production source code under `pmacs/` covering mutation engine, web layer, engines, cortex, data, schemas, execution, and storage. Reviewed against the Four-File Spec (Source.md, Architecture.md, Agents.md, Phases.md), Five Non-Negotiables, and Anti-Patterns from CLAUDE.md.

**Overall assessment:** The codebase is well-structured and spec-compliant. Anti-pattern grep checks pass cleanly. The Five Non-Negotiables are structurally enforced. One critical issue found (read-only DB bypass in web layer), plus warnings around error handling consistency, timestamp conventions, and web security patterns.

**Anti-pattern compliance:**
- `holding.state =` outside state_machine.py: **CLEAN** -- only in state_machine.py
- `eur_per_usd` field usage: **CLEAN** -- guarded by Pydantic validator
- Auto-promote mutations: **CLEAN** -- all mutations require TOTP
- `json.dumps(payload)` for audit: **CLEAN** -- dead_letter.py uses `json.dumps` for dead-letter storage, not audit
- `cycle_id=None` on audit: **CLEAN** -- only in system-level events where spec allows

## Critical Issues

### CR-01: Dashboard data.py opens read-write SQLite connections for write operations

**File:** `pmacs/web/data.py:779-791`
**Issue:** The `save_notification_level()` and `save_priority_scheme()` functions use `_sqlite_connect(db_path)` which returns a connection opened with `file:{path}?mode=ro` when the file exists. The `mode=ro` flag makes all write operations (`INSERT OR REPLACE`, `CREATE TABLE`) silently fail or raise `OperationalError`. The `save_notification_level` function catches all exceptions and returns `False`, masking this failure from the caller. This means notification level preferences and priority schemes cannot actually be persisted.

The `reorder_queue_item`, `pin_queue_item`, and `promote_all_p1` functions in the same file receive a pre-opened connection from route handlers that was opened via `get_readonly_db()`, which also uses `mode=ro`. All three attempt `UPDATE` and `COMMIT` on a read-only connection.

This violates Architecture.md section 2.2 ADR which states the dashboard process "literally cannot write" and section 4 data flow rules which say L7 (dashboard) writes go through L5 (nervous) via authenticated POST. However, the current implementation has the dashboard process attempting direct SQLite writes.

**Fix:**
Two paths -- either is correct:

(a) If notification persistence and queue writes should go through pmacs-nervous (preferred per spec):
Remove `save_notification_level`, `save_priority_scheme`, `reorder_queue_item`, `pin_queue_item`, `promote_all_p1` from the dashboard data layer. Route handlers should POST to pmacs-nervous endpoints instead.

(b) If dashboard-local writes are acceptable for settings:
Create a separate `_sqlite_connect_readwrite()` function that does not use `mode=ro`, and use it only in the settings/notification save functions. The queue write functions should still go through nervous.

## Warnings

### WR-01: Pipeline route handlers pass read-only DB to write functions

**File:** `pmacs/web/routes/pipeline.py:123-159`
**Issue:** `queue_reorder`, `queue_pin`, `queue_promote_all` all call `data_layer.get_readonly_db()` and then pass the read-only connection to `reorder_queue_item`, `pin_queue_item`, `promote_all_p1` which attempt `UPDATE` and `COMMIT`. These will fail silently (caught by `OperationalError` returning `False`).

**Fix:** Either route these through pmacs-nervous POST endpoints, or use a read-write connection for these specific operations.

### WR-02: `datetime.utcnow()` is deprecated in Python 3.12+

**File:** `pmacs/mutation/ab_runner.py:67,168,187,203` and `pmacs/logsys/dead_letter.py:26,102`
**Issue:** `datetime.utcnow()` creates a naive datetime without timezone info. Python 3.12+ deprecates it in favor of `datetime.now(timezone.utc)`. The daemon.py already uses `datetime.now(timezone.utc)` correctly, but ab_runner.py and dead_letter.py still use the deprecated form. This creates inconsistent timestamps -- some with timezone, some without.

**Fix:** Replace all `datetime.utcnow()` with `datetime.now(timezone.utc)` and import `timezone` from `datetime`.

### WR-03: Mutation daemon `main_loop` uses `time.sleep(60)` with no graceful shutdown

**File:** `pmacs/mutation/daemon.py:502`
**Issue:** The `main_loop` function uses `time.sleep(60)` in an infinite `while True` loop with no shutdown signal handling. The process cannot be gracefully stopped -- it will always wait up to 60 seconds before checking for termination. Additionally, if the process is killed mid-SQLite-write, WAL mode helps but the outcome row may be partially written.

**Fix:** Use `threading.Event` for shutdown signaling with `event.wait(timeout=60)` instead of `time.sleep(60)`. Register signal handlers for SIGTERM/SIGINT.

### WR-04: Mutation promote/reject/rollback JS sends candidate_id but backend ignores it

**File:** `pmacs/web/templates/settings.html:289-319`
**Issue:** The `promoteMutation`, `rejectMutation`, and `rollbackMutation` JavaScript functions open a TOTP modal with `callbackUrl` set to `/api/mutation/promote`, `/api/mutation/reject`, `/api/mutation/rollback`. The `executeGatedAction` function POSTs `{action_id: actionId}` to these URLs, but the `actionId` includes the candidate_id (e.g., `mutation.promote.abc123`). There are no corresponding FastAPI route handlers registered for these endpoints -- `pmacs/web/routes/settings.py` only has `/api/settings/notifications`. The POST will 404.

**Fix:** Add API route handlers for `/api/mutation/promote`, `/api/mutation/reject`, `/api/mutation/rollback` in settings.py or a dedicated mutation routes module, or route these through pmacs-nervous.

### WR-05: `failure_diagnostic.py` compares holding.state against string literals

**File:** `pmacs/engines/failure_diagnostic.py:93-111`
**Issue:** The failure diagnostic engine uses string comparison (`holding.state == "EXIT_THESIS_INVALIDATED"`) rather than comparing against the `HoldingState` enum. While this works, it will silently miss the correct classification if the enum value ever changes (e.g., due to a typo or refactoring). The state_machine.py correctly uses enum comparison.

**Fix:** Import `HoldingState` from `pmacs.schemas.contracts` and compare against `HoldingState.EXIT_THESIS_INVALIDATED`, etc.

### WR-06: SSE client does not use `Last-Event-ID` for resume

**File:** `pmacs/web/sse_client.py:42-46`
**Issue:** Architecture.md section 4.4 states: "reconnects on disconnect with `Last-Event-ID` for resume from last delivered event." The SSEClient reconnects on disconnect but does not track or send `Last-Event-ID` on reconnect, so events delivered during the disconnection gap are lost.

**Fix:** Track the last event ID from SSE frames and include `Last-Event-ID` header on reconnection:
```python
# Track: self._last_event_id = response.headers.get("Last-Event-ID")
# On reconnect: headers={"Last-Event-ID": self._last_event_id}
```

### WR-07: `escapeHtml` in app.js relies on DOM side-effect for escaping

**File:** `pmacs/web/static/app.js:11-15`
**Issue:** The `escapeHtml` function creates a DOM element, sets `textContent`, then reads `innerHTML` to get the escaped version. While functionally correct, subsequent uses of `innerHTML` assignment elsewhere in the file (lines 406, 421, 496, 1248, 1264, 1308, 1324) bypass this function. Lines 421 and 496 construct HTML via string concatenation using `escapeHtml(item.name)` which is safe, but the pattern is fragile -- any developer adding a new `innerHTML` assignment must remember to call `escapeHtml` on user-provided data.

**Fix:** For maximum safety, use `textContent` or `createElement`/`appendChild` instead of `innerHTML` for all dynamic content. At minimum, audit all `innerHTML` assignments to confirm they only use hardcoded HTML + `escapeHtml()`-wrapped values.

### WR-08: Dead letter queue is in-memory only -- lost on process restart

**File:** `pmacs/logsys/dead_letter.py:31-37`
**Issue:** The `DeadLetterQueue` stores entries in a Python list (`self._queue`) with no disk persistence. Architecture.md section 14 specifies dead-letter behavior. If the process restarts, all pending dead-letter entries are lost, and failed writes to Qdrant/KuzuDB may never be retried or surfaced to the operator.

**Fix:** Add SQLite-backed persistence for dead-letter entries. Write to a `dead_letter` table on enqueue, and clear on completion. This ensures entries survive process restarts.

### WR-09: Wizard step 4 stores credentials via `keyring.set_password` synchronously

**File:** `pmacs/web/routes/wizard.py:151-161`
**Issue:** The wizard's `_execute_step` for step 4 (Keychain) iterates over form data and stores each key-value pair via `keyring.set_password("pmacs.credentials", key, value)`. This stores raw form field names as keyring keys (e.g., "alpaca_api_key", "alpaca_secret"). If the form field names change, old credentials become orphaned in the keychain. There is also no validation that the keys are expected credential names.

**Fix:** Define an explicit allowlist of expected credential key names (e.g., `ALLOWED_CREDENTIAL_KEYS = {"alpaca_api_key", "alpaca_secret", ...}`) and only store keys from that set. Reject unknown keys.

## Info

### IN-01: Mutation `candidate_generator.py` uses string dimension values instead of enum

**File:** `pmacs/mutation/candidate_generator.py:17-72`
**Issue:** The `GENERATION_RULES` list uses `MutationDimension.PERSONA_PROMPT` (enum) but `MutationCandidateData` stores `dimension` as `str`. The dataclass at line 78 declares `dimension: str`. The enum value is stored as a string, which is fine, but the mixing of enum and string types in the rule definitions is inconsistent.

**Fix:** Either use enum throughout (type `dimension` as `MutationDimension`) or use strings throughout. Consistency aids readability.

### IN-02: `app.js` defines `CMD_K_ERROR_CODES` with sequential E001-E015 codes

**File:** `pmacs/web/static/app.js:330-343`
**Issue:** The error codes in `CMD_K_ERROR_CODES` (E001-E015) do not match the canonical error codes from Architecture.md section 5.5. The spec uses codes like `STALE_DATA`, `GBNF_PARSE_FAILURE`, `AUDIT_CHAIN_BREAK`, `KILL_SWITCH_ENGAGED`, etc. The `E001`-style codes appear to be an earlier design that was superseded.

**Fix:** Map the `CMD_K_ERROR_CODES` to the actual canonical error codes from Architecture.md section 5.5 (e.g., `{ name: "AUDIT_CHAIN_BREAK", href: "/debug?event=AUDIT_CHAIN_BREAK", ... }`).

### IN-03: Dashboard template accesses `d.verdict` and `d.ticker` on cycle decision rows

**File:** `pmacs/web/templates/dashboard.html:145-151`
**Issue:** The "Recent Decisions" section renders `d.ticker` and `d.verdict` for each decision row. However, `get_recent_decisions()` in data.py returns dicts with keys `cycle_id`, `opened_at`, `closed_at`, `state`, `trigger`, `mode` -- no `ticker` or `verdict` keys. Jinja2 will render these as empty strings (default behavior for undefined attributes), not an error, so the section will appear but with blank ticker/verdict values.

**Fix:** Either extend `get_recent_decisions()` to join with holdings/decisions tables for verdict and ticker data, or change the template to display cycle_id/trigger/mode instead.

### IN-04: `dashboard.py` TODO comment about cash_ledger integration

**File:** `pmacs/web/routes/dashboard.py:60`
**Issue:** `# TODO: integrate with cash_ledger once Architecture.md section 9 CashLedger is built`. This is a known gap documented inline. The portfolio value calculation currently uses `initial_capital + unrealized_pnl` without tracking cash spent on positions, which means if $2000 is invested in positions, the portfolio value still shows $5000 + unrealized P&L rather than $3000 cash + $2000 invested + unrealized P&L.

**Fix:** Track this as a known limitation. When CashLedger is implemented, replace the simplified calculation.

### IN-05: `data.py` sparkline functions create a new DuckDB adapter per call

**File:** `pmacs/web/data.py:187-223`
**Issue:** `get_sparkline_data()` creates a new `DuckDBAdapter` on every call, which opens and closes a DuckDB connection. `get_all_sparkline_data()` calls it 5 times in sequence for the dashboard page. This creates 5 separate DuckDB connections per dashboard load.

**Fix:** Consider a single connection/adapter per request cycle, or a shared adapter with connection pooling.

### IN-06: `data.py` cross-DB consistency check is a no-op for non-SQLite stores

**File:** `pmacs/web/data.py:651-661`
**Issue:** The `get_cortex_status` function checks cross-DB consistency but returns `"ok"` for KuzuDB, Qdrant, and DuckDB without actually verifying them. The comment says "Assume ok if no check implemented."

**Fix:** Add basic liveness checks for each store (e.g., KuzuDB: connect and run `RETURN 1`, Qdrant: health endpoint, DuckDB: `SELECT 1`).

### IN-07: Multiple modules import `sqlite3` inline at function level

**File:** `pmacs/mutation/ab_runner.py:108,143,159`, `pmacs/mutation/daemon.py:12,153`, `pmacs/mutation/rollback.py:70`
**Issue:** Several modules import `sqlite3` inside functions rather than at the module level. This is a style choice (lazy import) but makes it harder to see module dependencies at a glance and adds a tiny overhead on each call.

**Fix:** Move `import sqlite3` to the module top-level in all files.

### IN-08: `pipeline.html` kanban drag handlers do not persist verdict changes

**File:** `pmacs/web/templates/pipeline.html:239-244`
**Issue:** The `onDrop` function for kanban verdict columns receives the ticker and target verdict but does not call any API endpoint -- the function body just removes the CSS class. Dragging a card between verdict columns has no backend effect. The kanban columns are read-only displays of holdings by verdict.

**Fix:** This is likely intentional (verdicts are computed by the pipeline, not manually set). If so, consider making the kanban cards non-draggable for verdict columns to avoid confusing the operator.

---

_Reviewed: 2026-05-13T23:02:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
