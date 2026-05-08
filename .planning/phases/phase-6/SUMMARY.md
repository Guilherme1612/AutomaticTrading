# Phase 6 Summary — Calibration + FDE

## Status: COMPLETE

## Test Results
- **713 passed**, 3 failed (pre-existing API key), 6 skipped

## Deliverables

### PMACS Phase 11: Calibration + Lessons + Attribution

#### Storage Adapters
- `pmacs/storage/kuzu.py` — KuzuDB graph adapter (lineage, FailedAssumption queries)
- `pmacs/storage/qdrant.py` — Qdrant vector adapter (embedding, upsert, search)
- `pmacs/storage/duckdb.py` — DuckDB analytics (4 tables: rolling_metrics, persona_performance, persona_ticker_affinity, failure_taxonomy_counts)

#### Calibration Engines
- `pmacs/engines/calibration.py` — Brier scoring + persona weight refitting
- `pmacs/engines/causal_attribution.py` — Credit/blame apportionment per persona
- `pmacs/engines/lessons.py` — Lesson extraction from resolutions
- `pmacs/engines/flywheel_health.py` — Rolling health snapshot
- `pmacs/engines/crucible_calibration.py` — Severity multiplier tuning
- `pmacs/engines/override_learning.py` — Operator override clustering

#### Episodic Context
- `pmacs/agents/episodic_context.py` — 200-word context brief builder

#### Tests
- `tests/unit/test_calibration.py` — 34 tests
- `tests/integration/test_storage_adapters.py` — 17 tests

### PMACS Phase 12: FDE + Cross-DB + Reconciliation

#### Failure Diagnostic Engine (18 taxonomy types)
- `pmacs/engines/failure_diagnostic.py` — Full 18-type deterministic classifier
  - STOP_HUNTED vs STOP_LOSS_CORRECT differentiation
  - Thesis invalidation routing (fundamental/competitive/regulatory)
  - Persona-specific failure detection (moat, growth, forensics, insider, short)
  - Sizing and execution quality checks

#### Cross-DB + Reconciliation
- `pmacs/storage/consistency.py` — Cross-DB reconciler (SQLite/Kuzu/Qdrant/DuckDB)
- `pmacs/engines/reconciliation.py` — Paper-vs-broker reconciliation
- `pmacs/logsys/dead_letter.py` — Dead-letter queue with retry + exhaustion

#### Tests
- `tests/unit/test_fde.py` — 50 tests covering all 18 types + dead letter + reconciliation

## Exit Tests Status

| Exit Test | Status |
|---|---|
| Calibration refit after 20 resolutions | 34 unit tests |
| Lessons engine extract + store | Tested |
| CausalAttribution credit/blame | Tested |
| FlywheelHealth snapshot | Tested |
| All 18 FDE taxonomy types | 50 tests (all 18 types covered) |
| STOP_HUNTED vs STOP_LOSS_CORRECT | Both paths tested |
| Cross-DB reconciler | Tested |
| Dead-letter retry + exhaustion | Tested |
