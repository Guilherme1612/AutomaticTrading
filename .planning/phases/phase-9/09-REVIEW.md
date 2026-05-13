# Phase 9 Code Review

## Summary

Reviewed 11 files across the Phase 9 orchestrator implementation. The orchestrator (3,209 lines) is well-structured with correct step ordering, proper idempotency, and solid error handling for the happy path. However, there is one critical bug: `INTERRUPTED` is defined as a terminal state but has no entry in `VALID_TRANSITIONS`, making it unreachable via the state machine. This means the mid-cycle abort feature (S5-2) silently fails to transition holdings. Additionally, there are several unregistered error codes violating the Architecture.md §5.5 canonical registry requirement, and direct field mutations on Holding objects outside the state machine (lines 1553-1558) that bypass the intended invariant enforcement. The test suite is thorough across 6 integration test files covering all major paths, though some edge cases around the INTERRUPTED transition gap are untested because the bug masks them.

---

## Critical (must fix before Phase 10)

### C1: INTERRUPTED state is unreachable -- mid-cycle abort silently fails
- **File:** `pmacs/schemas/contracts.py:62-92` and `pmacs/nervous/orchestrator.py:1906`
- **What:** `HoldingState.INTERRUPTED` is included in `TERMINAL_STATES` (line 53) but has no entry in `VALID_TRANSITIONS` (lines 62-92). No state lists `INTERRUPTED` as a valid target. When `_interrupt_remaining_holdings` calls `is_valid_transition(holding.state, HoldingState.INTERRUPTED)` at line 1906, it returns `False` for every holding, and the transition is silently skipped. Holdings in states like `PHASE1_RESEARCH`, `PHASE2_CRUCIBLE`, or `APPROVED_PENDING` are never marked as interrupted.
- **Risk:** Mid-cycle abort (kill switch or SIGTERM) produces no INTERRUPTED records. The operator sees holdings vanish from the tracker with no state change, creating a discrepancy between what the cycle log says ("interrupted N holdings") and what the holding state actually is. In production, this could mask in-flight positions that the operator believes were interrupted but are still logically open.
- **Fix:** Add `INTERRUPTED` as a valid transition from every non-terminal state in `VALID_TRANSITIONS`:
```python
# In pmacs/schemas/contracts.py VALID_TRANSITIONS:

HoldingState.CANDIDATE: frozenset({
    HoldingState.PHASE1_RESEARCH, HoldingState.ABORTED_PRE_LLM,
    HoldingState.HALTED, HoldingState.INTERRUPTED,
}),
HoldingState.PHASE1_RESEARCH: frozenset({
    HoldingState.PHASE2_CRUCIBLE, HoldingState.ABORTED_LLM,
    HoldingState.PHASE1_TIMEOUT, HoldingState.INTERRUPTED,
}),
HoldingState.PHASE1_TIMEOUT: frozenset({
    HoldingState.ABORTED_LLM, HoldingState.INTERRUPTED,
}),
HoldingState.PHASE2_CRUCIBLE: frozenset({
    HoldingState.APPROVED_PENDING, HoldingState.ABORTED_LLM,
    HoldingState.INTERRUPTED,
}),
HoldingState.APPROVED_PENDING: frozenset({
    HoldingState.ACTIVE, HoldingState.ABORTED_RISK,
    HoldingState.INTERRUPTED,
}),
HoldingState.ACTIVE: frozenset({
    HoldingState.THESIS_AGING_REVIEW,
    HoldingState.STOPPED_OUT, HoldingState.EXIT_THESIS_INVALIDATED,
    HoldingState.EXIT_OPPORTUNITY_COST, HoldingState.EXIT_TRAILING_STOP,
    HoldingState.EXIT_FAILED, HoldingState.DELISTED,
    HoldingState.RESOLUTION_TIMEOUT, HoldingState.PANIC_EXIT,
    HoldingState.HALTED, HoldingState.INTERRUPTED,
}),
HoldingState.THESIS_AGING_REVIEW: frozenset({
    HoldingState.ACTIVE, HoldingState.EXIT_THESIS_INVALIDATED,
    HoldingState.INTERRUPTED,
}),
```

### C2: Direct Holding field mutation outside state_machine violates Architecture.md §16.1
- **File:** `pmacs/nervous/orchestrator.py:1553-1558`
- **What:** After transitioning to ACTIVE state, the code directly mutates Holding fields:
```python
holding.entry_price_usd = entry_price
holding.position_size_usd = sizing_result.target_usd
holding.stop_price_usd = stop_price
holding.verdict = verdict.value
holding.conviction_score = conviction_score
holding.sector = holding.sector  # no-op, but shows the pattern
```
The anti-pattern rule from Architecture.md §16.1 only explicitly forbids `holding.state = ...` outside state_machine, but the `Holding` model has `model_config = ConfigDict(frozen=False)` specifically so the state machine can mutate `state`. These direct field mutations on `entry_price_usd`, `position_size_usd`, etc. are not going through any validation layer. The last line `holding.sector = holding.sector` is a no-op (assigning to itself).
- **Risk:** Bypasses any future model-level validators on these fields. The `holding.sector = holding.sector` line is a clear copy-paste artifact. If Pydantic field validators are added for these fields later, they will be silently bypassed.
- **Fix:** Either (a) add a `update_holding_fields()` method to the state_machine module that sets execution-related fields with proper validation, or (b) at minimum remove the no-op line and add a comment explaining why direct mutation is acceptable for execution fields (not state):
```python
# Execution fields set post-transition (not state-related):
holding.entry_price_usd = entry_price
holding.position_size_usd = sizing_result.target_usd
holding.stop_price_usd = stop_price
holding.verdict = verdict.value
holding.conviction_score = conviction_score
# Note: holding.sector is already set during Holding creation
```

### C3: SQL injection surface in `_column_exists` via f-string interpolation
- **File:** `pmacs/storage/sqlite.py:193`
- **What:** The `_column_exists` function constructs a PRAGMA query using an f-string:
```python
cursor = conn.execute(f"PRAGMA table_info({table})")
```
The `table` parameter comes from hardcoded string literals in `_run_migrations` (e.g., `"stop_events"`, `"holdings"`), so it is not exploitable today. However, if this function is ever called with user-supplied input, it becomes a SQL injection vector.
- **Risk:** Currently safe because all callers use hardcoded table names. But the function signature accepts arbitrary strings with no sanitization, and there is no comment or type hint restricting it. Future callers could introduce a vulnerability.
- **Fix:** Add a validation guard and a safety comment:
```python
def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check if a column exists in a table.

    WARNING: table name is interpolated directly into SQL.
    Only call with hardcoded table names -- never user input.
    """
    import re
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table):
        raise ValueError(f"Invalid table name: {table}")
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())
```

---

## High (should fix)

### H1: Unregistered error codes violate Architecture.md §5.5
- **File:** `pmacs/nervous/orchestrator.py` (multiple locations) and `pmacs/logsys/error_classifier.py`
- **What:** The orchestrator uses several error codes that are NOT in the `VALID_ERROR_CODES` registry:
  - `"DATA_UNAVAILABLE"` (line 1065, 1067) -- not in registry
  - `"STEP_OVER_BUDGET"` (line 405) -- not in registry
  - `"MEMO_WRITER_FAILED"` (line 1485) -- not in registry
  - `"OPPORTUNITY_COST_FAILED"` (line 2509) -- not in registry
  - `"LEDGER_CONSTRAINT"` (line 1579) -- not in registry
  - `"DB_WRITE_FAILED"` (lines 1533, 1693, 3023) -- the registry has `SQLITE_WRITE_FAILED`, not `DB_WRITE_FAILED`
  
  Architecture.md §5.5 states: "Every WARN+ debug event MUST carry an error_code from this registry."
- **Risk:** Debug events with unregistered codes cannot be validated by monitoring tools. Downstream consumers that filter by canonical error codes will miss these events.
- **Fix:** Add all six error codes to `pmacs/logsys/error_classifier.py`:
```python
# In error_classifier.py, add:
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
STEP_OVER_BUDGET = "STEP_OVER_BUDGET"
MEMO_WRITER_FAILED = "MEMO_WRITER_FAILED"
OPPORTUNITY_COST_FAILED = "OPPORTUNITY_COST_FAILED"
LEDGER_CONSTRAINT = "LEDGER_CONSTRAINT"
DB_WRITE_FAILED = "DB_WRITE_FAILED"

# And add them to VALID_ERROR_CODES frozenset
```

### H2: `WHERE state = 'OPEN'` query will never match any holdings
- **File:** `pmacs/nervous/orchestrator.py:654`
- **What:** `_step_corporate_actions` queries `WHERE state = 'OPEN'` but there is no `HoldingState.OPEN` value. The valid active state is `ACTIVE`. This query will always return zero rows, meaning corporate actions are never applied to any holdings.
- **Risk:** Corporate action adjustments (splits, dividends) will never be processed, even when the corporate actions data source is implemented in a future phase.
- **Fix:**
```python
# Line 654: Change 'OPEN' to 'ACTIVE'
"SELECT id, ticker, entry_price_usd, position_size_usd "
"FROM holdings WHERE state = 'ACTIVE'"
```

### H3: ThreadPoolExecutor in `_dispatch_personas_with_timeout` may leak threads on TimeoutError
- **File:** `pmacs/nervous/orchestrator.py:1863-1889`
- **What:** When `future.result(timeout=timeout_seconds)` raises `FuturesTimeoutError`, the function re-raises as `TimeoutError`. However, the `with ThreadPoolExecutor(max_workers=1)` block ensures cleanup. The real concern is that the submitted `_dispatch_personas` call continues running in the background thread after the timeout -- the thread is not cancelled, just abandoned. If persona runners hold resources (LLM connections, file handles), those leak until the runners complete.
- **Risk:** On timeout, the background thread continues executing LLM calls that will complete but whose results are discarded. If the LLM process is slow, multiple timed-out symbols could pile up concurrent runners, exceeding the intended concurrency limits.
- **Fix:** This is a known limitation of Python's `ThreadPoolExecutor` -- `Future.cancel()` only prevents a pending task from starting, it cannot interrupt a running thread. Document this as an accepted risk:
```python
# NOTE: On timeout, the persona dispatch thread continues running in the
# background until it completes or the process exits. Python threads cannot
# be forcefully interrupted. This is acceptable because:
# 1. The orchestrator moves on and does not wait for the thread
# 2. The thread will eventually complete (LLM has its own timeouts)
# 3. At most 1 leaked thread per timed-out symbol
```

### H4: `_run_symbol` is 280+ lines -- extremely difficult to reason about
- **File:** `pmacs/nervous/orchestrator.py:926-1732`
- **What:** The `_run_symbol` method spans approximately 800 lines, covering 16 sub-steps (13a through 13p) with multiple early returns, abort paths, and inline imports. The cyclomatic complexity is very high due to the number of conditional branches (antipattern check, persona timeout, no signals, sizing abort, verdict skip, risk gate, execution path).
- **Risk:** Maintenance burden. Any bug fix or new step insertion requires understanding the entire 800-line method. Early returns make it easy to miss cleanup logic (e.g., `_symbol_holdings.pop` is called on most but not all abort paths -- line 987, 1055, 1098, 1133, but NOT on lines 1324, 1388, 1428 where the method returns after `transition()` to an abort state).
- **Fix:** Extract each sub-step (13a-13p) into its own method. This would also make the op_seq tracking clearer:
```python
def _run_symbol(self, cycle_id, item, op_seq):
    holding, op_seq = self._step_13a_create_holding(cycle_id, item, op_seq)
    if holding is None:
        return op_seq
    holding, op_seq = self._step_13b_antipattern(holding, cycle_id, op_seq)
    if holding is None:
        return op_seq
    # ... etc
```

### H5: `_symbol_holdings.pop(ticker, None)` missing on three abort paths
- **File:** `pmacs/nervous/orchestrator.py:1306-1428`
- **What:** When the pipeline aborts a symbol at the sizing, verdict, or risk gate stages, the method returns without calling `self._symbol_holdings.pop(ticker, None)`. This happens at:
  - Line 1324 (sizing abort): no pop before return
  - Line 1388 (verdict SKIP abort): no pop before return
  - Line 1428 (risk gate abort): no pop before return
  
  Compare with the antipattern abort (line 987), data unavailable abort (line 1055), and persona timeout abort (line 1098) which all call `pop` before returning.
- **Risk:** If a mid-cycle abort (kill switch or SIGTERM) occurs after one of these three abort paths, the `_interrupt_remaining_holdings` will attempt to transition a holding that is already in a terminal abort state (ABORTED_RISK), which will raise `InvalidStateTransition` or silently skip. The holding stays in `_symbol_holdings` dict forever (within the cycle), wasting memory and potentially confusing the interrupt logic.
- **Fix:** Add `self._symbol_holdings.pop(ticker, None)` before each return in the sizing, verdict, and risk gate abort paths:
```python
# Line 1324 (after sizing abort transition):
self._symbol_holdings.pop(ticker, None)
return op + 1

# Line 1388 (after verdict SKIP transition):
self._symbol_holdings.pop(ticker, None)
return op + 1

# Line 1428 (after risk gate abort transition):
self._symbol_holdings.pop(ticker, None)
return op + 1
```

---

## Medium (nice to fix)

### M1: Hardcoded dummy signing key in paper mode
- **File:** `pmacs/nervous/orchestrator.py:1610`
- **What:** The code uses `dummy_key = hashlib.sha256(b"pmacs_paper_mode").digest()` as a deterministic signing key. This is a hardcoded, publicly visible key that anyone reading the source can use to forge signatures.
- **Risk:** In paper mode this is acceptable since no real trades execute. However, the comment says "In production, keys come from config" but there is no code path that switches to real keys -- this will need to be replaced when moving to live mode. If someone accidentally runs in live mode without updating this, all trade signatures are forgeable.
- **Fix:** Add an assertion or mode check before signing to ensure this code path is only used in paper/sandbox modes:
```python
from pmacs.schemas.system import Mode
current_mode = self._current_mode(self._db_path)
assert current_mode in (Mode.INSTALLING.value, Mode.PAPER_TRADING.value), \
    f"Dummy signing key must not be used in {current_mode} mode"
```

### M2: Repeated connection-per-query pattern creates unnecessary overhead
- **File:** `pmacs/nervous/orchestrator.py` (multiple locations)
- **What:** Throughout the orchestrator, each database operation opens a new `sqlite3.connect()`, executes one query, and closes the connection. In `_step_weekly_reeval` (lines 2301-2342), this is done in a loop -- for every holding that needs re-evaluation, a new connection is opened and closed. Similarly in `_step_lessons` and `_step_fde`.
- **Risk:** Performance degradation with many holdings. Each `sqlite3.connect()` has overhead (file open, WAL lock acquisition). With 50+ active holdings, this could add seconds to the cycle.
- **Fix:** Use a single connection per step method:
```python
def _step_weekly_reeval(self, cycle_id: str) -> None:
    conn = sqlite3.connect(str(self._db_path))
    try:
        rows = conn.execute("SELECT ...").fetchall()
        for row in rows:
            conn.execute("UPDATE holdings SET ...", (...))
        conn.commit()
    finally:
        conn.close()
```

### M3: Univeres `_step_universe_sync` fetches halted tickers but excludes them
- **File:** `pmacs/nervous/orchestrator.py:730`
- **What:** The code calls `get_universe(conn, include_halted=False)` but then checks `halted` flag on the results (line 738: `halted = [e.ticker for e in entries if e.halted]`). Since `include_halted=False` already filters them out, the halted list will always be empty, and the log message always shows "0 halted".
- **Risk:** Misleading log output. If a holding becomes halted mid-cycle, the log will not reflect it because the query already excluded them. The operator gets no visibility into how many tickers are halted.
- **Fix:** Change to `include_halted=True` to get visibility, or remove the halted counting code:
```python
entries = get_universe(conn, include_halted=True)
self._universe_tickers = [e.ticker for e in entries if not e.halted]
halted = [e.ticker for e in entries if e.halted]
```

### M4: Duplicate `_step_override_learning` and `_step_override_learning_post`
- **File:** `pmacs/nervous/orchestrator.py:801-835` and `2773-2807`
- **What:** Steps 11 and 24 both call `cluster_overrides` with identical SQL queries (`SELECT original_verdict, override_verdict, ticker FROM operator_overrides ORDER BY id DESC LIMIT 50`). The only difference is the log event name (`CYCLE_OVERRIDE_LEARNING` vs `CYCLE_OVERRIDE_LEARNING_POST`).
- **Risk:** Code duplication. If the query or logic needs to change, both places must be updated. The clustering is run twice per cycle on the same data.
- **Fix:** Extract a shared `_query_override_clusters` helper method, or merge steps 11 and 24 if Architecture.md does not require them to be separate.

### M5: Dead code -- evidence fetch `try/pass` block
- **File:** `pmacs/nervous/orchestrator.py:1047-1069`
- **What:** There is a `try: pass except Exception:` block that is dead code:
```python
try:
    # Future wave: evidence = fetch_evidence(ticker, cycle_id)
    pass
except Exception as exc:
    # ... abort handling
```
The `pass` never raises, so the except block is unreachable. When evidence fetching is implemented, this will need to be rewritten anyway.
- **Risk:** The abort handling code is untested and may contain bugs that will only surface when evidence fetching is wired up.
- **Fix:** Either remove the dead code or replace with a TODO comment:
```python
# TODO: Future wave -- wire evidence fetching
# evidence = fetch_evidence(ticker, cycle_id)
evidence: list[Any] = []
```

### M6: `create TABLE IF NOT EXISTS` in step methods pollutes schema ownership
- **File:** `pmacs/nervous/orchestrator.py:1497-1507` and `2733-2739` and `2851-2857`
- **What:** Steps 13n (scan_records), 23 (lessons), and 25 (failure_classifications) each create their tables on-the-fly with `CREATE TABLE IF NOT EXISTS`. These tables should be in `pmacs/storage/sqlite.py`'s `SCHEMA_SQL` alongside all other tables.
- **Risk:** Schema definitions scattered across the codebase make it harder to understand the complete database schema. Migration logic in `sqlite.py` will not know about these tables.
- **Fix:** Move all three `CREATE TABLE` statements to `SCHEMA_SQL` in `pmacs/storage/sqlite.py`.

---

## Low (cosmetic)

### L1: Default lock path `/tmp/pmacs_cycle.lock` is predictable
- **File:** `pmacs/nervous/orchestrator.py:83,131`
- **Risk:** Predictable path in `/tmp` could be pre-created by a malicious local user to block cycle execution (DoS). Low risk since this is a single-operator local system, but worth noting.
- **Fix:** Consider using `XDG_RUNTIME_DIR` or a path under the PMACS data directory.

### L2: `datetime.utcnow` deprecation in Pydantic default factories
- **File:** `pmacs/schemas/contracts.py:111,139`
- **What:** Both `Thesis.created_at` and `Holding.created_at` use `datetime.utcnow` as default factory, which is deprecated since Python 3.12 in favor of `datetime.now(timezone.utc)`.
- **Fix:** Replace `datetime.utcnow` with `datetime.now(timezone.utc)` in both default_factory references.

### L3: Module-level `_current_mode` function duplicated
- **File:** `pmacs/nervous/orchestrator.py:3052-3064` and `3197-3208`
- **What:** `_current_mode` is defined both as a static method on `CycleOrchestrator` (line 3052) and as a module-level function (line 3197). Both have identical implementations.
- **Fix:** Have the module-level function call the static method, or vice versa.

---

## Passed Checks

- **Step ordering matches Architecture.md §9**: Steps 0, 0.5, 1, 2-3, 4, 5, 6-12, 13, 14-28, 29, 30 are executed in the correct sequence with proper op_seq tracking.
- **CycleLock correctly released on all exit paths**: The `CycleLock.__exit__` handles `OSError` on unlock (line 101-103) and sets `_fd = None` even on exception. The `run_cycle` method uses a `try/finally` block to restore signal handlers (line 345-348).
- **Kill switch check is AFTER lock acquisition**: Step 4 runs inside the `with CycleLock` block (line 258), not before it. This is correct.
- **All state transitions use `transition()` from state_machine**: No direct `holding.state =` assignments found in the orchestrator (the grep result at line 1553-1558 is field mutation, not state mutation).
- **Idempotency via op_idempotency table**: Every step checks `_skip_if_complete` before executing and calls `_mark_op_complete` after. The checkpoint module provides crash-resume capability.
- **Signal handlers properly restored**: `old_sigterm` and `old_sigint` are captured before registration (line 232-233) and restored in the `finally` block (line 347-348).
- **Audit chain uses AuditWriter throughout**: No `json.dumps()` for audit serialization found. All audit writes go through `AuditWriter.append()`.
- **`cycle_id` is provided on all `log_debug` calls**: Every `log_debug` call in the orchestrator includes `cycle_id=cycle_id`.
- **Error codes present on all WARN+ log events**: All WARN-level log_debug calls include `error_code=` parameter.
- **Evidence is scoped per-symbol**: The `evidence` list is created fresh for each `_run_symbol` call (line 1046), not shared between symbols.
- **Persona slot map matches Architecture.md §12.2**: Slot 0 has macro_regime + catalyst_summarizer, Slot 1 has moat_analyst + growth_hunter, Slot 2 has insider_activity + short_interest + forensics.
- **No hardcoded secrets or credentials**: The dummy signing key is explicitly for paper mode and marked as such.
- **No `eval()` or command injection vectors**: All SQL uses parameterized queries (`?` placeholders) except the PRAGMA call which uses hardcoded table names.

---

## Test Coverage Assessment

### Well tested:
- Full open-to-close cycle with audit chain verification (`test_cycle_skeleton.py`)
- Concurrent cycle prevention via CycleLock (`TestFlockPreventsConcurrentCycles`)
- Kill switch blocking before cycle start (`TestKillSwitchBlocksCycle`)
- Clock drift abort (`TestClockDriftAbort`)
- Pre-cycle pipeline steps 2-12 with FX, gatekeeper, queue composition (`test_precycle_pipeline.py`)
- Per-symbol state transitions through the full pipeline (`test_symbol_pipeline.py`)
- Persona dispatch across 3 slot groups with all 7 personas (`TestPersonaDispatch3Slots`)
- Arbitration through conviction with forced BUY verdicts (`TestArbitrationThroughConviction`)
- Antipattern abort path (no LLM calls made) (`TestSymbolAntipatternAbort`)
- Post-cycle flywheel steps 14-28 (`test_full_cycle.py`)
- FDE classification of terminal holdings (`TestPostCycleFlywheelEngines`)
- Kill switch mid-cycle abort with INTERRUPTED holdings (`TestKillSwitchMidCycle`)
- Graceful shutdown via SIGTERM simulation (`TestGracefulShutdown`)
- Empty queue edge case (`TestEmptyQueueCycle`)
- All symbols abort edge case (`TestAllSymbolsAbort`)
- Step timing instrumentation (`TestStepTimingRecorded`)
- Exit test with 3 synthetic tickers (`TestExitTestFullCycle`)
- Audit chain integrity across full cycle (`TestAuditChainIntegrity`)

### Missing coverage:
- **INTERRUPTED transition is never actually tested**: The `TestKillSwitchMidCycle` test passes because `is_valid_transition` returns `False` and holdings are silently skipped. The test does not verify that any holdings actually transitioned to INTERRUPTED state -- it only checks the cycle state is ABORTED. A proper test would insert a holding, trigger mid-cycle abort, then query the holding's state to confirm it is `INTERRUPTED`.
- **Sizing abort, verdict SKIP, and risk gate abort paths lack `_symbol_holdings.pop` test**: There is no test that verifies the holdings tracker is cleaned up when a symbol aborts at sizing/verdict/risk-gate. A test could run a cycle where one symbol aborts at verdict SKIP, then trigger a kill switch to verify `_interrupt_remaining_holdings` does not attempt to transition an already-terminal holding.
- **Corporate actions never tested with ACTIVE holdings**: The `WHERE state = 'OPEN'` bug (H2) means even if ACTIVE holdings existed, corporate actions would not process them. No test creates ACTIVE holdings and verifies corporate action processing.
- **Crash resume does not test actual resume**: `TestCrashResumeAtStep13g` runs a full cycle and then verifies checkpoints exist, but does not actually simulate a crash mid-cycle and resume from that point. A proper test would create a cycle with pre-cycle steps completed, then run a new orchestrator instance and verify it skips those steps.
- **Persona dispatch thread leak on timeout**: No test verifies that the leaked thread eventually completes or that resources are cleaned up.
- **Race between signal handler and step execution**: No test verifies that a signal received during a long-running step (e.g., persona dispatch) is properly handled without corrupting state.
