# Codebase Concerns

**Analysis Date:** 2026-05-28

## Tech Debt

**Execution Service uses HTTP instead of UDS:**
- Issue: Spec (`Architecture.md §4.5`) requires Unix Domain Socket for execution service communication. Implementation uses HTTP-based communication.
- Files: `pmacs/execution/service.py`
- Impact: Reduced security isolation between nervous and execution processes. UDS provides filesystem-level access control; HTTP does not.
- Fix approach: Add a Unix Domain Socket transport layer to `ExecutionService`, keeping HTTP as fallback for test mode. Update `pmacs/nervous/orchestrator.py` to connect via UDS path.

**Memory engine is a stub (always returns None):**
- Issue: `pmacs/engines/memory.py` (785 bytes) is a placeholder that never detects antipatterns.
- Files: `pmacs/engines/memory.py`
- Impact: The cycle's antipattern check (Architecture.md section 12 step 13) is a no-op. No protection against repetitive thesis patterns or stale thesis recycling.
- Fix approach: Implement pattern detection against Qdrant thesis embeddings. Compare new thesis hash/embedding against recent cycles; flag if cosine similarity > 0.95 to a thesis from the last 5 cycles on the same ticker.

**Corporate actions module is a stub:**
- Issue: `pmacs/data/corp_actions.py` exists but has no real data source integration.
- Files: `pmacs/data/corp_actions.py`
- Impact: Stock splits, dividends, and mergers are not handled. Split-adjusted prices will be wrong, leading to incorrect position sizes and stop-loss levels.
- Fix approach: Integrate Polygon corporate actions API or Alpaca asset endpoint for split/dividend data.

**Dashboard not running as separate read-only process:**
- Issue: Spec (`Architecture.md section 2.2`) requires `pmacs-dashboard` to run as a separate process with read-only SQLite access (`mode=ro`). The dashboard should have no filesystem write permissions.
- Files: `pmacs/web/app.py`, `pmacs/nervous/api.py`
- Impact: Security defense-in-depth gap. A vulnerability in dashboard HTTP handling could escalate to write access.
- Fix approach: Ensure the dashboard FastAPI app opens SQLite with `mode=ro` and that write actions route through `pmacs-nervous` POST endpoints only. Verify filesystem permissions deny write access to the dashboard user.

**Storage triple (KuzuDB + Qdrant + DuckDB) in graceful-degradation stub mode:**
- Issue: All three advanced stores degrade to stubs when their respective servers are not running. Many engine outputs (calibration, lessons, episodic context, FDE persistence, mutation A/B metrics) produce no-ops.
- Files: `pmacs/storage/kuzu.py`, `pmacs/storage/qdrant.py`, `pmacs/storage/duckdb.py`
- Impact: The flywheel (calibration, lessons, episodic context, FDE persistence, mutation A/B metrics) cannot function meaningfully without these stores. The system runs but does not learn.
- Fix approach: Operational -- install and configure KuzuDB, Qdrant, and DuckDB servers. The adapters already support real operations when services are available.

## Known Bugs

**No known production bugs at this time.** The system runs in SHADOW + PAPER mode only. Live trading is not enabled.

## Security Considerations

**TOTP secret storage:**
- Risk: TOTP secrets must be stored in macOS Keychain (per `Architecture.md section 1.3`). If Keychain is unavailable and fallback is used, secrets could leak.
- Files: `pmacs/cortex/totp.py`, `pmacs/storage/keychain.py`
- Current mitigation: Keychain wrapper in `pmacs/storage/keychain.py` with graceful fallback.
- Recommendations: Verify all TOTP secret reads go through Keychain. CI should grep for hardcoded TOTP secrets or `.env` reads of TOTP values.

**CSRF middleware disabled in test mode:**
- Risk: `_CSRF_ENABLED = "pytest" not in _sys.modules` disables CSRF checks during testing.
- Files: `pmacs/web/app.py` line 23
- Current mitigation: Only affects test runs, not production.
- Recommendations: Acceptable for test mode, but ensure production builds never import pytest.

**Execution signing key must be guarded for non-LIVE modes:**
- Risk: Dummy signing keys used in SHADOW/PAPER mode must never be used in LIVE mode.
- Files: `pmacs/execution/signing.py`
- Current mitigation: Assert guard added for non-LIVE modes (per MISSING.md L1 fix).
- Recommendations: Add a CI check that the signing key path is distinct per mode.

## Performance Bottlenecks

**Orchestrator per-symbol pipeline was monolithic (now decomposed):**
- Problem: The per-symbol pipeline method was 1085 lines. Decomposed into 7 sub-methods.
- Files: `pmacs/nervous/orchestrator.py`
- Cause: Organic growth across build phases.
- Improvement path: Further extraction of sub-methods into a `SymbolPipeline` class with its own state.

**Per-symbol persona dispatch 270s hard cap:**
- Problem: If all 7 personas plus Crucible run on a single ticker, wall-clock can approach 270s.
- Files: `pmacs/nervous/orchestrator.py`
- Cause: Sequential dispatch with 3 inference slots, plus Crucible 2-cycle loop (180s budget).
- Improvement path: Ensure parallel slot dispatch is fully utilized. Profile actual persona call times.

**DuckDB queries for flywheel health:**
- Problem: `flywheel_health.py` opens a new SQLite connection per metric query.
- Files: `pmacs/engines/flywheel_health.py`
- Cause: Helper functions create individual connections.
- Improvement path: Pass a single connection through the call chain, or use a connection pool.

## Fragile Areas

**Holding state machine transition table:**
- Files: `pmacs/schemas/contracts.py` (VALID_TRANSITIONS dict)
- Why fragile: 24 states with complex transition graph. Adding new states requires updating multiple frozensets. A missing transition = `InvalidStateTransition` at runtime.
- Safe modification: Add new states to the enum, then update `VALID_TRANSITIONS`, `TERMINAL_STATES`, `ABORT_STATES`, and `ABORT_REASON_STATES` as appropriate. Run `test_state_machine.py` after any change.
- Test coverage: High -- `tests/unit/test_state_machine.py` covers all valid and invalid transitions.

**Orchestrator cycle sequence:**
- Files: `pmacs/nervous/orchestrator.py`
- Why fragile: 30-step sequence with ordering invariants. Steps must run in exact order per `Architecture.md section 12`.
- Safe modification: Each step is a separate method. Modify one step at a time. The checkpoint system allows resume from the last completed step.
- Test coverage: Integration tests cover full cycle, 3-persona, 7-persona, and smoke-test cycles.

**Arbitration engine weights:**
- Files: `pmacs/engines/arbitration.py`
- Why fragile: Brier-inverse weighting, extreme-probability dampening, and MacroRegime 0.5x multiplier are all tuned constants.
- Safe modification: Constants are named and documented. Changes should be validated against `test_arbitration.py`.
- Test coverage: Unit tests cover weighted combination, bootstrap handling, and disagreement detection.

## Scaling Limits

**Maximum concurrent positions: 5:**
- Current capacity: 5 positions at 20% each = 100% of $5K paper capital
- Limit: Hardcoded in `risk.toml` and enforced by `portfolio_risk_gate.py`
- Scaling path: Increase via Settings page (TOTP-gated) when capital increases

**Maximum concurrent A/B tests: 3:**
- Current capacity: 3 concurrent mutation A/B tests
- Limit: Enforced by `pmacs/mutation/daemon.py`
- Scaling path: Increase in `config/mutation.toml` if needed

**Inference slots: 3:**
- Current capacity: 3 parallel persona calls to llama-server
- Limit: Constrained by local GPU/VRAM
- Scaling path: Configured in `config/resources.toml`

## Dependencies at Risk

**kuzu Python package:**
- Risk: Relatively new graph database. API stability not guaranteed across minor versions.
- Impact: KuzuDB adapter may break on package upgrade.
- Migration plan: Pin version in `pyproject.toml`. Test adapter on every upgrade.

**sentence-transformers (for BAAI/bge-base-en-v1.5):**
- Risk: Heavy dependency (~2GB model files). Version compatibility with PyTorch.
- Impact: Embedding generation fails if model cannot load.
- Migration plan: Pin both `sentence-transformers` and `torch` versions. Verify embedding dimensions in tests.

## Missing Critical Features

**Audit log replication (offsite):**
- Problem: Spec (`Architecture.md section 5.3`) requires hourly rsync to offsite with chain verification and 24h kill-switch escalation. Not implemented.
- Blocks: Full trust contract. Without offsite replication, a disk failure destroys the audit chain.

**Audit log daily rotation:**
- Problem: Spec requires daily rotation of audit files with chain spanning rotations. Current implementation has size-based rotation (50MB) but not daily date-stamped rotation.
- Files: `pmacs/storage/audit.py`
- Blocks: Proper audit log management for long-running systems.

## Test Coverage Gaps

**Real API integration tests (data sources):**
- What's not tested: Actual HTTP calls to the 13 data source APIs (Polygon, EDGAR, Finnhub, etc.)
- Files: `tests/integration/test_data_sources.py` uses mocks
- Risk: API contract changes or authentication failures go undetected until runtime
- Priority: Medium -- mock tests are adequate for development; real API tests should run in CI nightly

**Live inference backend integration test:**
- What's not tested: Actual llama-server call with GGUF model loaded
- Files: `tests/integration/test_llm_call.py`
- Risk: GBNF grammar may not constrain output as expected with the real model
- Priority: High -- this is the core LLM integration

**Cross-DB consistency in production mode:**
- What's not tested: Cross-database reconciliation with real KuzuDB, Qdrant, and DuckDB connections
- Files: `tests/integration/test_cross_db.py`
- Risk: Mismatches between stores may go undetected
- Priority: High -- the flywheel depends on cross-store consistency

**Stop-loss 48h price recovery check:**
- What's not tested: The STOP_HUNTED vs STOP_LOSS_CORRECT differentiation requires checking price 48h after exit. Logic exists but requires real price history.
- Files: `pmacs/engines/failure_diagnostic.py`, `tests/unit/test_fde.py`
- Risk: Classification may default to STOP_LOSS_CORRECT when price data is unavailable
- Priority: Medium -- only affects flywheel learning quality

## Post-Spec Features (NOT in spec files)

These features exist in the codebase but are NOT defined in any of the 4 spec files:

**Billing / Token-cost accounting:**
- Files: `pmacs/billing/` (8 files: `budget_enforcer.py`, `cost_calculator.py`, `drift_monitor.py`, `period_roller.py`, `pricing.py`, `reconciler.py`, `token_estimator.py`, `usage_logger.py`)
- Schema: `pmacs/schemas/billing.py`
- Note: Tracks LLM token usage and cost. Not mentioned in `Source.md`, `Architecture.md`, `Agents.md`, or `Phases.md`. The spec references no billing or cost-tracking subsystem.

**Cash ledger engine:**
- Files: `pmacs/engines/cash_ledger.py`
- Note: Tracks paper account cash balance separately from the position ledger. Referenced in MISSING.md as "DONE" but not in any spec file.

**Simulation mode (deterministic LLM outputs):**
- Files: `pmacs/agents/simulation.py`
- Note: Provides deterministic persona outputs for when llama-server is unavailable. Not specified in any spec file.

**Cycle compare feature:**
- Files: `pmacs/web/routes/compare.py`, `pmacs/web/templates/compare.html`
- Note: Listed in MISSING.md as DONE per `Source.md section 15.9` but this section does not exist in the spec. The compare route and template are post-spec additions.

**Ops profiling tools:**
- Files: `ops/profile_cycle.py`, `ops/profile_memory.py`, `ops/profile_system.py`
- Note: Performance profiling scripts. The spec (`Architecture.md section 20`) mentions performance budgets but does not define profiling scripts.

**Cost widget and cost settings:**
- Files: `pmacs/web/templates/cost_settings.html`, `pmacs/web/templates/cost_widget.html`
- Note: UI components for the billing subsystem. Not in spec.

**Data resolution subsystem:**
- Files: `pmacs/data/resolution/detector.py`, `pmacs/data/evidence_router.py`
- Note: Catalyst detection and evidence routing infrastructure. Referenced by `Architecture.md section 7` but with less detail than implemented.

**Nervous rate limiter:**
- Files: `pmacs/nervous/rate_limit.py`
- Note: Token bucket rate limiting for nervous API endpoints. Not explicitly specified in Architecture.md but aligns with the general rate-limit anti-pattern.

---

*Concerns audit: 2026-05-28*
