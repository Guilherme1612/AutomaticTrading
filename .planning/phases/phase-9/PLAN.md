# Phase 9: Core Orchestration

## Goal
Wire all 30 canonical cycle steps into `pmacs/nervous/orchestrator.py`, connecting 24+ existing but disconnected engines, agents, and storage modules into a working end-to-end decision pipeline.

## Key Decisions (from CONTEXT.md)
- **D1:** Orchestration only -- no new features. All engines exist; wire them.
- **D2:** Alpaca integration deferred to Phase 10. Mock fills only.
- **D3:** 6-wave build order (see waves below).
- **D4:** Step-dispatch table pattern. Each step = callable with `(cycle_id, op_seq, db, audit)` signature.

## Constraints (inviolable)
- State transitions ONLY via `state_machine.transition()` -- NEVER `holding.state =`
- `cycle_id` required on ALL audit events -- NEVER `cycle_id=None`
- `canonical_json()` for audit serialization -- NEVER `json.dumps()`
- `BUCKETS["source"].acquire()` for rate limiting -- NEVER custom rate-limit
- All mutations require operator TOTP -- no auto-promote
- Evidence scoped per-symbol -- no cross-ticker leakage
- Crucible hard-limited to 2 cycles, 90s/cycle
- Persona dispatch across 3 parallel slots (not sequential)

## Waves

### Wave 1: Core Skeleton (S1)
**Goal:** Cycle opens, acquires flock, checks kill switch, resumes from checkpoint, closes cleanly with audit + SSE.
**Depends on:** none
**Files:** `pmacs/nervous/orchestrator.py`, `tests/integration/test_cycle_skeleton.py`

#### Tasks

##### S1-1: CycleOrchestrator class + flock lock + step dispatch table `[ESTIMATE: Md]`
- **What:** Rewrite `orchestrator.py` from stub to full `CycleOrchestrator` class. Add `CycleLock` context manager using `fcntl.flock()` on `/var/db/pmacs/cycle.lock`. Add `step_dispatch` dict mapping step numbers `(0, 0.5, 1, ... 30)` to async-aware callables. Each callable signature: `(self, cycle_id: str, op_seq: int) -> int` returning next op_seq. Add `run_cycle(trigger: str) -> str` as the main entry point that iterates through the dispatch table in order. Wire existing `initiate_cycle()` and `close_cycle()` into step 0 and step 29 respectively. Add `_mark_op_complete(cycle_id, op_seq, op_type)` helper that writes to `op_idempotency` table. Add `_skip_if_complete(cycle_id, op_seq) -> bool` that checks idempotency.
- **Where:** `pmacs/nervous/orchestrator.py` (full rewrite, preserving `initiate_cycle` and `close_cycle` as internal methods)
- **Test:** `pytest tests/integration/test_cycle_skeleton.py::test_cycle_open_close -x`
- **Commit:** `feat(09): S1-1 CycleOrchestrator class with flock lock and dispatch table`

##### S1-2: Checkpoint resume + kill switch + clock drift checks `[ESTIMATE: Md]`
- **What:** Wire step 1 (`checkpoint.maybe_resume_cycle` -- check for existing OPEN cycle for today, replay completed ops from `op_idempotency`). Wire step 0.5 (`clock_monitor.check_ntp_drift` -- abort if drift > threshold). Wire step 4 (`kill_switch.is_engaged` -- abort cycle if engaged). Wire step 5: `flywheel_health.snapshot_health()` -- takes rolling metrics from DuckDB, returns `FlywheelHealthSnapshot`. This is a lightweight call; wire fully now (not deferred). After kill switch check fails, transition cycle state to ABORTED in SQLite. Add SSE events for cycle_aborted. Create integration test that: opens cycle, simulates crash (writes partial op_idempotency), resumes cycle, verifies completed steps are skipped.
- **Where:** `pmacs/nervous/orchestrator.py`, `tests/integration/test_cycle_skeleton.py`
- **Test:** `pytest tests/integration/test_cycle_skeleton.py::test_cycle_resume_from_checkpoint -x`
- **Commit:** `feat(09): S1-2 checkpoint resume, kill switch gate, clock drift check`

##### S1-3: Integration test scaffold -- cycle skeleton `[ESTIMATE: Sm]`
- **What:** Create `tests/integration/test_cycle_skeleton.py` with fixtures: in-memory SQLite with all required tables (cycles, op_idempotency, queue, kill_switch, holdings), temp audit log file, mock SSE publisher. Tests: (1) `test_cycle_open_close` -- full open-to-close with lock acquisition and audit trail, (2) `test_cycle_resume_from_checkpoint` -- partial cycle, resume skips completed steps, (3) `test_kill_switch_blocks_cycle` -- engage kill switch, verify cycle aborts, (4) `test_flock_prevents_concurrent_cycles` -- two CycleOrchestrator instances, second fails to acquire lock. All tests use synthetic data only, no LLM calls.
- **Where:** `tests/integration/test_cycle_skeleton.py`
- **Test:** `pytest tests/integration/test_cycle_skeleton.py -x`
- **Commit:** `feat(09): S1-3 cycle skeleton integration tests`

---

### Wave 2: Pre-Cycle Data + Queue Composition (S2)
**Goal:** Cycle fetches FX rates, syncs universe, runs gatekeeper, composes sorted queue of admitted tickers with correct priority bands.
**Depends on:** Wave 1
**Files:** `pmacs/nervous/orchestrator.py`, `tests/integration/test_precycle_pipeline.py`

#### Tasks

##### S2-1: FX snapshot + corporate actions + macro regime `[ESTIMATE: Md]`
- **What:** Wire step 2 (`fx.fetch_ecb_rate()` -- store ECB rate in `fx_snapshots` table via DataGateway with `BUCKETS["ecb"].acquire()` rate limiting). Wire step 3 (`corp_actions.adjust_price_for_split` / `adjust_cost_basis_for_dividend` for all active holdings from SQLite). Wire step 6 (`MacroRegimeRunner.run()` -- this is an LLM persona call, needs evidence fetch from gateway first; in test mode, accept a mock MacroRegime output). Store macro regime result in memory for later per-symbol episodic context injection (step 13c). Each step writes to `op_idempotency` on completion. Handle step-specific errors gracefully: FX fetch failure -> abort with `FX_UNAVAILABLE`, corp action failure -> log and continue (non-blocking).
- **Where:** `pmacs/nervous/orchestrator.py`
- **Test:** `pytest tests/integration/test_precycle_pipeline.py::test_fx_and_corp_actions -x`
- **Commit:** `feat(09): S2-1 FX snapshot, corporate actions, macro regime wiring`

##### S2-2: Catalyst resolution + universe sync + gatekeeper `[ESTIMATE: Md]`
- **What:** Wire step 7 (`catalyst_resolution` -- since `pmacs/data/resolution/` only has `__init__.py`, create a stub `CatalystResolutionDetector` class in `pmacs/data/resolution/detector.py` that queries pending catalysts from SQLite and marks resolved ones; full implementation deferred). Wire step 8 (`universe.get_universe()` + `universe.flag_halted()`). Wire step 9 (`gatekeeper.gate()` for each universe ticker -- the gate function takes a ticker and `RiskConfigLike` / `ConfigLike` protocol objects, returns `GatekeeperResult` with admitted bool and reason). Collect gatekeeper results for queue composition. Rate-limit gatekeeper calls via `BUCKETS`.
- **Where:** `pmacs/nervous/orchestrator.py`, `pmacs/data/resolution/detector.py` (new)
- **Test:** `pytest tests/integration/test_precycle_pipeline.py::test_gatekeeper_filters_universe -x`
- **Commit:** `feat(09): S2-2 catalyst resolution detector, universe sync, gatekeeper wiring`

##### S2-3: Lessons flagger + override learning + queue composition `[ESTIMATE: Md]`
- **What:** Wire step 10 (`lessons.extract_lesson_from_resolution` for daily flagging of resolution patterns from DuckDB). Wire step 11 (`override_learning.cluster_overrides` for recent operator overrides from SQLite). Wire step 12 (`queue.compose_queue` -- takes gatekeeper results + persistent pins from `persistent_pins` table + catalyst imminence scores, returns sorted list of `QueueItem` with priority bands P1-P4 per Architecture.md scoring formula: `priority_score = catalyst_imminence*3.0 + thesis_strength*2.0 + source_brier_avg*1.5 + portfolio_fit*1.0`). Active holdings always in P1. Write queue to `queue` table in SQLite. Emit SSE event `queue.composed` with ticker count per band.
- **Where:** `pmacs/nervous/orchestrator.py`, `tests/integration/test_precycle_pipeline.py`
- **Test:** `pytest tests/integration/test_precycle_pipeline.py::test_queue_composition_priority -x`
- **Commit:** `feat(09): S2-3 lessons flagger, override learning, queue composition`

---

### Wave 3: Per-Symbol Pipeline (S3)
**Goal:** Each ticker goes through full Phase 1 (7 personas, 3 slots) + Phase 2 (Crucible) -> verdict -> sizing -> risk gate -> mock execution. At least 1 of 3 synthetic tickers produces STRONG_BUY with mock fill.
**Depends on:** Wave 2
**Pre-flight (executor must verify before starting):**
1. All 7 persona runners have `build_prompt()`, `get_pydantic_model()`, `get_sanity_validator()` implementations (not stubs)
2. `crucible.py` implements the 2-cycle inner state machine (INITIAL -> REWRITE -> ABORT)
3. `engines/pricing.py` computes EV from probability vectors (not a stub)
4. Evidence assembly per-ticker works (may need `pmacs/data/evidence_builder.py`)
**Files:** `pmacs/nervous/orchestrator.py`, `tests/integration/test_symbol_pipeline.py`

#### Tasks

##### S3-1: Pre-flight checks + run_symbol skeleton + state transitions + episodic context `[ESTIMATE: Md]`
- **What:** Before entering per-symbol loop, add LLM inference health check: `GET http://localhost:8080/health` -- abort cycle with `INFERENCE_BACKEND_UNREACHABLE` if unreachable. Add `run_symbol(cycle_id, queue_item, op_seq) -> int` method. Wire step 13a: `state_machine.transition(holding, PHASE1_RESEARCH, "phase1 start", cycle_id, op_seq++)` -- creates a new `Holding` object in CANDIDATE state first, then transitions. Wire step 13b: `memory.check_antipattern(ticker, cycle_id)` -- if returns non-None abort reason, transition to ABORTED_PRE_LLM and skip to next symbol. Wire step 13c: `episodic_context.inject_and_log()` with batched data fetches per ticker (query KuzuDB for recent failures, DuckDB for persona affinity, Qdrant for similar lessons -- reuse across personas within same ticker to reduce queries from 672 to ~80). Store the context brief for passing to each persona. Handle missing stores gracefully (fallback to minimal brief with regime only).
- **Where:** `pmacs/nervous/orchestrator.py`
- **Test:** `pytest tests/integration/test_symbol_pipeline.py::test_symbol_state_transitions -x`
- **Commit:** `feat(09): S3-1 pre-flight check, run_symbol with state transitions and episodic context`

##### S3-2: Persona slot dispatcher (3 slots, 7 personas) `[ESTIMATE: Lg]`
- **What:** Implement `_dispatch_personas(evidence, episodic_context, cycle_id) -> dict[PersonaName, DirectionalProbability]` using `concurrent.futures.ThreadPoolExecutor(max_workers=3)`. Slot map per Architecture.md section 12.2: slot 0 = `[MacroRegime, CatalystSummarizer]`, slot 1 = `[MoatAnalyst, GrowthHunter]`, slot 2 = `[InsiderActivity, ShortInterest, Forensics]`. Within each slot, personas run sequentially. Across slots, they run in parallel. Import each runner from `pmacs/agents/<name>.py`. Each runner is a `PersonaRunner` subclass; call `runner.run(evidence, episodic_context=brief)`. Timeout: 270s total for all Phase 1 dispatch. On timeout for any persona: log WARN, mark that persona as failed. If ALL personas fail: `state_machine.transition(holding, PHASE1_TIMEOUT, ...)`. On any persona failure after 2 retries per `PersonaRunner`: individual persona skipped, not the whole pipeline. Collect `DirectionalProbability` outputs from successful personas.
- **Where:** `pmacs/nervous/orchestrator.py`
- **Test:** `pytest tests/integration/test_symbol_pipeline.py::test_persona_dispatch_3_slots -x`
- **Commit:** `feat(09): S3-2 persona slot dispatcher with 3-slot parallel dispatch`

##### S3-3: Arbitration + Crucible + EV + conviction + sizing `[ESTIMATE: Lg]`
- **What:** Wire step 13e: `arbitration.arbitrate(signals, weights)` where signals are the `DirectionalProbability` outputs from personas, weights from `persona_weights` table (or defaults). Wire step 13f: `state_machine.transition(holding, PHASE2_CRUCIBLE, ...)`. Wire step 13g: `CrucibleRunner.run()` with hard budget of 2 cycles, 90s/cycle -- wrap in timeout, default to NO_TRADE on timeout. Wire step 13h: `pricing.compute_ev(EvInputs(...))` using arbitrated probabilities. Wire step 13i: `state_machine.transition(holding, APPROVED_PENDING, ...)`. Wire step 13j: `sizing.size_position(SizingInputs(...))` with half-Kelly, bootstrap/limited-history haircuts. Wire step 13k: `conviction.compute_conviction(ConvictionInput(...))` + `conviction.score_to_verdict(score)` or `conviction.verdict_tier()` depending on result -- produces `ConvictionResult` with `VerdictTier`. If verdict is SKIP or HOLD: abort via ABORTED_RISK, no trade.
- **Where:** `pmacs/nervous/orchestrator.py`
- **Test:** `pytest tests/integration/test_symbol_pipeline.py::test_arbitration_through_conviction -x`
- **Commit:** `feat(09): S3-3 arbitration, crucible, EV, sizing, conviction pipeline`

##### S3-4: Risk gate + memo + scan record + mock execution `[ESTIMATE: Md]`
- **What:** Wire step 13l: `portfolio_risk_gate.evaluate_risk_gate(RiskGateInputs(...))` -- checks max positions (5), sector concentration, capital utilization. If blocked: `state_machine.transition(holding, ABORTED_RISK, ...)`. Wire step 13m: `MemoWriterRunner.run()` -- reads all persona outputs + Arbitrated + Crucible + Conviction + Verdict, produces operator-facing memo. Wire step 13n: write per-ticker `ScanRecord` to DuckDB with all pipeline outputs. Wire step 13o: if approved (verdict is BUY or STRONG_BUY AND risk gate passed): `state_machine.transition(holding, ACTIVE, "approved", cycle_id, op_seq++)`, then construct `TradePlan`, call `execution.service.ExecutionService.sign_and_send()` via UDS for mock fill, update `PaperLedger.open_position()`. Wire step 13p: place catastrophe-net stop at 15% below entry (mock for now, write to `stop_events` table). Skip catastrophe_net actual broker call per D2 (Alpaca deferred).
- **Where:** `pmacs/nervous/orchestrator.py`, `tests/integration/test_symbol_pipeline.py`
- **Test:** `pytest tests/integration/test_symbol_pipeline.py::test_full_symbol_pipeline_mock_fill -x`
- **Commit:** `feat(09): S3-4 risk gate, memo writer, scan record, mock execution`

##### S3-5: Integration test scaffold -- symbol pipeline `[ESTIMATE: Md]`
- **What:** Create `tests/integration/test_symbol_pipeline.py` with fixtures: mock persona runners that return synthetic `DirectionalProbability` outputs (3 UP, 2 FLAT, 2 DOWN to produce a BUY arbitrated result), mock LLM server (no actual inference), in-memory SQLite + temp DuckDB, mock execution service returning mock fills. Tests: (1) `test_symbol_state_transitions` -- holding goes CANDIDATE -> PHASE1_RESEARCH -> PHASE2_CRUCIBLE -> APPROVED_PENDING -> ACTIVE, (2) `test_persona_dispatch_3_slots` -- verify 3 slots, 7 personas, correct slot assignment, (3) `test_arbitration_through_conviction` -- given synthetic persona outputs, verify arbitrated result passes through crucible, produces BUY verdict, (4) `test_full_symbol_pipeline_mock_fill` -- 3 synthetic tickers, at least 1 produces STRONG_BUY -> mock fill -> audit trail complete, (5) `test_symbol_antipattern_abort` -- memory.check_antipattern returns non-None -> ABORTED_PRE_LLM, no LLM calls made.
- **Where:** `tests/integration/test_symbol_pipeline.py`
- **Test:** `pytest tests/integration/test_symbol_pipeline.py -x`
- **Commit:** `feat(09): S3-5 symbol pipeline integration tests`

---

### Wave 4: Post-Cycle Flywheel (S4)
**Goal:** All 15 post-cycle engines (steps 14-28) fire in correct order after the per-symbol pipeline completes.
**Depends on:** Wave 3
**Files:** `pmacs/nervous/orchestrator.py`, `tests/integration/test_full_cycle.py`

#### Tasks

##### S4-1: Re-evaluation + fills + reconciliation + opportunity cost `[ESTIMATE: Md]`
- **What:** Add `_run_post_cycle(cycle_id, op_seq) -> int` method. Wire step 14: `WeeklyReeval.run_if_due()` -- for active holdings where last re-eval was >= 7 days ago, re-run the per-symbol pipeline (reuse `run_symbol` with modified evidence). Wire step 15: `ThesisAging.run_if_90d()` -- for holdings >= 90 days old, mandatory re-eval via same pipeline reuse. Wire step 16: `Execution.process_fills()` -- check pending mock fills from execution service, update holdings and ledger. Wire step 17: `reconciliation.reconcile_paper_ledger()` -- compare paper ledger state against SQLite holdings, flag discrepancies. Wire step 18: for each active holding, `opportunity_cost.decide_hold_or_exit()` -- if exit recommended, queue for next cycle's sell processing. Each step checks idempotency before executing.
- **Where:** `pmacs/nervous/orchestrator.py`
- **Test:** `pytest tests/integration/test_full_cycle.py::test_post_cycle_reeval_and_recon -x`
- **Commit:** `feat(09): S4-1 re-evaluation, fills, reconciliation, opportunity cost`

##### S4-2: Calibration + causal attribution + lessons + FDE `[ESTIMATE: Md]`
- **What:** Wire step 19: `calibration.compute_brier()` on recent resolutions, `calibration.refit_persona_weights()` if enough data points. Wire step 20: `crucible_calibration.compute_severity_multiplier()` from crucible outcome history. Wire step 21: `causal_attribution.attribute_resolution()` -- credit/blame per persona for resolved holdings. Wire step 22: `memory.check_antipattern()` for recording resolutions (note: memory.py is currently a stub that always passes -- wire it anyway). Wire step 23: `lessons.extract_lesson_from_resolution()` for new resolutions, write to Qdrant lessons collection. Wire step 24: `override_learning.cluster_overrides()` for recent override outcomes. Wire step 25: `failure_diagnostic.classify()` for terminal holdings -- uses `HoldingContext` dataclass, produces `ClassifyResult` with taxonomy type. Write FailedAssumption nodes to KuzuDB.
- **Where:** `pmacs/nervous/orchestrator.py`
- **Test:** `pytest tests/integration/test_full_cycle.py::test_post_cycle_flywheel_engines -x`
- **Commit:** `feat(09): S4-2 calibration, attribution, lessons, FDE wiring`

##### S4-3: Drift stats + consistency + dead letter + cycle close `[ESTIMATE: Sm]`
- **What:** Wire step 26: `drift.DriftMonitor.check_drift(cycle_id)` -- capture cycle performance metrics. Wire step 27: `consistency.check_cross_db_consistency()` -- cross-validate SQLite/KuzuDB/Qdrant/DuckDB state. Wire step 28: `dead_letter.DeadLetterQueue.get_pending()` + retry exhausted entries. Wire step 29: `close_cycle()` with audit chain close event. Wire step 30: release flock lock (via `CycleLock.__exit__`). Emit final SSE event `cycle.complete` with summary stats (tickers scanned, trades executed, flywheel engines fired).
- **Where:** `pmacs/nervous/orchestrator.py`
- **Test:** `pytest tests/integration/test_full_cycle.py::test_full_cycle_all_30_steps -x`
- **Commit:** `feat(09): S4-3 drift, consistency, dead letter, cycle close`

##### S4-4: Integration test scaffold -- full 30-step cycle `[ESTIMATE: Md]`
- **What:** Create `tests/integration/test_full_cycle.py` with full synthetic cycle fixtures. Tests: (1) `test_post_cycle_reeval_and_recon` -- run cycle with 1 active holding due for weekly re-eval, verify re-eval triggers, reconciliation runs, (2) `test_post_cycle_flywheel_engines` -- verify calibration, lessons, FDE all fire and write to their respective stores, (3) `test_full_cycle_all_30_steps` -- complete cycle: 3 tickers, at least 1 STRONG_BUY, all 30 steps execute, audit chain verifies (hash chain intact from open to close), SSE events emitted at each major step, (4) `test_audit_chain_integrity` -- run full cycle, read audit.log, verify every entry's prev_sha256 chains correctly.
- **Where:** `tests/integration/test_full_cycle.py`
- **Test:** `pytest tests/integration/test_full_cycle.py -x`
- **Commit:** `feat(09): S4-4 full 30-step cycle integration tests`

---

### Wave 5: Hardening (S5)
**Goal:** Per-symbol timeouts, memory management, graceful shutdown, kill switch mid-cycle abort, crash resume at arbitrary step.
**Depends on:** Wave 4
**Files:** `pmacs/nervous/orchestrator.py`, `tests/integration/test_cycle_hardening.py`

#### Tasks

##### S5-1: Per-symbol timeouts + evidence memory management `[ESTIMATE: Md]`
- **What:** Add timeout enforcement to `run_symbol()`: Phase 1 budget 270s (persona dispatch), Crucible budget 90s/cycle (max 2 cycles = 180s). Use `concurrent.futures.ThreadPoolExecutor` with `timeout` parameter on `as_completed()`. On timeout: transition holding to `PHASE1_TIMEOUT` then `ABORTED_LLM` via `state_machine.transition()`. Add evidence memory management: evidence fetched per-symbol is scoped to the `run_symbol()` call stack -- no module-level caches between symbols. After `run_symbol()` completes, all evidence references are dropped. For DuckDB/KuzuDB episodic context queries, batch per ticker (not per persona) as noted in RESEARCH.md section 5.
- **Where:** `pmacs/nervous/orchestrator.py`
- **Test:** `pytest tests/integration/test_cycle_hardening.py::test_symbol_timeout_abort -x`
- **Commit:** `feat(09): S5-1 per-symbol timeouts and evidence memory management`

##### S5-2: Graceful shutdown + kill switch mid-cycle `[ESTIMATE: Md]`
- **What:** Add signal handlers for SIGTERM/SIGINT in `run_cycle()`: on signal, write current state to checkpoint (save op_seq, current symbol being processed), then release lock and exit cleanly. Add kill switch mid-cycle check: after each symbol completes in the per-symbol loop, check `kill_switch.is_engaged()`. If engaged mid-cycle: abort remaining symbols (transition each in-progress holding to `INTERRUPTED` via `state_machine.transition()`), run abbreviated post-cycle (steps 26-28 only), close cycle with `ABORTED` state. Emit SSE event `cycle.interrupted` with reason. Create test: engage kill switch after 2nd of 3 symbols, verify partial cycle completes cleanly with no orphaned holdings.
- **Where:** `pmacs/nervous/orchestrator.py`, `tests/integration/test_cycle_hardening.py`
- **Test:** `pytest tests/integration/test_cycle_hardening.py::test_kill_switch_mid_cycle -x`
- **Commit:** `feat(09): S5-2 graceful shutdown and kill switch mid-cycle abort`

##### S5-3: Crash resume integration test `[ESTIMATE: Sm]`
- **What:** Create `tests/integration/test_cycle_hardening.py`. Tests: (1) `test_symbol_timeout_abort` -- mock persona that sleeps 300s, verify timeout at 270s triggers ABORTED_LLM, (2) `test_kill_switch_mid_cycle` -- 3 tickers in queue, engage kill switch after ticker 2 completes, verify ticker 3 gets INTERRUPTED, cycle closes ABORTED, (3) `test_crash_resume_at_step_13g` -- simulate crash during Crucible (ticker 2 step 13g), resume cycle, verify ticker 1 skipped (completed), ticker 2 re-runs from PHASE1_RESEARCH start, ticker 3 runs fresh, (4) `test_graceful_shutdown` -- send SIGTERM during per-symbol pipeline, verify checkpoint written, lock released.
- **Where:** `tests/integration/test_cycle_hardening.py`
- **Test:** `pytest tests/integration/test_cycle_hardening.py -x`
- **Commit:** `feat(09): S5-3 hardening integration tests`

---

### Wave 6: Performance Validation (S6)
**Goal:** Verify orchestrator meets Architecture.md section 20 time budgets. End-to-end synthetic cycle runs in acceptable time. All edge cases covered.
**Depends on:** Wave 5
**Files:** `pmacs/nervous/orchestrator.py`, `tests/integration/test_performance.py`

#### Tasks

##### S6-1: Performance profiling + timing instrumentation `[ESTIMATE: Md]`
- **What:** Add per-step timing instrumentation to `CycleOrchestrator`: each step in the dispatch table records `time.monotonic()` before and after execution, logs to debug stream with `CYCLE_STEP_TIMING` event code and `step_num`, `duration_ms` fields. Add `cycle_metrics` dict accumulated through the cycle: total_time_ms, per_step_times, persona_dispatch_time_ms, crucible_time_ms, post_cycle_time_ms. Include metrics in the `cycle.close` SSE event. Verify against Architecture.md section 20 budgets: Phase 1 dispatch < 270s, Crucible < 180s (2 cycles), total cycle < 30 min for 16 tickers. If any step exceeds its budget, log WARN with `STEP_OVER_BUDGET` error code.
- **Where:** `pmacs/nervous/orchestrator.py`
- **Test:** `pytest tests/integration/test_performance.py::test_step_timing_recorded -x`
- **Commit:** `feat(09): S6-1 per-step timing instrumentation and budget checks`

##### S6-2: Edge cases + final validation `[ESTIMATE: Md]`
- **What:** Handle edge cases in orchestrator: (1) empty queue (no tickers pass gatekeeper) -- cycle completes with no per-symbol work, post-cycle still fires, (2) all symbols abort before LLM -- cycle completes with no trades, (3) evidence gateway timeout -- per-symbol abort with `DATA_UNAVAILABLE`, other symbols continue, (4) SQLite locked during write -- retry 3x with 100ms backoff, then abort step, (5) DuckDB/KuzuDB/Qdrant unavailable for episodic context -- fallback to minimal brief (regime only), log `STALE_DATA` warning. Run the exit test from CONTEXT.md: full synthetic cycle with 3 tickers, at least 1 STRONG_BUY, mock fill, audit chain verifies, crash resume at step 13g works.
- **Where:** `pmacs/nervous/orchestrator.py`, `tests/integration/test_performance.py`
- **Test:** `pytest tests/integration/test_performance.py::test_exit_test_full_cycle -x`
- **Commit:** `feat(09): S6-2 edge cases and final exit test validation`

##### S6-3: Performance test scaffold `[ESTIMATE: Sm]`
- **What:** Create `tests/integration/test_performance.py`. Tests: (1) `test_step_timing_recorded` -- run cycle, verify timing dict has entries for all 30 steps, (2) `test_empty_queue_cycle` -- all tickers fail gatekeeper, cycle completes cleanly, post-cycle fires, (3) `test_all_symbols_abort` -- all tickers hit antipattern check, no LLM calls, cycle completes, (4) `test_exit_test_full_cycle` -- the CONTEXT.md exit test: open cycle, pre-cycle steps, 3 synthetic tickers, at least 1 STRONG_BUY with mock fill, post-cycle flywheel fires, cycle closes, audit chain verifies, crash resume at step 13g. Verify total cycle time < 30s with mock LLM (no real inference).
- **Where:** `tests/integration/test_performance.py`
- **Test:** `pytest tests/integration/test_performance.py -x`
- **Commit:** `feat(09): S6-3 performance and exit test validation`

## Verification

### Exit Test (from CONTEXT.md)
Full synthetic cycle runs end-to-end:
- [ ] 1. Cycle opens, acquires lock
- [ ] 2. Pre-cycle steps execute (FX, universe, gatekeeper, queue)
- [ ] 3. 3 synthetic tickers pass through per-symbol pipeline
- [ ] 4. At least 1 produces STRONG_BUY with mock fill
- [ ] 5. Post-cycle flywheel engines fire
- [ ] 6. Cycle closes with complete audit trail
- [ ] 7. Audit chain verifies (no tampering)
- [ ] 8. Resume from checkpoint after simulated crash at step 13g

### Additional Checks
- [ ] No `holding.state =` outside `state_machine.py` (grep enforced)
- [ ] No `cycle_id=None` on audit-emitting functions (grep enforced)
- [ ] No `json.dumps()` for audit serialization (grep enforced)
- [ ] No custom rate-limit logic (grep enforced)
- [ ] All 30 canonical steps present in dispatch table
- [ ] Kill switch mid-cycle produces clean abort with no orphaned holdings
- [ ] `pytest tests/ -x` passes full suite

## Dependencies
- All engines from Phases 1-8 (arbitration, conviction, sizing, queue, calibration, etc.)
- All 10 persona runners (7 analysis + gatekeeper + crucible + memo_writer) in `pmacs/agents/`
- All 5 storage backends (SQLite, KuzuDB, Qdrant, DuckDB, audit.log)
- Data sources via `pmacs/data/gateway.py` with rate limiting
- Execution service via `pmacs/execution/service.py` (mock fills, Alpaca deferred per D2)
- `pmacs/cortex/kill_switch.py` for kill switch checks
- `pmacs/cortex/clock_monitor.py` for NTP drift checks
- `pmacs/nervous/checkpoint.py` for crash resume
- `pmacs/nervous/sse_publisher.py` for SSE event emission

## Module Import Map (executor reference)

```python
# Engines
from pmacs.engines.arbitration import arbitrate, ArbitrationSignal
from pmacs.engines.calibration import compute_brier, refit_persona_weights, CalibrationResult
from pmacs.engines.conviction import compute_conviction, score_to_verdict, ConvictionResult
from pmacs.engines.crucible_calibration import compute_severity_multiplier
from pmacs.engines.failure_diagnostic import classify, HoldingContext, ClassifyResult
from pmacs.engines.flywheel_health import snapshot_health, FlywheelHealthSnapshot
from pmacs.engines.memory import check_antipattern
from pmacs.engines.opportunity_cost import decide_hold_or_exit, OpportunityCostResult
from pmacs.engines.override_learning import cluster_overrides, OverrideCluster
from pmacs.engines.portfolio_risk_gate import evaluate_risk_gate, RiskGateInputs, RiskGateResult
from pmacs.engines.pricing import compute_ev, EvInputs, EvResult
from pmacs.engines.queue import compose_queue
from pmacs.engines.reconciliation import reconcile_paper_ledger
from pmacs.engines.sizing import size_position, SizingInputs, SizingResult
from pmacs.engines.state_machine import transition

# Agents
from pmacs.agents.base import PersonaRunner
from pmacs.agents.gatekeeper import gate, GatekeeperResult
from pmacs.agents.crucible import CrucibleRunner
from pmacs.agents.episodic_context import build_context_brief, inject_and_log
from pmacs.agents.memo_writer import MemoWriterRunner
from pmacs.agents.macro_regime import MacroRegimeRunner
from pmacs.agents.catalyst_summarizer import CatalystSummarizerRunner
from pmacs.agents.moat_analyst import MoatAnalystRunner
from pmacs.agents.growth_hunter import GrowthHunterRunner
from pmacs.agents.insider_activity import InsiderActivityRunner
from pmacs.agents.short_interest import ShortInterestRunner
from pmacs.agents.forensics import ForensicsRunner

# Storage
from pmacs.storage.audit import AuditWriter
from pmacs.storage.sqlite import (all tables via sqlite3)
from pmacs.storage.consistency import check_cross_db_consistency

# Data
from pmacs.data.fx import fetch_ecb_rate
from pmacs.data.corp_actions import adjust_price_for_split, adjust_cost_basis_for_dividend
from pmacs.data.universe import get_universe
from pmacs.data.gateway import DataGateway

# Cortex
from pmacs.cortex.kill_switch import is_engaged
from pmacs.cortex.clock_monitor import check_ntp_drift
from pmacs.cortex.drift import DriftMonitor

# Execution
from pmacs.execution.signing import sign_bytes
from pmacs.sim.ledger import PaperLedger

# Schemas
from pmacs.schemas.contracts import Holding, HoldingState
from pmacs.schemas.agents import PersonaName, DirectionalProbability, PersonaOutput
from pmacs.schemas.conviction import ConvictionInput, ConvictionResult, VerdictTier
from pmacs.schemas.trade import TradePlan, TradeDirection, OrderType, TradeResult
from pmacs.schemas.queue import QueueItem

# Nervous
from pmacs.nervous.sse_publisher import SSEPublisher
from pmacs.nervous.checkpoint import save_checkpoint, load_checkpoint, is_completed
from pmacs.logsys import log_debug
from pmacs.logsys.dead_letter import DeadLetterQueue
```
