# Phase 6 PLAN -- Calibration + FDE (Review Gap-Closure)

## Status
REPLAN -- Gap-closure from cross-AI review feedback

## Review Sources
- `.planning/REVIEWS.md` -- Independent code review (Phases 1, 2, 8)
- `.planning/phases/REVIEWS-5.md` -- Phase 5 review (Claude Sonnet)
- `.planning/phases/phase-6/CONTEXT.md` -- Full gap analysis with G1-G8

---

## What's Already Done (no changes needed)

These items are correctly implemented and verified by the existing 713 tests. Do NOT modify:

- FDE STOP_HUNTED 48h recovery check
- FDE STOP_LOSS_CORRECT 30d check
- Brier scoring (`compute_brier`)
- Persona weight refitting (`refit_persona_weights`)
- Causal attribution (`pmacs/engines/causal_attribution.py`)
- Lesson extraction (`pmacs/engines/lessons.py`)
- Flywheel health snapshot (`pmacs/engines/flywheel_health.py`)
- Override clustering (`pmacs/engines/override_learning.py`)
- Severity multiplier tuning (`pmacs/engines/crucible_calibration.py`)
- Episodic context 200-word brief (`pmacs/agents/episodic_context.py`)
- 619-line FDE unit test file (`tests/unit/test_fde.py`)
- 328-line calibration unit test file (`tests/unit/test_calibration.py`)
- Dead-letter queue enqueue/retry/exhaustion flow (mechanics correct, only backoff needs change)
- Reconciliation engine math (correct, only config loading needs change)

---

## Gaps to Close

### CRITICAL

#### G1: FDE taxonomy naming mismatches spec

**File:** `pmacs/schemas/failure.py`, `pmacs/engines/failure_diagnostic.py`

The `FailureTaxonomy` enum has 18 entries but uses WRONG names. The code has 18 non-matching codes. The spec (Agents.md section 15.1) defines the canonical 18. The Mutation Engine's FDE cluster aggregation (Agents.md section 17) depends on exact name matching.

**Current (WRONG) -> Spec (CORRECT):**
```
CATALYST_TIMING_MISREAD        -> CATALYST_FALSE_POSITIVE
REGIME_SHIFT_MISSED            -> EXOGENOUS_MACRO_SHOCK
SECTOR_CORRELATION_MISJUDGED   -> CORRELATION_REGIME_SHIFT
INSIDER_SIGNAL_NOISE           -> INSIDER_SIGNAL_FALSE
SHORT_THESIS_CROWDED           -> SHORT_INTEREST_CORRECT
FORENSIC_RED_FLAG_FALSE_POSITIVE -> FORENSICS_FLAG_IGNORED
THESIS_INVALIDATED_PREMATURE   -> THESIS_INVALIDATED_FUNDAMENTAL
THESIS_INVALIDATED_CORRECT     -> THESIS_INVALIDATED_COMPETITIVE  (one of 3 subtypes)
OPPORTUNITY_COST_EXCEEDED      -> OPPORTUNITY_COST_EXIT_CORRECT
ENTRY_TIMING_POOR              -> EXECUTION_SLIPPAGE
EXIT_TIMING_POOR               -> (removed -- not in spec)
SIZING_OVERCONFIDENT           -> SIZING_OVERLEVERAGED
SIZING_UNDERCONFIDENT          -> UNCLASSIFIED (fallback)
CORRELATION_BREAKDOWN          -> CORRELATION_REGIME_SHIFT (duplicate, merge)
CATALYST_FAILED_TO_MATERIALIZE -> CATALYST_TIMEOUT
```

**Spec canonical 18 (from Agents.md section 15.1):**
1. `THESIS_INVALIDATED_FUNDAMENTAL`
2. `THESIS_INVALIDATED_COMPETITIVE`
3. `THESIS_INVALIDATED_REGULATORY`
4. `CATALYST_FALSE_POSITIVE`
5. `CATALYST_TIMEOUT`
6. `STOP_HUNTED`
7. `STOP_LOSS_CORRECT`
8. `EXOGENOUS_MACRO_SHOCK`
9. `CORRELATION_REGIME_SHIFT`
10. `MOAT_DRIFT_OVERESTIMATE`
11. `GROWTH_STALL_MISSED`
12. `FORENSICS_FLAG_IGNORED`
13. `INSIDER_SIGNAL_FALSE`
14. `SHORT_INTEREST_CORRECT`
15. `SIZING_OVERLEVERAGED`
16. `EXECUTION_SLIPPAGE`
17. `OPPORTUNITY_COST_EXIT_CORRECT`
18. `UNCLASSIFIED`

**Fix:**
1. Rewrite `FailureTaxonomy` enum in `pmacs/schemas/failure.py` with exact spec names
2. Update ALL taxonomy references in `pmacs/engines/failure_diagnostic.py` to use new names
3. Add `THESIS_INVALIDATED_REGULATORY` classification path (currently missing)
4. Add `GROWTH_STALL_MISSED` classification path (currently `REGIME_SHIFT_MISSED` mapping is wrong)
5. Update `tests/unit/test_fde.py` to use new taxonomy names
6. Ensure every terminal state maps to exactly one taxonomy code

#### G2: Cross-DB reconciler is a stub

**File:** `pmacs/storage/consistency.py`

Current code only checks if paths/URLs are provided. Architecture.md section 14.2 requires actual foreign-key-like validation across all 4 stores. The `cross_db_audit()` function must:
- For every Holding in KuzuDB, verify SQLite has corresponding row (and vice versa)
- For every thesis_embedding_id in Holdings, verify Qdrant has corresponding vector
- For every Resolution in DuckDB, verify KuzuDB has corresponding node
- Mismatches must emit `CROSS_DB_INCONSISTENCY` debug event and write to `consistency_drift` table

**Fix:** Replace stub with real implementation that queries each store and cross-references.

#### G3: KuzuDB FailedAssumption writer is a stub

**File:** `pmacs/storage/kuzu.py`

`write_failed_assumption()` is `pass # STUB`. Architecture.md section 9.5 shows the exact Cypher to execute. Phases.md Phase 12 exit test 5 requires `MATCH (h:Holding)-[:FAILED_ASSUMPTION]->(fa) RETURN fa` to work.

**Fix:** Implement the Cypher execution from Architecture.md section 9.5 in `write_failed_assumption()`.

#### G4: Missing 4 integration test files

**Spec requires:**
- `tests/integration/test_calibration.py` -- Phase 11 exit test 1
- `tests/integration/test_qdrant.py` -- Phase 11 Qdrant operations
- `tests/integration/test_kuzu.py` -- Phase 11 KuzuDB operations
- `tests/integration/test_cross_db.py` -- Phase 12 exit test 3

**Actual:** None exist. Only `test_storage_adapters.py` (combined unit-level) and unit tests.

**Fix:** Create each file with integration-level tests that match Phases.md exit test specifications.

#### G5: Calibration engine missing audit logging

**File:** `pmacs/engines/calibration.py`

Architecture.md section 1.8 requires ALL engines log to BOTH audit AND debug streams. Architecture.md section 1.11 requires `cycle_id` on every audit-emitting function. Currently no logging at all.

**Fix:** Add `cycle_id` parameter to `refit_persona_weights()` and `compute_brier()`. Add audit event emission (`calibration_refit_completed`) and debug event emission.

#### G6: Storage adapters missing audit/debug logging

**Files:** `pmacs/storage/kuzu.py`, `pmacs/storage/qdrant.py`, `pmacs/storage/duckdb.py`

Architecture.md section 9 requires each engine logs both audit AND debug. Currently zero logging in any adapter.

**Fix:** Add logging calls to each adapter's core methods. Use the existing `pmacs.logsys` audit/debug infrastructure.

### MODERATE

#### G7: Dead-letter queue uses fixed delay

**File:** `pmacs/logsys/dead_letter.py`

Architecture.md section 14.1 specifies exponential backoff: 1s, 5s, 30s, 5min, 1h, 1d. Current code uses fixed 60s delay.

**Fix:** Replace `retry_delay_s` with backoff schedule array `[1, 5, 30, 300, 3600, 86400]`. Compute delay from `entry.attempts` index into the schedule.

#### G8: Reconciliation tolerance hardcoded

**File:** `pmacs/engines/reconciliation.py`

Tolerance hardcoded as `tolerance_usd=100.0, tolerance_pct=5.0`. Architecture.md specifies these in `config/risk.toml` as `reconciliation_tolerance_usd = 100` and `reconciliation_tolerance_pct = 0.05` (note: spec uses 0.05 fraction, not 5.0 percent).

**Fix:** Load tolerance from `config/risk.toml` using the existing config loading pattern. Keep function parameters as defaults for backward compat but load from config at call site.

### CROSS-PHASE

#### CP1: Test coverage -- integration test files

Covered by G4. Every exit test must map to a named integration test file.

#### CP2: Schema migration -- Phase 6 adds tables

Phase 6 adds DuckDB tables, KuzuDB nodes, Qdrant collections. The `init_tables()` / `create_collections()` methods handle this. Add a verification step in the integration tests.

#### CP3: ops/verify_isolation.py missing

Flagged in REVIEWS.md section 5. Not strictly Phase 6 scope but noted as cross-phase concern. Add as a final task in Wave 4 if time permits, or defer to Phase 8.

---

## Waves

```
Wave 1 (G1, G5, G6) -- Schema + logging fixes
  |
Wave 2 (G2, G3, G7, G8) -- Storage adapter real implementations + config fixes
  |
Wave 3 (G4) -- Integration test files
  |
Wave 4 (CP2, CP3) -- Cross-phase gaps (schema migration verify, ops tool)
```

Wave 1 is foundational: taxonomy names and logging are required by everything downstream.
Wave 2 depends on Wave 1 taxonomy names being correct (tests reference them).
Wave 3 depends on Wave 2 implementations being real (integration tests call real code).
Wave 4 depends on Wave 3 tests to verify schema migration correctness.

---

## Wave 1: Critical Schema + Logging Fixes

### Task 1.1: Fix FDE taxonomy enum and classifier to match Agents.md section 15

**Files:**
- `pmacs/schemas/failure.py`
- `pmacs/engines/failure_diagnostic.py`
- `tests/unit/test_fde.py`

**Action:**

1. In `pmacs/schemas/failure.py`, replace the entire `FailureTaxonomy` enum with the exact 18 canonical codes from Agents.md section 15.1:
   ```
   THESIS_INVALIDATED_FUNDAMENTAL, THESIS_INVALIDATED_COMPETITIVE,
   THESIS_INVALIDATED_REGULATORY, CATALYST_FALSE_POSITIVE, CATALYST_TIMEOUT,
   STOP_HUNTED, STOP_LOSS_CORRECT, EXOGENOUS_MACRO_SHOCK,
   CORRELATION_REGIME_SHIFT, MOAT_DRIFT_OVERESTIMATE, GROWTH_STALL_MISSED,
   FORENSICS_FLAG_IGNORED, INSIDER_SIGNAL_FALSE, SHORT_INTEREST_CORRECT,
   SIZING_OVERLEVERAGED, EXECUTION_SLIPPAGE, OPPORTUNITY_COST_EXIT_CORRECT,
   UNCLASSIFIED
   ```

2. In `pmacs/engines/failure_diagnostic.py`, update ALL `FailureTaxonomy` references:
   - `THESIS_INVALIDATED_PREMATURE` -> `UNCLASSIFIED` (for abort states -- spec says aborts are not failures, they map to UNCLASSIFIED with severity 0.0)
   - `_classify_thesis_invalidation`: route to `THESIS_INVALIDATED_FUNDAMENTAL` (default), `THESIS_INVALIDATED_COMPETITIVE` (keyword "competitive"/"moat"), `THESIS_INVALIDATED_REGULATORY` (keyword "regulatory"). Remove `THESIS_INVALIDATED_CORRECT` -- that concept is now one of the 3 invalidation subtypes.
   - `CATALYST_TIMING_MISREAD` -> `CATALYST_FALSE_POSITIVE`
   - `CATALYST_FAILED_TO_MATERIALIZE` -> `CATALYST_TIMEOUT`
   - `REGIME_SHIFT_MISSED` -> `GROWTH_STALL_MISSED`
   - `FORENSIC_RED_FLAG_FALSE_POSITIVE` -> `FORENSICS_FLAG_IGNORED`
   - `INSIDER_SIGNAL_NOISE` -> `INSIDER_SIGNAL_FALSE`
   - `SHORT_THESIS_CROWDED` -> `SHORT_INTEREST_CORRECT`
   - `SIZING_OVERCONFIDENT` -> `SIZING_OVERLEVERAGED`
   - `SIZING_UNDERCONFIDENT` -> `UNCLASSIFIED` (fallback)
   - `ENTRY_TIMING_POOR` -> `EXECUTION_SLIPPAGE`
   - `EXIT_TIMING_POOR` -> remove (not in spec)
   - `OPPORTUNITY_COST_EXCEEDED` -> `OPPORTUNITY_COST_EXIT_CORRECT`
   - `CORRELATION_BREAKDOWN` -> `CORRELATION_REGIME_SHIFT`
   - `_classify_stop` sector drop -> `EXOGENOUS_MACRO_SHOCK`
   - `_classify_stop` correlation fallback -> `CORRELATION_REGIME_SHIFT`
   - Fallback unclassified -> `UNCLASSIFIED`

3. Add `THESIS_INVALIDATED_REGULATORY` path in `_classify_thesis_invalidation`: if exit_reason contains "regulatory" -> `THESIS_INVALIDATED_REGULATORY`.

4. Update `tests/unit/test_fde.py` to use all new taxonomy names. Ensure every taxonomy code has at least one test case.

**Verify:** `pytest tests/unit/test_fde.py -x` passes. Every `FailureTaxonomy` member is referenced in at least one test. Grep for old names returns zero hits: `grep -r 'THESIS_INVALIDATED_PREMATURE\|CATALYST_TIMING_MISREAD\|REGIME_SHIFT_MISSED\|INSIDER_SIGNAL_NOISE\|SHORT_THESIS_CROWDED\|FORENSIC_RED_FLAG_FALSE_POSITIVE\|THESIS_INVALIDATED_CORRECT\|OPPORTUNITY_COST_EXCEEDED\|ENTRY_TIMING_POOR\|EXIT_TIMING_POOR\|SIZING_OVERCONFIDENT\|SIZING_UNDERCONFIDENT\|CORRELATION_BREAKDOWN\|CATALYST_FAILED_TO_MATERIALIZE' pmacs/ tests/`

**Done:** All 18 taxonomy codes exactly match Agents.md section 15.1. No stale names in codebase. All existing tests updated and passing.

---

### Task 1.2: Add audit/debug logging to calibration engine and storage adapters

**Files:**
- `pmacs/engines/calibration.py`
- `pmacs/storage/kuzu.py`
- `pmacs/storage/qdrant.py`
- `pmacs/storage/duckdb.py`

**Action:**

1. In `pmacs/engines/calibration.py`:
   - Add `import logging` and `logger = logging.getLogger(__name__)`
   - Add `cycle_id: str = ""` parameter to `refit_persona_weights()`
   - Before returning weights: emit `logger.info("calibration_refit_completed", extra={"cycle_id": cycle_id, "personas": list(persona_briers.keys()), "samples_used": min_samples})` -- this is the debug stream event
   - Add `logger.debug("brier_computed", extra={"p_up": p_up, "actual": actual, "brier": result})` to `compute_brier()`
   - Add audit event emission: create a helper that writes to the audit log via the canonical_json/audit_write pattern. For now, use `logger.info("audit.calibration_refit", extra={"cycle_id": cycle_id, ...})` since the full audit infrastructure may not be wired yet. The key requirement is that BOTH audit and debug events are emitted.

2. In `pmacs/storage/kuzu.py`:
   - Add `import logging` and `logger = logging.getLogger(__name__)`
   - Add logging to `execute()`: `logger.debug("kuzu_query", extra={"query": query[:200]})`
   - Add logging to `write_failed_assumption()`: `logger.info("audit.failed_assumption_written", extra={"fa_id": fa_id, "taxonomy": taxonomy, "cycle_id": cycle_id})`
   - Add logging to `get_failures_for_ticker()`: `logger.debug("kuzu_failures_retrieved", extra={"ticker": ticker, "count": len(results)})`
   - Add logging to `get_lineage()`: `logger.debug("kuzu_lineage_retrieved", extra={"holding_id": holding_id})`

3. In `pmacs/storage/qdrant.py`:
   - Add `import logging` and `logger = logging.getLogger(__name__)`
   - Add logging to `upsert()`: `logger.info("audit.qdrant_upsert", extra={"collection": collection, "id": id})`
   - Add logging to `search()`: `logger.debug("qdrant_search", extra={"collection": collection, "limit": limit})`
   - Add logging to `create_collections()`: `logger.info("audit.qdrant_collections_created", extra={"collections": self.COLLECTIONS})`

4. In `pmacs/storage/duckdb.py`:
   - Add `import logging` and `logger = logging.getLogger(__name__)`
   - Add logging to `init_tables()`: `logger.info("audit.duckdb_tables_initialized")`
   - Add logging to write methods: `logger.debug("duckdb_write", extra={"table": table_name})`

**Verify:** `pytest tests/unit/test_calibration.py tests/unit/test_fde.py -x` passes. `grep -l 'logging.getLogger' pmacs/engines/calibration.py pmacs/storage/kuzu.py pmacs/storage/qdrant.py pmacs/storage/duckdb.py` returns all 4 files. `grep 'cycle_id' pmacs/engines/calibration.py` shows the parameter.

**Done:** Every engine and storage adapter has both audit (`logger.info("audit.*")`) and debug (`logger.debug(...)`) logging. `cycle_id` is present on all calibration audit-emitting functions.

---

## Wave 2: Storage Adapter Real Implementations + Config Fixes

### Task 2.1: Implement cross-DB reconciler and KuzuDB FailedAssumption writer

**Files:**
- `pmacs/storage/consistency.py`
- `pmacs/storage/kuzu.py`
- `pmacs/logsys/dead_letter.py`
- `pmacs/engines/reconciliation.py`

**Action:**

1. In `pmacs/storage/consistency.py`, replace the entire stub `check_cross_db_consistency()` with a real implementation:
   - Accept actual store connection objects (SQLite connection, KuzuDB adapter, Qdrant adapter, DuckDB adapter) instead of path strings
   - **SQLite <-> KuzuDB:** Query holdings from SQLite, query Holding nodes from KuzuDB, find IDs present in one but not the other
   - **KuzuDB <-> Qdrant:** For each thesis_embedding_id found in KuzuDB holdings, verify Qdrant has a vector with that ID
   - **DuckDB <-> KuzuDB:** For each Resolution in DuckDB, verify KuzuDB has a corresponding node
   - On mismatch: emit `CROSS_DB_INCONSISTENCY` debug event with details (`logger.warning("CROSS_DB_INCONSISTENCY", extra={...})`)
   - Return `ConsistencyResult` per check with actual `drift_count` and `details` containing the mismatched IDs
   - Keep the function signature backward-compatible: path strings still work (returns UNAVAILABLE), but connection objects enable real checks

2. In `pmacs/storage/kuzu.py`, implement `write_failed_assumption()`:
   - Execute the Cypher from Architecture.md section 9.5:
     ```cypher
     CREATE (fa:FailedAssumption {
         id: $id, taxonomy: $tax, severity: $sev, ts: $ts,
         holding_id: $hid, cycle_id: $cid, summary: $summary
     })
     WITH fa
     MATCH (h:Holding {id: $hid})
     CREATE (h)-[:FAILED_ASSUMPTION]->(fa)
     ```
   - If KuzuDB connection is not available (stub mode), log a warning and return gracefully
   - Add logging (from Task 1.2)

3. In `pmacs/logsys/dead_letter.py`, replace fixed delay with exponential backoff:
   - Replace `retry_delay_s: float = 60.0` with `backoff_schedule: list[float] = field(default_factory=lambda: [1, 5, 30, 300, 3600, 86400])`
   - In `get_pending()`, compute delay from `backoff_schedule[min(entry.attempts, len(self.backoff_schedule) - 1)]`
   - Keep `max_attempts` at 6 (matching the 6-step backoff schedule in Architecture.md section 14.1)
   - After 6 attempts: status=FAILED, emit `DEAD_LETTER_QUEUED` warning log

4. In `pmacs/engines/reconciliation.py`, load tolerance from config:
   - Add a module-level `load_tolerance_from_config()` that reads `config/risk.toml`
   - Use the existing config loading pattern from `pmacs/config.py` or direct `tomllib` read
   - Default to the current values (100.0 USD, 5.0 pct) if config not found
   - Note: spec `reconciliation_tolerance_pct = 0.05` means 5%, so convert fraction to pct (multiply by 100) for comparison, OR keep as fraction and adjust the comparison. Align with how `diff_pct` is calculated (currently multiplied by 100 in the function).
   - Update function signature: keep params as defaults, but call site should prefer config values

**Verify:**
- `pytest tests/unit/test_fde.py -x` passes
- `grep -c 'CROSS_DB_INCONSISTENCY' pmacs/storage/consistency.py` returns >= 1
- `grep 'backoff_schedule' pmacs/logsys/dead_letter.py` returns non-empty
- `grep 'risk.toml\|tomllib\|load_tolerance' pmacs/engines/reconciliation.py` returns non-empty
- `grep 'CREATE.*FailedAssumption' pmacs/storage/kuzu.py` returns non-empty

**Done:** Cross-DB reconciler performs actual cross-store validation. FailedAssumption writer executes real Cypher. Dead-letter queue uses exponential backoff. Reconciliation loads tolerance from config/risk.toml.

---

## Wave 3: Integration Test Files

### Task 3.1: Create 4 integration test files per Phases.md spec

**Files:**
- `tests/integration/test_calibration.py`
- `tests/integration/test_qdrant.py`
- `tests/integration/test_kuzu.py`
- `tests/integration/test_cross_db.py`

**Action:**

Each test file must be self-contained with fixtures and teardown. Use the existing adapter classes directly (they work in stub mode when real DBs are not running).

1. `tests/integration/test_calibration.py` (Phase 11 exit test 1):
   - Test: After 20 synthetic resolutions, `refit_persona_weights()` adjusts weights; verify Brier improves
   - Test: `compute_brier()` handles all 3 outcomes (up, flat, down)
   - Test: `cycle_id` is present in logging output (verify logger was called with cycle_id)
   - Test: Calibration result has all expected fields

2. `tests/integration/test_qdrant.py` (Phase 11 storage):
   - Test: `upsert()` and `search()` round-trip (stub mode returns deterministic results)
   - Test: `create_collections()` completes without error
   - Test: `get_embedding()` returns consistent length vector
   - Test: Audit logging is emitted on upsert

3. `tests/integration/test_kuzu.py` (Phase 11 storage):
   - Test: `write_failed_assumption()` executes without error (stub mode is graceful)
   - Test: `get_failures_for_ticker()` returns empty list for unknown ticker
   - Test: `get_lineage()` returns empty dict for unknown holding
   - Test: FailedAssumption Cypher template matches spec (test the query string)
   - Test: Audit logging is emitted on write

4. `tests/integration/test_cross_db.py` (Phase 12 exit test 3):
   - Test: `check_cross_db_consistency()` returns 4 results (one per store) when called with paths
   - Test: With stub adapters, all checks return CONSISTENT or UNAVAILABLE
   - Test: Dead-letter queue exponential backoff: enqueue -> get_pending returns immediately -> mark_retry -> get_pending waits for backoff[0] (1s)
   - Test: Dead-letter queue exhaustion after 6 attempts
   - Test: Reconciliation loads tolerance from config when available
   - Test: `reconcile_paper_ledger()` detects mismatch (100 USD tolerance, $200 diff -> requires_action=True)
   - Test: STOP_HUNTED vs STOP_LOSS_CORRECT differentiation (integration-level with FDE engine)

**Verify:** `pytest tests/integration/test_calibration.py tests/integration/test_qdrant.py tests/integration/test_kuzu.py tests/integration/test_cross_db.py -v --tb=short` passes. Each file has at least 4 test functions.

**Done:** All 4 integration test files exist and pass. Every Phases.md Phase 11/12 exit test maps to a named test file. Test names are descriptive (e.g., `test_calibration_refit_after_20_resolutions`, `test_cross_db_reconciler_detects_mismatch`).

---

## Wave 4: Cross-Phase Gaps

### Task 4.1: Schema migration verification + ops/verify_isolation.py stub

**Files:**
- `tests/integration/test_schema_migration.py` (new)
- `ops/verify_isolation.py` (new)

**Action:**

1. Create `tests/integration/test_schema_migration.py`:
   - Test: DuckDB adapter `init_tables()` creates all 4 tables without error
   - Test: Qdrant adapter `create_collections()` completes without error
   - Test: KuzuDB adapter handles FailedAssumption node creation gracefully (stub mode)
   - Test: SQLite dead_letter table schema matches Architecture.md section 14.1 columns

2. Create `ops/verify_isolation.py`:
   - Stub script that checks:
     - Process `pmacs-inference` is pf-blocked from internet (check pf rules exist)
     - Dashboard only binds to loopback (check :8001 binding)
     - Nervous only binds to loopback (check :8000 binding)
     - Execution uses UDS, not TCP
   - Print results as pass/fail per check
   - This is a stub -- full implementation deferred to Phase 8 per REVIEWS.md

**Verify:** `pytest tests/integration/test_schema_migration.py -v` passes. `python ops/verify_isolation.py` runs and prints results.

**Done:** Schema migration verified by integration test. `ops/verify_isolation.py` stub exists and runs.

---

## Exit Tests

Updated to verify gaps are closed. Run AFTER all waves complete.

| # | Exit Test | File | Verification |
|---|-----------|------|-------------|
| 1 | All 18 taxonomy codes match Agents.md section 15.1 exactly | `tests/unit/test_fde.py` | `pytest tests/unit/test_fde.py -v` -- 18 taxonomy codes, each with test |
| 2 | Calibration refit after 20 synthetic resolutions | `tests/integration/test_calibration.py` | `pytest tests/integration/test_calibration.py -v` |
| 3 | Lessons engine extract + store | Already tested (unit) | `pytest tests/unit/test_calibration.py -k lesson -v` |
| 4 | CausalAttribution credit/blame | Already tested (unit) | Existing tests pass |
| 5 | FlywheelHealth snapshot | Already tested (unit) | Existing tests pass |
| 6 | KuzuDB lineage traversable | `tests/integration/test_kuzu.py` | `pytest tests/integration/test_kuzu.py -v` |
| 7 | Cross-DB reconciler detects mismatches | `tests/integration/test_cross_db.py` | `pytest tests/integration/test_cross_db.py -v` |
| 8 | STOP_HUNTED vs STOP_LOSS_CORRECT | `tests/integration/test_cross_db.py` | Test in cross_db integration file |
| 9 | Dead-letter exponential backoff | `tests/integration/test_cross_db.py` | Verify backoff schedule [1,5,30,300,3600,86400] |
| 10 | FailedAssumption nodes Cypher-traversable | `tests/integration/test_kuzu.py` | Verify Cypher template matches Architecture.md section 9.5 |
| 11 | Reconciliation tolerance from config | `tests/integration/test_cross_db.py` | Verify loading from risk.toml |
| 12 | Audit logging with cycle_id | `tests/integration/test_calibration.py` | Verify logger calls include cycle_id |
| 13 | All 713+ existing tests still pass | Full suite | `pytest tests/ --tb=short` |

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Taxonomy rename breaks downstream code (Phase 7 Mutation Engine references old names) | Medium | High | Grep entire codebase for old names after rename; Phase 7 hasn't been built yet |
| Cross-DB reconciler requires real DB connections that aren't available in test | Low | Medium | Design reconciler to work with adapter objects; adapters work in stub mode for testing |
| KuzuDB Cypher execution fails in stub mode | Low | Low | Stub mode gracefully logs warning; real implementation requires `kuzu` package |
| Config loading fails if risk.toml missing | Low | Medium | Keep hardcoded defaults as fallback |
| Integration tests are slow or flaky | Low | Low | Use stub adapters; no real DB dependencies |

---

## Anti-Patterns Check (Architecture.md section 16)

| Anti-Pattern | Status |
|---|---|
| 16.1 Direct state mutation (`holding.state =`) | Not touched in this plan |
| 16.2 `json.dumps()` for audit | Not used -- logging via `logger.info` with structured extra |
| 16.3 Custom rate-limit logic | Not relevant to this plan |
| 16.4 Packet mutation in staleness check | Not relevant to this plan |
| 16.5 `cycle_id=None` on audit-emitting functions | **FIXED** -- G5 adds cycle_id to calibration engine |
| 16.6 Bootstrap abort cascade | Not relevant to this plan |
| 16.7 Tight broker-side stops | Not relevant to this plan |
| 16.8 `eur_per_usd` field | Not relevant to this plan |
| Missing `error_code` on WARN+ events | Verify logging includes structured error codes |

---

## Execution Notes

- **Run existing tests first:** `pytest tests/unit/test_fde.py tests/unit/test_calibration.py -x` to establish baseline
- **Taxonomy rename is the highest-risk change:** Do it first (Task 1.1), run tests immediately, fix any breakage before proceeding
- **Integration tests can use stub adapters:** No need for running KuzuDB/Qdrant/DuckDB instances
- **After all waves:** Run full test suite to verify no regressions: `pytest tests/ --tb=short`
