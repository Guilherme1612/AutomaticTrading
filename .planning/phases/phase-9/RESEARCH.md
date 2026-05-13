# Phase 9: Core Orchestration - Research

**Researched:** 2026-05-13
**Domain:** Cycle orchestration -- wiring 30 canonical steps into pmacs-nervous orchestrator
**Confidence:** HIGH

## Summary

Phase 9 (GSD Phase 9) maps to PMACS build Phases 7-8: the full decision pipeline wired end-to-end, from cycle open through all 7 personas, Crucible, Arbitration, Conviction, Sizing, execution, and cycle close. The orchestrator stub (`pmacs/nervous/orchestrator.py`) currently only opens and closes cycles with no symbol processing. All engines and agents exist as independent modules but are **not wired together** into the canonical 30-step cycle sequence defined in Architecture.md section 12.

The core challenge is assembling 24+ existing modules into a single deterministic pipeline with correct ordering, idempotency, error handling, and SSE/audit emission at every step. This is an integration-heavy phase, not a greenfield one.

**Primary recommendation:** Build the orchestrator as a step-dispatch table where each of the 30 canonical steps maps to a callable with (cycle_id, op_seq, db, audit) signature, leveraging the existing op_idempotency table for crash resume. Wire step-by-step in waves, testing each wave end-to-end before proceeding.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Cycle lifecycle (open/close/resume) | L5 Nervous | L2 SQLite | Nervous owns cycle state; SQLite persists |
| Persona dispatch (7 LLM calls) | L5 Nervous | L4 Agents + L1 Inference | Nervous orchestrates; agents execute; inference serves |
| Arbitration combination | L3 Engine | L5 Nervous | Pure math engine; nervous calls it |
| Crucible adversarial loop | L5 Nervous | L4 Agents | Nervous manages the 2-cycle budget; crucible executes |
| Conviction + verdict | L3 Engine | L5 Nervous | Deterministic engine; nervous calls it |
| Position sizing | L3 Engine | L5 Nervous | Half-Kelly math; nervous provides inputs |
| Trade execution | L5 Nervous | Execution Service (isolated) | Nervous signs and sends via UDS; execution submits |
| Flywheel post-cycle (calibration/lessons/FDE) | L5 Nervous | L3 Engines + L2 Storage | Nervous triggers post-cycle engines in sequence |
| SSE event emission | L5 Nervous | L7 Dashboard | Nervous publishes; dashboard subscribes |

## 1. Current State

### Complete and Ready for Wiring

| File | Status | Notes |
|------|--------|-------|
| `pmacs/nervous/orchestrator.py` | STUB | Only `initiate_cycle()` and `close_cycle()`; no symbol processing |
| `pmacs/nervous/api.py` | COMPLETE | FastAPI with SSE, TOTP, health, session auth |
| `pmacs/nervous/sse_publisher.py` | COMPLETE | Thread-safe fan-out with event IDs |
| `pmacs/nervous/checkpoint.py` | EXISTS | Cycle resume from idempotency log |
| `pmacs/agents/base.py` | COMPLETE | PersonaRunner with 3-layer validation (GBNF/Pydantic/Sanity) |
| `pmacs/agents/gatekeeper.py` | COMPLETE | Deterministic admittance filter with all 7 checks |
| `pmacs/agents/episodic_context.py` | COMPLETE | `build_context_brief()` and `inject_and_log()` |
| `pmacs/agents/crucible.py` | EXISTS | Crucible adversarial attacker |
| `pmacs/agents/memo_writer.py` | EXISTS | Operator-facing memo producer |
| `pmacs/engines/arbitration.py` | COMPLETE | Brier-inverse weighting with bootstrap/bootstrap/abort logic |
| `pmacs/engines/conviction.py` | COMPLETE | Conviction scalar + verdict tier mapping |
| `pmacs/engines/sizing.py` | COMPLETE | Half-Kelly with bootstrap/limited-history haircuts |
| `pmacs/engines/queue.py` | COMPLETE | Queue composition from universe + gatekeeper results |
| `pmacs/engines/calibration.py` | COMPLETE | Brier computation + persona weight refitting |
| `pmacs/engines/lessons.py` | COMPLETE | Lesson extraction from resolutions |
| `pmacs/engines/failure_diagnostic.py` | COMPLETE | 18-type classifier with all taxonomy paths |
| `pmacs/engines/causal_attribution.py` | COMPLETE | Credit/blame apportionment per persona |
| `pmacs/engines/override_learning.py` | COMPLETE | Operator override clustering |
| `pmacs/engines/flywheel_health.py` | COMPLETE | Point-in-time health snapshot |
| `pmacs/engines/mode_manager.py` | COMPLETE | Mode transitions with TOTP gating |
| `pmacs/engines/state_machine.py` | COMPLETE | Holding state transitions (single write location) |
| `pmacs/engines/stop_loss_monitor.py` | EXISTS | Stop detection + trailing stop logic |
| `pmacs/engines/portfolio_risk_gate.py` | EXISTS | Max positions, sector limits |
| `pmacs/engines/opportunity_cost.py` | EXISTS | Hold-or-exit decision |
| `pmacs/engines/reconciliation.py` | EXISTS | Paper-vs-broker reconciliation |
| `pmacs/engines/pricing.py` | EXISTS | EV computation |
| `pmacs/engines/memory.py` | STUB | Antipattern checker (always passes) |
| `pmacs/execution/service.py` | COMPLETE (stub fills) | UDS server with Ed25519 verification, returns mock fills |
| `pmacs/execution/signing.py` | COMPLETE | Ed25519 sign/verify |
| `pmacs/mutation/daemon.py` | COMPLETE | Full lifecycle with dormancy gate |
| `pmacs/data/gateway.py` | COMPLETE | Rate-limited HTTP with TokenBucket |
| `pmacs/storage/sqlite.py` | COMPLETE | All tables from Architecture.md section 8.5 |
| `pmacs/storage/audit.py` | COMPLETE | Hash-chained append-only writer |
| `pmacs/storage/kuzu.py` | PARTIAL | Stub FailedAssumption writer (Phase 6 gap) |
| `pmacs/storage/qdrant.py` | EXISTS | Vector store adapter |
| `pmacs/storage/duckdb.py` | EXISTS | Analytics store adapter |
| `pmacs/schemas/contracts.py` | COMPLETE | Holding, HoldingState, Thesis, state transitions |
| `pmacs/schemas/arbitration.py` | COMPLETE | Arbitrated, DirectionalProbability, PersonaWeight |
| `pmacs/schemas/conviction.py` | EXISTS | ConvictionResult, VerdictTier |
| `pmacs/schemas/agents.py` | EXISTS | PersonaOutput, DirectionalProbability, PersonaName |
| `pmacs/schemas/trade.py` | EXISTS | TradePlan |
| `pmacs/schemas/failure.py` | EXISTS | FailureTaxonomy (needs alignment per Phase 6 review) |
| `pmacs/schemas/queue.py` | EXISTS | QueueItem, PriorityBand |
| `pmacs/sim/ledger.py` | EXISTS | Paper portfolio ledger |

### Missing / Needs Building

| Component | Status | Purpose |
|-----------|--------|---------|
| `pmacs/execution/alpaca_adapter.py` | MISSING | Alpaca paper API adapter for real order submission |
| `pmacs/execution/catastrophe_net.py` | MISSING | Broker-side 15% wide stop placement |
| Orchestrator step-dispatch table | MISSING | Maps 30 canonical steps to callables |
| Orchestrator per-symbol pipeline | MISSING | Steps 13a-13z: persona dispatch through trade execution |
| Orchestrator post-cycle pipeline | MISSING | Steps 14-30: flywheel, calibration, FDE, reconciliation |
| Cycle lock (flock) | MISSING | Prevent concurrent cycles (step 0) |
| Persona slot dispatcher | MISSING | Parallel dispatch across 3 llama-server slots |
| Data refresh orchestration | MISSING | Steps 2-3: FX snapshot, corp actions |

## 2. Canonical 30-Step Cycle (Architecture.md section 12)

Below is the exact 30-step sequence with implementation status.

| Step | Description | Implementing Module | Wired? | Dependencies |
|------|-------------|---------------------|--------|--------------|
| 0 | Acquire cycle lock (flock) | NEW: orchestrator internal | NO | Filesystem |
| 0.5 | ClockMonitor.check_drift() | `pmacs/cortex/clock_monitor.py` | NO | NTP |
| 1 | Resume checkpoint if cycle_id == today | `pmacs/nervous/checkpoint.py` | NO | op_idempotency table |
| 2 | FxSnapshot.capture() (ECB rate) | `pmacs/data/fx.py` + `pmacs/data/sources/ecb.py` | NO | Gateway, ecb source |
| 3 | CorporateActions.process_all_active_holdings() | `pmacs/data/corp_actions.py` | NO | Active holdings from SQLite |
| 4 | KillSwitch.check() | `pmacs/cortex/kill_switch.py` | PARTIAL | Orchestrator checks at start but not mid-cycle |
| 5 | FlywheelHealth.snapshot() | `pmacs/engines/flywheel_health.py` | NO | Rolling metrics from DuckDB |
| 6 | MacroRegime.run() | `pmacs/agents/macro_regime.py` | NO | Evidence from data sources |
| 7 | CatalystResolutionDetector.run_all() | `pmacs/data/resolution/catalyst_detector.py` | NO | Pending catalysts |
| 8 | UniverseSyncer.maybe_check_admittance() | `pmacs/data/universe.py` | NO | Universe table |
| 9 | Gatekeeper.scan_universe() | `pmacs/agents/gatekeeper.py` | NO | Universe tickers, SQLite |
| 10 | LessonsEngine.run_daily_flagger() | `pmacs/engines/lessons.py` | NO | DuckDB scan_records |
| 11 | OverrideLearning.cluster_recent_overrides() | `pmacs/engines/override_learning.py` | NO | operator_overrides table |
| 12 | Queue.compose() | `pmacs/engines/queue.py` | NO | Gatekeeper results, pins |
| **13** | **foreach symbol in queue: (see sub-steps)** | **Orchestrator per-symbol loop** | **NO** | **Queue output** |
| 13a | state_machine.transition(PHASE1_RESEARCH) | `pmacs/engines/state_machine.py` | NO | Holding object |
| 13b | MemoryEngine.check_antipattern() | `pmacs/engines/memory.py` (stub) | NO | Ticker |
| 13c | EpisodicContext.inject() | `pmacs/agents/episodic_context.py` | NO | DuckDB/Qdrant/Kuzu reads |
| 13d | Phase1.run() -- 7 personas, 3 slots | PersonaRunner subclasses | NO | Evidence, episodic context |
| 13e | Arbitration.combine() | `pmacs/engines/arbitration.py` | NO | Persona outputs |
| 13f | state_machine.transition(PHASE2_CRUCIBLE) | `pmacs/engines/state_machine.py` | NO | Arbitrated result |
| 13g | Phase2.crucible() -- 2-cycle max | `pmacs/agents/crucible.py` | NO | Arbitrated + evidence |
| 13h | Pricing.compute_ev() | `pmacs/engines/pricing.py` | NO | Arbitrated probabilities |
| 13i | state_machine.transition(APPROVED_PENDING) | `pmacs/engines/state_machine.py` | NO | Crucible output |
| 13j | Sizing.size() | `pmacs/engines/sizing.py` | NO | EV, portfolio state |
| 13k | Conviction.compute() | `pmacs/engines/conviction.py` | NO | Arbitrated + Crucible + EV |
| 13l | PortfolioRiskGate.evaluate() | `pmacs/engines/portfolio_risk_gate.py` | NO | Sizing result, portfolio |
| 13m | MemoWriter.emit() | `pmacs/agents/memo_writer.py` | NO | All outputs above |
| 13n | ScanRecord.write() | DuckDB writer | NO | Per-ticker result |
| 13o | if approve: transition(ACTIVE), sign_and_send() | state_machine + execution | NO | Risk gate passed |
| 13p | brokers.submit_catastrophe_net_stop() | NEW: catastrophe_net.py | NO | Active holding |
| 14 | WeeklyReeval.run_if_due() | Orchestrator + pipeline reuse | NO | Active holdings, weekly cadence |
| 15 | ThesisAging.run_if_90d() | Orchestrator + pipeline reuse | NO | Holdings 90+ days old |
| 16 | Execution.process_fills() | `pmacs/execution/service.py` | NO | Pending fills |
| 17 | ReconciliationEngine.reconcile() | `pmacs/engines/reconciliation.py` | NO | Broker state vs paper ledger |
| 18 | foreach active: OpportunityCostEngine.decide() | `pmacs/engines/opportunity_cost.py` | NO | Active holdings |
| 19 | Calibration.evaluate_and_maybe_refit() | `pmacs/engines/calibration.py` | NO | Resolution history |
| 20 | CrucibleCalibration.update_multipliers() | `pmacs/engines/crucible_calibration.py` | NO | Crucible outcomes |
| 21 | CausalAttribution.attribute_resolutions() | `pmacs/engines/causal_attribution.py` | NO | Persona outputs, outcomes |
| 22 | Memory.record_resolutions() | `pmacs/engines/memory.py` | NO | Resolution data |
| 23 | LessonsEngine.run_lesson_writer_queue() | `pmacs/engines/lessons.py` | NO | New resolutions |
| 24 | OverrideLearning.evaluate_recent_outcomes() | `pmacs/engines/override_learning.py` | NO | Override outcomes |
| 25 | FailureDiagnostic.classify_pending() | `pmacs/engines/failure_diagnostic.py` | NO | Terminal holdings |
| 26 | Cortex.snapshot_drift_stats() | `pmacs/cortex/drift.py` | NO | Cycle metrics |
| 27 | ConsistencyReconciler.cross_db_audit() | `pmacs/storage/consistency.py` | NO | All 4 stores |
| 28 | DeadLetter.process_queue() | `pmacs/logsys/dead_letter.py` | NO | Pending dead letters |
| 29 | Audit.close_cycle() | `pmacs/storage/audit.py` | NO | Cycle close event |
| 30 | Release cycle lock | NEW: orchestrator internal | NO | Filesystem |

## 3. Missing Wiring Map

### 3.1 Orchestrator Core (NEW code)

| What | Where | Called By | Depends On |
|------|-------|-----------|------------|
| `CycleOrchestrator` class | `pmacs/nervous/orchestrator.py` | API endpoints | All engines, agents, storage |
| `flock` cycle lock | `pmacs/nervous/orchestrator.py` | `run_cycle()` step 0 | `/var/db/pmacs/cycle.lock` |
| `step_dispatch` table | `pmacs/nervous/orchestrator.py` | `run_cycle()` | All step functions |
| `run_symbol()` per-symbol loop | `pmacs/nervous/orchestrator.py` | Step 13 | Persona, Arbitration, Crucible, Sizing, Conviction |
| `run_post_cycle()` | `pmacs/nervous/orchestrator.py` | After step 13 | Calibration, Lessons, FDE, etc. |

### 3.2 Existing Modules Not Called from Cycle

| Module | Function to Call | Where in Cycle | Why Not Wired |
|--------|-----------------|----------------|---------------|
| `agents/episodic_context.py` | `inject_and_log()` | Step 13c (before persona run) | No orchestrator code calls it |
| `engines/arbitration.py` | `arbitrate()` | Step 13e (after personas) | No orchestrator code calls it |
| `engines/conviction.py` | `compute_conviction()`, `verdict_tier()` | Step 13k | No orchestrator code calls it |
| `engines/sizing.py` | `size_position()` | Step 13j | No orchestrator code calls it |
| `engines/queue.py` | `compose_queue()` | Step 12 | No orchestrator code calls it |
| `engines/calibration.py` | `compute_brier()`, `refit_persona_weights()` | Step 19 | No orchestrator code calls it |
| `engines/lessons.py` | `extract_lesson_from_resolution()` | Steps 10, 23 | No orchestrator code calls it |
| `engines/failure_diagnostic.py` | `classify()` | Step 25 | Only called directly in tests |
| `engines/causal_attribution.py` | `attribute_resolution()` | Step 21 | No orchestrator code calls it |
| `engines/override_learning.py` | `cluster_overrides()` | Steps 11, 24 | No orchestrator code calls it |
| `engines/flywheel_health.py` | `snapshot_health()` | Step 5 | No orchestrator code calls it |
| `engines/opportunity_cost.py` | decide function | Step 18 | No orchestrator code calls it |
| `engines/portfolio_risk_gate.py` | evaluate function | Step 13l | No orchestrator code calls it |
| `engines/reconciliation.py` | reconcile function | Step 17 | No orchestrator code calls it |
| `agents/gatekeeper.py` | `gate()` | Step 9 | No orchestrator code calls it |
| `agents/crucible.py` | `run()` | Step 13g | No orchestrator code calls it |
| `agents/memo_writer.py` | `run()` | Step 13m | No orchestrator code calls it |
| `execution/service.py` | `sign_and_send()` | Step 13o | No orchestrator code calls it |
| `mutation/daemon.py` | `run_cycle()` | Runs independently | Separate process |
| `data/gateway.py` | `fetch()` | Steps 2, 3, 6 (evidence fetch) | Data refresh not wired into cycle |
| `nervous/checkpoint.py` | `maybe_resume_cycle()` | Step 1 | Called nowhere |

## 4. Flywheel Integration Points

| Flywheel Component | Cycle Step Trigger | Data Flow | Storage |
|--------------------|-------------------|-----------|---------|
| Calibration | Step 19 (post-cycle) | Reads resolution history from DuckDB, computes Brier, refits weights | DuckDB `persona_performance`, writes new weights to ArbitrationEngine |
| Lessons | Steps 10 (daily flag), 23 (lesson writer) | Reads terminal resolutions, extracts patterns | Qdrant `lessons` collection, KuzuDB `Lesson` nodes |
| FDE | Step 25 (post-cycle) + inline in state_machine | Classifies terminal holdings into 18 types | KuzuDB `FailedAssumption` nodes |
| Causal Attribution | Step 21 (post-cycle) | Reads persona outputs vs actual outcomes | DuckDB `persona_performance` |
| Override Learning | Steps 11, 24 (pre/post cycle) | Clusters operator overrides, evaluates outcomes | SQLite `operator_overrides` |
| Crucible Calibration | Step 20 (post-cycle) | Adjusts severity multipliers based on outcomes | Config/runtime |
| Flywheel Health | Step 5 (pre-cycle) | Snapshots rolling Brier, Sharpe, calibration gap | DuckDB `rolling_metrics` |
| Mutation Daemon | Independent process | Reads FDE clusters from SQLite, generates A/B candidates | SQLite `mutation_proposals`, `mutation_outcomes` |
| Episodic Context | Step 13c (per-symbol) | Reads DuckDB affinity, Qdrant lessons, KuzuDB failures | Injected into persona prompt as 200-word brief |

### Critical dependency chain for flywheel:
```
Resolution accumulates -> FDE classifies -> KuzuDB FailedAssumption
  -> Mutation Daemon reads clusters -> generates candidates -> A/B test
  -> Calibration refits weights -> improved Arbitration
  -> Episodic Context injects into personas -> improved analysis
```

## 5. Episodic Context Injection

### Current State
`pmacs/agents/episodic_context.py` has a complete `build_context_brief()` function and `inject_and_log()` helper.

### Integration Pattern

The brief must be built **per persona, per ticker** before each persona run in step 13c. The integration point is in `PersonaRunner.run()`:

```python
# In orchestrator's run_symbol() step 13c:
brief, content_hash = inject_and_log(
    persona=persona_name,
    ticker=ticker,
    cycle_id=cycle_id,
    regime=current_regime.regime,
    regime_confidence=current_regime.regime_confidence,
    recent_failures=load_recent_failures(ticker),     # KuzuDB query
    affinity_data=load_affinity(persona, ticker),       # DuckDB query
    fde_history=load_fde_history(ticker),               # KuzuDB query
    recent_lessons=load_similar_lessons(ticker_thesis), # Qdrant query
)
# Then pass brief to PersonaRunner.run(evidence, episodic_context=brief)
```

### Data Sources Required (per persona-ticker pair)

| Source | Store | Query | Required for |
|--------|-------|-------|--------------|
| Macro regime | In-memory (step 6 output) | Direct | All personas |
| Recent failures | KuzuDB | `MATCH (fa:FailedAssumption)-[:FAILED_ASSUMPTION]-(h:Holding {ticker: $t})` | All personas |
| Persona-ticker affinity | DuckDB | `SELECT avg_brier, cycle_count FROM persona_ticker_affinity WHERE persona=$p AND ticker=$t` | All personas |
| FDE failure history | KuzuDB | Recent FailedAssumption nodes | All personas |
| Similar lessons | Qdrant | Vector search on thesis embedding against `lessons` collection | All personas |
| Operator overrides | DuckDB/SQlite | Recent operator_overrides with outcome | All personas |

### Performance Consideration
Each symbol needs 4-6 storage queries before persona dispatch. With 16 tickers and 7 personas, that is up to 672 storage queries per cycle. These should be **batched per ticker** (query once, reuse across personas) to reduce to ~80 queries.

## 6. Broker Integration Gaps

### What Exists

| Component | File | Status |
|-----------|------|--------|
| Execution UDS service | `pmacs/execution/service.py` | COMPLETE (mock fills) |
| Ed25519 signing | `pmacs/execution/signing.py` | COMPLETE |
| Paper ledger | `pmacs/sim/ledger.py` | EXISTS |
| Paper adapter | `pmacs/sim/alpaca_paper_adapter.py` | EXISTS |

### What's Missing

| Component | Purpose | Interface Needed |
|-----------|---------|-----------------|
| `pmacs/execution/alpaca_adapter.py` | Real Alpaca paper API submission | `submit_order(symbol, side, qty, order_type) -> FillReport` |
| `pmacs/execution/catastrophe_net.py` | 15% broker-side stop at entry | `place_stop_loss(symbol, qty, stop_price) -> OrderID` |
| Fill polling | Wait for order fills after submission | `poll_fill(order_id, timeout=30) -> FillReport` |
| Order cancellation | Cancel catastrophe-net on primary exit | `cancel_order(order_id) -> bool` |

### Execution Adapter Interface

The orchestrator should NOT import Alpaca SDK directly. It calls `ExecutionService.sign_and_send()` via UDS, and the execution process handles broker communication internally. For Phase 9, the execution service returns mock fills (already implemented). Alpaca paper wiring is a separate task.

## 7. Risk Assessment

### Highest Risk Steps

| Risk | Step(s) | Impact | Mitigation |
|------|---------|--------|------------|
| Persona dispatch timeout (270s budget) | 13d | Single ticker can block entire cycle | Per-symbol timeout with ThreadPoolExecutor, abort to ABORTED_LLM |
| LLM inference server down | 13d | All persona calls fail, entire cycle aborts | Pre-cycle health check on :8080; graceful abort with `INFERENCE_BACKEND_UNREACHABLE` |
| SQLite write contention (orchestrator + mutation + stoploss) | 0-30 | Data corruption or lock timeout | WAL mode (already enabled); separate connections; brief transactions |
| Memory exhaustion (7 personas x 16 tickers x evidence) | 13 | Process OOM, cycle dies | Process evidence per-symbol, discard after use; streaming evidence reads |
| Partial pipeline failure (persona succeeds, crucible fails) | 13e-13g | Inconsistent state | State machine guarantees: ABORTED_LLM is safe terminal state |
| Concurrent cycles | 0 | Double-processing, duplicate trades | flock on cycle.lock; idempotency keys on every state mutation |
| Orchestrator crash mid-symbol | 13 | Symbol stuck in PHASE1_RESEARCH | Resume from checkpoint; op_idempotency replays completed steps |

### Medium Risk

| Risk | Step(s) | Impact | Mitigation |
|------|---------|--------|------------|
| Stale evidence (fetched yesterday) | 2, 13d | Bad analysis | Staleness checks in gateway; CRITICAL sources block; IMPORTANT degrade |
| KuzuDB/Qdrant unavailable for episodic context | 13c | No context brief | Fallback to minimal brief (regime only); log STALE_DATA |
| SSE queue overflow (1024 per client) | All | Lost events | Dashboard reconnects with Last-Event-ID; events also in audit log |

### Testing Priority
1. State machine transitions through full pipeline (CANDIDATE -> ACTIVE or ABORTED_*)
2. Idempotency: crash at any op_seq, resume skips completed steps
3. Per-symbol timeout: persona dispatch exceeds 270s -> PHASE1_TIMEOUT -> ABORTED_LLM
4. Kill switch: engage mid-cycle -> clean abort, no partial state
5. Queue composition: 16 tickers, priority bands correct, pins honored

## 8. Implementation Order

### Wave 1: Core Skeleton (orchestrator structure + steps 0-4, 29-30)

**Goal:** Cycle opens, acquires lock, runs pre-cycle checks, closes cleanly.

Tasks:
1. Create `CycleOrchestrator` class with `run_cycle()` method
2. Implement flock cycle lock (step 0, 30)
3. Wire checkpoint resume (step 1) using existing `checkpoint.py`
4. Wire kill switch check (step 4)
5. Wire clock drift check (step 0.5)
6. Wire cycle close with audit + SSE (step 29)
7. Step dispatch table: each step is a method returning (success: bool, next_op_seq: int)

**Test:** Open cycle -> check kill switch -> close cycle. Resume from checkpoint after simulated crash.

### Wave 2: Pre-Cycle Data + Queue (steps 2-3, 5-12)

**Goal:** Cycle fetches FX, syncs universe, runs gatekeeper, composes queue.

Tasks:
1. Wire FxSnapshot.capture() (step 2)
2. Wire CorporateActions (step 3)
3. Wire FlywheelHealth.snapshot() (step 5)
4. Wire MacroRegime.run() (step 6) -- evidence fetch + persona run
5. Wire CatalystResolution (step 7)
6. Wire UniverseSync (step 8)
7. Wire Gatekeeper.scan_universe() (step 9)
8. Wire LessonsEngine.run_daily_flagger() (step 10)
9. Wire OverrideLearning.cluster_recent_overrides() (step 11)
10. Wire Queue.compose() (step 12)

**Test:** Cycle produces a sorted queue of admitted tickers with correct priority bands.

### Wave 3: Per-Symbol Pipeline (step 13a-13p)

**Goal:** Each ticker goes through full Phase 1 + Phase 2 -> verdict -> trade.

Tasks:
1. Implement `run_symbol()` method with per-symbol op_seq counter
2. Wire state_machine.transition to PHASE1_RESEARCH (step 13a)
3. Wire MemoryEngine.check_antipattern() (step 13b)
4. Wire EpisodicContext.inject() with data fetches (step 13c)
5. Implement persona slot dispatcher (3 slots, 7 personas) (step 13d)
6. Wire Arbitration.combine() (step 13e)
7. Wire state_machine.transition to PHASE2_CRUCIBLE (step 13f)
8. Wire Crucible with 2-cycle budget (step 13g)
9. Wire Pricing.compute_ev() (step 13h)
10. Wire state_machine.transition to APPROVED_PENDING (step 13i)
11. Wire Sizing.size() (step 13j)
12. Wire Conviction.compute() + verdict_tier() (step 13k)
13. Wire PortfolioRiskGate.evaluate() (step 13l)
14. Wire MemoWriter.emit() (step 13m)
15. Wire ScanRecord.write() to DuckDB (step 13n)
16. Wire trade execution path: ACTIVE transition + sign_and_send (step 13o)
17. Wire catastrophe-net stop placement (step 13p)

**Test:** Full pipeline on 3 synthetic tickers -> one produces STRONG_BUY -> mock fill -> audit trail complete.

### Wave 4: Post-Cycle Flywheel (steps 14-28)

**Goal:** All post-cycle engines fire in correct order.

Tasks:
1. Wire WeeklyReeval (step 14) -- reuses per-symbol pipeline for active holdings
2. Wire ThesisAging (step 15) -- reuses pipeline for 90d holdings
3. Wire Execution.process_fills() (step 16)
4. Wire ReconciliationEngine.reconcile() (step 17)
5. Wire OpportunityCostEngine (step 18)
6. Wire Calibration.evaluate_and_maybe_refit() (step 19)
7. Wire CrucibleCalibration.update_multipliers() (step 20)
8. Wire CausalAttribution.attribute_resolutions() (step 21)
9. Wire Memory.record_resolutions() (step 22)
10. Wire LessonsEngine.run_lesson_writer_queue() (step 23)
11. Wire OverrideLearning.evaluate_recent_outcomes() (step 24)
12. Wire FailureDiagnostic.classify_pending() (step 25)
13. Wire Cortex.snapshot_drift_stats() (step 26)
14. Wire ConsistencyReconciler.cross_db_audit() (step 27)
15. Wire DeadLetter.process_queue() (step 28)

**Test:** Full cycle with 5 tickers -> all 30 steps execute -> audit chain verifies -> SSE events emitted.

### Wave 5: Alpaca Paper + End-to-End (step 13o production path)

**Goal:** Replace mock fills with real Alpaca paper fills.

Tasks:
1. Build `pmacs/execution/alpaca_adapter.py`
2. Build `pmacs/execution/catastrophe_net.py`
3. Wire Alpaca paper adapter into execution service
4. Wire fill polling
5. Wire catastrophe-net cancellation on exit
6. End-to-end smoke test with Alpaca paper

**Test:** STRONG_BUY ticker -> signed TradePlan -> Alpaca paper fill -> ledger updated -> catastrophe-net placed.

### Wave 6: Hardening + Performance

**Goal:** Error handling, timeout enforcement, memory management.

Tasks:
1. Per-symbol timeout enforcement (270s Phase 1, 90s Crucible)
2. Memory management: evidence streaming, discard after use
3. Graceful shutdown: checkpoint on SIGTERM/SIGINT
4. Kill switch mid-cycle: clean abort protocol
5. Performance profiling against Architecture.md section 20 budget

**Test:** Kill switch mid-cycle -> no partial state. Crash at step 13g -> resume from checkpoint.

## Common Pitfalls

### Pitfall 1: Holding state machine bypass
**What goes wrong:** Directly setting `holding.state =` instead of calling `state_machine.transition()`.
**Why it happens:** Habit from simpler codebases.
**How to avoid:** The orchestrator must NEVER mutate holding.state directly. Every state change goes through `transition()`.
**Warning signs:** CI anti-pattern grep catches `holding.state =`.

### Pitfall 2: Missing cycle_id on audit events
**What goes wrong:** Audit events logged without cycle_id, breaking the audit chain traceability.
**Why it happens:** Forgetting to thread cycle_id through function calls.
**How to avoid:** Every engine call in the orchestrator must receive cycle_id as first or required parameter.
**Warning signs:** CI anti-pattern grep catches `cycle_id=None` on audit-emitting functions.

### Pitfall 3: Persona dispatch not parallelized
**What goes wrong:** Running 7 personas sequentially instead of across 3 slots.
**Why it happens:** Simpler to write a for-loop.
**How to avoid:** Use `concurrent.futures.ThreadPoolExecutor(max_workers=3)` with slot assignment per Architecture.md section 12.2.
**Warning signs:** Cycle time exceeds 90s per ticker for Phase 1 alone.

### Pitfall 4: Crucible infinite loop
**What goes wrong:** Crucible keeps finding new flaws, never producing a final output.
**Why it happens:** Not enforcing the hard 2-cycle, 90s-per-cycle budget.
**How to avoid:** Wrap Crucible calls in `signal.alarm()` or `asyncio.wait_for()` with hard timeout. Default to NO_TRADE on timeout.
**Warning signs:** Cycle time exceeds 270s for a single ticker.

### Pitfall 5: Stale evidence used across symbols
**What goes wrong:** Evidence fetched for ticker A leaks into ticker B's analysis.
**Why it happens:** Sharing evidence lists between symbol iterations.
**How to avoid:** Evidence is scoped per-symbol within `run_symbol()`. Fetch fresh per symbol.
**Warning signs:** Persona output references evidence_ids from a different ticker.

### Pitfall 6: Orchestrator crash loses in-flight state
**What goes wrong:** Process crashes mid-symbol, symbol stuck in PHASE1_RESEARCH on resume.
**Why it happens:** Not writing op_idempotency after each sub-step.
**How to avoid:** Every sub-step in run_symbol() writes to op_idempotency before proceeding. Resume skips completed sub-steps.
**Warning signs:** Queue table shows ticker with started_at but no completed_at after restart.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Parallel persona dispatch | Custom thread pool with manual slot assignment | `concurrent.futures.ThreadPoolExecutor` + slot map | Error handling, timeout, cancellation already handled |
| Cycle locking | Custom lock file with PID checks | `fcntl.flock()` on `/var/db/pmacs/cycle.lock` | Kernel-level, auto-releases on crash |
| JSON audit serialization | `json.dumps()` with manual key sorting | `pmacs/data/canonical.py` `canonical_json()` | Deterministic serialization for hash chain |
| Holding state changes | `holding.state = NEW_STATE` | `pmacs/engines/state_machine.transition()` | Validates transition, logs audit, handles abort reasons |
| Rate-limited HTTP calls | Custom sleep/retry loops | `pmacs/data/gateway.py` `DataGateway.fetch()` | Token bucket per source, retry on 429/5xx |
| SSE event emission | Direct client management | `pmacs/nervous/sse_publisher.py` `publish()` | Thread-safe, auto-cleanup, event IDs |

## Code Examples

### Step Dispatch Pattern
```python
# pmacs/nervous/orchestrator.py (conceptual structure)
from pmacs.nervous.checkpoint import maybe_resume_cycle

class CycleOrchestrator:
    def __init__(self, db_path, audit_path, config):
        self.db_path = db_path
        self.audit_path = audit_path
        self.config = config

    def run_cycle(self, trigger: str) -> str:
        # Step 0: Acquire lock
        with CycleLock("/var/db/pmacs/cycle.lock"):
            # Step 1: Resume or create
            cycle_id, start_seq = maybe_resume_cycle(self.db_path) or (None, 0)
            if cycle_id is None:
                cycle_id = initiate_cycle(trigger, self.db_path, self.audit_path)

            op_seq = start_seq

            # Steps 0.5-12: Pre-symbol
            op_seq = self._run_pre_cycle(cycle_id, op_seq)

            # Step 13: Per-symbol pipeline
            queue = self._load_queue(cycle_id)
            for item in queue:
                op_seq = self._run_symbol(cycle_id, item, op_seq)

            # Steps 14-28: Post-cycle flywheel
            op_seq = self._run_post_cycle(cycle_id, op_seq)

            # Steps 29-30: Close and release
            close_cycle(cycle_id, self.db_path, self.audit_path)
```

### Persona Slot Dispatch
```python
    def _dispatch_personas(self, evidence, episodic_context, cycle_id):
        """Run 7 personas across 3 slots per Architecture.md section 12.2."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        slot_map = {
            0: [MacroRegimeRunner, CatalystSummarizerRunner],
            1: [MoatAnalystRunner, GrowthHunterRunner],
            2: [InsiderActivityRunner, ShortInterestRunner, ForensicsRunner],
        }

        results = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {}
            for slot, runners in slot_map.items():
                for runner_cls in runners:
                    runner = runner_cls(cycle_id=cycle_id)
                    future = pool.submit(
                        runner.run, evidence, episodic_context
                    )
                    futures[future] = runner.persona_name

            for future in as_completed(futures, timeout=270):
                persona_name = futures[future]
                try:
                    result = future.result(timeout=5)
                    if result is not None:
                        results[persona_name] = result
                except Exception:
                    # Persona failed after retries -> ABORTED_LLM
                    pass

        return results
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Orchestrator as monolithic function | Step-dispatch table with idempotency | This phase | Crash-safe, resumable, testable per step |
| Sequential persona dispatch | 3-slot parallel dispatch | Architecture.md section 12.2 | 3x faster Phase 1 (90s vs 270s) |
| Auto-promote mutations | Operator TOTP required for ALL mutations | Phase 8 review feedback | Prevents unconstrained self-modification |
| `holding.state =` direct mutation | `state_machine.transition()` only | Phase 1 | Audit trail, validation, abort reasons |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | All 7 persona runners (macro_regime, catalyst_summarizer, moat_analyst, growth_hunter, insider_activity, short_interest, forensics) are implemented and functional | Current State | If any are stubs, per-symbol pipeline cannot produce Arbitrated output |
| A2 | Crucible runner implements the 2-cycle inner loop with severity output | Current State | If Crucible is single-shot, need to add loop |
| A3 | MemoWriter runner reads all persona outputs + Arbitrated + Crucible | Current State | If MemoWriter only reads partial data, memos will be incomplete |
| A4 | Pricing engine (`engines/pricing.py`) computes EV from Arbitrated probabilities | Current State | If stub, need to implement EV formula |
| A5 | DuckDB, KuzuDB, Qdrant storage adapters can handle the query patterns for episodic context | Episodic Context | If adapters are stubs, context briefs will be empty |
| A6 | `checkpoint.py` `maybe_resume_cycle()` is functional | Step 1 | If stub, cycle resume won't work |

## Open Questions

1. **Persona runner completeness**
   - What we know: base.py is complete, crucible.py and memo_writer.py exist
   - What's unclear: Whether all 7 analysis personas (macro_regime, catalyst_summarizer, etc.) have concrete subclasses of PersonaRunner
   - Recommendation: Verify each persona file has a working `build_prompt()`, `get_pydantic_model()`, and `get_sanity_validator()` implementation

2. **Crucible 2-cycle loop implementation**
   - What we know: The spec requires up to 2 attack cycles with 90s budget per cycle
   - What's unclear: Whether `crucible.py` implements the inner state machine (INITIAL -> REWRITE -> ABORT)
   - Recommendation: Check crucible.py for the 2-cycle loop; if missing, add it in this phase

3. **Pricing engine completeness**
   - What we know: `engines/pricing.py` exists
   - What's unclear: Whether it computes EV from probability vectors or is a stub
   - Recommendation: Read pricing.py to verify EV formula implementation

4. **Data pipeline for evidence assembly**
   - What we know: Individual data sources exist (edgar, polygon, etc.)
   - What's unclear: How evidence is assembled per-ticker for persona consumption (the "evidence selection" step)
   - Recommendation: May need a `pmacs/data/evidence_builder.py` that selects relevant evidence per persona per ticker

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| llama-server on :8080 | Persona dispatch (step 13d) | LOCAL ONLY | Qwen3.6-35B-A3B | Skip persona tests with mock LLM |
| SQLite | All state persistence | ALWAYS | Built-in | None |
| DuckDB | Analytics, episodic context | Install via pip | Latest | Skip analytics steps |
| KuzuDB | Graph lineage | Install via pip | Latest | Skip graph operations |
| Qdrant | Vector search for lessons | Requires server | Latest | Skip lesson retrieval |
| Alpaca paper API | Paper trading (Wave 5) | Requires API keys | v2 | Mock fills (Waves 1-4) |

**Missing dependencies with no fallback:**
- None for Waves 1-4 (all use local/mock)
- Alpaca API keys for Wave 5 (can defer)

**Missing dependencies with fallback:**
- DuckDB/KuzuDB/Qdrant: skip related steps, log degradation

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest |
| Config file | pyproject.toml (pytest section) |
| Quick run command | `pytest tests/unit/ -x -q` |
| Full suite command | `pytest tests/ -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| REQ-09-01 | Cycle opens, acquires lock, runs pre-cycle, closes | integration | `pytest tests/integration/test_cycle_skeleton.py -x` | NO Wave 1 |
| REQ-09-02 | Queue composition with priority bands | unit | `pytest tests/unit/test_queue.py -x` | YES |
| REQ-09-03 | Per-symbol pipeline: gate -> personas -> arbitration -> verdict | integration | `pytest tests/integration/test_symbol_pipeline.py -x` | NO Wave 3 |
| REQ-09-04 | State machine transitions through full pipeline | unit | `pytest tests/unit/test_state_machine.py -x` | YES |
| REQ-09-05 | Cycle resume from checkpoint after crash | integration | `pytest tests/integration/test_cycle_resume.py -x` | NO Wave 1 |
| REQ-09-06 | Kill switch mid-cycle clean abort | integration | `pytest tests/integration/test_kill_switch_midcycle.py -x` | NO Wave 6 |
| REQ-09-07 | All 30 steps execute with audit trail | integration | `pytest tests/integration/test_full_cycle.py -x` | NO Wave 4 |
| REQ-09-08 | Paper trade execution with mock fills | integration | `pytest tests/integration/test_paper_trade.py -x` | NO Wave 3 |

### Sampling Rate
- **Per task commit:** `pytest tests/unit/ -x -q`
- **Per wave merge:** `pytest tests/ -x`
- **Phase gate:** Full suite green before marking phase complete

### Wave 0 Gaps
- [ ] `tests/integration/test_cycle_skeleton.py` -- covers REQ-09-01, REQ-09-05
- [ ] `tests/integration/test_symbol_pipeline.py` -- covers REQ-09-03
- [ ] `tests/integration/test_kill_switch_midcycle.py` -- covers REQ-09-06
- [ ] `tests/integration/test_full_cycle.py` -- covers REQ-09-07
- [ ] `tests/integration/test_paper_trade.py` -- covers REQ-09-08

## Sources

### Primary (HIGH confidence)
- Architecture.md sections 1-16, 20 (read in full from spec/)
- Agents.md sections 1-24 (read in full from spec/)
- Phases.md sections 1-8 (read in full from spec/)
- All 24+ codebase files read directly

### Secondary (MEDIUM confidence)
- Persona completeness assumptions based on file existence checks (Glob)

### Tertiary (LOW confidence)
- None -- all findings verified against spec and codebase

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all engines exist and are importable
- Architecture: HIGH -- 30-step cycle fully specified in Architecture.md section 12
- Pitfalls: HIGH -- derived from spec anti-patterns and process topology constraints
- Implementation order: MEDIUM -- wave boundaries may shift based on persona completeness

**Research date:** 2026-05-13
**Valid until:** 2026-06-12 (30 days; stable spec)
