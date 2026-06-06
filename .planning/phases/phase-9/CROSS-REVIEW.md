# Phase 9 Cross-Review: Plan vs. Spec vs. Implementation

**Reviewed:** 2026-05-26T12:35:00Z
**Reviewer:** Claude (gsd-code-reviewer)
**Scope:** PLAN.md, 09-REVIEW.md, spec/Architecture.md SS12, spec/Source.md SS22, pmacs/nervous/orchestrator.py (~3,575 lines)

---

## Plan-Spec Alignment (score: 4/5)

The PLAN.md maps well to the canonical 30-step sequence in Architecture.md SS12. All 30 steps are addressed across the 6 waves. The step numbering in the implementation matches the spec exactly: 0, 0.5, 1, 2-3, 4, 5, 6-12, 13 (13a-13p), 14-28, 29, 30.

**Strengths:**
- Step ordering is correct. Kill switch (step 4) is after lock acquisition (step 0) as the spec requires. The spec says "fail-fast if held" for the lock, and the implementation uses `LOCK_NB` (non-blocking) per spec.
- Per-symbol sub-sequence (13a-13p) follows Architecture.md SS12.2 exactly: state transition -> antipattern check -> episodic context -> persona dispatch (3 slots) -> arbitration -> crucible -> EV -> sizing -> conviction -> risk gate -> memo -> scan record -> execution -> catastrophe net.
- Queue scoring formula in PLAN matches Architecture.md SS12: `priority_score = catalyst_imminence*3.0 + thesis_strength*2.0 + source_brier_avg*1.5 + portfolio_fit*1.0`.
- Wave ordering respects dependencies correctly (skeleton -> data -> symbol -> post-cycle -> hardening -> performance).
- Alpaca deferred per D2, mock fills only.

**Gaps:**
1. **Step numbering skip in implementation:** The implementation jumps from op_seq=5 (flywheel health) directly to op_seq=3 (FX snapshot in `_run_pre_cycle`). This is because steps 2-3 are batched into a single op_seq=3 entry. The PLAN says "Steps 2-3 (FX + corp actions)" and the implementation uses op_seq=3 for both. This is functionally correct but means the op_seq values in `op_idempotency` will have gaps (1, 2, 3, 5, 6, 7...). Not a bug but a traceability concern -- an operator reading the idempotency table might wonder where op_seq=4 went (it is the kill switch check, but it is assigned as op_seq=4 in `run_cycle` before `_run_pre_cycle` is called with start_op_seq=3). Actually on closer reading: `run_cycle` assigns op_seq=4 for kill switch and op_seq=5 for flywheel health explicitly, then calls `_run_pre_cycle(cycle_id, 3)`. This means the pre-cycle re-assigns op_seq starting from 3, which is correct. The ordering is: 0 (initiate), 1 (clock drift), 2 (checkpoint), 4 (kill switch), 5 (flywheel), then 3, 6, 7, 8...12. Step 3 runs AFTER step 5. **This is a spec violation** -- Architecture.md says step 2 (FX) comes before step 4 (kill switch) and step 5 (flywheel health). The implementation runs kill switch and flywheel health before FX snapshot.

2. **Spec says step 0 is lock acquisition, step 29 is audit close, step 30 is lock release.** The implementation correctly uses `CycleLock` context manager for step 0/30 and `close_cycle()` for step 29. Aligned.

3. **Architecture.md SS12 step 13 sub-step ordering:** The spec says the order within step 13 is: state transition, antipattern, episodic context, Phase1 (personas), Arbitration, transition to PHASE2_CRUCIBLE, Crucible, EV, transition to APPROVED_PENDING, Sizing, Conviction, RiskGate, MemoWriter, ScanRecord, execution. The implementation follows this order exactly in `_run_symbol` and its sub-methods. Aligned.

4. **Architecture.md SS12 says MemoWriter "MUST run last in per-symbol pipeline; reads ALL persona outputs + Arbitrated + Crucible + Conviction + Verdict".** The implementation runs memo at step 13m, which is before execution (13o) and catastrophe net (13p). This is correct -- memo runs before the execution/audit steps that close out the symbol.

---

## Exit Test Coverage (score: 4/5)

The PLAN defines 8 exit test checkboxes and 7 additional grep-enforced checks.

**Well covered:**
- Exit test 1 (cycle opens, acquires lock): `test_cycle_skeleton.py::test_cycle_open_close`
- Exit test 2 (pre-cycle steps): `test_precycle_pipeline.py` with FX, gatekeeper, queue tests
- Exit test 3 (3 synthetic tickers): `test_symbol_pipeline.py::test_full_symbol_pipeline_mock_fill`
- Exit test 4 (at least 1 STRONG_BUY): same test as above
- Exit test 5 (post-cycle flywheel): `test_full_cycle.py::test_full_cycle_all_30_steps`
- Exit test 6 (cycle closes with audit trail): `test_full_cycle.py::test_audit_chain_integrity`
- Exit test 7 (audit chain verifies): same test
- Exit test 8 (resume from checkpoint at step 13g): `test_cycle_hardening.py::test_crash_resume_at_step_13g`

**Gaps:**
1. **Grep-enforced checks are not automated:** The PLAN says "No `holding.state =` outside state_machine.py (grep enforced)" but there is no pre-commit hook or CI config visible that actually runs these greps. They are listed as manual checks.
2. **Crash resume test (exit test 8) does not actually simulate a crash and resume:** Per the prior review (09-REVIEW.md), `TestCrashResumeAtStep13g` runs a full cycle and verifies checkpoints exist, but does not create a mid-cycle crash scenario and resume from it. This is a significant gap -- the idempotency mechanism is only validated as a side effect, not as a primary behavior.
3. **INTERRUPTED transition was never tested to verify it actually works:** The prior review flagged this. The fix was applied (INTERRUPTED is now in VALID_TRANSITIONS), but no test verifies that `_interrupt_remaining_holdings` actually transitions holdings to INTERRUPTED. The existing `TestKillSwitchMidCycle` test only checks cycle state, not holding state.

---

## Implementation Quality (score: 3/5)

The orchestrator is a substantial, well-structured piece of work (~3,575 lines) that correctly implements the vast majority of the spec. However, several quality issues from the prior review remain unaddressed.

**Strengths:**
- Clean class hierarchy: `CycleOrchestrator` with `CycleLock`, `initiate_cycle`, and `close_cycle` as module-level functions for backward compat.
- Idempotency via `op_idempotency` table with `_skip_if_complete` / `_mark_op_complete` on every step.
- Per-step timing instrumentation (S6-1) with budget thresholds and `STEP_OVER_BUDGET` warnings.
- Signal handlers (SIGTERM/SIGINT) registered and properly restored in `finally` block.
- Kill switch checked after each symbol in the per-symbol loop, plus pre-symbol check.
- Evidence scoped per-symbol (no cross-ticker leakage).
- All state transitions use `state_machine.transition()` -- no direct `holding.state =` found.
- `canonical_json` via `AuditWriter` throughout -- no `json.dumps()` for audit.
- `cycle_id` present on all `log_debug` calls.
- Crucible correctly implements 2-cycle loop with 180s total budget and 90s per-cycle timeout.
- Persona slot map matches Architecture.md SS12.2 exactly.
- The `_query_override_clusters` shared helper resolves the M4 duplication from prior review.
- The `_current_mode` duplication from prior review (L3) is resolved -- module-level function now delegates to static method.

**Issues carried from prior review (09-REVIEW.md) that remain:**

| ID | Severity | Status | Description |
|---|---|---|---|
| C1 (INTERRUPTED unreachable) | Critical | **FIXED** | INTERRUPTED now in all VALID_TRANSITIONS entries |
| C2 (direct Holding field mutation) | Critical | **PARTIAL FIX** | No-op `holding.sector = holding.sector` removed; comment added explaining pattern. Fields still mutated directly but documented. |
| C3 (SQL injection in `_column_exists`) | Critical | **NOT IN ORCHESTRATOR** | This is in sqlite.py, not orchestrator. Not in scope for this review. |
| H2 (`WHERE state = 'OPEN'`) | High | **FIXED** | Changed to `'ACTIVE'` at line 677 |
| H3 (ThreadPoolExecutor thread leak) | High | **DOCUMENTED** | Comment added at line 1998-2003 explaining accepted risk |
| H4 (_run_symbol complexity) | High | **PARTIAL** | `_run_symbol` refactored into sub-methods (_step_13b, _step_13c, _step_13d, etc.) -- this is a significant improvement from 800 lines to a dispatcher calling sub-methods |
| H5 (missing `_symbol_holdings.pop`) | High | **FIXED** | All abort paths now call `self._symbol_holdings.pop(ticker, None)` before returning |
| M1 (hardcoded dummy signing key) | Medium | **FIXED** | Assertion added at line 1664 checking mode is not LIVE |
| M3 (halted tickers excluded) | Medium | **FIXED** | Changed to `include_halted=True` at line 753 |
| M5 (dead code evidence fetch) | Medium | **FIXED** | Replaced with real evidence fetch via `fetch_evidence_for_ticker` at line 1199 |

**Remaining implementation issues:**

1. **Step ordering violation (High):** As noted in Plan-Spec Alignment, steps 2-3 (FX + corp actions) are executed AFTER steps 4-5 (kill switch + flywheel health) in the implementation. The spec clearly orders FX snapshot at step 2, corp actions at step 3, kill switch at step 4. The implementation does: step 0 -> 0.5 -> 1 -> 4 -> 5 -> 3 -> 6-12. This means FX rate is unavailable during flywheel health snapshot (step 5), which could affect multi-currency portfolio valuations.

2. **Weekly re-eval creates new Holding objects instead of loading existing ones (lines 2529-2534):** The re-eval pipeline constructs a fresh `Holding(id=holding_id, ...)` with `cycle_id_opened=cycle_id`. This overwrites the original `cycle_id_opened` with the current cycle, losing the provenance of when the holding was actually opened. Should load the existing holding from DB or at minimum preserve the original `cycle_id_opened`.

3. **Connection-per-query pattern persists (Medium):** Multiple methods open a new `sqlite3.connect()` for each query. The `_db_execute_with_retry` helper exists but is not used consistently. High-frequency operations like `_step_fde` (50 holdings) and `_step_lessons` (50 resolutions) each open/close connections in tight loops.

4. **CREATE TABLE statements in step methods (Medium):** Tables `scan_records`, `failure_classifications`, and `lessons` are created on-the-fly in step methods (lines 1574, 3138, 3024) rather than in `SCHEMA_SQL` in `sqlite.py`. This scatters schema ownership.

5. **`_current_price` stored as instance attribute (lines 1230, 1427, 1602):** `self._current_price` is set during `_step_13d_personas` and read in `_step_13h_l_decision` and `_step_13op_execution`. This is fragile -- if symbols are processed in parallel (they are not currently, but the slot dispatch is), this would be a race condition. More importantly, it means a failed symbol could leave a stale price for the next symbol.

6. **Error codes not all registered (Info):** The prior review flagged `DATA_UNAVAILABLE`, `STEP_OVER_BUDGET`, `MEMO_WRITER_FAILED`, `OPPORTUNITY_COST_FAILED`, `LEDGER_CONSTRAINT`, `DB_WRITE_FAILED` as unregistered. These are still used in the orchestrator without being added to `error_classifier.py`'s `VALID_ERROR_CODES`.

---

## Gaps and Risks

### Gap 1: Step ordering deviates from spec
The implementation runs kill switch check (step 4) and flywheel health (step 5) before FX snapshot (step 2) and corporate actions (step 3). Architecture.md SS12 explicitly lists the sequence as 0, 0.5, 1, 2, 3, 4, 5, 6... The current code orders it 0, 0.5, 1, 4, 5, 3, 6... **Risk:** Flywheel health snapshot at step 5 may use stale FX data from a previous cycle, producing incorrect multi-currency portfolio valuations. **Fix:** Move `_run_pre_cycle` call to before step 4 (kill switch), or at minimum move the FX snapshot (op_seq=3) to execute before steps 4-5.

### Gap 2: Crash resume is untested end-to-end
The idempotency mechanism is the primary defense against partial-cycle corruption. It is tested only as a side effect -- no test creates a partial cycle, simulates a crash (process exit), and verifies that a new orchestrator instance resumes correctly from the checkpoint. **Risk:** A bug in the resume logic would only surface in production after a real crash. **Fix:** Create a dedicated crash-resume test that: (1) runs a cycle with pre-cycle steps completed, (2) terminates the orchestrator mid-symbol, (3) instantiates a new `CycleOrchestrator`, (4) runs `run_cycle()` again, (5) verifies completed steps are skipped and the interrupted symbol re-runs.

### Gap 3: No LLM inference health check
PLAN.md S3-1 specifies: "Before entering per-symbol loop, add LLM inference health check: `GET http://localhost:8080/health` -- abort cycle with `INFERENCE_BACKEND_UNREACHABLE` if unreachable." The implementation does not include this health check. **Risk:** The cycle will proceed to persona dispatch (step 13d) and fail on every symbol, wasting the entire pre-cycle pipeline work, instead of failing fast at step 13 entry. **Fix:** Add `_step_inference_health_check` before the per-symbol loop in `_run_all_symbols`.

### Gap 4: Audit chain integrity is not verified in the close path
The spec says "every state transition is hash-chained" (Five Non-Negotiables #3). The orchestrator writes audit events via `AuditWriter.append()`, which chains via `prev_sha256`. However, no step in the cycle verifies that the chain is intact before closing. A corrupted audit event (disk error, truncated write) would go undetected until the next cortex verification cycle. **Risk:** A mid-cycle audit corruption is not caught at cycle close. **Fix:** Add a chain verification step before `close_cycle()` (step 29) that reads back the audit entries for this `cycle_id` and verifies the hash chain.

### Risk 1: `op_seq` is not monotonic
Because the implementation batches steps 2-3 into op_seq=3 and runs them after steps 4-5, the `op_idempotency` table will have entries with op_seq values that are not monotonically increasing by time: 0, 1, 2, 4, 5, 3, 6, 7... This could confuse crash-resume logic if the resume protocol assumes monotonic ordering. The current `_skip_if_complete` only checks existence, not ordering, so this works correctly. But any future change that uses `last_op_seq` to determine resume position could break.

### Risk 2: Paper signing key assertion checks config mode, not actual mode
Line 1664 checks `self._config.get("mode", "PAPER")` rather than the actual mode from `mode_history` table. If the config file says "PAPER" but the operator has promoted to LIVE via TOTP-gated promotion, the assertion would pass incorrectly, allowing the dummy key in LIVE mode. **Fix:** Use `self._current_mode(self._db_path)` instead of config-based check.

---

## Recommendations

1. **Fix step ordering (High priority):** Move FX snapshot (step 2) and corporate actions (step 3) to execute before kill switch check (step 4). This is a spec compliance issue. The fix is straightforward -- call `_run_pre_cycle` in two phases, or move FX/corp-actions into `run_cycle` directly before step 4.

2. **Add crash-resume integration test (High priority):** This is the most important missing test. The idempotency mechanism is the primary defense against data corruption on crash. It must be tested as a primary behavior, not a side effect.

3. **Add inference health check before per-symbol loop (Medium priority):** This was specified in the PLAN (S3-1) but not implemented. It is a cheap fail-fast that prevents wasting the entire per-symbol pipeline on an unreachable LLM.

4. **Register error codes in error_classifier.py (Medium priority):** Six error codes used in the orchestrator are not in the canonical registry. This violates Architecture.md SS5.5.

5. **Move CREATE TABLE statements to sqlite.py SCHEMA_SQL (Low priority):** Schema ownership should be centralized. Scattered DDL in step methods makes migrations harder.

6. **Fix paper signing key assertion to use actual mode (Low priority):** Use `_current_mode()` instead of config value.

---

## Overall Score: 3.5/5

The implementation is substantial and mostly correct. All 30 canonical steps are wired, the step ordering within each phase group (pre-cycle, per-symbol, post-cycle) matches the spec, and the hardening features (kill switch mid-cycle, graceful shutdown, idempotency, timing) are well-implemented. The prior review's critical C1 bug (INTERRUPTED unreachable) has been fixed. The `_run_symbol` method was refactored into clean sub-methods (addressing H4). Multiple medium-severity issues from the prior review have been resolved.

The two main detractors are:
1. The step ordering violation (FX snapshot runs after kill switch/flywheel health), which is a spec compliance gap that could affect data correctness.
2. The crash-resume mechanism is the system's primary defense against crash corruption, and it lacks an end-to-end test that actually exercises the resume path.

Neither is a blocking issue for Phase 10, but both should be addressed before the system enters PAPER mode with real (paper) money on the line.

**Score breakdown:**
- Plan-Spec Alignment: 4/5 (minor ordering gap)
- Exit Test Coverage: 4/5 (crash resume gap)
- Implementation Quality: 3/5 (ordering violation, untested resume, missing health check)
- Overall: 3.5/5

---

_Reviewed: 2026-05-26T12:35:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
