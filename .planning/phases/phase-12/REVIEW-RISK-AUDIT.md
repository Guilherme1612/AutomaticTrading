# Risk Audit -- Phase 12

## Summary

Phase 12 replaces stubs with real implementations across all five storage backends simultaneously while also activating the evidence pipeline, real-time pricing, catalyst resolution, and multiple engine completions. This is the highest-risk phase in the project because a failure mid-migration leaves the system in an inconsistent state where some stores have real data and others still return empty defaults, with no migration path to unwind.

## Risk Register

### [CRITICAL] RA-01: Storage Migration Has No Rollback Path
- **Plan ref:** Wave 2.2-2.4
- **Category:** Operational / Data Integrity
- **Description:** The plan migrates KuzuDB, Qdrant, and DuckDB from stub mode to real operations in a single pass per store. Each adapter uses lazy initialization (`_ensure_connection` / `_get_conn`) that permanently switches from "return empty" to "write to database" once the dependency is installed and connected. There is no mechanism to revert to stub mode after real data has been written. If any one of the three stores fails to initialize (e.g., KuzuDB Cypher DDL error, Qdrant port conflict, DuckDB file lock), the system enters a split-brain state where, for example, KuzuDB has real lineage edges but Qdrant has no vectors and DuckDB has no metrics. The orchestrator does not validate all three stores initialized successfully before proceeding with writes.
- **Likelihood:** MEDIUM
- **Impact:** HIGH
- **Mitigation:** Add a `_storage_health_check()` at orchestrator startup that verifies all three adapters can perform a read/write round-trip. If any store fails, either (a) block cycle execution with a clear error, or (b) degrade gracefully to stub for the failed store only. Add a migration state flag in SQLite (`storage_migration_state` table) tracking which stores have completed migration, so a partial migration can be detected and resumed.
- **Residual Risk:** MEDIUM -- split-brain is preventable but requires new code not in the plan.

### [CRITICAL] RA-02: Orchestrator Audit Chain Breaks During Partial Cycle Failure
- **Plan ref:** Wave 1.1, 1.2
- **Category:** Data Integrity
- **Description:** The orchestrator creates a new `AuditWriter` instance for each audit write in many code paths (e.g., lines 1962-1987 in orchestrator.py create a fresh `AuditWriter` per trade execution, and `_close_cycle_aborted` creates another). Each `AuditWriter.__init__` calls `_recover_last_sha()` which reads the entire file to find the last hash. If two audit writes happen concurrently (e.g., from parallel persona slots in `ThreadPoolExecutor`), both writers may read the same `_prev_sha`, produce the same hash chain link, and break chain integrity. The `AuditWriter` is not thread-safe -- it has no locking mechanism around `_prev_sha` state.
- **Likelihood:** MEDIUM -- persona dispatch uses ThreadPoolExecutor with 3 workers, but audit writes happen in the main thread after dispatch completes. However, `_step_weekly_reeval` (lines 2658-2844) creates AuditWriter instances inside a loop that could overlap with SSE publishing or signal handlers.
- **Impact:** HIGH -- broken hash chain violates Non-Negotiable #3.
- **Mitigation:** Make AuditWriter thread-safe with a file-level lock (`fcntl.flock` on the audit file) or use a singleton AuditWriter with internal locking. Alternatively, ensure all audit writes go through a single-threaded queue. The plan should explicitly test concurrent audit chain writes.
- **Residual Risk:** LOW after fix, but the plan does not address this.

### [CRITICAL] RA-03: Deterministic Paper Signing Key Is a Security Weakness
- **Plan ref:** Wave 3 (existing code, not planned change, but relevant)
- **Category:** Security
- **Description:** Lines 1864-1867 of `orchestrator.py` derive the Ed25519 signing key from `hashlib.sha256(b"pmacs_paper_mode").digest()[:32]`. This key is hardcoded and publicly knowable. Anyone who reads the source code can forge trade signatures in paper mode. While this is paper trading ($5K), the spec (Non-Negotiable #1: "LLMs never sign trades") and the audit chain rely on signature integrity. If the system is ever in a state where paper and live modes coexist (e.g., during mode promotion testing), a confused deputy could accept a forged paper signature as valid.
- **Likelihood:** LOW in practice (single-operator, local-only), but violates the spirit of Non-Negotiable #1.
- **Impact:** MEDIUM -- undermines audit chain trust for paper mode.
- **Mitigation:** Generate a random signing key at wizard install time and store it in macOS Keychain (the Keychain wrapper already exists at `pmacs/security/keychain.py`). Fall back to the deterministic key only in unit test mode. The plan should add this to Wave 3 or Wave 5.
- **Residual Risk:** LOW -- single-operator system limits blast radius.

### [HIGH] RA-04: Price Fallback to 1.0 Silently Corrupts EV and Sizing
- **Plan ref:** Wave 1.2, 1.4
- **Category:** Operational / Data Integrity
- **Description:** When the price cache returns `None`, `current_price` falls back to `1.0` (orchestrator line 1168). This default then flows into `compute_ev()` (which uses it as `current_price`), `size_position()` (which divides by it to get share count), and `stop_price` calculation (which multiplies it). A price of $1.0 for a $50 stock means: (a) sizing computes ~5000 shares instead of ~100, (b) stop is set at $0.85 instead of $42.50, (c) position_size_usd is correct but entry_price_usd is catastrophically wrong. The WARN log at line 1175 is emitted but execution continues. The plan says "Wire into: stop_loss_daemon, trailing_stop, pricing engine, sizing engine" but does not address what happens when the price feed is unavailable for some tickers but not others.
- **Likelihood:** MEDIUM -- API rate limits, network issues, or Alpaca maintenance windows can cause price fetch failures.
- **Impact:** HIGH -- wrong number of shares, wrong stop price, potential for oversized positions.
- **Mitigation:** When `current_price` is the fallback `1.0`, the symbol should be ABORTED with a clear error code (e.g., `PRICE_UNAVAILABLE`), not processed with a bogus price. Add a guard: `if current_price <= 1.0: abort with DATA_UNAVAILABLE`.
- **Residual Risk:** LOW after fix.

### [HIGH] RA-05: Weekly Re-Eval Creates New Event Loop Per Holding
- **Plan ref:** Wave 3.2
- **Category:** Operational / Performance
- **Description:** `_step_weekly_reeval` (line 2658) calls `_dispatch_personas_with_timeout` for each holding needing re-evaluation. That method internally calls `_dispatch_personas` which creates a `ThreadPoolExecutor(max_workers=3)`. For 5 active holdings, this creates 5 executor pools with 3 threads each = 15 threads, plus the LLM inference calls. With a 16-ticker universe and 5 concurrent positions, re-eval could attempt to make 35 LLM calls (7 personas x 5 tickers) within the post-cycle step. The plan does not mention resource budgets for re-eval, and the `resources.toml` allocates only 3 parallel slots and 270s per symbol.
- **Likelihood:** HIGH -- with 5 active positions, this will trigger every cycle after 7 days.
- **Impact:** MEDIUM -- could exceed daily LLM time budget (18,000s from `resources.toml`) or cause inference process overload.
- **Mitigation:** Add re-eval concurrency limits: process re-eval holdings sequentially, not in parallel. Cap re-eval LLM calls per cycle (e.g., max 2 holdings re-evaluated per cycle). Add a budget check before each re-eval invocation.
- **Residual Risk:** MEDIUM -- needs explicit budget allocation in config.

### [HIGH] RA-06: KuzuDB Cypher Query Parameter Binding Incompatibility
- **Plan ref:** Wave 2.2
- **Category:** Operational
- **Description:** The KuzuDB adapter uses parameterized Cypher queries (e.g., `self._conn.execute(query, params or {})` at line 170). However, KuzuDB's Python API (`kuzu.Connection.execute()`) accepts parameters as a list, not a dict. The code passes `{"id": fa_id, "tax": taxonomy, ...}` but KuzuDB expects `[$id, $tax, ...]` with positional parameters. This will cause `TypeError` on every real write operation once KuzuDB is installed. The stub mode hides this because `_ensure_connection()` returns `False` and the code returns early without executing the Cypher.
- **Likelihood:** HIGH -- every write method in KuzuDBAdapter uses dict params.
- **Impact:** HIGH -- no KuzuDB data will be written; lineage tracking silently fails.
- **Mitigation:** Verify KuzuDB Python API parameter binding format before activation. Test with `kuzu.Connection.execute("CREATE (n:Test {id: $id})", {"id": "test"})` to confirm. If KuzuDB requires positional params, refactor all `execute()` calls to use `$1, $2, ...` with list params.
- **Residual Risk:** LOW after verification, but this is a silent failure mode that stub mode masks.

### [HIGH] RA-07: DuckDB File Lock Contention Between Orchestrator and Dashboard
- **Plan ref:** Wave 2.4
- **Category:** Operational
- **Description:** DuckDB uses a single-file database (`pmacs_analytics.duckdb`). The orchestrator writes to it during cycles (via `DuckDBAdapter`), and the dashboard reads from it for analytics displays. DuckDB supports concurrent reads but only one writer at a time. If the dashboard queries DuckDB while the orchestrator is mid-write, DuckDB will throw a "Catalog Error: database is locked" or similar. The current code opens a new connection per operation (`_get_conn()` returns a singleton connection but does not handle lock errors).
- **Likelihood:** MEDIUM -- dashboard reads are infrequent but will happen during cycles.
- **Impact:** MEDIUM -- dashboard queries fail; orchestrator writes may fail if dashboard holds a read lock.
- **Mitigation:** Use DuckDB's `READ_ONLY` mode for dashboard queries. Add retry logic with exponential backoff for write operations. Consider WAL mode if DuckDB supports it.
- **Residual Risk:** LOW after READ_ONLY mode for dashboard.

### [HIGH] RA-08: Missing Embedding Model Download Verification Step
- **Plan ref:** Wave 2.1
- **Category:** Operational
- **Description:** The plan says "Download BAAI/bge-base-en-v1.5 (~420MB) via sentence-transformers" but does not specify: (a) where the model is cached, (b) how to verify the download integrity, (c) what happens on first run without internet (the inference process is pf-blocked from internet per Non-Negotiable #4). If `sentence-transformers` downloads the model on first call, it will fail because the orchestrator runs in a process that may or may not have internet access. The model download must happen during the wizard/install step, not at runtime.
- **Likelihood:** HIGH -- first run after Wave 2.1 will attempt download at runtime.
- **Impact:** HIGH -- QdrantAdapter falls back to hash-based dummy vectors (line 273 of qdrant.py), which are not semantically meaningful. All similarity searches will return garbage results. The system will appear to work but episodic context, lessons retrieval, and mutation similarity will be meaningless.
- **Mitigation:** Add model download as a wizard step (step 4.5 as mentioned in the plan). Pre-download the model to a configurable path. Add a startup check that verifies the model loads before accepting cycles. Block onboarding if the model is not available.
- **Residual Risk:** LOW if added to wizard; HIGH if not.

### [MEDIUM] RA-09: Evidence Router Lacks Rate Limit Protection
- **Plan ref:** Wave 1.1
- **Category:** Operational
- **Description:** The plan wires evidence fetching into the orchestrator cycle. The `DataGateway` uses `TokenBucket` rate limiting per source, but the plan does not address what happens when fetching evidence for 16 tickers across 13 sources in a single cycle. That is up to 208 API calls per cycle (16 tickers x 13 sources). With rate limits configured per source, some sources may exhaust their bucket mid-cycle, causing later tickers to receive partial evidence while earlier tickers get full evidence. The gatekeeper then admits tickers asymmetrically based on evidence availability rather than thesis quality.
- **Likelihood:** MEDIUM -- depends on rate limit budgets vs. cycle demand.
- **Impact:** MEDIUM -- inconsistent admission decisions across tickers within the same cycle.
- **Mitigation:** Pre-fetch all evidence before gatekeeper runs (batch fetch), then distribute. If a source is rate-limited, apply the same staleness to all tickers equally rather than silently skipping later ones. Log per-source rate limit hits as a quality metric.
- **Residual Risk:** LOW -- inconsistent evidence is logged and can be detected.

### [MEDIUM] RA-10: Catalyst Resolution Depends on External APIs Without Retry
- **Plan ref:** Wave 1.3
- **Category:** Operational
- **Description:** The catalyst resolution subsystem (already implemented in `pmacs/data/resolution/`) makes API calls to detect earnings outcomes, FDA decisions, etc. If any of these APIs are down during a cycle, catalysts that should resolve will remain in PENDING state indefinitely. The plan does not specify retry logic or a backoff strategy for resolution failures. The existing `corroboration.py` (297 lines) has a 3-sigma outlier guard but no retry mechanism.
- **Likelihood:** MEDIUM -- API outages are common, especially for FDA/OpenFDA.
- **Impact:** MEDIUM -- unresolved catalysts block position exits, potentially leading to larger-than-expected losses.
- **Mitigation:** Add dead-letter queuing for failed resolution attempts with exponential backoff. The `dead_letter.py` module exists but is not wired to catalyst resolution.
- **Residual Risk:** LOW after wiring to dead letter queue.

### [MEDIUM] RA-11: FDE STOP_HUNTED 48-Hour Post-Exit Check Requires Persistent State
- **Plan ref:** Wave 3.4
- **Category:** Operational / Data Integrity
- **Description:** The STOP_HUNTED detection requires checking the price 48 hours after exit. This implies a persistent queue of pending checks that survive process restarts. The plan does not specify where this queue lives. If it lives only in memory (daemon process), a process restart drops all pending checks. If it lives in SQLite, the schema must be created and the stop-loss daemon must process it.
- **Likelihood:** MEDIUM -- process restarts are expected (crash loop detection exists for this reason).
- **Impact:** MEDIUM -- missed STOP_HUNTED classifications mean FDE accuracy degrades.
- **Mitigation:** Use the existing `stop_events` SQLite table with a `pending_48h_check_at` column. The stop-loss daemon checks this table on each iteration. Add this schema change to Wave 3.4.
- **Residual Risk:** LOW with SQLite-backed queue.

### [MEDIUM] RA-12: Test Infrastructure Gaps for Multi-Store Integration
- **Plan ref:** Wave 5.2
- **Category:** Testing
- **Description:** The plan lists 14 new test files but does not specify test infrastructure for multi-store scenarios. Specifically: (a) no test fixture that sets up all 5 stores simultaneously with consistent test data, (b) no test that validates cross-store consistency after a cycle (KuzuDB lineage should match SQLite holdings, DuckDB metrics should match cycle results), (c) the SMKT fixture provides synthetic data for single-ticker scenarios but not for a 16-ticker universe with multiple active positions and resolution history.
- **Likelihood:** HIGH -- tests will be written but may miss cross-store consistency bugs.
- **Impact:** MEDIUM -- cross-store bugs will only surface in production cycles.
- **Mitigation:** Create a `tests/fixtures/multi_store.py` fixture that populates all 5 stores with consistent synthetic data. Add `test_cross_db.py` as the plan specifies, but ensure it tests write-during-cycle scenarios, not just read-after-seed.
- **Residual Risk:** MEDIUM -- cross-store consistency is inherently complex.

### [MEDIUM] RA-13: Ops Scripts Run Without Idempotency Guarantees
- **Plan ref:** Wave 5.1
- **Category:** Operational
- **Description:** The ops scripts (`install_launchd.sh`, `install_pf_rules.sh`, `install_system_users.sh`) modify system state. The plan does not require them to be idempotent. Running `install_launchd.sh` twice could create duplicate plist entries. Running `install_pf_rules.sh` twice could create duplicate firewall rules. Running `install_system_users.sh` twice is probably safe (`useradd` on duplicate user fails gracefully), but it is not guaranteed across macOS versions.
- **Likelihood:** MEDIUM -- operators will re-run scripts when troubleshooting.
- **Impact:** LOW -- duplicate launchd entries cause warnings, not crashes; duplicate pf rules are cumulative.
- **Mitigation:** Require all ops scripts to check for existing state before modifying. `install_launchd.sh` should check if the plist is already loaded. `install_pf_rules.sh` should use `pfctl -sr` to check before adding.
- **Residual Risk:** LOW.

### [LOW] RA-14: Phase 15 Polish Items Mixed into Phase 12
- **Plan ref:** Wave 5.3
- **Category:** Operational
- **Description:** Wave 5.3 includes Phase 15 polish items (animations, drag-drop, sparklines, Cmd-K, keyboard shortcuts, accessibility audit). These are UI-only changes that have zero dependency on Waves 1-4 and add risk of regression bugs in the dashboard during the critical migration phase. Mixing cosmetic changes with infrastructure migration increases the blast radius of any Wave 5 regression.
- **Likelihood:** LOW -- UI changes are isolated to templates and JS.
- **Impact:** LOW -- cosmetic regressions do not affect data integrity.
- **Mitigation:** Defer Wave 5.3 to a separate phase after Phase 12 exit test passes.
- **Residual Risk:** LOW.

## Critical Path Analysis

The minimum that must not fail, in order:

1. **Wave 1.2 (Price Feed)** -- Without real prices, sizing computes wrong share counts and stops are wrong. This is the single highest-leverage failure point. If prices are wrong, everything downstream is wrong.

2. **Wave 2.1 (Embedding Model)** -- Without the real embedding model, all Qdrant similarity searches return semantically meaningless results. Episodic context, lessons retrieval, and mutation evaluation all depend on this.

3. **Wave 2.2-2.4 (Storage Activation)** -- All three stores must initialize successfully or the system must detect and report the failure. Split-brain state (some stores real, some stub) is the worst outcome.

4. **Wave 3.1 (Crucible Loop)** -- Already implemented but the plan says it needs real evidence flow to actually test the 2-iteration rewrite. This is the primary safety net that prevents bad trades.

5. **Audit Chain Verification** -- Must pass after a full cycle with real data flowing. This is the exit test's final gate.

## Rollback Safety

| Wave | Can Roll Back? | Mechanism | Notes |
|------|---------------|-----------|-------|
| 1.1 Evidence Pipeline | Partial | Remove evidence router call in orchestrator | Reverts to `evidence=[]` for personas |
| 1.2 Price Feed | Yes | Remove `_get_price_cache()` call | Reverts to `current_price=1.0` (known bad) |
| 1.3 Catalyst Resolution | Yes | Skip step 7 in orchestrator | Catalysts stay PENDING |
| 1.4 Pricing Engine | Yes | Revert `compute_ev()` to hardcoded values | Stubs already exist |
| 2.1 Embedding Model | Partial | Delete model cache | Qdrant falls back to hash vectors |
| 2.2 KuzuDB | **No** | No rollback mechanism | Once real data is written, reverting to stub loses it |
| 2.3 Qdrant | **No** | No rollback mechanism | Same as KuzuDB |
| 2.4 DuckDB | Partial | Delete DuckDB file | Loses all analytics history |
| 3.1-3.5 Engines | Yes | Revert to stub returns | Engines are stateless |
| 4.1-4.4 Flywheel | Yes | Revert to stub returns | Depends on stores for data |
| 5.1 Ops Scripts | Partial | Manual uninstall steps | Not automated |
| 5.2 Tests | Yes | Delete test files | Tests are non-destructive |

**Key finding:** Waves 2.2 and 2.3 have no rollback path. Once KuzuDB and Qdrant have real data, there is no automated way to revert to stub mode. This makes Wave 2 the highest-risk wave for irreversible state changes.

## Test Coverage Assessment

### Existing Coverage (Strong)
- Unit tests: 76 test files covering state machine, conviction, sizing, arbitration, TOTP, kill switch, schemas, audit chain, and all 7 personas.
- Integration tests: 30 files covering cycle skeleton, storage adapters, stop loss, wizard.
- Property tests: Probability invariants and FX symmetry.
- SMKT fixtures: Deterministic synthetic data for single-ticker scenarios.

### Planned Coverage (Gaps)
The plan specifies 14 new test files. Assessment of each:

| Test File | Covers | Risk | Gap |
|-----------|--------|------|-----|
| `test_data_sources.py` | 10/13 sources | MEDIUM | No test for rate limit exhaustion behavior |
| `test_llm_call.py` | Inference backend | LOW | |
| `test_3persona_cycle.py` | 3-persona cycle | LOW | |
| `test_7persona_cycle.py` | Full persona cycle | LOW | |
| `test_full_pipeline.py` | Complete pipeline | MEDIUM | No test for pipeline with store failures mid-cycle |
| `test_crucible_budget.py` | Crucible timing | LOW | Already exists as unit test |
| `test_calibration.py` | Calibration refit | LOW | |
| `test_fde.py` | 18 taxonomy types | MEDIUM | No test for 48h delayed STOP_HUNTED check |
| `test_cross_db.py` | Cross-DB consistency | HIGH | Critical: no test for write-during-cycle consistency |
| `test_episodic.py` | Episodic context | MEDIUM | No test for fallback when stores unavailable |
| `test_mutation_lifecycle.py` | Mutation lifecycle | LOW | |
| `test_rollback.py` | 5-level rollback | LOW | |
| `test_smoke_cycle.py` | Full smoke cycle | MEDIUM | Must cover 16-ticker universe, not just SMKT |

**Critical gap:** No test covers the scenario where one store fails mid-cycle while others succeed. The `test_cross_db.py` test is the right place for this but must be explicitly designed to test partial failure.

## Overall Risk Rating

**HIGH**

Phase 12 is the highest-risk phase because:
1. It touches all 5 storage backends simultaneously with no rollback mechanism for 2 of them.
2. The price feed fallback silently corrupts sizing and EV calculations.
3. The audit chain is not thread-safe for concurrent writes.
4. The embedding model must be pre-downloaded before the pf-firewalled system can use it.
5. The plan mixes cosmetic changes (Wave 5.3) with critical infrastructure migration.

The system is well-designed for its steady state -- stubs degrade gracefully, error handling is thorough, and logging is comprehensive. The risk is specifically in the transition from stubs to real data, which the plan does not address with sufficient defensive measures.

### Recommended Actions Before Starting Wave 1

1. Add `current_price <= 1.0` guard to abort symbol processing with DATA_UNAVAILABLE.
2. Add storage health check at orchestrator startup (all 3 stores must pass).
3. Verify KuzuDB Python API parameter binding format.
4. Add embedding model download as a prerequisite step (wizard or ops script).
5. Make AuditWriter thread-safe before enabling concurrent audit writes.
6. Defer Wave 5.3 (polish) to a separate phase.
