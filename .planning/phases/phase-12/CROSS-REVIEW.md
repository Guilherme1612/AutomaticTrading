# Phase 12: Cross-Review -- Spec Gap Closure

**Reviewed:** 2026-05-26T12:50:00Z
**Reviewer:** Claude (gsd-code-reviewer)
**Depth:** Deep (spec cross-reference + live verification + test execution)
**Source Documents:** PLAN.md, REVIEWS.md, REVIEW-SPEC-COMPLIANCE.md, spec/Architecture.md, spec/Phases.md, MISSING.md, implementation files
**Live Tests:** 2157 passed, 2 skipped, 0 failures

---

## Plan-Spec Alignment (score: 3/5)

### What the plan gets right

The plan's wave structure follows correct dependency ordering: data pipeline first, storage second, engines third, flywheel fourth. This matches the spec's layering (Architecture.md SS2.1 data flow rules). The plan identifies the right general areas of work.

### What the plan gets wrong

**Stale inventory (critical):** The plan was authored against 85/158 DONE components. Current MISSING.md shows 118/158 DONE as of 2026-05-24, with commit `41a773b` on 2026-05-18 declaring "SPEC-COMPLIANT." Of 25 plan tasks, approximately 20 target already-completed work:

- 1.1 Evidence Pipeline -- MISSING.md Cross-Cutting A: DONE
- 1.3 Catalyst Resolution -- MISSING.md 2.11-2.14: DONE (all 4 files exist)
- 3.1 Crucible 2-Iteration Loop -- MISSING.md 7.2: DONE
- 3.2 Weekly Re-Eval -- MISSING.md 9.4, 9.5, 9.8: DONE
- 3.3 Cash Ledger -- MISSING.md 8.6: DONE
- 3.5 MARKET_ON_OPEN -- MISSING.md 9.7: DONE
- 4.3 Mutation SSE -- MISSING.md 14.10: DONE
- 5.1 Ops Scripts -- MISSING.md Cross-Cutting E: DONE (all 7 files exist)
- 5.3 Phase 15 Polish -- Multiple items DONE (15.4, 15.5, 15.13)

**Incorrect spec section references (verified against Architecture.md):**

| Plan Item | Plan Ref | Actual Section (line #) | Verdict |
|-----------|----------|------------------------|---------|
| 1.1 Evidence Pipeline | Arch SS6.2 | SS6 starts at line 866 -- no SS6.2 subsection | Wrong |
| 1.2 Price Feed | Arch SS9.3 | SS9.3 is SizingEngine; SS9.4 (line 1860) is PricingEngine | Wrong |
| 1.4 EV/Pricing | Arch SS9.4 | SS9.4 (line 1860) confirmed -- PricingEngine | Correct |
| 2.4 DuckDB | Arch SS8.5 | SS8.5 is SQLite; SS8.6 would be DuckDB but section numbering is flat within SS8 | Misleading |

**Plan scope exceeds Phase 12:** The plan spans PMACS Build Phases 7-15, but PMACS Phase 12 (per spec/Phases.md line 446) has specific scope: FDE + cross-DB consistency + reconciliation. The plan conflates the GSD "Phase 12" meta-phase with PMACS Build Phase 12.

---

## Exit Test Coverage (score: 4/5)

### Spec-mandated PMACS Phase 12 exit tests (Phases.md lines 464-469)

Five exit tests defined. Coverage verified:

| # | Exit Test | Status | Evidence |
|---|-----------|--------|----------|
| 1 | test_fde.py: all 18 taxonomy types classify correctly | PASS | 53 tests passed, covers all 18 types + STOP_HUNTED/STOP_LOSS_CORRECT |
| 2 | STOP_HUNTED vs STOP_LOSS_CORRECT differentiation | PASS | test_cross_db.py TestStopTypeDifferentiation: 5 tests including 48h recovery, stays-low, trailing, macro shock, default |
| 3 | Cross-DB reconciler detects mismatch | PASS | test_cross_db.py TestCrossDbConsistencyIntegration: 4 tests + test_fde.py TestCrossDbConsistency: 4 tests |
| 4 | Dead-letter queue: write failure -> queued -> retry succeeds | PASS | test_cross_db.py TestDeadLetterBackoffSchedule: 5 tests covering default/custom schedules, exhaustion, retry |
| 5 | FailedAssumption KuzuDB traversal | PASS | Verified live: `MATCH (h:Holding)-[:FAILED_ASSUMPTION]->(fa) RETURN fa` returns correct results with real kuzu 0.11.3 |

### What the plan's custom exit test covers (PLAN.md lines 20-26)

The plan defines a broader integration test (16-ticker universe, real evidence, real prices, all stores). This is a good integration-level gate but does NOT replace the 5 specific Phase 12 exit criteria. The REVIEWS.md correctly flags this gap.

### Missing from plan's exit test

- No explicit dead-letter retry simulation test (exit test 4)
- No explicit FailedAssumption KuzuDB traversal verification (exit test 5)
- No cross-DB mismatch detection test (exit test 3)

These tests exist on disk and pass, but the plan did not enumerate them as acceptance criteria.

---

## Implementation Quality (score: 4/5)

### Pricing Engine (pmacs/engines/pricing.py, 103 lines)

**Deterministic -- LLMs never math (Non-Negotiable #2): VERIFIED.**
- `compute_ev()` uses pure arithmetic: `p_up * target_gain - p_down * stop_loss`
- No LLM calls, no network, no randomness
- ATR-based targets via `compute_target_and_stop()` with configurable fallbacks
- MAX_STOP_LOSS_PCT capped at 0.15 (catastrophe-net, matches Source.md)
- Config loaded from risk.toml via `load_config()`, with safe fallback defaults
- `EvInputs.current_price` defaults to 1.0 -- **risk noted below**, but pricing engine itself is correct

### Flywheel Health (pmacs/engines/flywheel_health.py, 327 lines)

**Spec-compliant.**
- `check_promotion_gates()` implements Phases.md SS3.2 with all 5 gates (cycles, trades, brier, sharpe, drawdown)
- `check_demotion_gates()` implements Phases.md SS3.5 with OR logic and tier-by-tier demotion
- Reads real data from DuckDB (`get_rolling_brier`, `get_rolling_sharpe`, `get_max_drawdown`) with graceful fallback to 0.0 when DuckDB unavailable
- All audit events include `cycle_id` (anti-pattern SS16.5 compliant)
- `log_debug` calls include appropriate levels and payloads

### Storage Adapters

**DuckDB (pmacs/storage/duckdb.py, 750 lines):** Production-quality.
- 9 tables defined matching Architecture.md SS8.6 spec
- Graceful degradation: returns empty/defaults when duckdb not installed
- All write methods include both stub-mode logging and real-mode logging
- `cycle_id` parameter on all domain methods (SS16.5 compliant)
- `error_code` on all WARN+ log events (SS16.14 compliant)

**Qdrant (pmacs/storage/qdrant.py, 378 lines):** Production-quality.
- 6 collections defined (theses, memos_persona, memos_aggregated, evidence_chunks, lessons, episodic)
- Real embedding via sentence-transformers with deterministic hash fallback
- Verified live: 768-dim embedding generated, upsert/search round-trip succeeds, cosine similarity = 1.0 for identical vectors
- Supports embedded persistent mode (no server needed), HTTP mode, and in-memory fallback

**KuzuDB (pmacs/storage/kuzu.py, 590 lines):** Production-quality.
- Full graph schema: 7 node tables + 9 edge tables matching Architecture.md SS8.4
- Verified live: FailedAssumption node creation + traversal works with kuzu 0.11.3
- Domain helpers for all CRUD operations with proper error handling
- Silent failure on link creation when source node missing (acceptable for eventual consistency)

**SQLite (pmacs/storage/sqlite.py):** Fully operational since Phase 1.
- All tables from SS8.5 present: cycles, mode_history, queue, holdings, stop_events, mutations, paper_account, dead_letter, persistent_pins

### All 5 Storage Backends: VERIFIED ACTIVE

| Store | Status | Evidence |
|-------|--------|----------|
| SQLite | ACTIVE | Tables created on init, used in all tests |
| KuzuDB | ACTIVE | kuzu 0.11.3 installed, schema creates, Cypher queries execute, FailedAssumption traversal works |
| Qdrant | ACTIVE | qdrant-client 1.18.0 installed, embedding model loaded (768-dim), upsert/search verified |
| DuckDB | ACTIVE | duckdb 1.5.2 installed, 9 tables create, queries execute |
| audit.log | ACTIVE | Hash-chained writer in pmacs/storage/audit.py, verified in test_audit_chain tests |

---

## Gaps & Risks

### GAP-01: Plan is severely stale (HIGH)

The plan was written when 85/158 components were DONE. Now 118/158 are DONE with a "SPEC-COMPLIANT" commit on 2026-05-18. Approximately 80% of plan tasks target completed work. The REVIEWS.md (2026-05-19) flagged this but the plan was not regenerated.

**Impact:** Any agent executing this plan risks overwriting working code, duplicating effort, or creating regressions.

### GAP-02: `EvInputs.current_price` defaults to 1.0 (MEDIUM)

In `pmacs/engines/pricing.py` line 43: `current_price: float = 1.0`. This is the data-class default, not a runtime fallback. If a caller constructs `EvInputs` without setting `current_price`, EV computation proceeds silently with garbage input.

The REVIEWS.md flagged this as a critical risk (price=1.0 corrupts sizing), but the code still has this default. The actual risk depends on whether callers always provide a real price. The orchestrator uses the PriceCache (Cross-Cutting B: DONE), so this is mitigated in practice but not structurally defended.

**Recommendation:** Change default to `None` and add validation: `if current_price is None or current_price <= 0: raise ValueError`.

### GAP-03: Qdrant search returns deterministic hash vectors when model unavailable (LOW)

The `get_embedding()` fallback (qdrant.py line 347-348) generates a deterministic but semantically meaningless 768-dim vector from SHA256. If sentence-transformers is not installed, similarity searches will produce meaningless results without any warning to the operator.

**Recommendation:** Log a WARN-level event when fallback embedding is used, with `error_code="QDRANT_DUMMY_EMBEDDING"`.

### GAP-04: Flywheel reads return 0.0 when DuckDB has no data (MEDIUM)

`get_rolling_brier()`, `get_rolling_sharpe()`, and `get_max_drawdown()` all return 0.0 when DuckDB has no data or no rows. For promotion gates, 0.0 brier passes (lower is better), 0.0 sharpe fails (higher is better), 0.0 drawdown passes (lower is better). This means early in the system lifecycle with empty DuckDB:
- brier gate: passes (0.0 <= threshold)
- sharpe gate: fails (0.0 < min_sharpe)
- drawdown gate: passes (0.0 <= threshold)

This is actually safe because the sharpe gate blocks promotion when no data exists, but it is not documented behavior. If thresholds change, this silent-default could become dangerous.

### GAP-05: KuzuDB `timestamp()` function in Cypher may not work (LOW)

In kuzu.py line 229: `ts: timestamp($ts)`. KuzuDB 0.11.3 may not support a `timestamp()` function. In live testing, the node was created successfully, but the `ts` field value was not verified. If `timestamp()` fails silently, the ts field may be NULL, which would not break traversal but could affect time-based queries.

### GAP-06: Missing integration test for real storage round-trip (MEDIUM)

While individual adapter tests pass, there is no integration test that runs a full cycle with all 5 stores connected and verifies data consistency across them. The `test_cross_db.py` tests use mock adapters. The `test_smoke_cycle.py` tests run the pipeline but with stub storage.

The spec's Phase 12 exit test 3 requires: "Cross-DB reconciler detects a deliberately introduced mismatch." This test exists but uses mock paths, not real storage connections.

### GAP-07: Corporate actions still STUB (LOW)

MISSING.md 2.4: `corp_actions.py` is STUB. Not directly in Phase 12 scope but affects data pipeline completeness for real trading.

---

## Recommendations

1. **Regenerate PLAN.md from current MISSING.md.** The plan should only contain tasks for STUB, PARTIAL, and MISSING items. DONE items should be converted to verification tasks with explicit pass criteria. This was recommended by REVIEWS.md on 2026-05-19 and has not been done.

2. **Add structural guard on price default.** Change `EvInputs.current_price` default from `1.0` to `None` with validation. This eliminates the class of bugs where a caller forgets to pass a real price.

3. **Add real-storage integration test.** Create a test that initializes all 5 stores with real backends (kuzu, qdrant embedded, duckdb file), runs a synthetic cycle, and verifies: KuzuDB lineage traversal, Qdrant similarity search returns stored thesis, DuckDB rolling_metrics row exists, SQLite holding row exists, audit chain verifies. This directly addresses the Phase 12 exit test requirement for cross-DB consistency.

4. **Remove Phase 15 polish items from Phase 12 plan.** Wave 5.3 (agents animations, drag-drop, sparklines, Cmd-K, keyboard shortcuts, accessibility) is Phase 15 scope and most items are already DONE. Clutters the plan.

5. **Document flywheel gate behavior with empty DuckDB.** Add a comment or docstring in `flywheel_health.py` explaining that 0.0 defaults are safe because the sharpe gate blocks promotion when no data exists.

6. **Add WARN log for Qdrant dummy embedding fallback.** One line change to log when hash-based embeddings are used instead of real sentence-transformers embeddings.

7. **Verify KuzuDB timestamp storage.** Run a query after writing a FailedAssumption to confirm the `ts` field contains a valid timestamp string, not NULL.

---

## Overall Score (3.5/5)

The implementation quality is strong (4/5): pricing engine is deterministic, storage adapters are production-quality with graceful degradation, all 5 stores are installed and functional, 2157 tests pass, and the "SPEC-COMPLIANT" milestone is substantively earned. The Phase 12 exit tests (all 5) pass.

The plan itself is the weak point (2/5): it is severely stale, targets 80% already-completed work, has incorrect spec section references, and defines a custom exit test that does not map to the spec's Phase 12 exit criteria. The three independent reviewers (REVIEWS.md) unanimously flagged these issues on 2026-05-19, but the plan was not regenerated.

The system is functionally complete for PMACS Phase 12 scope. The remaining gaps are:
- STUB: corporate actions (Phase 2 scope)
- PARTIAL: DuckDB/KuzuDB/Qdrant wired but need real-storage integration verification
- PARTIAL: notification policy UI, dashboard sparkline selector (Phase 15 scope)

**Verdict: System passes Phase 12 exit tests. Plan needs regeneration before further execution.**

---

_Reviewed: 2026-05-26T12:50:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
