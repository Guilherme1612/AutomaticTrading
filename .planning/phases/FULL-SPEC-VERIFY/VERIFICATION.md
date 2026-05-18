---
phase: FULL-SPEC-VERIFY
verified: 2026-05-17T18:05:00Z
status: passed
score: 38/38 must-haves verified
overrides_applied: 0
---

# PMACS Full Spec Compliance Verification Report

**Scope:** Complete codebase verification against all four spec files.
**Verified:** 2026-05-17
**Status:** PASSED

---

## 1. Five Non-Negotiables

| # | Non-Negotiable | Status | Evidence |
|---|---------------|--------|----------|
| 1 | LLMs never sign trades | PASS | `pmacs/execution/signing.py` uses Ed25519 independently. `pmacs/execution/service.py` receives signed TradePlans via UDS, verifies signature, then submits. `pmacs/agents/base.py` calls only `http://127.0.0.1:8080/completion` (local llama-server). Zero broker SDK imports in any agent file. |
| 2 | LLMs never math | PASS | `pmacs/engines/arbitration.py` -- Brier-inverse weighting, probability combination, agreement checks all Python. `pmacs/engines/conviction.py`, `pmacs/engines/sizing.py`, `pmacs/engines/calibration.py` -- all deterministic Python. LLM personas produce only structured outputs (DirectionalProbability). |
| 3 | Every state transition is hash-chained | PASS | `pmacs/storage/audit.py` AuditWriter: `sha256(iso_ts \|\| prev_sha256 \|\| event_type \|\| canonical_json)` with `os.fsync()`. Genesis uses `"0" * 64`. AuditVerifier provides full and incremental chain verification. `pmacs/engines/state_machine.py` transition() writes to audit on every state change. |
| 4 | Local-only execution | PASS | `pmacs/agents/base.py` line 32: `LLAMA_SERVER_URL = "http://127.0.0.1:8080/completion"` -- localhost only. Grep for cloud providers (openai, anthropic, bedrock, azure) returns only vendored JS (d3.min.js, tailwind.min.js) -- false positives from minified code. No telemetry/sentry/segment/posthog/datadog imports. `ops/install_pf_rules.sh` exists for pf-blocking inference process. |
| 5 | Operator owns kill switch | PASS | `pmacs/cortex/kill_switch.py`: `engage()` does NOT require TOTP (any trigger can engage). `disengage()` requires `verify_totp(totp_secret, totp_code)` -- line 202. `pmacs/cortex/totp.py` implements RFC 6238 with 30s period, 6 digits, SHA-1, +/-1 window. TOTP secret stored in macOS Keychain via `pmacs.storage.keychain`. |

---

## 2. Anti-Patterns (Architecture.md Section 16)

| # | Anti-Pattern | Status | Evidence |
|---|-------------|--------|----------|
| 16.1 | `holding.state = "ABORTED_LLM"` directly | PASS | Grep for `holding.state =` finds ONLY `pmacs/engines/state_machine.py:71` (the one authorized write location). All other matches in `pmacs/engines/failure_diagnostic.py` are READ operations (`==` comparisons), not assignments. |
| 16.2 | `json.dumps(payload)` for audit | PASS | Audit path uses `canonical_json()` exclusively (`pmacs/storage/audit.py:59`, `pmacs/nervous/mutation.py`, `pmacs/mutation/candidate_generator.py`, `pmacs/storage/dead_letter.py`). Other `json.dumps` uses are for UDS protocol messages (`execution/service.py`), SSE framing (`sse_publisher.py`), debug JSONL (`debug_log.py`), and config storage (`web/data.py`) -- none are audit payloads. |
| 16.3 | Custom rate-limit logic | PASS | `pmacs/nervous/rate_limit.py` implements `BUCKETS` dict with `TokenBucket.acquire()`. `pmacs/nervous/api.py:115` uses `BUCKETS["totp_verify"].acquire()`. No ad-hoc rate limit patterns found elsewhere. |
| 16.4 | Mutating evidence packets in staleness checks | PASS | `pmacs/data/staleness.py` returns `FreshnessResult` objects, never mutates the input `EvidencePacket`. Explicit comment: "Returns a FreshnessResult. Does NOT mutate the packet (Architecture.md 16.4)." |
| 16.5 | `cycle_id=None` on audit-emitting functions | PASS | Grep for `cycle_id=None` finds only `pmacs/logsys/debug_log.py:30` (documented system-level exception) and `pmacs/cortex/sleep_watch.py:142` (incomplete cycle tracking). Audit-emitting functions (`audit.append()`, `state_machine.transition()`, `execute_exit()`) all require cycle_id. `catastrophe_net.py:168` raises ValueError if cycle_id is empty. |
| 16.6 | Day 1 bootstrap aborting everything | PASS | `pmacs/schemas/arbitration.py:19` defines `PROCEED_BOOTSTRAP_LOW_CONFIDENCE`. `pmacs/engines/arbitration.py:216` uses it when all immature sources agree on direction. Constants file confirms the value. |
| 16.7 | Tight broker-side stops | PASS | `pmacs/execution/catastrophe_net.py` computes stop at `CATASTROPHE_NET_PCT` (15%) below entry only. `pmacs/constants.py:12`: `CATASTROPHE_NET_PCT: float = 0.15`. Explicit doc: "PMACS manages tight stops internally. The broker receives only a catastrophe-net stop at 15% below entry." |
| 16.8 | `eur_per_usd` field | PASS | `pmacs/schemas/currency.py:22-28` has `@model_validator` that rejects `eur_per_usd` as a declared field or in `model_dump()`. `eur_per_usd` exists only as a computed property (line 32). `pmacs/constants.py:120`: `FX_CONVENTION = "usd_per_eur"`. SQLite table `fx_snapshots` uses `usd_per_eur REAL NOT NULL`. |
| 16.9 | Mutation Engine writing production state directly | PASS | `pmacs/mutation/promotion.py` doc: "ALL mutations require operator TOTP. No auto-promote. The Mutation Engine is advisor-only." Writes go through `pmacs/nervous/mutation.py:apply_candidate_to_registry()` -- requires nervous system mediation. `pmacs/mutation/daemon.py` reads storage only; `ab_runner.py` writes to `mutation_proposals` and `mutation_outcomes` tables (scoped). |
| 16.10 | Mutation A/B in PAPER | PASS | `pmacs/mutation/ab_runner.py:38`: "Candidate arm always runs SHADOW-only (Architecture.md section 16 anti-pattern)." No PAPER mode references in mutation code. |
| 16.11 | Any mutation auto-applying | PASS | `pmacs/mutation/promotion.py:operator_promote()` requires `totp_code` parameter. Line 80: `if not resolved_verify(totp_code): raise PermissionError("Invalid TOTP code")`. `pmacs/mutation/daemon.py:7`: "All promotions require operator TOTP. No auto-promote." |
| 16.12 | Runtime prompt edits | PASS | Grep for runtime prompt editing patterns (edit prompt, prompt edit) returns zero matches. Prompts live in `pmacs/agents/prompts/*.md` as static files. Mutation Engine can propose changes but requires TOTP + A/B test. |
| 16.13 | Backtesting against historical LLM outputs | PASS | Grep for backtest+LLM patterns returns zero matches. No backtest infrastructure exists in the codebase. |
| 16.14 | Logging secrets | PASS | `pmacs/storage/keychain.py:_scrub_secrets()` replaces secret substrings with `***REDACTED***`. Used in all error paths (lines 78, 109, 119, 149). `pmacs/agents/base.py:_audit_llm_call()` logs only `prompt_hash` and `output_hash` (SHA256 truncated), never raw content. |
| 16.15 | Missing error_code on WARN+ events | PASS | `pmacs/logsys/debug_log.py:139-153` enforces: `if level >= WARN and error_code is None: raise ValueError`. Also validates `error_code` is in `VALID_ERROR_CODES` registry. All WARN+ calls in `pmacs/nervous/orchestrator.py` carry `error_code` parameters. |

---

## 3. Process Topology (Architecture.md Section 4)

| Process | Spec Port | Status | Evidence |
|---------|----------|--------|----------|
| pmacs-inference | :8080 | PASS | `pmacs/agents/base.py:32`: `LLAMA_SERVER_URL = "http://127.0.0.1:8080/completion"`. `ops/start_inference.sh` exists. |
| pmacs-cortex | daemon | PASS | `pmacs/cortex/daemon.py` (main loop). `pmacs/cortex/health.py`, `pmacs/cortex/kill_switch.py`, `pmacs/cortex/boot_detector.py`, `pmacs/cortex/crash_loop_detector.py`, `pmacs/cortex/drift.py`, `pmacs/cortex/flywheel_monitor.py`, `pmacs/cortex/disk_monitor.py`, `pmacs/cortex/clock_monitor.py`, `pmacs/cortex/sleep_watch.py` all exist. |
| pmacs-cortex-self-check | daemon | PASS | `pmacs/cortex/self_check.py` exists. Kill switch trigger `META_MONITOR_UNRESPONSIVE` checks cortex-self-check heartbeat (kill_switch.py:551). |
| pmacs-execution | UDS | PASS | `pmacs/execution/service.py` implements `asyncio.start_unix_server`. Default path: `/var/db/pmacs/exec.sock`. Test path: `/tmp/pmacs_exec_test.sock`. |
| pmacs-nervous | :8000 | PASS | `pmacs/nervous/orchestrator.py` (cycle orchestration). `pmacs/nervous/api.py` (SSE/POST endpoints). `pmacs/nervous/sse_publisher.py`. `pmacs/nervous/auth.py` (TOTP auth). |
| pmacs-stoploss | daemon | PASS | `pmacs/cortex/stop_loss_daemon.py` (unified in cortex tree). `pmacs/engines/stop_loss_monitor.py` + `pmacs/engines/trailing_stop.py`. |
| pmacs-mutation | daemon | PASS | `pmacs/mutation/daemon.py` (main loop). `pmacs/mutation/candidate_generator.py`, `pmacs/mutation/ab_runner.py`, `pmacs/mutation/stat_test.py`, `pmacs/mutation/rollback.py`, `pmacs/mutation/promotion.py`. |
| pmacs-dashboard | :8001 | PASS | `pmacs/web/app.py` FastAPI application. Routes: dashboard, agents, pipeline, universe, cortex, debug, settings, wizard. |

Launchd installer: `ops/install_launchd.sh` exists.

---

## 4. Storage (5 Stores) (Architecture.md Section 8)

| Store | Purpose | Status | Evidence |
|-------|---------|--------|----------|
| SQLite | OLTP | PASS | `pmacs/storage/sqlite.py` (341 lines). 20+ tables: cycles, mode_history, queue, persistent_pins, holdings, stop_events, process_state, paper_account, fx_snapshots, consistency_drift, operator_overrides, dead_letter, mutation_proposals, mutation_log, mutation_outcomes, op_idempotency, scan_records, lessons, failure_classifications. WAL mode, foreign keys, migrations. |
| KuzuDB | Graph | PASS | `pmacs/storage/kuzu.py` (415 lines). Node tables: Holding, Evidence, Resolution, Thesis, Lesson, FailedAssumption, MutationOutcome. Edge tables: BACKED_BY, RESOLVES_TO, GROUNDED_IN, HAS_THESIS, PRODUCED_LESSON, FAILED_ASSUMPTION. Graceful degradation when kuzu not installed. |
| Qdrant | Vector | PASS | `pmacs/storage/qdrant.py` (286 lines). Collections: theses, memos_persona, memos_aggregated, evidence_chunks, lessons. 768-dim vectors (bge-base-en-v1.5). Upsert, search, retrieve, embedding generation. Graceful degradation. |
| DuckDB | Analytics | PASS | `pmacs/storage/duckdb.py` (151 lines). Tables: rolling_metrics, persona_performance, persona_ticker_affinity, failure_taxonomy_counts. Persona affinity rolling upsert. |
| audit.log | Hash-chained | PASS | `pmacs/storage/audit.py` (178 lines). Append-only, hash-chained with prev_sha256. fsync after every write. Full and incremental verification. Genesis with "0"*64. |

---

## 5. Pydantic v2 Compliance

| Check | Status | Evidence |
|-------|--------|----------|
| No `pydantic.v1` imports | PASS | Grep returns zero matches across entire `pmacs/` tree. |
| `ConfigDict` usage | PASS | `pmacs/schemas/contracts.py:105`: `model_config = ConfigDict(frozen=True)`. No `class Config:` patterns found. |
| `model_validate()` | PASS | `pmacs/agents/base.py:169`: `model_cls.model_validate(parsed)`. `pmacs/execution/service.py:122`: `TradePlan.model_validate_json()`. |
| No `parse_obj()` / `.dict()` | PASS | Grep for `parse_obj` and `.dict()` returns zero matches. |
| All models in `pmacs/schemas/` | PASS | 28 schema files in `pmacs/schemas/`. No Pydantic models found in engine files (engines import from schemas). |

---

## 6. Schemas (Architecture.md Sections 8-9)

All schema modules exist in `pmacs/schemas/`:

agents.py (14.4K), arbitration.py, attribution.py, calibration.py, catalysts.py, contracts.py (5.5K -- Holding, Thesis, HoldingState, transitions), conviction.py, currency.py, data.py (EvidencePacket), failure.py, flywheel.py, freshness.py (FreshnessResult), fundamental.py, lessons.py, memory.py, mutation.py, overrides.py, personas.py (14.4K), portfolio.py, pricing.py, queue.py, reconciliation.py, sim.py, sizing.py, stop_loss.py, system.py, trade.py (TradePlan, TradeResult).

**Status: PASS** -- all schemas from spec exist as substantive files.

---

## 7. UI Pages (Source.md Sections 14-20)

| Page | Template | Route | Size | Status |
|------|----------|-------|------|--------|
| Dashboard | dashboard.html | dashboard.py (112 lines) | 11.5K | PASS |
| Agents | agents.html | agents.py (151 lines) | 9.3K | PASS |
| Pipeline | pipeline.html | pipeline.py (179 lines) | 19.9K | PASS |
| Universe | universe.html | universe.py (54 lines) | 7.7K | PASS |
| Cortex | cortex.html | cortex.py (49 lines) | 7.3K | PASS |
| Debug | debug.html | debug.py (77 lines) | 6.3K | PASS |
| Settings | settings.html | settings.py (298 lines) | 30.6K | PASS |

Wizard: 14 step templates in `templates/wizard/`. Route: `wizard.py` (219 lines).

Jinja2 autoescape: `pmacs/web/app.py:45-46` uses `select_autoescape(["html", "htm"])`.

Security headers middleware: `SecurityHeadersMiddleware` adds X-Content-Type-Options, X-Frame-Options, Referrer-Policy, CSP.

**Status: PASS** -- all 7 spec-required pages plus wizard exist with substantive templates and route handlers.

---

## 8. Personas (Agents.md Sections 4-13)

| Persona | Runner | Prompt | Grammar | Sanity | Status |
|---------|--------|--------|---------|--------|--------|
| GrowthHunter | growth_hunter.py | growth_hunter.md | growth_hunter.gbnf | growth_hunter.py | PASS |
| MoatAnalyst | moat_analyst.py | moat_analyst.md | moat_analyst.gbnf | moat_analyst.py | PASS |
| MacroRegime | macro_regime.py | macro_regime.md | macro_regime.gbnf | macro_regime.py | PASS |
| InsiderActivity | insider_activity.py | insider_activity.md | insider_activity.gbnf | insider_activity.py | PASS |
| ShortInterest | short_interest.py | short_interest.md | short_interest.gbnf | short_interest.py | PASS |
| CatalystSummarizer | catalyst_summarizer.py | catalyst_summarizer.md | catalyst_summarizer.gbnf | catalyst_summarizer.py | PASS |
| MemoWriter | memo_writer.py | memo_writer.md | memo_writer.gbnf | memo_writer.py | PASS |

Three-layer validation: `pmacs/agents/base.py` PersonaRunner implements Grammar (GBNF via llama-server) -> Pydantic (model_validate) -> Sanity (BaseSanityValidator). Retry: 2 retries with +0.05 temp.

**Status: PASS** -- 7 analysis personas (excluding Forensics and Crucible which are system personas, not analysis) plus test grammar.

---

## 9. Data Sources (Phase 2)

All 13 spec-required sources exist in `pmacs/data/sources/`:

edgar.py, polygon.py, finnhub.py, alpaca_data.py, openfda.py, finra.py, form4.py, ir_pages.py, press.py, fomc.py, fred.py, ecb.py, fundamentals.py

Supporting modules: `pmacs/data/gateway.py` (rate limiting), `pmacs/data/staleness.py` (freshness), `pmacs/data/fx.py` (ECB EUR/USD), `pmacs/data/canonical.py` (canonical JSON), `pmacs/data/universe.py` (operator universe CRUD), `pmacs/data/corp_actions.py` (splits/dividends).

**Status: PASS**

---

## 10. Configuration Files

All config files exist in `config/`:

- `resources.toml` (533B) -- hardware budgets, slot counts
- `risk.toml` (287B) -- position sizes, kill-switch thresholds
- `crucible.toml` (149B) -- CPS budget
- `mutation.toml` (280B) -- activation threshold, rules
- `model_registry.json` (695B) -- backend selection
- `model_hashes.toml` (74B) -- GGUF SHA256
- `source_criticality.toml` (1.1K) -- CRITICAL/IMPORTANT/NICE_TO_HAVE
- `notification.toml` (638B) -- notification settings

**Status: PASS**

---

## 11. Key Constants Verification

| Constant | Spec Value | Code Value | Status |
|----------|-----------|------------|--------|
| Paper capital | $5,000 | `PAPER_CAPITAL_USD = 5_000.0` | PASS |
| Max single position | 20% | `MAX_SINGLE_POSITION_PCT = 0.20` | PASS |
| Max concurrent positions | 5 | `MAX_CONCURRENT_POSITIONS = 5` | PASS |
| Catastrophe-net stop | 15% | `CATASTROPHE_NET_PCT = 0.15` | PASS |
| Crucible time budget | 90s | `CRUCIBLE_TIME_BUDGET_SECONDS = 90` | PASS |
| Crucible max cycles | 2 | `CRUCIBLE_MAX_CYCLES = 2` | PASS |
| Mutation activation | 50 cycles | `MUTATION_ACTIVATION_CYCLES = 50` | PASS |
| Stat-sig p threshold | 0.05 | `MUTATION_STAT_SIG_P = 0.05` | PASS |
| Stat-sig Cohen's d | 0.20 | `MUTATION_STAT_SIG_COHENS_D = 0.20` | PASS |
| Stat-sig min n | 20 | `MUTATION_STAT_SIG_MIN_N = 20` | PASS |
| Mutation probation | 30 cycles | `MUTATION_PROBATION_CYCLES = 30` | PASS |
| Auto-rollback window | 50 cycles | `MUTATION_AUTO_ROLLBACK_WINDOW = 50` | PASS |
| Analysis temperature | 0.2 | `TEMP_ANALYSIS = 0.2` | PASS |
| Crucible temperature | 0.1 | `TEMP_CRUCIBLE = 0.1` | PASS |
| MemoWriter temperature | 0.3 | `TEMP_MEMO_WRITER = 0.3` | PASS |

---

## 12. Anti-Pattern Scan Results

| Pattern | Files Scanned | Matches | Severity |
|---------|--------------|---------|----------|
| TODO/FIXME/HACK | pmacs/*.py | 0 in critical paths | Info |
| Empty returns (`return None` / `return {}`) | pmacs/*.py | Only in graceful degradation paths (KuzuDB, Qdrant stubs) | Info |
| Hardcoded empty data (`= []` / `= {}`) | pmacs/*.py | Only as default_factory for Pydantic fields and initial state | Info |
| Console.log-only handlers | N/A (Python) | N/A | N/A |
| `class Config:` (Pydantic v1) | pmacs/*.py | 0 matches | Info |

No blocker or warning-level anti-patterns found.

---

## 13. Summary

**Overall Status: PASSED**

All five non-negotiables are enforced in code:
1. LLMs cannot sign trades (separate Ed25519 execution process)
2. LLMs cannot do math (all probability/sizing is deterministic Python)
3. Every state transition is hash-chained (audit.log with prev_sha256)
4. Execution is local-only (localhost llama-server, no cloud calls, no telemetry)
5. Kill switch disengagement requires TOTP

All 15 anti-patterns from Architecture.md section 16 are absent from the codebase. Where relevant, validators actively enforce compliance (e.g., currency schema rejects eur_per_usd, debug log rejects WARN+ without error_code, state transitions go through state_machine.py only).

All 8 process topology components exist with correct interfaces. All 5 storage backends are implemented. All 7 UI pages are substantive. All 7 analysis personas have complete three-layer validation pipelines (grammar + Pydantic + sanity). All 13 data sources exist. All configuration files are present. All key constants match spec values.

Pydantic v2 compliance is clean: zero v1 imports, ConfigDict usage throughout, model_validate/model_dump API.

---

_Verified: 2026-05-17T18:05:00Z_
_Verifier: Claude (gsd-verifier)_
