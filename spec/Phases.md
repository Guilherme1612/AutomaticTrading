# PMACS — Phases

**File 4 of 4. Build sequence, per-phase exit tests, mode promotion/demotion gates, file-by-file dependency graph.**

> Companion files: `Source.md` (vision and operator surface), `Architecture.md` (build, processes, IPC, storage), `Agents.md` (LLM personas, prompts, structured-output contracts, FDE taxonomy, Crucible loop, Mutation Engine reasoning).
>
> **Reading order for Claude Code:** Read `Source.md` first (what and why). Read `Architecture.md` second (how it's built). Read `Agents.md` when touching LLM code. Read this file to decide *what to build next* and *what counts as done.*
>
> **If anything contradicts:** This file wins for *build sequence and what-ships-when.* `Architecture.md` wins for *implementation specifics.* `Agents.md` wins for *LLM contracts.* `Source.md` wins for *vision and operator-facing behavior.*
>
> **Section anchors are stable.** Other files cite this file as `Phases.md §<n>`.

---

## Table of contents

```
0.   Cross-reference index
1.   How to read this file
2.   Build phases (Phase 1 through Phase 15)
3.   Mode promotion and demotion gates (numerical)
4.   File-by-file dependency graph
5.   Phase-to-mode mapping
6.   Risk checkpoints
7.   What "done" means
8.   Connection to companion files
```

---

## 0. Cross-reference index

| Concept | Lives in | Section |
|---|---|---|
| Vision, non-negotiables, trust contract | `Source.md` | §2-§5 |
| Mode ladder (semantics and operator-facing) | `Source.md` | §9 |
| Auto-demotion operator experience | `Source.md` | §9.4 |
| UI pages (what Phase N must render) | `Source.md` | §14-§20 |
| Run wizard (what Phase 1 must produce) | `Source.md` | §12 |
| The day-in-the-life (what Phase 10+ must support) | `Source.md` | §22 |
| 7-layer architecture | `Architecture.md` | §2 |
| Process topology (what processes must exist per phase) | `Architecture.md` | §4 |
| Repo tree (file paths) | `Architecture.md` | §3 |
| Storage schemas (what tables must exist per phase) | `Architecture.md` | §8 |
| Cycle orchestration (the canonical sequence every phase must converge toward) | `Architecture.md` | §12 |
| Kill switch (must be functional by Phase 4) | `Architecture.md` | §13 |
| Deterministic engines (per-engine build phase) | `Architecture.md` | §9 |
| Mutation Engine process | `Architecture.md` | §10 |
| StopLossMonitor process | `Architecture.md` | §11 |
| Anti-patterns (enforced from Phase 1 onward) | `Architecture.md` | §16 |
| Testing strategy (what CI checks per phase) | `Architecture.md` | §19 |
| Per-persona specs (which phase introduces each) | `Agents.md` | §4-§13 |
| 18 FDE taxonomy types | `Agents.md` | §15 |
| Crucible inner loop | `Agents.md` | §16 |
| Mutation Engine candidate rules | `Agents.md` | §17 |
| Episodic context injection | `Agents.md` | §18 |

---

## 1. How to read this file

### 1.1 Two kinds of phases

This file defines two distinct phase systems:

1. **Build phases** (§2): what gets built, in what order, with what exit test. These are development milestones. There are 15 of them. They are sequential: Phase N depends on Phase N-1 being complete.

2. **Mode phases** (§3): SHADOW + PAPER → PAPER_VALIDATED → LIVE_EARLY → LIVE_STANDARD → LIVE_EXPANDED. These are production-time modes with numerical promotion and demotion gates. They are defined in `Source.md §9` and given concrete numbers here.

The two systems interact: **build phases produce the capabilities that enable mode phases.** The system cannot enter SHADOW + PAPER mode until build Phase 8 is complete. The system cannot enter PAPER_VALIDATED until build Phase 12 is complete AND the numerical promotion gates pass. The mapping is in §5.

### 1.2 What an exit test is

Every build phase has an **exit test** — a concrete, verifiable condition that must be true before the next phase begins. Exit tests are not aspirational. They are binary: pass or don't advance.

Exit tests take four forms:
- **Unit test pass:** a named test file passes in CI
- **Integration test pass:** a named integration test passes
- **Behavior demonstration:** the system exhibits a specific end-to-end behavior (documented as a scenario)
- **Operator confirmation:** the operator verifies a specific UX behavior (only for UI phases)

### 1.3 The "do not advance" rule

If a phase's exit test fails, **do not proceed to the next phase.** Fix the failure first. Building Phase N+1 on a broken Phase N produces compounding technical debt that becomes more expensive to fix at every subsequent layer.

Claude Code: before starting work on any phase, verify the previous phase's exit test passes. If it doesn't, fix Phase N-1 before touching Phase N.

### 1.4 Anti-patterns enforced from Phase 1

`Architecture.md §16` anti-patterns are enforced in CI from Phase 1 onward. They are not deferred. Every PR, regardless of which phase it belongs to, must pass:
- No direct `holding.state =` outside `state_machine.py`
- No `json.dumps` on audit payloads (must use `canonical_json`)
- No secrets in log output
- No `pydantic.v1` imports
- `cycle_id` required on every audit-emitting function
- Every `log_debug(level >= "WARN")` has a canonical `error_code`

---

## 2. Build phases

### Phase 1: Foundation — schemas, config, storage, audit

**Goal:** The skeleton exists. Every Pydantic model compiles. Every database initializes. The audit chain works end-to-end. Nothing runs yet, but the foundation is sound.

**What gets built:**
- `pmacs/schemas/*.py` — ALL Pydantic models (complete, even for engines not yet implemented)
- `pmacs/data/canonical.py` — canonical JSON serialization
- `pmacs/storage/sqlite.py` — SQLite initialization with all tables from `Architecture.md §8.5`
- `pmacs/storage/audit.py` — hash-chained audit writer + verifier
- `pmacs/storage/keychain.py` — macOS Keychain wrapper
- `pmacs/config.py` — config loader for `config/*.toml` + `config/model_registry.json`
- `config/` — all config files with production defaults
- `pmacs/constants.py` — anti-pattern thresholds
- `pmacs/logsys/` — debug log writer, error classifier
- `pmacs/engines/state_machine.py` — Holding state transitions with full transition table
- `tests/unit/test_schemas.py` — validates all schemas compile and cross-field validators work
- `tests/unit/test_audit_chain.py` — validates genesis, append, verify, break-detection
- `tests/unit/test_state_machine.py` — validates every valid transition and rejects every invalid one
- `.pre-commit-config.yaml` — anti-pattern grep hooks

**Files NOT built yet:** agents, engines (except state_machine), nervous, cortex, execution, web, mutation, installer.

**Exit test:**
1. `pytest tests/unit/test_schemas.py` — ALL pass
2. `pytest tests/unit/test_audit_chain.py` — chain genesis → 100 appends → verify passes; tamper one line → verify catches it
3. `pytest tests/unit/test_state_machine.py` — every valid transition succeeds; every invalid transition raises `InvalidStateTransition`
4. `python -c "from pmacs.config import load_config; load_config()"` — succeeds on a fresh repo
5. All anti-pattern grep checks pass

**Duration estimate:** 3-5 days.

---

### Phase 2: Data layer — sources, staleness, rate limiting, FX

**Goal:** PMACS can fetch real market data from every source, enforce staleness budgets, and produce well-typed EvidencePackets.

**What gets built:**
- `pmacs/data/gateway.py` — rate-limited HTTP wrapper with TokenBucket per source
- `pmacs/data/staleness.py` — FreshnessResult-returning staleness checker (no packet mutation; `Architecture.md §16.4`)
- `pmacs/data/fx.py` — ECB EUR/USD with `usd_per_eur` convention
- `pmacs/data/corp_actions.py` — splits, dividends, mergers
- `pmacs/data/universe.py` — operator-curated universe CRUD
- `pmacs/data/sources/*.py` — one module per source (edgar, polygon, finnhub, alpaca_data, openfda, finra, form4, ir_pages, press, fomc, fred, ecb, fundamentals)
- `pmacs/schemas/data.py` — EvidencePacket
- `pmacs/schemas/freshness.py` — FreshnessResult
- `pmacs/schemas/currency.py` — FxRate, FxSnapshot
- `config/source_criticality.toml`
- `tests/unit/test_staleness.py`
- `tests/unit/test_fx.py`
- `tests/integration/test_data_sources.py` — each source fetches one real data point and returns a valid EvidencePacket

**Dependencies:** Phase 1 (schemas, config, Keychain for API keys).

**Exit test:**
1. `pytest tests/unit/test_staleness.py` — all budgets enforced; CRITICAL raises; IMPORTANT degrades; NICE_TO_HAVE degrades
2. `pytest tests/unit/test_fx.py` — `usd_to_eur(eur_to_usd(100, snap), snap) ≈ 100`
3. `pytest tests/integration/test_data_sources.py` — at least 10 of 13 sources return a valid EvidencePacket (3 can be NICE_TO_HAVE failures)
4. Rate limiting demonstrated: 20 rapid calls to Polygon complete without 429 errors

**Duration estimate:** 5-7 days.

---

### Phase 3: Inference backend — llama-server integration

**Goal:** PMACS can send a prompt to llama-server, receive structured output constrained by GBNF, parse it through Pydantic, and log the call to audit.

**What gets built:**
- `pmacs/agents/base.py` — `PersonaRunner` base class
- `pmacs/agents/grammars/test_grammar.gbnf` — a minimal test grammar
- `pmacs/agents/sanity/base.py` — base sanity validator
- llama-server invocation script in `ops/start_inference.sh`
- `config/model_hashes.toml` — SHA256 of the GGUF
- `pmacs/cortex/model_integrity.py` — GGUF SHA256 verification
- `tests/integration/test_llm_call.py` — send a simple prompt, receive GBNF-constrained JSON, validate through Pydantic

**Dependencies:** Phase 1 (schemas, audit for logging the LLM call).

**Exit test:**
1. llama-server starts with the configured GGUF and responds on :8080
2. `pytest tests/integration/test_llm_call.py` — send prompt with GBNF → receive valid JSON → Pydantic validates → audit event logged with prompt + output + model_hash + grammar_version
3. Model integrity check passes (GGUF SHA256 matches `model_hashes.toml`)
4. Deliberate GBNF violation (send without grammar) produces output that FAILS Pydantic → demonstrating the grammar's value

**Duration estimate:** 5-7 days (includes GBNF grammar development and JSON Schema equivalents for Ollama; grammar-to-Pydantic alignment is iterative).

---

### Phase 4: Core processes — Cortex, Nervous, Execution, kill switch

**Goal:** The process topology exists. All 8 launchd processes can start, heartbeat, and be monitored. The kill switch works end-to-end. The nervous system can orchestrate a stub cycle.

**What gets built:**
- `pmacs/cortex/daemon.py` — main loop
- `pmacs/cortex/health.py` — heartbeat monitoring
- `pmacs/cortex/kill_switch.py` — engage/disengage with operator confirmation
- `pmacs/cortex/boot_detector.py` — gap detection
- `pmacs/cortex/crash_loop_detector.py`
- `pmacs/cortex/self_check.py` — meta-monitor
- `pmacs/cortex/clock_monitor.py`
- `pmacs/cortex/disk_monitor.py`
- `pmacs/nervous/orchestrator.py` — stub cycle (open → close, no symbols)
- `pmacs/nervous/api.py` — FastAPI app with `/events` SSE
- `pmacs/nervous/sse_publisher.py`
- `pmacs/nervous/checkpoint.py` — cycle resume
- `pmacs/nervous/auth.py` — session token verification
- `pmacs/execution/service.py` — stub (accepts TradePlan via UDS, logs, returns mock fill)
- `pmacs/execution/signing.py` — Ed25519 keypair generation and signing
- `launchd/*.plist` — all 8 plists
- `ops/install_launchd.sh`
- `ops/install_pf_rules.sh` — network egress rules
- `tests/integration/test_kill_switch.py`
- `tests/integration/test_heartbeats.py`
- `tests/integration/test_cycle_stub.py`

**Dependencies:** Phase 1 (schemas, audit, SQLite), Phase 2 (data gateway for boot detector's refresh).

**Exit test:**
1. All 8 processes start via launchd, heartbeat within 10s, Cortex monitors all
2. `pytest tests/integration/test_kill_switch.py` — engage → verify no new cycles start → disengage with operator confirmation → cycles resume
3. `pytest tests/integration/test_cycle_stub.py` — Nervous opens a cycle, writes audit open + close, SSE emits cycle.open + cycle.close
4. Ed25519 signing: sign a test TradePlan → verify signature → tamper one byte → verification fails
5. Crash loop: restart a process 5 times in 60s → Cortex marks BROKEN_CRASH_LOOP → kill switch engages
6. `pf` rules verified: llama-server process cannot reach external IP

**Duration estimate:** 7-10 days (most complex phase).

---

### Phase 5: Gatekeeper + first 3 personas (MacroRegime, CatalystSummarizer, MoatAnalyst)

**Goal:** Phase 0 (Gatekeeper) and the first three LLM personas run end-to-end on real data. Arbitration combines their signals. The cycle processes real tickers.

**What gets built:**
- `pmacs/agents/gatekeeper.py` — deterministic admittance filter (`Agents.md §4`)
- `pmacs/agents/macro_regime.py` + `prompts/macro_regime.md` + `grammars/macro_regime.gbnf` + `sanity/macro_regime.py` (`Agents.md §5`)
- `pmacs/agents/catalyst_summarizer.py` + prompts + grammar + sanity (`Agents.md §6`)
- `pmacs/agents/moat_analyst.py` + prompts + grammar + sanity (`Agents.md §7`)
- `pmacs/engines/arbitration.py` — full ArbitrationEngine (`Architecture.md §9.1`)
- `pmacs/engines/queue.py` — queue composition from universe + priority bands
- `pmacs/engines/memory.py` — antipattern checker (stub; no patterns yet)
- Nervous orchestrator updated: steps 0-13 (partial: 3 personas, no Crucible)
- `tests/integration/test_gatekeeper.py`
- `tests/integration/test_3persona_cycle.py` — run a full cycle with 3 personas on 3 test tickers

**Dependencies:** Phase 1 (schemas), Phase 2 (data sources), Phase 3 (inference), Phase 4 (processes).

**Exit test:**
1. Gatekeeper filters: a halted ticker is rejected; a stale-data ticker is rejected; a valid ticker is admitted
2. `pytest tests/integration/test_3persona_cycle.py` — 3 tickers processed; each produces 3 `DirectionalProbability` outputs; Arbitration combines them; audit log shows all events; cycle opens and closes cleanly
3. GBNF violations are caught: feed MacroRegime a prompt that produces invalid JSON without grammar → grammar enforces valid JSON
4. Sanity validators catch: manually inject `p_up=1.0, p_flat=0.0, p_down=0.0` → sanity rejects (degenerate distribution); retry fires; retry produces valid output

**Duration estimate:** 7-10 days.

---

### Phase 6: Remaining 4 personas (GrowthHunter, InsiderActivity, ShortInterest, Forensics)

**Goal:** All 7 analysis personas operational. Full Phase 1 pipeline runs on all admitted tickers.

**What gets built:**
- `pmacs/agents/growth_hunter.py` + prompts + grammar + sanity (`Agents.md §8`)
- `pmacs/agents/insider_activity.py` + prompts + grammar + sanity (`Agents.md §9`)
- `pmacs/agents/short_interest.py` + prompts + grammar + sanity (`Agents.md §10`)
- `pmacs/agents/forensics.py` + prompts + grammar + sanity (`Agents.md §11`)
- Nervous orchestrator updated: step 13 dispatches all 7 personas across 3 inference slots (`Architecture.md §12.2`)
- `tests/integration/test_7persona_cycle.py`

**Dependencies:** Phase 5 (first 3 personas + arbitration working).

**Exit test:**
1. `pytest tests/integration/test_7persona_cycle.py` — full universe cycle; all 7 personas produce valid outputs on 5+ tickers; Arbitration combines 7 signals; audit trail complete
2. Parallel slot dispatch: 3 personas run concurrently (wall-clock < 3× sequential single-persona time for a 3-ticker cycle)
3. Each persona's sanity validator has at least 3 test cases (unit) covering pass, fail-retry-pass, and fail-all-retries-abort

**Duration estimate:** 5-7 days.

---

### Phase 7: Crucible + conviction + sizing + risk gate

**Goal:** The full decision pipeline runs: Phase 0 → Phase 1 (7 personas) → Arbitration → Phase 2 (Crucible) → EV → Sizing → Conviction → Risk Gate → Verdict. The system can produce STRONG_BUY / BUY / SKIP verdicts.

**What gets built:**
- `pmacs/agents/crucible.py` + prompts + grammar + sanity (`Agents.md §12`)
- Crucible inner loop (`Agents.md §16`) — 2-cycle max, 90s budget, NO_TRADE default
- `pmacs/engines/conviction.py` — conviction scoring (`Architecture.md §9.2`, `Source.md §7.2`)
- `pmacs/engines/pricing.py` — EV computation
- `pmacs/engines/sizing.py` — half-Kelly + bootstrap haircut + limited-history haircut (`Architecture.md §9.3`)
- `pmacs/engines/portfolio_risk_gate.py` — max positions, sector limits, concentration
- `pmacs/agents/memo_writer.py` + prompts + grammar + sanity (`Agents.md §13`)
- Nervous orchestrator updated: full step 13 (all sub-steps through TradePlan)
- `tests/integration/test_full_pipeline.py`
- `tests/unit/test_conviction.py`
- `tests/unit/test_sizing.py`
- `tests/unit/test_crucible_budget.py`

**Dependencies:** Phase 6 (all 7 personas).

**Exit test:**
1. `pytest tests/integration/test_full_pipeline.py` — one ticker goes through the complete pipeline: Gatekeeper → 7 personas → Arbitration → Crucible (with attack) → EV → Sizing → Conviction → Risk Gate → Verdict → MemoWriter. Audit trail shows every step.
2. `pytest tests/unit/test_conviction.py` — conviction formula produces expected outputs for known inputs; STRONG_BUY ≥ 0.6, BUY ≥ 0.3, SKIP < 0.3
3. `pytest tests/unit/test_sizing.py` — bootstrap haircuts apply correctly; limited-history haircut stacks; half-Kelly produces sane sizes; max-position-% caps
4. `pytest tests/unit/test_crucible_budget.py` — Crucible times out at 90s → NO_TRADE; Crucible exceeds 2 cycles → NO_TRADE; severity > 0.6 cycle 1 → NO_TRADE without cycle 2
5. A ticker with Crucible severity > 0.6 produces SKIP. A ticker with low Crucible severity and high arbitrated p_up produces STRONG_BUY or BUY.

**Duration estimate:** 7-10 days.

---

### Phase 8: Paper trading — Alpaca paper + sim ledger + wizard

**Goal:** The system trades paper money. Alpaca paper API integration. The wizard works end-to-end. SHADOW + PAPER mode concurrent from first boot.

**What gets built:**
- `pmacs/sim/ledger.py` — paper portfolio ledger ($5K start)
- `pmacs/sim/alpaca_paper_adapter.py` — Alpaca paper order submission + fill polling
- `pmacs/execution/alpaca_adapter.py` — real adapter (not stub)
- `pmacs/execution/catastrophe_net.py` — broker-side wide stop placement at entry
- `pmacs/installer/wizard.py` + `steps/*.py` — the 10-step wizard (`Source.md §12`)
- Mode management: `INSTALLING → SHADOW + PAPER` transition
- `pmacs/schemas/system.py` — Mode enum, mode transition logic
- SQLite `mode_history`, `paper_account` tables
- Nervous orchestrator updated: step 13 concludes with TradePlan.sign_and_send() + catastrophe-net stop for PAPER mode
- `tests/integration/test_paper_trade.py` — submit order → receive fill → update ledger → update holding → audit
- `tests/integration/test_wizard.py` — run all 10 steps with mocked APIs
- `tests/e2e/test_smoke_cycle.py` — the smoke-test cycle from wizard step 10

**Dependencies:** Phase 7 (full decision pipeline to produce TradePlans).

**Exit test:**
1. Wizard completes all 10 steps on a fresh machine (prerequisite: `ops/install_system_users.sh` has been run with sudo to create _pmacs_* system users) (with mocked API keys in test mode)
2. `pytest tests/integration/test_paper_trade.py` — a STRONG_BUY ticker → TradePlan signed → submitted to Alpaca paper → fill received → ledger updated → holding transitions to ACTIVE → catastrophe-net stop placed → audit trail complete
3. `pytest tests/e2e/test_smoke_cycle.py` — full cycle on synthetic fixtures; audit chain verifies; all engines fire
4. SHADOW mode concurrently captures audit-only signals (no fake-trades in SHADOW)
5. The paper ledger balance starts at $5,000 and reflects the fill correctly

**Duration estimate:** 7-10 days.

**After Phase 8:** The system is operationally usable. The operator can boot, run cycles, observe paper trades, and review verdicts. Everything after Phase 8 is improvement — important, but the core loop works.

---

### Phase 9: StopLossMonitor + trailing stop + thesis re-evaluation

**Goal:** Active positions are monitored during RTH. Stop-losses fire. Trailing stops arm and ratchet. Weekly re-evaluation and 90-day thesis aging run.

**What gets built:**
- `pmacs/cortex/stop_loss_daemon.py` — the `pmacs-stoploss` process body (`Architecture.md §11`)
- `pmacs/engines/stop_loss_monitor.py` — detection logic, gap-down handling
- Trailing stop arming and ratcheting (`Architecture.md §11.4`)
- Weekly re-evaluation: Nervous step 14 (`Architecture.md §12`)
- Thesis aging review: Nervous step 15 + `THESIS_AGING_REVIEW` state (`Architecture.md §8.2`)
- `pmacs/engines/opportunity_cost.py` — hold-or-exit decision (`Architecture.md §12` step 18)
- SQLite `stop_events` table
- `tests/integration/test_stop_loss.py` — price breaches stop → StopTrigger written → Nervous polls → TradePlan → fill → STOPPED_OUT
- `tests/unit/test_trailing_stop.py` — arms at 1.5R, ratchets up, never down

**Dependencies:** Phase 8 (active positions exist in PAPER mode).

**Exit test:**
1. `pytest tests/integration/test_stop_loss.py` — complete stop-loss execution path works
2. `pytest tests/unit/test_trailing_stop.py` — trailing math is correct
3. Gap-down: price opens 5% below stop → `MARKET_ON_OPEN` order type selected
4. Weekly re-eval: a held position gets full pipeline re-run; thesis validated → stays ACTIVE; thesis broken → EXIT_THESIS_INVALIDATED
5. 90-day thesis aging: a position held 90+ days triggers THESIS_AGING_REVIEW state; re-eval runs; outcome recorded

**Duration estimate:** 5-7 days.

---

### Phase 10: Dashboard — all 7 pages

**Goal:** The operator-facing web application is functional. All 7 pages render real data. SSE drives real-time updates. The Agents page shows persona progress in real time.

**What gets built:**
- `pmacs/web/app.py` — FastAPI dashboard
- `pmacs/web/sse_client.py` — subscribes to Nervous `/events`
- `pmacs/web/routes/*.py` — all 7 page routes (dashboard, agents, pipeline, universe, cortex, debug, settings)
- `pmacs/web/templates/*.html` — Jinja2 + HTMX
- `pmacs/web/components/*.html` — reusable partials (card, statblock, persona_card, ticker_chip, etc.)
- `pmacs/web/static/` — Tailwind CSS, D3 for Sankey, minimal JS
- Visual identity tokens from `Source.md §13.1`
- Cmd-K command palette
- confirmation modal
- Toast notifications
- All empty states and loading states per `Source.md §13.4`
- `tests/e2e/test_dashboard_renders.py` — each page returns 200 with expected content

**Dependencies:** Phase 8 (data exists in DBs), Phase 9 (stop-loss events to display).

**Exit test:**
1. All 7 pages render at `localhost:8000` with real cycle data
2. Agents page shows persona progress during an active cycle (SSE-driven, not polling)
3. Pipeline page shows verdict cards in kanban columns
4. Universe page shows all seeded tickers with correct flags
5. Cortex page shows heartbeats and audit chain status
6. Debug page streams live events
7. Settings page renders all sections; confirmation modal appears on gated actions
8. Dashboard page shows portfolio summary, risk metrics, and recent decisions
9. Operator can reorder queue from Pipeline right rail
10. Operator can add a ticker from Universe page (operator-confirmed)

**Duration estimate:** 10-14 days (largest UI phase).

---

### Phase 11: Calibration + lessons + causal attribution + override learning

**Goal:** The flywheel's passive components work. The system learns from resolutions. Calibration refits. Lessons are extracted and stored in Qdrant for RAG retrieval.

**What gets built:**
- `pmacs/engines/calibration.py` — Brier-based probability refit
- `pmacs/engines/causal_attribution.py` — credit/blame apportionment
- `pmacs/engines/override_learning.py` — operator override clustering
- `pmacs/engines/lessons.py` — lesson extraction + Qdrant write
- `pmacs/engines/crucible_calibration.py` — severity multiplier tuning
- `pmacs/engines/flywheel_health.py` — monitors all calibration components
- `pmacs/storage/qdrant.py` — Qdrant collection management + embedding
- `pmacs/storage/kuzu.py` — KuzuDB graph operations
- `pmacs/storage/duckdb.py` — DuckDB analytics table management
- Nervous orchestrator steps 19-24 (`Architecture.md §12`)
- DuckDB `rolling_metrics`, `persona_performance` tables
- Qdrant `theses`, `memos_persona`, `memos_aggregated`, `evidence_chunks`, `lessons` collections
- `tests/integration/test_calibration.py`
- `tests/integration/test_qdrant.py`
- `tests/integration/test_kuzu.py`

**Dependencies:** Phase 8 (resolutions accumulating in PAPER), Phase 9 (terminal states exist).

**Exit test:**
1. `pytest tests/integration/test_calibration.py` — after 20 synthetic resolutions, calibration refit adjusts persona weights; Brier improves
2. Lessons engine extracts a lesson from a resolution → writes to Qdrant → retrieval query returns it
3. CausalAttribution attributes a resolution to the contributing personas (verifiable apportionment)
4. FlywheelHealth snapshot records rolling Brier, Sharpe, and calibration gap status
5. KuzuDB has Holding → Evidence → Resolution → Lesson lineage traversable via Cypher

**Duration estimate:** 7-10 days.

---

### Phase 12: Failure Diagnostic Engine + cross-DB consistency + reconciliation

**Goal:** Every terminal state is classified. Cross-DB integrity is verified. Reconciliation catches mismatches. The system's failure taxonomy is fully operational.

**What gets built:**
- `pmacs/engines/failure_diagnostic.py` — full 18-type classifier (`Agents.md §15`)
- `pmacs/storage/consistency.py` — cross-DB reconciler
- `pmacs/engines/reconciliation.py` — paper-vs-broker reconciliation
- `pmacs/logsys/dead_letter.py` — dead-letter queue with backoff
- Nervous orchestrator steps 25-28 (`Architecture.md §12`)
- SQLite `dead_letter` table
- DuckDB `failure_taxonomy_counts` table
- KuzuDB `FailedAssumption` nodes and `FAILED_ASSUMPTION` edges
- `tests/unit/test_fde.py` — each of 18 taxonomy types has at least one test case
- `tests/integration/test_cross_db.py`

**Dependencies:** Phase 11 (calibration + Kuzu + DuckDB operational).

**Exit test:**
1. `pytest tests/unit/test_fde.py` — all 18 taxonomy types classify correctly from synthetic holdings
2. STOP_HUNTED vs STOP_LOSS_CORRECT differentiation: a holding that recovers within 48h classifies as STOP_HUNTED; one that doesn't classifies as STOP_LOSS_CORRECT
3. Cross-DB reconciler detects a deliberately introduced mismatch (missing Qdrant vector for a Kuzu thesis) and reports `CROSS_DB_INCONSISTENCY`
4. Dead-letter queue: simulate a Qdrant write failure → queued → retry succeeds on next attempt
5. FailedAssumption nodes written to KuzuDB are traversable: `MATCH (h:Holding)-[:FAILED_ASSUMPTION]->(fa) RETURN fa`

**Duration estimate:** 5-7 days.

---

### Phase 13: Episodic context injection

**Goal:** Every persona receives its short-term memory brief. The flywheel feeds back into reasoning.

**What gets built:**
- `pmacs/agents/episodic_context.py` — `build_context_brief()` implementation (`Agents.md §18`)
- DuckDB `persona_ticker_affinity` and `persona_subsector_affinity` tables (populated by calibration)
- Qdrant `lessons` collection retrieval integration
- Update all persona prompts to include `{episodic_context}` placeholder
- Audit event `episodic_context_injected` with content_hash
- `tests/integration/test_episodic.py`

**Dependencies:** Phase 11 (calibration, Qdrant, DuckDB populated), Phase 12 (FDE for failure history).

**Exit test:**
1. `pytest tests/integration/test_episodic.py` — a persona running on a ticker with 5+ past cycles receives a non-empty context brief containing persona-ticker affinity, recent failures, and macro context
2. Context brief is ≤ 200 words
3. Audit event `episodic_context_injected` is logged with content_hash
4. A persona running on a ticker with zero history receives a minimal brief (macro context only)
5. Before-and-after comparison: same ticker, same evidence, with vs without episodic context → outputs differ (demonstrating the context influences reasoning)

**Duration estimate:** 3-5 days.

---

### Phase 14: Mutation Engine

**Goal:** The active flywheel is operational. The system proposes, A/B tests, and promotes (or rejects) variants of its own components. All five rollback safety levels are functional.

**What gets built:**
- `pmacs/mutation/daemon.py` — main loop (`Architecture.md §10.4`)
- `pmacs/mutation/candidate_generator.py` — rule-based generation (`Agents.md §17`)
- `pmacs/mutation/ab_runner.py` — SHADOW-only A/B execution
- `pmacs/mutation/stat_test.py` — Welch's t-test + Cohen's d
- `pmacs/mutation/promotion.py` — auto-promote + operator-promote
- `pmacs/mutation/rollback.py` — auto-rollback + manual rollback (`Agents.md §17.4`)
- SQLite `mutation_proposals`, `mutation_outcomes` tables (already created in Phase 1 schema but now populated)
- Settings → Mutation Engine panel (connect to existing Settings page from Phase 10)
- SSE events: `mutation.*`
- `config/mutation.toml` — activation threshold, recommendation thresholds
- `pmacs-mutation` launchd plist activation
- `tests/unit/test_stat_test.py`
- `tests/integration/test_mutation_lifecycle.py`
- `tests/integration/test_rollback.py`
- `tests/mutation_eval/` — offline A/B test harness

**Dependencies:** Phase 12 (FDE operational — candidate generation reads FDE clusters), Phase 13 (episodic context — mutations to prompts affect context injection).

**Exit test:**
1. `pytest tests/unit/test_stat_test.py` — Welch's t-test produces correct p-values on known distributions; Cohen's d correct
2. `pytest tests/integration/test_mutation_lifecycle.py` — synthetic FDE cluster (N=5 MOAT_DRIFT_OVERESTIMATE) → candidate generated → A/B started → 20 synthetic cycles → stat test → result classified → if significant: staged as recommendation for operator approval (ALL mutations require operator confirmation)
3. `pytest tests/integration/test_rollback.py`:
   - **Level 1:** mutation process cannot write to `model_registry.json` (filesystem permission denied)
   - **Level 2:** baseline_config and rollback_config are identical and immutable after proposal creation
   - **Level 3:** promotion is atomic (verified by killing process mid-write → old config persists)
   - **Level 4:** auto-rollback fires after 50-cycle regression (synthetic regression → rollback → config restored → audit logged)
   - **Level 5:** kill switch engagement → 3 most recent promotions flagged → rollback one → config restored
4. Maximum concurrent A/B test cap (3) enforced: 4th proposal queues in PROPOSED status
5. Mutation Engine dormant before 50 PAPER cycles (verified by checking no proposals generated in early test cycles)

**Duration estimate:** 10-14 days (most complex engine).

---

### Phase 15: Polish, performance, operator experience

**Goal:** The system is production-quality for paper trading. All operator workflows from `Source.md §21` work smoothly. Performance is within budget. The first-30-days experience from `Source.md §23` is pleasant.

**What gets built:**
- Agents page animations (persona progress bars, Sankey, Math view) — `Source.md §15.5`
- Pipeline page kanban refinement (smooth drag-drop, priority bands)
- Dashboard sparklines and time-window selector
- Cmd-K command palette (full: tickers, pages, quick actions, audit search)
- Keyboard shortcuts (`Source.md §13.6`)
- Accessibility audit (`Source.md §13.7`)
- Performance profiling: per-cycle throughput verified against `Architecture.md §20.1`
- Memory profiling: RAM usage verified against `Architecture.md §20.2`
- `ops/spec_consistency.py` — cross-file reference checker for CI
- `ops/backup_verify.py` — backup + restore tested
- `ops/audit_chain_verify.py` — standalone verification tool
- Documentation: `docs/operator_runbook.md`
- All empty states, loading states, error states per `Source.md §13.4`
- Notification policy implementation (`Source.md §13.5`)
- Cycle compare feature (`Source.md §15.9`)
- "Copy for Claude Code" button on every debug event

**Dependencies:** All previous phases.

**Exit test:**
1. All 8 operator workflows from `Source.md §21` complete in ≤ 3 clicks (excluding operator confirmation)
2. Full cycle on 16-ticker universe completes within 3 hours on M1 Max 64GB
3. RAM usage under 50GB during cycle peak
4. Audit chain verifies after 100+ cycles of accumulated data
5. `ops/spec_consistency.py` passes (every Source.md operator-promise has an Architecture.md implementation pointer)
6. Backup + restore: back up all 5 DBs → wipe → restore → audit chain verifies → system resumes cycling
7. Accessibility: axe-core scan on all 7 pages returns zero critical violations
8. All toast notifications, modal dialogs, and keyboard shortcuts function per spec

**Duration estimate:** 7-10 days.

---

## 3. Mode promotion and demotion gates

These are the numerical thresholds that govern when the system moves between production modes. Build phases produce the capability; mode gates verify the performance.

### 3.1 Promotion gates

| From | To | Min cycles | Min trades | Brier ≤ | Rolling Sharpe ≥ | Max drawdown ≤ | Manual gate |
|---|---|---|---|---|---|---|---|
| SHADOW + PAPER | PAPER_VALIDATED | 90 | 200 | 0.30 | 0.0 | 15% | Yes — operator |
| PAPER_VALIDATED | LIVE_EARLY | 90 | 200 | 0.28 | 0.5 | 12% | Yes — operator |
| LIVE_EARLY | LIVE_STANDARD | 90 | 200 | 0.27 | 0.7 | 10% | Yes — operator |
| LIVE_STANDARD | LIVE_EXPANDED | 120 | 300 | 0.25 | 0.8 | 8% | Yes — operator |

**All gates must pass simultaneously.** If Brier is ≤ 0.30 but Sharpe < 0.0, promotion is blocked. The dashboard's mode badge shows which gates pass and which block.

**Cycles and trades are cumulative** within the current mode. Promoting to PAPER_VALIDATED resets the counters for the next mode's gates.

**The operator controls the timing.** Even when all gates pass, promotion does not happen automatically. The operator clicks Promote → confirm → mode changes. This is deliberate friction (`Source.md §4` promise 5).

### 3.2 Gate computation

```python
# pmacs/engines/flywheel_health.py
def check_promotion_gates(current_mode: str, target_mode: str) -> PromotionGateResult:
    """
    Returns which gates pass and which fail. UI displays this in the mode badge.
    """
    thresholds = PROMOTION_THRESHOLDS[f"{current_mode}_to_{target_mode}"]

    current_cycles = count_cycles_in_mode(current_mode)
    current_trades = count_trades_in_mode(current_mode)
    rolling_brier = get_rolling_brier(window=30)
    rolling_sharpe = get_rolling_sharpe(window=20)
    rolling_drawdown = get_max_drawdown(window=90)

    gates = {
        "min_cycles": current_cycles >= thresholds.min_cycles,
        "min_trades": current_trades >= thresholds.min_trades,
        "brier": rolling_brier <= thresholds.max_brier,
        "sharpe": rolling_sharpe >= thresholds.min_sharpe,
        "drawdown": rolling_drawdown <= thresholds.max_drawdown,
    }

    all_pass = all(gates.values())

    return PromotionGateResult(
        current_mode=current_mode,
        target_mode=target_mode,
        gates=gates,
        all_pass=all_pass,
        current_values={
            "cycles": current_cycles,
            "trades": current_trades,
            "brier": rolling_brier,
            "sharpe": rolling_sharpe,
            "drawdown": rolling_drawdown,
        },
        thresholds=thresholds,
    )
```

### 3.3 Where the operator sees this

Dashboard → mode badge → click → modal showing:
```
PAPER → PAPER_VALIDATED
  ☑ Cycles: 94 / 90 required     ✓
  ☑ Trades: 213 / 200 required   ✓
  ☑ Brier: 0.28 / ≤ 0.30        ✓
  ☑ Sharpe: 0.71 / ≥ 0.0         ✓
  ☑ Drawdown: 8.2% / ≤ 15%      ✓

  All gates pass. [Promote → confirm]
```

Or:
```
PAPER → PAPER_VALIDATED
  ☑ Cycles: 94 / 90 required     ✓
  ☒ Trades: 187 / 200 required   ✗ (13 more needed)
  ☑ Brier: 0.28 / ≤ 0.30        ✓
  ☑ Sharpe: 0.71 / ≥ 0.0         ✓
  ☑ Drawdown: 8.2% / ≤ 15%      ✓

  1 gate failing. Promote button disabled.
```

### 3.4 Mode override

The operator can force-promote or force-demote from Settings → Operator → Mode override (operator-confirmed). This bypasses gates with an explicit warning: "You are overriding the promotion gates. The system has not yet proven its performance at the required level. Do you want to proceed?" The override is logged in audit with `gates_overridden=true`.

### 3.5 Demotion gates

Auto-demotion occurs when performance regresses. Demotion is one tier at a time. Demotion engages the kill switch first; the operator must disengage before the demoted mode's normal operation resumes.

| From | To | Trigger |
|---|---|---|
| LIVE_EXPANDED | LIVE_STANDARD | Rolling 20-cycle Sharpe < 0 OR drawdown > 12% |
| LIVE_STANDARD | LIVE_EARLY | Rolling 20-cycle Sharpe < 0 OR drawdown > 14% |
| LIVE_EARLY | PAPER_VALIDATED | Rolling 20-cycle Sharpe < 0 OR drawdown > 16% |
| PAPER_VALIDATED | PAPER | Rolling 30-cycle Brier > 0.32 OR Sharpe < -0.3 |

Demotion triggers:
1. Kill switch engages (automatic)
2. Mode transitions down one tier (automatic)
3. Audit log records `mode_changed` with `triggered_by='AUTO_DEMOTION'`
4. SSE event `system.mode_changed` (dashboard badge updates)
5. Operator sees the kill switch panel with the demotion reason
6. Operator disengages kill switch (operator-confirmed) → system resumes at the demoted tier
7. A 10-cycle observation period begins before any promotion attempt is allowed

### 3.6 Demotion override

The operator can challenge a demotion from Settings → Operator → Mode override (operator-confirmed). This restores the previous tier with `gates_overridden=true` in audit. The system will re-demote if the trigger condition persists after the override.

### 3.7 The SHADOW + PAPER special case

SHADOW and PAPER are concurrent from day 1 (`Source.md §9.2`). There is no separate "SHADOW only" phase in production. SHADOW provides the math-gate audit trail; PAPER provides the execution trail. Together they accumulate the data needed for PAPER_VALIDATED promotion.

SHADOW does not have promotion gates; it runs continuously alongside whatever the current active mode is. Even in LIVE_STANDARD, SHADOW continues capturing audit-only signals. This is by design — SHADOW is the mutation engine's A/B test arena.

---

## 4. File-by-file dependency graph

This graph shows which files depend on which other files being complete. Claude Code: if you're about to create file X and file Y isn't done yet, check whether X depends on Y.

### 4.1 Layer 0: Foundation (no dependencies)

```
pmacs/schemas/*.py
pmacs/constants.py
pmacs/data/canonical.py
config/*.toml
config/model_registry.json
```

### 4.2 Layer 1: Depends on Layer 0

```
pmacs/config.py                  → schemas, config files
pmacs/storage/keychain.py        → (standalone)
pmacs/storage/sqlite.py          → schemas
pmacs/storage/audit.py           → schemas, canonical.py
pmacs/logsys/*.py                → schemas
pmacs/engines/state_machine.py   → schemas/contracts.py
```

### 4.3 Layer 2: Depends on Layer 1

```
pmacs/data/gateway.py            → config.py, keychain.py, logsys
pmacs/data/staleness.py          → schemas/freshness.py, logsys
pmacs/data/fx.py                 → schemas/currency.py
pmacs/data/universe.py           → storage/sqlite.py, schemas
pmacs/data/sources/*.py          → gateway.py, staleness.py, schemas/data.py
pmacs/storage/kuzu.py            → schemas
pmacs/storage/qdrant.py          → schemas
pmacs/storage/duckdb.py          → schemas
```

### 4.4 Layer 3: Depends on Layer 2

```
pmacs/agents/base.py             → schemas/agents.py, data sources, logsys
pmacs/agents/gatekeeper.py       → storage/sqlite.py, data/staleness.py, engines/state_machine.py
pmacs/cortex/model_integrity.py  → config/model_hashes.toml
pmacs/execution/signing.py       → storage/keychain.py, schemas/trade.py
```

### 4.5 Layer 4: Depends on Layer 3

```
pmacs/agents/macro_regime.py     → agents/base.py, prompts, grammars, sanity
pmacs/agents/catalyst_summarizer.py → agents/base.py, ...
pmacs/agents/moat_analyst.py     → agents/base.py, ...
pmacs/agents/growth_hunter.py    → agents/base.py, ...
pmacs/agents/insider_activity.py → agents/base.py, ...
pmacs/agents/short_interest.py   → agents/base.py, ...
pmacs/agents/forensics.py        → agents/base.py, ...
pmacs/engines/arbitration.py     → schemas/arbitration.py, logsys
pmacs/engines/queue.py           → schemas/queue.py, storage/sqlite.py
```

### 4.6 Layer 5: Depends on Layer 4

```
pmacs/agents/crucible.py         → agents/base.py, schemas/arbitration.py (reads Arbitrated)
pmacs/engines/conviction.py      → schemas/arbitration.py, schemas/conviction.py
pmacs/engines/pricing.py         → schemas/pricing.py, data sources
pmacs/engines/sizing.py          → schemas/sizing.py, config/risk.toml
pmacs/engines/portfolio_risk_gate.py → schemas/portfolio.py, storage/sqlite.py
pmacs/agents/memo_writer.py      → agents/base.py, all persona outputs
```

### 4.7 Layer 6: Depends on Layer 5

```
pmacs/nervous/orchestrator.py    → ALL engines, ALL agents, ALL storage, execution/signing
pmacs/nervous/api.py             → orchestrator.py, auth.py
pmacs/cortex/daemon.py           → storage/sqlite.py, cortex/*.py
pmacs/execution/service.py       → execution/signing.py, execution/alpaca_adapter.py
pmacs/cortex/stop_loss_daemon.py → engines/stop_loss_monitor.py, storage/sqlite.py
```

### 4.8 Layer 7: Depends on Layer 6

```
pmacs/engines/calibration.py     → storage/duckdb.py, schemas/calibration.py (requires resolutions)
pmacs/engines/lessons.py         → storage/qdrant.py, storage/kuzu.py
pmacs/engines/causal_attribution.py → storage/kuzu.py
pmacs/engines/failure_diagnostic.py → schemas/failure.py, storage/kuzu.py, state_machine.py
pmacs/storage/consistency.py     → ALL storage modules
```

### 4.9 Layer 8: Depends on Layer 7

```
pmacs/agents/episodic_context.py → storage/duckdb.py, storage/qdrant.py, storage/kuzu.py, engines/failure_diagnostic.py
pmacs/engines/mutation.py        → engines/failure_diagnostic.py, storage/duckdb.py
pmacs/mutation/*.py              → engines/mutation.py, storage/sqlite.py
```

### 4.10 Layer 9: Depends on Layer 8

```
pmacs/web/*.py                   → ALL (read-only access to everything)
pmacs/installer/wizard.py        → ALL (sets up everything)
```

---

## 5. Phase-to-mode mapping

| Build phase | What it enables | Mode unlocked |
|---|---|---|
| Phase 1-4 | Foundation + processes | System can start (INSTALLING) |
| **Phase 5-8** | **Full pipeline + paper trading + wizard** | **SHADOW + PAPER** |
| Phase 9 | Stop-loss + re-evaluation | SHADOW + PAPER (with active position monitoring) |
| Phase 10 | Dashboard | SHADOW + PAPER (with operator visibility) |
| Phase 11-12 | Calibration + FDE | Prerequisites for PAPER_VALIDATED promotion gates |
| Phase 13 | Episodic context | Improved persona quality (no new mode) |
| **Phase 14** | **Mutation Engine** | **PAPER_VALIDATED** (requires Mutation Engine for full flywheel) |
| Phase 15 | Polish | Ready for LIVE_EARLY evaluation |

**The operator can start using the system at Phase 8.** Phases 9-15 improve quality, add monitoring, and add the flywheel — but the core decision pipeline works at Phase 8.

**PAPER_VALIDATED requires Phase 14** because the Mutation Engine is part of the flywheel health check. FlywheelHealth checks that the Mutation Engine is active and producing candidates before allowing PAPER_VALIDATED promotion.

**LIVE_EARLY requires Phase 15** because the system must be production-quality before real money enters.

---

## 6. Risk checkpoints

At three points in the build, stop and explicitly verify risk properties before proceeding.

### 6.1 Checkpoint A: after Phase 4 (processes + kill switch)

**Verify:**
- [ ] Kill switch engages on all 10 triggers
- [ ] Kill switch disengagement requires operator confirmation
- [ ] Audit chain break → immediate kill switch
- [ ] llama-server process cannot reach external IP (pf verified)
- [ ] Execution process is the only one with broker imports
- [ ] Ed25519 signing works and tamper-detection works
- [ ] Crash loop detection works

**If any fails:** Do not proceed to Phase 5. Fix the risk property first.

### 6.2 Checkpoint B: after Phase 8 (paper trading)

**Verify:**
- [ ] Paper trades execute end-to-end with correct fills
- [ ] Catastrophe-net stops are placed for every new position
- [ ] Ledger balance is accurate after 10+ trades
- [ ] Wizard completes without error
- [ ] The operator can engage and disengage the kill switch from the UI (Cortex page)
- [ ] The operator can force-exit a position from the Pipeline page (operator-confirmed)
- [ ] Mode is SHADOW + PAPER after wizard completes

**If any fails:** Do not proceed to Phase 9. The system is trading (paper) money.

### 6.3 Checkpoint C: after Phase 14 (Mutation Engine)

**Verify:**
- [ ] Mutation Engine cannot write to production config directly (filesystem permission denied)
- [ ] Auto-rollback fires on synthetic regression (tested with injected bad mutation)
- [ ] Kill switch engagement flags last 3 promotions
- [ ] Maximum 3 concurrent A/B tests enforced
- [ ] No mutation is ever applied without operator confirmation (verify: attempt auto-apply → rejected)
- [ ] Mutation candidates cannot target excluded paths (arbitration formula, state machine, kill switch, etc.)
- [ ] `reversible=True` is enforced on every MutationCandidate
- [ ] Operator confirmation required for prompt and threshold mutations

**If any fails:** Do not proceed to Phase 15. The Mutation Engine is the highest-risk component — an unrestricted self-modifying system will destroy itself.

---

## 7. What "done" means

### 7.1 A phase is done when:

1. All listed files exist and compile (no import errors)
2. All listed tests pass in CI
3. The exit test scenario passes end-to-end
4. Anti-pattern checks pass (`Architecture.md §16`)
5. If a risk checkpoint applies: all checkbox items verified
6. Previous phases' exit tests still pass (regression check)

### 7.2 The system is "paper-ready" when:

Phase 8 complete + Checkpoint B verified. The operator can run the wizard, boot PMACS, and observe paper trades.

### 7.3 The system is "flywheel-ready" when:

Phase 14 complete + Checkpoint C verified. The Mutation Engine is operational. The system improves itself within strict safety bounds.

### 7.4 The system is "LIVE-ready" when:

Phase 15 complete + PAPER_VALIDATED mode gates pass + operator reviews the full audit trail + operator is satisfied with system quality. **Only the operator decides when real money enters.** The system never decides this on its own (`Source.md §4` promise 5).

### 7.5 Total estimated duration

| Phases | Est. days | Cumulative |
|---|---|---|
| Phase 1-4 (foundation + processes) | 22-32 | 22-32 |
| Phase 5-8 (pipeline + paper trading) | 26-37 | 45-65 |
| Phase 9-12 (monitoring + calibration + FDE) | 24-38 | 69-103 |
| Phase 13-15 (episodic + mutation + polish) | 20-29 | 89-132 |

**Realistic: 3.5-5 months** of focused development to reach Phase 15. Paper trading (Phase 8) can start at **6-10 weeks**. This assumes one developer (the operator, aided by Claude Code) working consistently.

The timeline is honest, not optimistic. Rushed phases produce fragile systems. PMACS is designed to run for years; an extra week in Phase 4 to get the kill switch right is a better investment than saving a week and debugging it later in LIVE_EARLY.

---

## 8. Connection to companion files

### 8.1 → Source.md

This file tells you *what to build when.* `Source.md` tells you *what the operator expects to see when it's done.* Key connections:

- Phase 8 exit test must produce the wizard experience from `Source.md §12`
- Phase 10 exit test must produce the 7 pages from `Source.md §14-§20`
- Phase 15 exit test must produce the operator workflows from `Source.md §21`
- Mode gates (§3) implement the mode ladder from `Source.md §9`
- The first-30-days experience (`Source.md §23`) is what Phase 8-10 collectively produce

### 8.2 → Architecture.md

This file tells you *when.* `Architecture.md` tells you *how.* Key connections:

- Each phase's "what gets built" references specific files from `Architecture.md §3` (repo tree)
- Engine implementations are in `Architecture.md §9`
- Cycle steps referenced in phases map to `Architecture.md §12`
- The Mutation Engine process is in `Architecture.md §10`
- The kill switch (verified at Checkpoint A) is in `Architecture.md §13`
- Anti-patterns (enforced from Phase 1) are in `Architecture.md §16`
- Performance budgets (verified at Phase 15) are in `Architecture.md §20`

### 8.3 → Agents.md

This file tells you *when each persona ships.* `Agents.md` tells you *what each persona does.* Key connections:

- Phase 5 introduces MacroRegime, CatalystSummarizer, MoatAnalyst (`Agents.md §5-§7`)
- Phase 6 introduces GrowthHunter, InsiderActivity, ShortInterest, Forensics (`Agents.md §8-§11`)
- Phase 7 introduces the Crucible (`Agents.md §12`) and MemoWriter (`Agents.md §13`)
- Phase 12 introduces the FDE 18-taxonomy classifier (`Agents.md §15`)
- Phase 13 introduces episodic context injection (`Agents.md §18`)
- Phase 14 introduces Mutation Engine candidate generation rules (`Agents.md §17`)

### 8.4 What this file does NOT contain

- **No implementation details.** Lives in `Architecture.md`.
- **No persona specifications.** Lives in `Agents.md`.
- **No operator-facing behavior.** Lives in `Source.md`.
- **No code.** This file contains only pseudo-code for the gate-check function (§3.2) to illustrate the logic.

### 8.5 The four-file invariant (restated)

Every operator-facing behavior in `Source.md` has at least one implementation pointer in `Architecture.md`. Every persona behavior in `Agents.md` has at least one contract specification. Every build-time dependency in this file has at least one entry in `Architecture.md` (repo tree) and `Agents.md` (if LLM-touching). Verified by `ops/spec_consistency.py` in CI.

---

*End of Phases.md. v1. Pair with Source.md, Architecture.md, Agents.md.*
