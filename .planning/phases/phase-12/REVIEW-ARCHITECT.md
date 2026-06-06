# Architect Review -- Phase 12: Spec Gap Closure

## Summary

The Phase 12 plan is significantly stale relative to the current MISSING.md. The plan was authored against a state of 85/158 DONE and 38 MISSING, but MISSING.md now shows 118/158 DONE and only 20 MISSING. Roughly half the work items in the plan target components that are already DONE, conflating "storage activation" (infrastructure provisioning) with "storage implementation" (code writing). The wave ordering is logical for the remaining real gaps, but the plan must be refreshed before execution to avoid redundant work and misallocated effort.

## Findings

### [CRITICAL] Plan Written Against Stale MISSING.md -- ~40% of Tasks Already Done
- **Plan ref:** All waves, global
- **Issue:** The plan states "85/158 DONE, 18 PARTIAL, 17 STUB, 38 MISSING" (line 13-16). The current MISSING.md (audited 2026-05-18) shows "118 DONE, 8 PARTIAL, 12 STUB, 20 MISSING." That is 33 additional components completed since the plan was written. The following plan items target already-DONE work:

  - **1.1 Evidence Fetching Pipeline** -- MISSING.md Cross-Cutting A: "Evidence Per-Persona Filtering -- DONE." The evidence router and `filter_evidence_for_persona()` exist and work. `orchestrator.py` already calls evidence fetch at step 13.
  - **1.3 Catalyst Resolution Subsystem** -- MISSING.md items 2.11-2.14 are all DONE. All 4 files exist: `catalyst_detector.py` (275 lines), `earnings_resolver.py` (196 lines), `fda_resolver.py` (194 lines), `corroboration.py` (297 lines). The plan says "Files to create: 4 new files" -- they already exist.
  - **1.2 Real-Time Price Feed** -- MISSING.md Cross-Cutting B: "Real-Time Price Feed -- DONE." PriceCache with 3-source strategy (Polygon -> Finnhub -> Alpaca) is wired at orchestrator step 13d. The `current_price = 1.0` is only a fallback default, not the primary path.
  - **3.1 Crucible 2-Iteration Rewrite Loop** -- MISSING.md item 7.2: DONE. 2-iteration rewrite loop with `_rebuild_evidence_brief()` implemented.
  - **3.2 Weekly Thesis Re-Evaluation** -- MISSING.md items 9.4, 9.5, 9.8: all DONE. `thesis_reeval.py` wired to orchestrator steps 14-15.
  - **3.3 Cash Ledger Engine** -- MISSING.md item 8.6: DONE. CashLedger engine wired to orchestrator with lazy init, seed, apply_flow, dashboard integration.
  - **3.5 MARKET_ON_OPEN for Gap-Down** -- MISSING.md item 9.7: DONE. `OrderType.MARKET_ON_OPEN` + OPG time-in-force in alpaca adapter.
  - **4.3 Mutation SSE Events** -- MISSING.md item 14.10: DONE. All 8 event types wired in `daemon.py`.
  - **5.1 Ops Scripts** -- ALL 7 ops scripts already exist on disk: `start_inference.sh`, `install_launchd.sh`, `install_pf_rules.sh`, `install_system_users.sh`, `audit_chain_verify.py`, `backup_verify.py`, `spec_consistency.py`.
  - **5.2 Integration Tests** -- ALL 14 test files the plan says to create already exist: `test_data_sources.py`, `test_llm_call.py`, `test_3persona_cycle.py`, `test_7persona_cycle.py`, `test_full_pipeline.py` (symlink to `test_full_cycle.py`), `test_crucible_budget.py` (exists in both unit/ and integration/), `test_calibration.py`, `test_fde.py`, `test_cross_db.py`, `test_episodic.py`, `test_mutation_lifecycle.py`, `test_rollback.py`, `test_smoke_cycle.py` (fixtures/smkt.py), and `tests/fixtures/` exists.

  Roughly 20 of the plan's ~25 tasks are already complete. Executing the plan as-written would waste effort re-implementing DONE components.

- **Recommendation:** Regenerate the plan from the current MISSING.md. Filter to only STUB and MISSING items. Group those into waves.

### [HIGH] EV/Pricing Engine Classification Discrepancy
- **Plan ref:** 1.4, MISSING item 7.4
- **Issue:** MISSING.md lists item 7.4 "EV / pricing engine" as STUB with note "hardcoded target_gain=0.10, stop=0.15." But inspection of `pmacs/engines/pricing.py` reveals it uses `compute_target_and_stop(atr_pct)` with ATR-based dynamic targets, not hardcoded 0.10/0.15. The `DEFAULT_TARGET_GAIN_PCT` is used only as a fallback when ATR is unavailable. The MISSING.md description may be stale. The plan's task 1.4 ("Replace hardcoded target_gain=0.10, stop=0.15") may already be done.
- **Recommendation:** Verify pricing.py against Arch section 9.4 spec. If ATR-based targets match spec, update MISSING.md item 7.4 from STUB to DONE and remove task 1.4.

### [HIGH] Storage Activation Conflates Code with Infrastructure
- **Plan ref:** 2.1-2.4
- **Issue:** The plan correctly identifies that KuzuDB, Qdrant, and DuckDB adapters are in stub mode (MISSING items 11.7-11.12). However, the plan describes this as "Stub-to-Real Migration" implying code changes. The storage adapter code is structurally complete -- it already has real Cypher/vector/SQL operations guarded behind `if kuzu_available` / `if qdrant_available` / `if duckdb_available` checks. What is actually MISSING is:
  1. Installing `kuzu`, `qdrant-client`, `duckdb` Python packages (add to pyproject.toml)
  2. Running Qdrant server (Docker or local)
  3. Downloading the embedding model (item 8.12)
  4. Populating initial schema/collections

  This is an infrastructure provisioning task, not a code-writing task. The plan's descriptions of "Replace stub returns with real operations" may mislead the implementer into rewriting working code.
- **Recommendation:** Reframe Wave 2 as "Infrastructure Provisioning + Smoke Testing" rather than "Stub-to-Real Migration." Verify the adapters already handle the real-client path correctly before assuming code changes are needed.

### [HIGH] FDE STOP_HUNTED: Dependency on 48h Post-Exit Price Data
- **Plan ref:** 3.4
- **Issue:** The plan describes implementing STOP_HUNTED detection as "48h post-exit price check: if price recovers above entry + 2% within 48h." MISSING.md item 12.8 says "Logic defined, needs real price data for 48h check." The implementation challenge here is not code but data: the system needs to schedule a delayed check 48 hours after exit, requiring either a background job or a scan of historical exits on each cycle. The plan does not address the scheduling mechanism.
- **Recommendation:** Specify the scheduling approach. Options: (a) stop-loss daemon scans recent exits hourly, (b) a scheduled callback stored in SQLite, (c) on-boot scan. The spec (Agents section 15) should dictate which; if silent, choose (a) as simplest.

### [MEDIUM] Missing Items Not Covered by Plan
- **Plan ref:** Global completeness
- **Issue:** The current MISSING.md lists 20 MISSING items. Several are not addressed in the plan:
  - **Item 8.9: Live trading adapter (IBKR)** -- Plan omits entirely. This is a LIVE-mode requirement; may be legitimately deferred past Phase 12 (paper trading). Should be explicitly noted as out-of-scope.
  - **Item 10.12: Empty/loading/error states** -- PARTIAL. Not addressed in the plan.
  - **Item 10.14: Notification policy UI** -- PARTIAL. Not addressed in the plan.
  - **Item 10.15: Cycle compare feature** -- MISSING. Not addressed in the plan.
  - **Item 15.6: Accessibility audit (axe-core)** -- MISSING. Listed in Wave 5.3 but that section is a catch-all with no concrete specification.
  - **Item 15.11: docs/operator_runbook.md** -- MISSING. Not mentioned in the plan despite being in Phase 15 exit test.
  - **Item 15.12: Notification policy implementation** -- PARTIAL. Not addressed.
  - **Item 14.13: Offline A/B test harness** -- MISSING. The plan mentions `test_mutation_lifecycle.py` and `test_rollback.py` (both exist) but not the `tests/mutation_eval/` directory.
- **Recommendation:** For each uncovered item, explicitly mark as "in scope," "deferred to Phase 13/14," or "deferred to Phase 15." Do not silently omit items.

### [MEDIUM] Exit Test is Aspirational, Not Measurable
- **Plan ref:** Exit Test (lines 19-27)
- **Issue:** The exit test says "Fetches real evidence from >= 10/13 data sources" and "All 15 spec-defined exit test categories pass." This is the right aspiration but the wrong level for a plan-level exit test. The plan should map each spec exit test to a specific automated test file or verification command. Saying "all 15 pass" without listing them means the implementer cannot check progress incrementally.
- **Recommendation:** Enumerate the 15 spec exit tests with their test file paths (most already exist). Add a checklist with current pass/fail status for each.

### [MEDIUM] Wave 3 and 4 Dependency Graph Incorrect
- **Plan ref:** Execution Order diagram (lines 287-304)
- **Issue:** The diagram shows Wave 3 tasks (Crucible, re-eval, cash ledger, FDE, MOO) as depending on Waves 1-2. But as noted above, 4 of 5 Wave 3 tasks are already DONE. The only real Wave 3 task is FDE STOP_HUNTED (3.4), which depends on real price data (Wave 1.2 -- also already DONE). Similarly, Wave 4's tasks (Lessons, Episodic, Mutation SSE, Mode Gates) -- items 4.3 (mutation SSE) is DONE, and items 4.1/4.2/4.4 depend on storage activation (Wave 2), which is the real dependency.
- **Recommendation:** Rebuild the dependency graph from the actual remaining gaps only. The real dependency chain is: Wave 2 (storage provisioning) -> Wave 4 (engines needing storage) -> Wave 5 (polish).

### [LOW] Phase 15 Polish Items in a Phase 12 Plan
- **Plan ref:** 5.3
- **Issue:** Wave 5.3 includes "Agents page animations," "Pipeline drag-drop refinement," "Dashboard sparklines," "Cmd-K command palette," "Toast notification system," etc. Per CLAUDE.md's GSD mapping, Phase 15 maps to GSD Phase 8 (Polish). Including Phase 15 items in a Phase 12 plan is scope creep. The plan's stated goal is "Spec Gap Closure" for Phases 1-14 -- Phase 15 polish should be its own plan.
- **Recommendation:** Remove Wave 5.3 from this plan. Create a separate Phase 15 plan for polish items.

### [LOW] DuckDB Storage: Missing Table Inventory
- **Plan ref:** 2.4
- **Issue:** The plan lists 9 DuckDB tables to create. Verify this matches the spec. From Arch section 8.5, the expected DuckDB tables are: `resolutions_history`, `rolling_metrics`, `persona_performance`, `persona_ticker_affinity`, `persona_subsector_affinity`, `evidence_archive`, `scan_records`, `failure_taxonomy_counts`. That is 8 tables. The plan lists 9 -- verify the extra one is not a phantom.
- **Recommendation:** Cross-reference the DuckDB table list against `pmacs/storage/duckdb.py` and Arch section 8.5 to confirm exact table inventory.

## Wave-by-Wave Assessment

- **Wave 1 (Data Pipeline Activation):** 3 of 4 tasks already DONE (evidence pipeline, price feed, catalyst resolution). Only EV/pricing engine (task 1.4) may need work, and even that needs verification against current code. Wave is nearly complete before starting.

- **Wave 2 (Storage Activation):** This is the highest-value wave in the plan. Storage stub-to-real is the primary blocker for the flywheel. Correctly sequenced after data flows in. The dependency on embedding model (2.1) before Qdrant activation (2.3) is correct.

- **Wave 3 (Engine Completion):** 4 of 5 tasks already DONE. Only FDE STOP_HUNTED (3.4) remains as real work. The wave should be reduced to a single task.

- **Wave 4 (Flywheel Closure):** 1 of 4 tasks already DONE (mutation SSE). Remaining 3 tasks (Lessons, Episodic, Mode Gates) are real gaps that depend on Wave 2 storage activation. Correctly sequenced.

- **Wave 5 (Ops + Tests + Polish):** Ops scripts (5.1) and integration tests (5.2) already exist on disk. Wave 5.3 (Phase 15 polish) is scope creep. This wave should be re-scoped to "verify existing ops/tests pass" rather than "create from scratch."

## Overall Assessment

**REQUEST_CHANGES**

The plan is architecturally sound in its wave ordering and dependency logic, but it was authored against a stale component inventory. Before execution, the plan must be regenerated from the current MISSING.md (118 DONE / 20 MISSING). The real remaining work is approximately:

1. Storage activation: install kuzu/qdrant-client/duckdb packages, provision Qdrant server, download embedding model (infrastructure, not code)
2. Storage adapter smoke testing: verify existing real-client code paths work with live servers
3. FDE STOP_HUNTED: implement 48h post-exit price monitoring
4. Lessons/Episodic/Mode Gates: wire to real storage (code depends on #1)
5. Phase 15 Polish items: should be a separate plan

Estimated remaining real work: 30-40% of what the plan describes. Regenerating from current state will produce a more focused and executable plan.

---

_Reviewed: 2026-05-19T12:08:00Z_
_Reviewer: Claude (gsd-code-reviewer architect)_
