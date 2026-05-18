---
phase: 12
plan: Spec Gap Closure — From Structural to Functional
subsystem: full-stack
tags: [data-pipeline, storage, engines, flywheel, ops]
completion: 2026-05-18
duration: 1 session
---

Phase 12 transforms PMACS from structurally complete (all files exist, schemas compile) to functionally complete (evidence flows, prices are real, stores persist data, flywheel closes).

## Wave 1: Data Pipeline Activation (pre-existing)

All 4 tasks verified complete from prior sessions:
- 1.1 Evidence fetching pipeline — 13 sources, persona mapping, staleness, dedup
- 1.2 Real-time price feed — PriceCache (Polygon+Alpaca), wired to all consumers
- 1.3 Catalyst resolution — 4 resolvers with Tier A/B/C corroboration + 3σ outlier guard
- 1.4 EV/Pricing — ATR-based volatility formulas, real compute_ev()

## Wave 2: Storage Activation

| Task | Result | Files |
|------|--------|-------|
| 2.1 Embedding model | `dim=768` confirmed, sentence-transformers working | — |
| 2.2 KuzuDB | Added 3 missing edges + 5 helper methods (420→590 lines) | `pmacs/storage/kuzu.py` |
| 2.3 Qdrant | Spec-complete, 2 logging fixes | `pmacs/storage/qdrant.py` |
| 2.4 DuckDB | 4 tables added (resolutions_history, persona_subsector_affinity, evidence_archive, scan_records) + 4 helper methods | `pmacs/storage/duckdb.py` |

## Wave 3: Engine Completion

| Task | Result | Files |
|------|--------|-------|
| 3.1 Crucible loop | 2-iteration INITIAL→REWRITE→DONE/ABORT state machine with `_rebuild_evidence_brief()` | `pmacs/nervous/orchestrator.py` |
| 3.2 Re-eval wiring | Full pipeline wired (evidence→personas→arbitration), EXIT_THESIS_INVALIDATED on broken thesis | `pmacs/nervous/orchestrator.py` |
| 3.3 Cash ledger | Created with canonical schema (append-only snapshots), seed/apply_flow/validate_total | `pmacs/engines/cash_ledger.py` |
| 3.4 STOP_HUNTED | Pre-existing — already implemented | — |
| 3.5 MARKET_ON_OPEN | Added OPG time-in-force with version-safe fallback | `pmacs/sim/alpaca_paper_adapter.py` |

## Wave 4: Flywheel Closure

| Task | Result | Files |
|------|--------|-------|
| 4.1 Lessons engine | Pre-existing — real data flow already wired | — |
| 4.2 Episodic context | Pre-existing — DuckDB affinity + FDE history | — |
| 4.3 Mutation SSE | All 8 event types added, 2 naming bugs fixed (promoted, rolled_back) | `pmacs/mutation/daemon.py`, `pmacs/mutation/rollback.py`, `pmacs/nervous/mutation.py` |
| 4.4 Mode promotion | Pre-existing — real Brier/Sharpe/drawdown from DuckDB | — |

## Wave 5: Ops + Test Coverage

| Task | Result | Files |
|------|--------|-------|
| 5.1 Ops scripts | Created `install_system_users.sh`; all others pre-existing | `ops/install_system_users.sh` |
| 5.2 Integration tests | All 34 test files pre-existing | — |
| 5.3 Polish items | Deferred (UI work, lower priority) | — |

## Verification

- Unit tests: 746/747 pass (1 pre-existing `test_cdf_accuracy` failure)
- All modified files parse cleanly
- DuckDB: 8 tables created, all helper methods callable
- CashLedger: seed/apply_flow/validate_total working
- KuzuDB: 7 node tables + 9 edge tables
- Qdrant: 5 collections, 768-dim cosine vectors
- Orchestrator: Crucible loop + re-eval pipeline parse and structurally correct

## Deviations

- Cash ledger uses canonical `paper_account` schema from `sqlite.py` (append-only snapshots) instead of single-row UPDATE
- Crucible `_rebuild_evidence_brief()` annotates original evidence with attack context (deterministic Python, no LLM)
- Re-eval uses 180s persona timeout (vs normal 270s) for faster batch processing
- 2 naming bugs fixed in mutation SSE: `mutation.promoted` was `mutation.ready_for_review`, `mutation.rolled_back` was `mutation.rollback`
- Wave 5.3 polish items (UI animations, sparklines, Cmd-K) deferred as lower priority
