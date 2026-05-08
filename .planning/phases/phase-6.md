# GSD Phase 6: Calibration + FDE

**Implements PMACS Build Phases 11-12** (spec/Phases.md §2)

## Milestone

Flywheel passive components, 18 taxonomy types.

---

## PMACS Phase 11: Calibration + lessons + causal attribution + override learning

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

**Exit test:**
1. `pytest tests/integration/test_calibration.py` — after 20 synthetic resolutions, calibration refit adjusts persona weights; Brier improves
2. Lessons engine extracts a lesson from a resolution → writes to Qdrant → retrieval query returns it
3. CausalAttribution attributes a resolution to the contributing personas (verifiable apportionment)
4. FlywheelHealth snapshot records rolling Brier, Sharpe, and calibration gap status
5. KuzuDB has Holding → Evidence → Resolution → Lesson lineage traversable via Cypher

**Dependencies:** Phase 8 (resolutions accumulating in PAPER), Phase 9 (terminal states exist).

---

## PMACS Phase 12: Failure Diagnostic Engine + cross-DB consistency + reconciliation

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

**Exit test:**
1. `pytest tests/unit/test_fde.py` — all 18 taxonomy types classify correctly from synthetic holdings
2. STOP_HUNTED vs STOP_LOSS_CORRECT differentiation: a holding that recovers within 48h classifies as STOP_HUNTED; one that doesn't classifies as STOP_LOSS_CORRECT
3. Cross-DB reconciler detects a deliberately introduced mismatch (missing Qdrant vector for a Kuzu thesis) and reports `CROSS_DB_INCONSISTENCY`
4. Dead-letter queue: simulate a Qdrant write failure → queued → retry succeeds on next attempt
5. FailedAssumption nodes written to KuzuDB are traversable: `MATCH (h:Holding)-[:FAILED_ASSUMPTION]->(fa) RETURN fa`

**Dependencies:** Phase 11 (calibration + Kuzu + DuckDB operational).

---

## Next-phase dependency

GSD Phase 7 requires:
- All PMACS Phase 11-12 exit tests pass
- Calibration refits persona weights
- Lessons stored and retrievable from Qdrant
- KuzuDB lineage traversable
- All 18 FDE taxonomy types classify correctly
- Cross-DB reconciler operational
