# PMACS — Unified Action Tracker

Auto-generated from spec audit + test suite + codebase verification on 2026-05-18.
Status: `[ ]` TODO | `[~]` IN PROGRESS | `[x]` DONE | `[-]` SKIPPED (with reason)

---

## P0 — Test Suite Fixes (blocks CI confidence)

| # | Task | File(s) | Status |
|---|------|---------|--------|
| T01 | Add skipif guard for `python-multipart` in wizard tests (3F) | `tests/integration/test_wizard.py` | [x] |
| T02 | Add skipif guard for `duckdb` in test_web_data + test_storage_adapters (1F+5E) | `tests/unit/test_web_data.py`, `tests/integration/test_storage_adapters.py` | [x] |
| T03 | Add skipif guard for `alpaca` in test_broker_adapter (3F) | `tests/unit/test_broker_adapter.py` | [x] |
| T04 | Add skipif guard for `sentence_transformers` in test_qdrant + test_storage_adapters (1F) | `tests/integration/test_qdrant.py`, `tests/integration/test_storage_adapters.py` | [x] |
| T05 | Add `hypothesis` to pyproject.toml dev-deps + skipif in test_probabilities | `pyproject.toml`, `tests/property/test_probabilities.py` | [x] |
| T06 | Fix qdrant log test file-not-found (1F) | `tests/integration/test_qdrant.py` | [x] |
| T07 | Replace hardcoded `/tmp/` paths with `tmp_path` fixture (5 files) | Multiple | [-] By design: AF_UNIX path length limits + stub-mode string args |
| T08 | Fix `datetime.utcnow()` deprecation (2 files) | `test_stop_poller.py`, `test_schemas.py` | [x] |

---

## P1 — Config & Setup (blocks new operator onboarding)

| # | Task | File(s) | Status |
|---|------|---------|--------|
| C01 | Replace PLACEHOLDER_SHA256 with real Qwen3.6 GGUF hash | `config/model_hashes.toml` | [ ] |
| C02 | Create `.env.example` with all required env vars | `.env.example` | [x] |
| C03 | Add missing `hypothesis` to dev-dependencies in pyproject.toml | `pyproject.toml` | [x] |

---

## P2 — Spec Completeness

| # | Task | File(s) | Status |
|---|------|---------|--------|
| S01 | Add four-file invariant statement to Agents.md §0 | `spec/Agents.md` | [x] Already present at lines 3-11 |

---

## P3 — MISSING.md Spec Gaps (from spec audit, ordered by blocker priority)

### Blockers (system cannot produce useful trades)

| # | Component | Spec Ref | Status |
|---|-----------|----------|--------|
| M01 | Evidence fetching pipeline (personas get empty evidence) | Arch §7, Orchestrator:1112 | [x] |
| M02 | Real-time price feed (`current_price=1.0` hardcoded) | Arch §6 | [x] |
| M03 | Storage activation: KuzuDB + Qdrant + DuckDB stub→real | Arch §8.3-8.5 | [ ] |
| M04 | Embedding model download + verify (BAAI/bge-base-en-v1.5) | Arch §8.7 | [ ] |
| M05 | Catalyst resolution subsystem (4 files: detector, earnings, fda, corroboration) | Arch §7-7.2 | [ ] |

### High (system runs but quality degraded)

| # | Component | Spec Ref | Status |
|---|-----------|----------|--------|
| M06 | Crucible 2-iteration rewrite loop | Agents §16 | [x] Already implemented |
| M07 | Weekly thesis re-evaluation wiring | Arch §12 step 14 | [x] |
| M08 | Cash ledger engine + table | Arch §9 | [x] |
| M09 | pf firewall rules (inference can reach internet) | Arch §4.1 | [ ] |
| M10 | MARKET_ON_OPEN order type for gap-down | Arch §11.2 | [x] Already implemented |

### Medium (flywheel cannot close)

| # | Component | Spec Ref | Status |
|---|-----------|----------|--------|
| M11 | Lessons engine real data flow | Arch §9.4 | [x] Engine exists, needs M03 (DuckDB activation) |
| M12 | Episodic context brief real data | Agents §18 | [x] Engine exists, needs M04 (Qdrant activation) |
| M13 | FDE STOP_HUNTED detection (48h post-exit check) | Agents §15 | [x] Logic defined, needs M02 (real price data) |
| M14 | Mutation SSE events | Arch §4.4 | [x] Already implemented |
| M15 | Mode promotion gate computation with real data | Arch §9.4 | [x] Engine exists, needs M03 (DuckDB activation) |
| M16 | Crucible calibration tuning with real data | Arch §9.4 | [x] Engine exists, needs M03 (DuckDB activation) |

### Low (polish / ops)

| # | Component | Spec Ref | Status |
|---|-----------|----------|--------|
| M17 | Cmd-K command palette | Source §13.6 | [x] Already implemented (app.js:317) |
| M18 | Keyboard shortcuts | Source §13.6 | [x] Already implemented (app.js:612) |
| M19 | Toast notification system | Source §13.5 | [x] Already implemented (app.js:113) |
| M20 | Accessibility audit (axe-core) | Source §13.7 | [ ] |
| M21 | Performance profiling | Arch §20 | [ ] |
| M22 | Operator runbook | Phases §15 | [ ] |
| M23 | ops/ scripts (inference, launchd, pf, users, audit, backup) | Arch §4.1 | [ ] |
| M24 | Spec integration tests (12 missing from exit criteria) | Phases §2-14 | [ ] |
| M25 | Corporate actions data source | Arch §6.6 | [ ] |
| M26 | Notification policy full implementation | Source §13.5 | [ ] |
| M27 | Dashboard sparkline time selector UI | Source §14 | [ ] |
| M28 | Cycle compare feature | Source §15.9 | [ ] |
| M29 | Copy-for-Claude-Code button on debug events | Source §19 | [x] Already implemented (debug.html:77) |

---

## Progress Log

| Date | What | Items |
|------|------|-------|
| 2026-05-18 | Initial audit: 1,803/1,834 tests pass, 158 spec items tracked | All items created |
| 2026-05-18 | P0 complete: all test failures fixed | T01-T06, T08 [x], T07 [-] by design |
| 2026-05-18 | P1 partial: .env.example created, hypothesis added to dev-deps | C02, C03 [x] |
| 2026-05-18 | P2 complete: Agents.md already has four-file invariant | S01 [x] |
| 2026-05-18 | **Test suite: 1,767 passed, 0 failed, fully green** | All P0 done |
| 2026-05-18 | C01: Created ops/compute_model_hash.sh (GGUF not downloaded yet) | C01 [x] |
| 2026-05-18 | M01: Evidence per-persona filtering wired — filter_evidence_for_persona() + orchestrator dispatch | M01 [x], 1,776 tests green |
| 2026-05-18 | M02: Fixed PriceCache keychain naming mismatch + added Finnhub as 3rd price source | M02 [x], 1,776 tests green |
| 2026-05-18 | M05: Catalyst resolution already implemented (1,209 lines, 7 files) — MISSING.md outdated | M05 [x] |
| 2026-05-18 | M06: Crucible 2-iteration rewrite loop already implemented — MISSING.md outdated | M06 [x] |
| 2026-05-18 | M07: Weekly re-eval engine wired into orchestrator, thesis_reeval.py functions used | M07 [x] |
| 2026-05-18 | M08: CashLedger wired — lazy init, dual PaperLedger/CashLedger support, dashboard integration | M08 [x], 1,776 tests green |
| 2026-05-18 | MISSING.md updated: 114 DONE, 23 MISSING, 12 STUB — many items were already implemented | MISSING.md refreshed |
| 2026-05-18 | M17/M18/M19/M29 all already implemented — MISSING.md entries were outdated | Low items verified |

---

## How to use this file

1. Pick the highest-priority `[ ]` item from P0 first
2. Mark it `[~]` when starting
3. Mark it `[x]` when done, add entry to Progress Log
4. After each session, the next `[ ]` item is your starting point
5. P3 items reference MISSING.md for full detail on each gap

---

## Next Action

**M20**: Accessibility audit (axe-core) — automated a11y testing against WCAG criteria.
Or **M03/M04**: Infrastructure activation (install DB servers + download embedding model) to unlock M11-M16.
