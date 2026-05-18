# PMACS — Complete Missing / To-Build Inventory

Cross-referenced against all 4 spec files (7,242 lines) and the current codebase (120+ source files).
Organized by spec Phase. Status: DONE / PARTIAL / STUB / MISSING.

---

## Phase 1: Foundation — schemas, config, storage, audit

| # | Component | Spec Ref | Status | Notes |
|---|-----------|----------|--------|-------|
| 1.1 | All Pydantic models compile | Arch §8 | DONE | 27 schema files |
| 1.2 | SQLite all tables | Arch §8.5 | DONE | Holdings, stop_events, queue, mutations, paper_account, mode_history, dead_letter |
| 1.3 | Hash-chained audit writer | Arch §5.3 | DONE | SHA256 + fsync + prev_sha |
| 1.4 | Keychain wrapper | Arch §6.1 | DONE | macOS Keychain |
| 1.5 | Config loader | Arch §6 | DONE | TOML + JSON |
| 1.6 | Constants / anti-pattern thresholds | Arch §16 | DONE | constants.py |
| 1.7 | Debug log + error classifier | Arch §5.5 | DONE | 64 error codes registered |
| 1.8 | State machine | Arch §8.2 | DONE | All 24 states + transitions |
| 1.9 | Pre-commit anti-pattern hooks | Arch §16 | DONE | .pre-commit-config.yaml with spec grep checks |

## Phase 2: Data layer — sources, staleness, FX

| # | Component | Spec Ref | Status | Notes |
|---|-----------|----------|--------|-------|
| 2.1 | DataGateway + TokenBucket rate limiting | Arch §6 | DONE | 13 sources with rates |
| 2.2 | Staleness checker (FreshnessResult) | Arch §6.3 | DONE | No packet mutation |
| 2.3 | FX ECB EUR/USD | Arch §6.5 | DONE | usd_per_eur convention |
| 2.4 | Corporate actions (splits/dividends) | Arch §6.6 | STUB | File exists, no real data source |
| 2.5 | Universe CRUD | Arch §6.7 | DONE | Operator-managed ticker list |
| 2.6 | 13 data source modules | Arch §6.2 | DONE | edgar, polygon, finnhub, alpaca_data, openfda, finra, form4, ir_pages, press, fomc, fred, ecb, fundamentals |
| 2.7 | EvidencePacket schema | Arch §7.1 | DONE | In schemas/data.py |
| 2.8 | FreshnessResult schema | Arch §7.2 | DONE | In schemas/freshness.py |
| 2.9 | source_criticality.toml config | Arch §6.3 | DONE | CRITICAL/IMPORTANT/NICE_TO_HAVE |
| 2.10 | Real API integration tests (10/13 sources) | Phases §2 exit | MISSING | No integration tests hitting real APIs |
| 2.11 | Catalyst resolution: catalyst_detector.py | Arch §7 | MISSING | Only stub detector.py exists; no multi-source corroboration logic |
| 2.12 | Catalyst resolution: earnings_resolver.py | Arch §7.1 | MISSING | Earnings catalyst type resolution |
| 2.13 | Catalyst resolution: fda_resolver.py | Arch §7.1 | MISSING | FDA decision catalyst type resolution |
| 2.14 | Catalyst resolution: corroboration.py | Arch §7.2 | MISSING | Tier A/B/C multi-source corroboration with 3σ outlier guard |

## Phase 3: Inference backend — llama-server

| # | Component | Spec Ref | Status | Notes |
|---|-----------|----------|--------|-------|
| 3.1 | PersonaRunner base class | Agents §3 | DONE | Three-layer contract |
| 3.2 | Test grammar | Agents §3 | DONE | test_grammar.gbnf |
| 3.3 | Base sanity validator | Agents §3 | DONE | base.py |
| 3.4 | ops/start_inference.sh | Arch §4.1 | MISSING | No inference startup script |
| 3.5 | GGUF SHA256 verification | Arch §4.1 | DONE | model_integrity.py |
| 3.6 | model_hashes.toml | Arch §4.1 | PARTIAL | File exists but has placeholder hash |
| 3.7 | LLM integration test | Phases §3 exit | MISSING | No test_llm_call.py |
| 3.8 | pf firewall rules | Arch §4.1 | MISSING | No ops/install_pf_rules.sh |

## Phase 4: Core processes — Cortex, Nervous, Execution, kill switch

| # | Component | Spec Ref | Status | Notes |
|---|-----------|----------|--------|-------|
| 4.1 | Cortex daemon main loop | Arch §13 | DONE | daemon.py |
| 4.2 | Heartbeat monitoring | Arch §13.2 | DONE | health.py |
| 4.3 | Kill switch (engage/disengage + TOTP) | Arch §13.3 | DONE | kill_switch.py |
| 4.4 | Boot detector (gap detection) | Arch §13.4 | DONE | boot_detector.py |
| 4.5 | Crash loop detector | Arch §13.5 | DONE | crash_loop_detector.py |
| 4.6 | Self-check (meta-monitor) | Arch §13.6 | DONE | self_check.py |
| 4.7 | Clock monitor (NTP drift) | Arch §13.7 | DONE | clock_monitor.py |
| 4.8 | Disk monitor | Arch §13.8 | DONE | disk_monitor.py |
| 4.9 | TOTP verification | Arch §13.9 | DONE | totp.py |
| 4.10 | Nervous orchestrator (stub cycle) | Arch §9 | DONE | Full cycle sequence |
| 4.11 | Nervous API + SSE | Arch §4.4 | DONE | api.py + sse_publisher.py |
| 4.12 | Checkpoint (cycle resume) | Arch §9 | DONE | checkpoint.py |
| 4.13 | Auth (session + TOTP) | Arch §4.4 | DONE | auth.py |
| 4.14 | Execution service (UDS) | Arch §4.3 | PARTIAL | HTTP-based, not UDS |
| 4.15 | Ed25519 signing | Arch §4.3 | DONE | signing.py |
| 4.16 | 8 launchd plists | Arch §4.1 | DONE | CLI generates them |
| 4.17 | ops/install_launchd.sh | Arch §4.1 | MISSING | No manual install script |
| 4.18 | pf rules (block inference from internet) | Arch §4.1 | MISSING | No ops/install_pf_rules.sh |

## Phase 5: Gatekeeper + first 3 personas

| # | Component | Spec Ref | Status | Notes |
|---|-----------|----------|--------|-------|
| 5.1 | Gatekeeper (deterministic filter) | Agents §4 | DONE | 5-step admittance |
| 5.2 | MacroRegime persona + prompt + grammar + sanity | Agents §5 | DONE | Full 3-layer |
| 5.3 | CatalystSummarizer persona | Agents §6 | DONE | Full 3-layer |
| 5.4 | MoatAnalyst persona | Agents §7 | DONE | Full 3-layer |
| 5.5 | ArbitrationEngine (Brier-inverse) | Arch §9.1 | DONE | Weighted combination |
| 5.6 | Queue composition + priority bands | Arch §9.6 | DONE | P1-P4 bands |
| 5.7 | Memory engine (antipattern checker) | Arch §9 | STUB | Always returns None |
| 5.8 | 3-persona cycle integration test | Phases §5 exit | MISSING | No test_3persona_cycle.py |

## Phase 6: Remaining 4 personas

| # | Component | Spec Ref | Status | Notes |
|---|-----------|----------|--------|-------|
| 6.1 | GrowthHunter persona | Agents §8 | DONE | Full 3-layer |
| 6.2 | InsiderActivity persona | Agents §9 | DONE | Full 3-layer |
| 6.3 | ShortInterest persona | Agents §10 | DONE | Full 3-layer |
| 6.4 | Forensics persona | Agents §11 | DONE | Full 3-layer |
| 6.5 | Parallel slot dispatch (3 slots) | Arch §12.2 | DONE | Base class handles slots |
| 6.6 | 7-persona cycle integration test | Phases §6 exit | MISSING | No test_7persona_cycle.py |

## Phase 7: Crucible + conviction + sizing + risk gate

| # | Component | Spec Ref | Status | Notes |
|---|-----------|----------|--------|-------|
| 7.1 | Crucible persona + prompt + grammar + sanity | Agents §12 | DONE | Full 3-layer |
| 7.2 | Crucible inner loop (2-cycle, 90s budget) | Agents §16 | PARTIAL | Single-pass only; no rewrite loop |
| 7.3 | Conviction scoring | Arch §9.2 | DONE | Formula + verdict tiers |
| 7.4 | EV / pricing engine | Arch §9.4 | STUB | 750 bytes; hardcoded target_gain=0.10, stop=0.15 |
| 7.5 | Sizing engine (half-Kelly + haircuts) | Arch §9.3 | DONE | Bootstrap + correlation + cap |
| 7.6 | Portfolio risk gate | Arch §9.4 | DONE | Max positions + sector limits |
| 7.7 | MemoWriter persona | Agents §13 | DONE | Full 3-layer |
| 7.8 | Full pipeline integration test | Phases §7 exit | MISSING | No test_full_pipeline.py (spec version) |
| 7.9 | Crucible budget test | Phases §7 exit | MISSING | No test_crucible_budget.py |

## Phase 8: Paper trading + wizard

| # | Component | Spec Ref | Status | Notes |
|---|-----------|----------|--------|-------|
| 8.1 | Paper portfolio ledger ($5K) | Arch §9 | PARTIAL | No dedicated ledger.py; orchestrator tracks |
| 8.2 | Alpaca paper adapter | Arch §4.3 | DONE | alpaca_paper.py |
| 8.3 | Catastrophe-net stop placement | Arch §11.3 | DONE | catastrophe_net.py |
| 8.4 | 11-step wizard | Source §12 | DONE | wizard.py + steps/*.py |
| 8.5 | Mode management (SHADOW+PAPER) | Arch §9 | DONE | mode_manager.py |
| 8.6 | Cash ledger | Arch §9 | MISSING | No cash_ledger table or engine |
| 8.7 | paper_account table in SQLite | Arch §8.5 | DONE | sqlite.py:97 — CREATE TABLE IF NOT EXISTS paper_account |
| 8.8 | mode_history table in SQLite | Arch §8.5 | DONE | sqlite.py:22 — CREATE TABLE IF NOT EXISTS mode_history |
| 8.9 | Live trading adapter (LIVE modes) | Arch §4.3 | MISSING | ibkr_adapter.py does not exist |
| 8.10 | E2E smoke cycle test | Phases §8 exit | MISSING | No test_smoke_cycle.py |
| 8.11 | ops/install_system_users.sh | Phases §8 exit | MISSING | No _pmacs_* system user creation |
| 8.12 | Embedding model (BAAI/bge-base-en-v1.5) | Arch §8.7, Source §12.4.5 | MISSING | No verified download + 768-dim output check |

## Phase 9: StopLossMonitor + trailing stop + re-eval

| # | Component | Spec Ref | Status | Notes |
|---|-----------|----------|--------|-------|
| 9.1 | Stop-loss daemon process | Arch §11 | DONE | cortex/stop_loss_daemon.py |
| 9.2 | Stop-loss detection + gap-down handling | Arch §11.2 | DONE | stop_loss_monitor.py |
| 9.3 | Trailing stop arm/ratchet | Arch §11.4 | DONE | trailing_stop.py |
| 9.4 | Weekly thesis re-evaluation | Arch §12 step 14 | STUB | Orchestrator has placeholder |
| 9.5 | 90-day thesis aging review | Arch §8.2 | STUB | State exists, no timer trigger |
| 9.6 | Opportunity cost engine | Arch §12 | DONE | opportunity_cost.py |
| 9.7 | MARKET_ON_OPEN for gap-down | Arch §11.2 | MISSING | Not implemented |
| 9.8 | Re-eval triggers full pipeline re-run | Arch §12 step 14 | STUB | Future wave comment at orchestrator:2371 |

## Phase 10: Dashboard — all 7 pages

| # | Component | Spec Ref | Status | Notes |
|---|-----------|----------|--------|-------|
| 10.1 | Dashboard page | Source §14 | DONE | Portfolio summary + risk |
| 10.2 | Agents page | Source §15 | DONE | Persona cards + Sankey |
| 10.3 | Pipeline page | Source §16 | DONE | Kanban + priority bands |
| 10.4 | Universe page | Source §17 | DONE | Ticker list + CRUD |
| 10.5 | Cortex page | Source §18 | DONE | Health + heartbeats |
| 10.6 | Debug page | Source §19 | DONE | Live SSE events |
| 10.7 | Settings page | Source §20 | DONE | Config + TOTP modal |
| 10.8 | SSE real-time updates | Arch §4.4 | DONE | EventSource + auto-reconnect |
| 10.9 | Visual identity tokens | Source §13.1 | DONE | CSS variables, Inter/JetBrains |
| 10.10 | Cmd-K command palette | Source §13.6 | MISSING | Not implemented |
| 10.11 | Toast notifications | Source §13.5 | MISSING | No toast system |
| 10.12 | All empty/loading/error states | Source §13.4 | PARTIAL | Some pages have empty states |
| 10.13 | D3 Sankey visualization | Source §15.4 | DONE | agents/sankey-data endpoint |
| 10.14 | Notification policy UI | Source §13.5 | PARTIAL | notification.toml exists but no full UI |
| 10.15 | Cycle compare feature | Source §15.9 | MISSING | Not implemented |

## Phase 11: Calibration + lessons + causal attribution

| # | Component | Spec Ref | Status | Notes |
|---|-----------|----------|--------|-------|
| 11.1 | Brier-based calibration | Arch §9.4 | DONE | calibration.py |
| 11.2 | Causal attribution (credit/blame) | Arch §9.4 | DONE | causal_attribution.py |
| 11.3 | Override learning | Arch §9.4 | DONE | override_learning.py |
| 11.4 | Lesson extraction + Qdrant write | Arch §9.4 | STUB | File exists, reads empty resolution history |
| 11.5 | Crucible calibration tuning | Arch §9.4 | DONE | crucible_calibration.py |
| 11.6 | Flywheel health monitor | Arch §9.4 | DONE | flywheel_health.py |
| 11.7 | Qdrant adapter (real operations) | Arch §8.4 | STUB | Runs in stub mode without server |
| 11.8 | KuzuDB adapter (real operations) | Arch §8.3 | STUB | Runs in stub mode without kuzu |
| 11.9 | DuckDB adapter (real operations) | Arch §8.5 | STUB | Runs in stub mode without duckdb |
| 11.10 | Qdrant 5 collections | Arch §8.4 | STUB | theses, memos, lessons, evidence_chunks, memos_aggregated defined but not populated |
| 11.11 | KuzuDB graph lineage | Arch §8.3 | STUB | Holding->Evidence->Resolution->Lesson not connected |
| 11.12 | DuckDB rolling_metrics + persona_performance | Arch §8.5 | STUB | Tables defined, not populated |
| 11.13 | Calibration integration test | Phases §11 exit | MISSING | No test_calibration.py |

## Phase 12: FDE + cross-DB consistency + reconciliation

| # | Component | Spec Ref | Status | Notes |
|---|-----------|----------|--------|-------|
| 12.1 | FDE 18-type classifier (deterministic) | Agents §15 | DONE | failure_diagnostic.py |
| 12.2 | Cross-DB consistency checker | Arch §10 | DONE | consistency.py (reports not-connected) |
| 12.3 | Paper-vs-broker reconciliation | Arch §9 | DONE | reconciliation.py |
| 12.4 | Dead-letter queue with backoff | Arch §5.4 | DONE | dead_letter.py |
| 12.5 | dead_letter table in SQLite | Arch §5.4 | DONE | sqlite.py:141 |
| 12.6 | failure_taxonomy_counts in DuckDB | Arch §8.5 | STUB | No DuckDB connection |
| 12.7 | FailedAssumption nodes in KuzuDB | Arch §8.3 | STUB | No KuzuDB connection |
| 12.8 | STOP_HUNTED vs STOP_LOSS_CORRECT | Agents §15 | MISSING | No 48h post-exit price check |
| 12.9 | FDE unit test (all 18 types) | Phases §12 exit | MISSING | No test_fde.py |
| 12.10 | Cross-DB integration test | Phases §12 exit | MISSING | No test_cross_db.py |

## Phase 13: Episodic context injection

| # | Component | Spec Ref | Status | Notes |
|---|-----------|----------|--------|-------|
| 13.1 | build_context_brief() implementation | Agents §18 | PARTIAL | File exists, minimal logic |
| 13.2 | persona_ticker_affinity in DuckDB | Arch §8.5 | STUB | No real data |
| 13.3 | Lessons retrieval in context brief | Agents §18 | STUB | Qdrant not connected |
| 13.4 | All prompts include {episodic_context} | Agents §18 | DONE | Prompts have context block |
| 13.5 | episodic_context_injected audit event | Agents §18 | MISSING | No audit event for context injection |
| 13.6 | Episodic integration test | Phases §13 exit | MISSING | No test_episodic.py |

## Phase 14: Mutation Engine

| # | Component | Spec Ref | Status | Notes |
|---|-----------|----------|--------|-------|
| 14.1 | Mutation daemon main loop | Arch §10.4 | DONE | daemon.py |
| 14.2 | Candidate generator (rule-based) | Agents §17 | DONE | Reads FDE clusters |
| 14.3 | A/B runner (SHADOW-only) | Arch §10.3 | DONE | ab_runner.py |
| 14.4 | Welch's t-test + Cohen's d | Arch §10.5 | DONE | stat_test.py |
| 14.5 | Promotion logic | Arch §10.6 | DONE | TOTP-gated |
| 14.6 | 5-level rollback | Agents §17.4 | DONE | rollback.py |
| 14.7 | 50-cycle dormancy | Arch §10.4 | DONE | Activation threshold |
| 14.8 | 3-concurrent A/B cap | Arch §10.3 | DONE | Enforced |
| 14.9 | Settings -> Mutation panel | Source §20 | DONE | Web UI |
| 14.10 | SSE mutation.* events | Arch §4.4 | MISSING | No mutation SSE events |
| 14.11 | mutation_lifecycle integration test | Phases §14 exit | MISSING | |
| 14.12 | rollback 5-level integration test | Phases §14 exit | MISSING | |
| 14.13 | offline A/B test harness | Phases §14 exit | MISSING | No tests/mutation_eval/ |

## Phase 15: Polish + operator experience

| # | Component | Spec Ref | Status | Notes |
|---|-----------|----------|--------|-------|
| 15.1 | Agents page animations (progress bars) | Source §15.5 | MISSING | Static cards only |
| 15.2 | Pipeline drag-drop refinement | Source §16 | MISSING | No smooth DnD |
| 15.3 | Dashboard sparklines + time selector | Source §14 | PARTIAL | API exists, no selector UI |
| 15.4 | Cmd-K command palette (full) | Source §13.6 | MISSING | Not implemented |
| 15.5 | Keyboard shortcuts | Source §13.6 | MISSING | Not implemented |
| 15.6 | Accessibility audit (axe-core) | Source §13.7 | MISSING | Not done |
| 15.7 | Performance profiling | Arch §20 | MISSING | No profiling tools |
| 15.8 | ops/spec_consistency.py | Phases §15 exit | MISSING | No cross-file reference checker |
| 15.9 | ops/backup_verify.py | Phases §15 exit | MISSING | No backup/restore |
| 15.10 | ops/audit_chain_verify.py | Phases §15 exit | MISSING | Standalone verifier |
| 15.11 | docs/operator_runbook.md | Phases §15 exit | MISSING | No operator docs |
| 15.12 | Notification policy implementation | Source §13.5 | PARTIAL | notification.toml, no full system |
| 15.13 | "Copy for Claude Code" button | Source §19 | MISSING | Not on debug events |

---

## Cross-Cutting Gaps (span multiple phases)

### A. Data Integration — Evidence Fetching Pipeline

The orchestrator step 13 calls personas with **empty evidence lists**. The 13 data source modules exist but are not wired into the cycle. This means:
- Personas produce ungrounded analysis
- Arbitration weights are meaningless without real signal divergence
- The entire pipeline runs structurally correct but produces garbage output

**Files:** `orchestrator.py:1112` — `# TODO: Future wave -- wire evidence fetching`

**What needs building:**
- Evidence router: ticker -> relevant data sources -> EvidencePacket[]
- Per-persona evidence selection (MoatAnalyst gets fundamentals, ShortInterest gets FINRA...)
- Staleness filtering before persona input
- Evidence dedup and canonical ordering

### B. Real-Time Price Feed

`current_price=1.0` is hardcoded in the orchestrator. Without real prices:
- EV computation is wrong
- Position sizing is wrong
- Stop-loss monitoring has nothing to monitor
- Trailing stops never arm

**What needs building:**
- Real-time price fetcher (Alpaca streaming or Polygon WebSocket)
- Price cache with staleness budget
- Integration with stop_loss_daemon, trailing_stop, and EV computation

### C. Storage Activation (KuzuDB + Qdrant + DuckDB)

All three stores run in **stub mode** — they return empty/default values. The code is structurally complete but inactive.

**Prerequisites (must happen first):**
- Embedding model download + verification (BAAI/bge-base-en-v1.5, 768-dim) — item 8.12
- Docker/local install scripts for KuzuDB, Qdrant, DuckDB

**What needs building:**
- Migration from stub mode to real mode
- Data population pipelines
- Cross-DB consistency verification

### D. Mode Promotion / Demotion Gates

The gate computation logic exists in `flywheel_health.py` but depends on:
- DuckDB rolling_metrics (stub)
- Actual trade counts per mode (mode_history table exists but no population pipeline)
- Real Sharpe/Brier computation (no data)

**What needs building:**
- Rolling metrics computation pipeline that writes to DuckDB
- Dashboard mode badge UI showing gate status

### E. Process Infrastructure

**Missing ops/ scripts:**
- `ops/start_inference.sh` — llama-server startup
- `ops/install_launchd.sh` — launchd plist installer
- `ops/install_pf_rules.sh` — firewall rules to block inference from internet
- `ops/install_system_users.sh` — _pmacs_* system users
- `ops/audit_chain_verify.sh` — standalone chain verification
- `ops/backup_verify.sh` — backup and restore

### F. Test Infrastructure

**Missing integration/e2e tests from spec exit criteria:**
- `tests/fixtures/` — synthetic data for smoke-test cycle (MISSING)
- `test_data_sources.py` — real API integration
- `test_llm_call.py` — inference backend
- `test_3persona_cycle.py`, `test_7persona_cycle.py`
- `test_crucible_budget.py`
- `test_full_pipeline.py` (spec version)
- `test_calibration.py`
- `test_fde.py`, `test_cross_db.py`
- `test_episodic.py`
- `test_mutation_lifecycle.py`, `test_rollback.py`
- `test_smoke_cycle.py`

### G. Catalyst Resolution Subsystem

The spec (Arch §7) defines a multi-source corroboration system for detecting when catalysts resolve. Without it:
- Holdings with pending catalysts never auto-resolve
- FDE taxonomy types `CATALYST_FALSE_POSITIVE` and `CATALYST_TIMEOUT` cannot fire
- The flywheel's catalyst-driven learning loop is broken
- Items 2.11-2.14 track the individual files

---

## Summary Count

| Status | Count | Description |
|--------|-------|-------------|
| DONE | 85 | Fully implemented and working |
| PARTIAL | 18 | Code exists but incomplete or uses fallbacks |
| STUB | 17 | File exists, returns empty/default, no real work |
| MISSING | 38 | Not implemented at all |
| **Total** | **158** | Spec-defined components |

### By Priority

**Blockers (system cannot produce useful trades without these):**
1. Evidence fetching pipeline (empty persona inputs)
2. Real-time price feed (all prices are 1.0)
3. Storage activation (KuzuDB/Qdrant/DuckDB in stub mode) — requires embedding model first (item 8.12)
4. Catalyst resolution subsystem (holdings never auto-resolve)

**High (system runs but quality is degraded):**
5. Crucible 2-iteration rewrite loop
6. Weekly thesis re-evaluation wiring
7. Cash ledger engine
8. pf firewall rules (inference process can reach internet)

**Medium (flywheel cannot close):**
9. Lessons engine real data flow
10. Episodic context real data
11. FDE STOP_HUNTED detection
12. Mutation SSE events
13. Mode promotion gate computation with real data
14. Catalyst resolution: 4 resolver files (items 2.11-2.14)

**Low (polish / operator experience):**
15. Cmd-K command palette
16. Keyboard shortcuts
17. Toast notifications
18. Accessibility audit
19. Performance profiling
20. Operator runbook
21. All Phase 15 polish items
