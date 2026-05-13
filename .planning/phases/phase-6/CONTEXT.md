# Phase 6 Context — Calibration + FDE

## PMACS Phases Covered
- Phase 11: Calibration + lessons + causal attribution + override learning
- Phase 12: Failure Diagnostic Engine (18 taxonomy types) + cross-DB + reconciliation

## Spec References
- Agents.md §15 (18 FDE taxonomy types with exact trigger conditions)
- Agents.md §18 (Episodic context injection — 200-word context brief)
- Architecture.md §9.4 (CalibrationEngine), §9.5 (FDE), §15 (Memory hierarchy)
- Architecture.md §14 (Cross-DB consistency and dead letter)

## Exit Tests
1. Calibration refit adjusts persona weights after 20 synthetic resolutions
2. Lessons engine extracts + writes to vector store + retrieval returns it
3. CausalAttribution attributes resolution to contributing personas
4. FlywheelHealth snapshot records rolling metrics
5. All 18 FDE taxonomy types classify correctly
6. STOP_HUNTED vs STOP_LOSS_CORRECT differentiation
7. Cross-DB reconciler detects mismatches
8. Dead-letter queue: fail → queue → retry → succeed
9. FailedAssumption nodes traversable in graph DB

## Review Feedback (from --reviews replan)

### Cross-AI Review Sources
- `.planning/REVIEWS.md` — Independent code review (Phases 1, 2, 8)
- `.planning/phases/REVIEWS-5.md` — Phase 5 review (Claude Sonnet)

### Critical Gaps Found (must fix)

#### G1. FDE taxonomy naming mismatches spec
**File:** `pmacs/schemas/failure.py`
**Spec:** Agents.md §15 defines 18 exact taxonomy codes (e.g., `THESIS_INVALIDATED_FUNDAMENTAL`, `CATALYST_FALSE_POSITIVE`, `FORENSICS_FLAG_IGNORED`, `SHORT_INTEREST_CORRECT`, `SIZING_OVERLEVERAGED`, `EXECUTION_SLIPPAGE`, `OPPORTUNITY_COST_EXIT_CORRECT`)
**Code:** Has 20 codes with different names (e.g., `THESIS_INVALIDATED_PREMATURE`, `CATALYST_TIMING_MISREAD`, `FORENSIC_RED_FLAG_FALSE_POSITIVE`, `SHORT_THESIS_CROWDED`, `SIZING_OVERCONFIDENT`, `ENTRY_TIMING_POOR`, `OPPORTUNITY_COST_EXCEEDED`)
**Impact:** FDE outputs won't match spec. Mutation Engine FDE cluster aggregation (Agents.md §17) will fail. Canonical naming is non-negotiable per Agents.md §15.

#### G2. Cross-DB reconciler is a stub
**File:** `pmacs/storage/consistency.py`
**Spec:** Architecture.md §14 requires actual validation of SQLite↔Kuzu↔Qdrant↔DuckDB foreign-key-like references.
**Code:** Only checks if paths/URLs are provided, no actual cross-store validation.
**Impact:** Cannot detect inconsistencies. Exit test 7 cannot pass.

#### G3. KuzuDB FailedAssumption writer is a stub
**File:** `pmacs/storage/kuzu.py`
**Spec:** Phases.md Phase 12 exit test 5 requires `MATCH (h:Holding)-[:FAILED_ASSUMPTION]->(fa) RETURN fa` to work.
**Code:** `write_failed_assumption()` is `pass # STUB`. No Cypher execution.
**Impact:** Mutation Engine cannot consume FDE output. Exit test 9 cannot pass.

#### G4. Missing integration test files
**Spec requires:** `tests/integration/test_calibration.py`, `test_qdrant.py`, `test_kuzu.py`, `test_cross_db.py`
**Actual:** None of these exist. Only `test_storage_adapters.py` (combined) and unit tests.
**Impact:** Phase 11/12 exit tests cannot run as specified.

#### G5. Calibration engine missing audit logging
**File:** `pmacs/engines/calibration.py`
**Spec:** Architecture.md §1.8 requires ALL engines log to both audit AND debug streams. Architecture.md §1.11 requires cycle_id on every audit-emitting function.
**Code:** No audit logging present.
**Impact:** Cannot trace calibration decisions through audit chain. Violates Non-Negotiable 3 (hash-chained audit).

#### G6. Storage adapters missing audit/debug logging
**Files:** `pmacs/storage/kuzu.py`, `pmacs/storage/qdrant.py`, `pmacs/storage/duckdb.py`
**Spec:** Architecture.md §9 — each engine logs both audit AND debug.
**Code:** No logging in any storage adapter.
**Impact:** No audit trail for storage operations.

### Moderate Gaps (should fix)

#### G7. Dead-letter queue uses fixed delay
**File:** `pmacs/logsys/dead_letter.py`
**Spec:** Architecture.md §14 implies exponential backoff.
**Code:** Fixed 60s delay, not exponential.

#### G8. Reconciliation tolerance hardcoded
**File:** `pmacs/engines/reconciliation.py`
**Spec:** Architecture.md specifies tolerance in risk.toml (`reconciliation_tolerance_usd = 100`, `reconciliation_tolerance_pct = 0.05`).
**Code:** Hardcoded function parameters, not loaded from config.

### Cross-Phase Review Concerns

#### CP1. Test coverage gaps compound (REVIEWS.md §2)
Every "Verifies" line should map to a named test file. Phase 6 unit tests exist but integration test coverage is missing.

#### CP2. Schema migration strategy (REVIEWS.md §6)
Phase 6 adds DuckDB tables, KuzuDB node types, Qdrant collections. No migration tool (`ops/migrate.py`) exists.

#### CP3. Missing ops/verify_isolation.py (REVIEWS.md §5)
Cross-DB process isolation verification tool not built. Relevant since Phase 6 adds storage adapters with multi-store access.

### What's Already Correct
- FDE STOP_HUNTED 48h recovery check ✅
- FDE STOP_LOSS_CORRECT 30d check ✅
- Brier scoring ✅
- Persona weight refitting ✅
- Causal attribution ✅
- Lesson extraction ✅
- Flywheel health snapshot ✅
- Override clustering ✅
- Severity multiplier tuning ✅
- Episodic context 200-word brief ✅
- 619-line FDE unit test file ✅
- 328-line calibration unit test file ✅
