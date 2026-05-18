# Phase 12: Spec Gap Closure — From Structural to Functional

**Goal:** Close all remaining spec gaps identified in MISSING.md. Transform the system from structurally complete (all files exist, all schemas compile) to functionally complete (evidence flows, prices are real, stores persist data, flywheel closes).

**Spec Authority:** `spec/Phases.md` exit tests are the acceptance criteria. All 15 PMACS phases have exit tests that must pass.

**Dependencies:** Phases 1-11 (GSD) complete. System is structurally LIVE-READY.

---

## Current State

- 85/158 spec components DONE
- 18 PARTIAL (code exists, incomplete)
- 17 STUB (file exists, returns defaults)
- 38 MISSING (not implemented)

## Exit Test

The system runs a full cycle on a 16-ticker universe that:
1. Fetches real evidence from >= 10/13 data sources
2. Passes real prices (not 1.0) through EV/sizing/stop-loss
3. Writes resolution data to KuzuDB, Qdrant, and DuckDB (not stubs)
4. Resolves catalysts via multi-source corroboration
5. All 15 spec-defined exit test categories pass (or have explicit skip rationale with TODO)
6. Audit chain verifies after the full cycle

---

## Wave 1: Data Pipeline Activation (Blockers 1-4)

**Why first:** Without real evidence and prices, every downstream component operates on garbage. This is the highest-leverage wave.

### 1.1 Evidence Fetching Pipeline
- **MISSING ref:** Cross-Cutting A
- **Spec:** Arch §6.2, Phases §2 exit test 3
- **What:**
  - `orchestrator.py`: Wire step 13 to call `data_gateway.fetch_evidence(ticker)` before persona dispatch
  - Evidence router: map ticker -> relevant data sources -> `EvidencePacket[]`
  - Per-persona evidence selection: MoatAnalyst gets fundamentals, ShortInterest gets FINRA, etc.
  - Staleness filtering before persona input using existing `staleness.py`
  - Evidence dedup and canonical ordering
- **Files to modify:** `pmacs/nervous/orchestrator.py`, possibly new `pmacs/data/evidence_router.py`
- **Test:** Personas receive non-empty evidence lists with real source data

### 1.2 Real-Time Price Feed
- **MISSING ref:** Cross-Cutting B
- **Spec:** Arch §6.1 (Polygon, Alpaca data), Arch §9.3 (current_price in sizing)
- **What:**
  - Replace `current_price=1.0` in orchestrator with real price fetch
  - Price cache with staleness budget (use existing TokenBucket + Alpaca data source)
  - Wire into: stop_loss_daemon, trailing_stop, pricing engine, sizing engine
  - Integration with existing `pmacs/data/sources/alpaca_data.py` and `polygon.py`
- **Files to modify:** `pmacs/nervous/orchestrator.py`, `pmacs/engines/pricing.py`
- **Test:** `current_price` reflects actual market data; EV computation uses real numbers

### 1.3 Catalyst Resolution Subsystem
- **MISSING ref:** Items 2.11-2.14, Cross-Cutting G
- **Spec:** Arch §7 (full section), Arch §3 repo tree (`data/resolution/`)
- **What:**
  - `pmacs/data/resolution/catalyst_detector.py` — scan evidence for catalyst resolution signals
  - `pmacs/data/resolution/earnings_resolver.py` — detect earnings release outcomes
  - `pmacs/data/resolution/fda_resolver.py` — detect FDA decision outcomes
  - `pmacs/data/resolution/corroboration.py` — Tier A/B/C multi-source corroboration with 3σ outlier guard
  - Wire into orchestrator post-cycle steps for pending catalyst resolution checks
- **Files to create:** 4 new files in `pmacs/data/resolution/`
- **Test:** A synthetic catalyst resolves correctly via multi-source corroboration

### 1.4 EV / Pricing Engine Real Logic
- **MISSING ref:** Item 7.4
- **Spec:** Arch §9.4 (PricingEngine)
- **What:**
  - Replace hardcoded `target_gain=0.10, stop=0.15` with real EV computation
  - Use arbitrated probabilities + current price + analyst target data
  - Compute expected value: `EV = p_up * target_gain - p_down * stop_loss`
  - Return `ev_multiple = EV / min_ev_threshold`
- **Files to modify:** `pmacs/engines/pricing.py`
- **Test:** EV computation produces sensible values for known inputs

---

## Wave 2: Storage Activation (Blocker 3 + Dependencies)

**Why second:** Storage activation requires the embedding model and depends on Wave 1 data flowing in.

### 2.1 Embedding Model Setup
- **MISSING ref:** Item 8.12
- **Spec:** Arch §8.7, Source §12.4.5
- **What:**
  - Download `BAAI/bge-base-en-v1.5` (~420MB) via sentence-transformers
  - Verify model loads and produces 768-dim vector on test input
  - Integrate into wizard step 4.5 (or add if missing)
  - Wire into Qdrant adapter for thesis/evidence/lesson embeddings
- **Files to modify:** `pmacs/storage/qdrant.py`, possibly `pmacs/installer/wizard.py`
- **Test:** Embedding produces 768-dim vector; Qdrant write succeeds

### 2.2 KuzuDB Stub-to-Real Migration
- **MISSING ref:** Items 11.8, 11.11
- **Spec:** Arch §8.3, Arch §8.4
- **What:**
  - Install kuzu Python package (add to pyproject.toml if missing)
  - Replace stub returns in `pmacs/storage/kuzu.py` with real Cypher operations
  - Create graph schema (Holding, Evidence, Resolution, Lesson, FailedAssumption nodes + edges)
  - Wire lineage writes from: state_machine (Holding), orchestrator (Evidence), lessons engine, FDE
- **Files to modify:** `pmacs/storage/kuzu.py`
- **Test:** `MATCH (h:Holding)-[:FAILED_ASSUMPTION]->(fa) RETURN fa` returns real data after FDE runs

### 2.3 Qdrant Stub-to-Real Migration
- **MISSING ref:** Items 11.7, 11.10
- **Spec:** Arch §8.7
- **What:**
  - Install qdrant-client (add to pyproject.toml if missing)
  - Replace stub returns in `pmacs/storage/qdrant.py` with real Qdrant operations
  - Create 5 collections: theses, memos_persona, memos_aggregated, evidence_chunks, lessons
  - Wire embedding + upsert from: orchestrator (theses), memo writer, lessons engine, evidence router
- **Files to modify:** `pmacs/storage/qdrant.py`
- **Test:** Thesis embedding stored → similarity search returns it

### 2.4 DuckDB Stub-to-Real Migration
- **MISSING ref:** Items 11.9, 11.12
- **Spec:** Arch §8.5, Arch §8.6
- **What:**
  - Install duckdb (add to pyproject.toml if missing)
  - Replace stub returns in `pmacs/storage/duckdb.py` with real DuckDB operations
  - Create tables: resolutions_history, rolling_metrics, persona_performance, persona_ticker_affinity, persona_subsector_affinity, evidence_archive, scan_records, failure_taxonomy_counts
  - Wire writes from: reconciliation (resolutions), calibration (metrics), orchestrator (scan_records)
- **Files to modify:** `pmacs/storage/duckdb.py`
- **Test:** `SELECT * FROM rolling_metrics` returns real data after a cycle

---

## Wave 3: Engine Completion (High Priority)

### 3.1 Crucible 2-Iteration Rewrite Loop
- **MISSING ref:** Item 7.2
- **Spec:** Agents §16 (full state machine)
- **What:**
  - Implement the INITIAL → REWRITE → DONE/ABORT state machine from Agents §16.1
  - Cycle 1: send thesis + Arbitrated + evidence → Crucible → receive output
  - If severity 0.3-0.6: rebuild "revised evidence brief" addressing cycle-1 attacks → Cycle 2
  - If severity >= 0.6: immediate ABORT (NO_TRADE)
  - Hard limits: 2 cycles max, 90s per cycle, NO_TRADE on budget exhaust
- **Files to modify:** `pmacs/agents/crucible.py`, `pmacs/nervous/orchestrator.py`
- **Test:** `test_crucible_budget.py` — times out at 90s → NO_TRADE; exceeds 2 cycles → NO_TRADE

### 3.2 Weekly Thesis Re-Evaluation Wiring
- **MISSING ref:** Items 9.4, 9.5, 9.8
- **Spec:** Arch §12 step 14-15, Source §7.3
- **What:**
  - Wire orchestrator step 14 to detect held positions past 7-day re-eval threshold
  - Full pipeline re-run on re-eval candidates (gatekeeper → personas → arbitration → crucible)
  - Thesis validated → stays ACTIVE with refreshed thesis_review_due_date
  - Thesis broken → transition to EXIT_THESIS_INVALIDATED
  - 90-day THESIS_AGING_REVIEW trigger: timer-based check in orchestrator startup
- **Files to modify:** `pmacs/nervous/orchestrator.py`
- **Test:** Held position past 7 days gets full re-eval; broken thesis exits

### 3.3 Cash Ledger Engine
- **MISSING ref:** Item 8.6
- **Spec:** Arch §9, Arch §8.5
- **What:**
  - Create `pmacs/engines/cash_ledger.py` (or similar)
  - Track cash balance changes: starting capital, trade fills, dividend credits, fee debits
  - Write to paper_account table on each balance change
  - Validate: paper_account.total_value_usd = cash + sum(position values)
- **Files to create:** new engine file
- **Test:** After 3 paper trades, ledger balance reflects all fills correctly

### 3.4 FDE STOP_HUNTED Detection
- **MISSING ref:** Item 12.8
- **Spec:** Agents §15 (taxonomy types 6-7)
- **What:**
  - Implement 48h post-exit price check: if price recovers above entry + 2% within 48h → STOP_HUNTED
  - Implement 30d post-exit price check: if price stays below stop → STOP_LOSS_CORRECT
  - Requires real price data (depends on Wave 1.2)
- **Files to modify:** `pmacs/engines/failure_diagnostic.py`
- **Test:** Synthetic stop-out with price recovery → STOP_HUNTED classification

### 3.5 MARKET_ON_OPEN for Gap-Down
- **MISSING ref:** Item 9.7
- **Spec:** Arch §11.2
- **What:**
  - When stop-loss detects gap-down (open price below stop), use MARKET_ON_OPEN order type
  - Modify execution service to support this order type
- **Files to modify:** `pmacs/engines/stop_loss_monitor.py`, `pmacs/execution/alpaca_paper.py`
- **Test:** Gap-down scenario selects MARKET_ON_OPEN order type

---

## Wave 4: Flywheel Closure (Medium Priority)

### 4.1 Lessons Engine Real Data Flow
- **MISSING ref:** Item 11.4
- **Spec:** Arch §9.4 (LessonsEngine)
- **What:**
  - Wire lessons engine to read real resolution history from DuckDB (not stub empty list)
  - Extract structured lessons from resolutions
  - Write lessons to Qdrant (depends on Wave 2.3)
  - Wire into episodic context retrieval
- **Files to modify:** `pmacs/engines/lessons.py`
- **Test:** After 5 synthetic resolutions, lesson extraction produces non-empty results

### 4.2 Episodic Context Real Data
- **MISSING ref:** Items 13.1, 13.2, 13.3, 13.5
- **Spec:** Agents §18
- **What:**
  - Wire `build_context_brief()` to read real data from DuckDB (persona_ticker_affinity) and Qdrant (lessons)
  - Log `episodic_context_injected` audit event with content_hash
  - Handle no-history case: return macro context only
- **Files to modify:** `pmacs/agents/episodic_context.py`
- **Test:** Persona with 5+ past cycles receives non-empty brief with real affinity data

### 4.3 Mutation SSE Events
- **MISSING ref:** Item 14.10
- **Spec:** Arch §4.4 (mutation.* event stream)
- **What:**
  - Emit SSE events: mutation.proposed, mutation.ab_started, mutation.ab_progress, mutation.ab_complete, mutation.ready_for_review, mutation.promoted, mutation.rejected, mutation.rolled_back
  - Wire into mutation daemon lifecycle
- **Files to modify:** `pmacs/mutation/daemon.py`, `pmacs/nervous/sse_publisher.py`
- **Test:** Mutation proposal triggers SSE event visible on dashboard

### 4.4 Mode Promotion Gate with Real Data
- **MISSING ref:** Cross-Cutting D
- **Spec:** Phases §3, Arch §9.4 (FlywheelHealth)
- **What:**
  - Wire flywheel_health.check_promotion_gates() to read real rolling_metrics from DuckDB
  - Compute real cycle counts from mode_history table
  - Dashboard mode badge shows gate pass/fail status
- **Files to modify:** `pmacs/engines/flywheel_health.py`, dashboard template
- **Test:** After 5 synthetic PAPER cycles, gate check returns real Brier/Sharpe values

---

## Wave 5: Ops Infrastructure + Test Coverage (Low Priority)

### 5.1 Ops Scripts
- **MISSING ref:** Cross-Cutting E, Items 3.4, 3.8, 4.17, 8.11
- **What:**
  - `ops/start_inference.sh` — llama-server startup with model verification
  - `ops/install_launchd.sh` — launchd plist installer
  - `ops/install_pf_rules.sh` — pf firewall rules blocking inference egress
  - `ops/install_system_users.sh` — _pmacs_* system user creation
  - `ops/audit_chain_verify.py` — standalone chain verification tool
  - `ops/backup_verify.py` — backup + restore
  - `ops/spec_consistency.py` — cross-file reference checker for CI
- **Files to create:** 7 new files in `ops/`

### 5.2 Integration Tests from Spec Exit Criteria
- **MISSING ref:** Cross-Cutting F
- **What:**
  - `tests/fixtures/` — synthetic data for smoke-test cycle
  - `tests/integration/test_data_sources.py` — 10/13 sources return valid EvidencePacket
  - `tests/integration/test_llm_call.py` — inference backend integration
  - `tests/integration/test_3persona_cycle.py` — 3-persona cycle
  - `tests/integration/test_7persona_cycle.py` — 7-persona cycle
  - `tests/integration/test_full_pipeline.py` — complete pipeline
  - `tests/unit/test_crucible_budget.py` — Crucible timing limits
  - `tests/integration/test_calibration.py` — calibration refit
  - `tests/unit/test_fde.py` — all 18 taxonomy types
  - `tests/integration/test_cross_db.py` — cross-DB consistency
  - `tests/integration/test_episodic.py` — episodic context injection
  - `tests/integration/test_mutation_lifecycle.py` — mutation lifecycle
  - `tests/integration/test_rollback.py` — 5-level rollback
  - `tests/e2e/test_smoke_cycle.py` — full smoke cycle
- **Files to create:** 14 new test files

### 5.3 Phase 15 Polish Items
- **MISSING ref:** Items 15.1-15.13
- **What:**
  - Agents page animations (persona progress bars)
  - Pipeline drag-drop refinement
  - Dashboard sparklines + time-window selector
  - Cmd-K command palette
  - Keyboard shortcuts (Cmd-1 through Cmd-7, Cmd-R, Cmd-K, etc.)
  - Toast notification system
  - Accessibility audit (axe-core)
  - Performance profiling (cycle timing)
  - Operator runbook documentation
  - "Copy for Claude Code" button on debug events
  - Notification policy full implementation
- **Files to modify:** Various web templates, static JS/CSS

---

## Execution Order (Dependency Graph)

```
Wave 1.1 Evidence Pipeline ─────┐
Wave 1.2 Price Feed ────────────┤
Wave 1.3 Catalyst Resolution ───┤──→ Wave 3.1 Crucible Loop
Wave 1.4 Pricing Engine ────────┘    Wave 3.2 Re-Eval Wiring
                                      Wave 3.3 Cash Ledger
                                      Wave 3.4 FDE STOP_HUNTED
                                      Wave 3.5 MARKET_ON_OPEN
                                              │
Wave 2.1 Embedding Model ──→ Wave 2.2 KuzuDB ─┤
                             Wave 2.3 Qdrant ──┤──→ Wave 4.1 Lessons Engine
                             Wave 2.4 DuckDB ──┘    Wave 4.2 Episodic Context
                                                     Wave 4.3 Mutation SSE
                                                     Wave 4.4 Mode Gates
                                                             │
Wave 5.1 Ops Scripts ───────────────────────────────────────┤
Wave 5.2 Integration Tests ──────────────────────────────────┤
Wave 5.3 Polish Items ───────────────────────────────────────┘
```

Waves 1-2 can run in parallel (no dependency between data pipeline and storage install).
Waves 3-4 are sequential (engines need data + storage).
Wave 5 is catch-all and can overlap with 3-4.

## Risk Checkpoints

- **After Wave 1:** Evidence flows to personas with real data. Prices are not 1.0. Verify: run one cycle and inspect persona inputs.
- **After Wave 2:** All 3 stores write real data. Verify: run one cycle and query KuzuDB/Qdrant/DuckDB for non-empty results.
- **After Wave 3:** All engines produce real outputs. Verify: Crucible runs 2 cycles, re-eval works, FDE classifies STOP_HUNTED.
- **After Wave 4:** Flywheel closes. Verify: lessons extracted, episodic context injected, mutation events visible.
- **After Wave 5:** Exit test passes. Verify: full 16-ticker cycle completes, audit chain verifies, all test suites pass.
