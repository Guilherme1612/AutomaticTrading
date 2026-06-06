# Spec Compliance Review -- Phase 12

**Reviewed:** 2026-05-19T12:09:00Z
**Reviewer:** Claude (gsd-code-reviewer)
**Plan:** `.planning/phases/phase-12/PLAN.md`
**Depth:** Standard (spec cross-reference analysis)

---

## Summary

The Phase 12 plan is structurally sound in its wave organization and dependency graph, but contains significant stale references to MISSING.md items that have already been implemented. Multiple plan items describe creating or implementing components that are already marked DONE in MISSING.md and exist on disk. The plan conflates PMACS Build Phases 1-15 with GSD Phase 12 (which is itself a meta-phase spanning PMACS phases). This creates ambiguity about scope. The plan's exit test is reasonable but does not map cleanly to any single PMACS build phase exit test. Most critically, the plan omits the specific PMACS Phase 12 exit test from Phases.md (FDE 18-type classifier + cross-DB consistency + reconciliation) and instead redefines exit criteria.

## Spec Traceability Matrix

| Plan Item | Plan Spec Ref | Actual Spec | Match? | Notes |
|-----------|---------------|-------------|--------|-------|
| 1.1 Evidence Pipeline | Arch 6.2, Phases 2 exit 3 | Arch 6.2 (Universe), Arch 6.1 (Sources) | PARTIAL | Ref 6.2 is Universe schema, not evidence routing. Evidence routing is a cross-cutting concern across Arch 6.1-6.4. MISSING.md shows this as DONE (Cross-Cutting A). |
| 1.2 Real-Time Price Feed | Arch 6.1, Arch 9.3 | Arch 6.1 (Sources), Arch 9.3 (SizingEngine) | PARTIAL | Spec ref is imprecise. PricingEngine is Arch 9.4, not 9.3 (9.3 is SizingEngine). MISSING.md shows PriceCache as DONE (Cross-Cutting B). |
| 1.3 Catalyst Resolution | Arch 7, Arch 3 | Arch 7 (full section) | YES | Accurate. But MISSING.md 2.11-2.14 show all 4 files as DONE and on disk. Plan says "Files to create: 4 new files." |
| 1.4 EV/Pricing Engine | Arch 9.4 | Arch 9.4 (PricingEngine) | YES | Accurate. MISSING.md 7.4 shows STUB status. Correct item to work on. |
| 2.1 Embedding Model | Arch 8.7, Source 12.4.5 | Arch 8.7 | YES | Accurate. MISSING.md 8.12 shows MISSING. Correct. |
| 2.2 KuzuDB Migration | Arch 8.3, 8.4 | Arch 8.3 (Holding), 8.4 (KuzuDB graph) | YES | Accurate. MISSING.md 11.8 shows STUB. Correct. |
| 2.3 Qdrant Migration | Arch 8.7 | Arch 8.7 (Qdrant collections) | YES | Accurate. MISSING.md 11.7 shows STUB. Correct. |
| 2.4 DuckDB Migration | Arch 8.5, 8.6 | Arch 8.5 (SQLite), 8.6 (DuckDB analytics) | PARTIAL | Ref 8.5 is SQLite tables, not DuckDB. Should reference Arch 8.6 specifically. MISSING.md 11.9 shows STUB. |
| 3.1 Crucible 2-Iteration Loop | Agents 16 | Agents 16.1-16.3 | YES | Accurate. But MISSING.md 7.2 shows DONE with 2-iteration rewrite loop implemented. Plan item is stale. |
| 3.2 Weekly Re-Eval | Arch 12 step 14-15, Source 7.3 | Arch 12 steps 14-15 | YES | Accurate. But MISSING.md 9.4, 9.5, 9.8 all show DONE. Plan item is stale. |
| 3.3 Cash Ledger | Arch 9, Arch 8.5 | Arch 9 (engines), Arch 8.5 (SQLite paper_account) | PARTIAL | Missing.md 8.6 shows DONE. CashLedger engine file exists on disk. Plan item is stale. |
| 3.4 FDE STOP_HUNTED | Agents 15 (types 6-7) | Agents 15.1 types 6-7 | PARTIAL | MISSING.md 12.8 shows DONE with logic defined. Plan describes implementation that already exists. Only missing is real price data (Wave 1 dependency). |
| 3.5 MARKET_ON_OPEN | Arch 11.2 | Arch 11.2 (StopLossMonitor gap-down) | YES | MISSING.md 9.7 shows DONE with OrderType.MARKET_ON_OPEN. Plan item is stale. |
| 4.1 Lessons Engine | Arch 9.4 | Arch 9.4 (mentioned in engine list) | PARTIAL | MISSING.md 11.4 shows DONE. Dependency on DuckDB activation is real, but the engine itself is implemented. |
| 4.2 Episodic Context | Agents 18 | Agents 18.1-18.4 | YES | Accurate scope. MISSING.md 13.1 shows DONE, but 13.2-13.3 show STUB and 13.5 shows MISSING. Mixed status -- plan partially valid. |
| 4.3 Mutation SSE Events | Arch 4.4 | Arch 4.4 (SSE), Arch 10 (Mutation) | PARTIAL | MISSING.md 14.10 shows DONE with "All 8 event types wired in daemon.py." Plan item is stale. |
| 4.4 Mode Promotion Gates | Phases 3, Arch 9.4 | Phases 3 (gates), Arch 9.4 (FlywheelHealth) | YES | Accurate concept. Plan is valid for wiring real data. |
| 5.1 Ops Scripts | Cross-Cutting E | Arch 4.1 (ops scripts) | PARTIAL | Plan lists 7 files but ops/ already contains start_inference.sh, install_launchd.sh, install_pf_rules.sh, install_system_users.sh. Plan says "Files to create: 7 new files" but 4 already exist. |
| 5.2 Integration Tests | Cross-Cutting F | Phases 2-15 exit tests | YES | Accurate. These tests are genuinely missing per MISSING.md. |
| 5.3 Phase 15 Polish | Items 15.1-15.13 | Source 13-15, Phases 15 | PARTIAL | Several items marked DONE in MISSING.md: 15.4 Cmd-K (DONE), 15.5 Keyboard shortcuts (DONE), 15.13 Copy button (DONE). Plan does not acknowledge this. |

## Deviations from Spec

### [HIGH] D-01: Plan Re-implements Already-DONE Components

- **Plan refs:** 1.3 (Catalyst Resolution), 3.1 (Crucible Loop), 3.2 (Weekly Re-Eval), 3.3 (Cash Ledger), 3.5 (MARKET_ON_OPEN), 4.3 (Mutation SSE)
- **Spec says:** MISSING.md (which cross-references all 4 spec files) marks these as DONE with implementation details.
- **Plan does:** Describes implementing these as if they are missing. Plan item 1.3 says "Files to create: 4 new files in pmacs/data/resolution/" but all 4 files already exist on disk (catalyst_detector.py, earnings_resolver.py, fda_resolver.py, corroboration.py).
- **Risk:** Wasted effort re-implementing working code. Risk of breaking existing implementations by overwriting. Risk of introducing divergence between what the plan says was done vs. what was already done.
- **Recommendation:** Mark these items as VALIDATION tasks instead of implementation tasks. Verify the existing implementations match spec, and only modify if they fall short. Update plan language from "Files to create" to "Files to verify."

### [HIGH] D-02: Plan Exit Test Does Not Match PMACS Phase 12 Exit Test

- **Plan ref:** Exit Test section (lines 20-26)
- **Spec says:** Phases.md Phase 12 exit test requires: (1) all 18 FDE taxonomy types classify correctly, (2) STOP_HUNTED vs STOP_LOSS_CORRECT differentiation with 48h/30d price checks, (3) cross-DB reconciler detects deliberately introduced mismatches, (4) dead-letter queue retry behavior, (5) FailedAssumption KuzuDB traversal.
- **Plan does:** Defines a custom exit test: "system runs a full cycle on 16-ticker universe that fetches real evidence, passes real prices, writes resolution data, resolves catalysts, all 15 exit test categories pass, audit chain verifies."
- **Risk:** The plan's exit test is broader but less precise than the spec's Phase 12 exit test. It does not specifically call out the 5 required Phase 12 exit criteria. A system could pass the plan's exit test while failing the spec's.
- **Recommendation:** Add the exact 5 Phase 12 exit criteria from Phases.md as explicit acceptance criteria. The broader "full cycle" test is a good integration check but should be in addition to, not replacing, the spec's specific tests.

### [MEDIUM] D-03: Plan Scope Spans Multiple PMACS Phases Without Acknowledging It

- **Plan ref:** Entire plan (Waves 1-5)
- **Spec says:** Phases.md defines 15 sequential build phases, each with specific exit tests. GSD Phase 12 maps to PMACS Phases 11-12 per CLAUDE.md.
- **Plan does:** Includes items from PMACS Phase 7 (Crucible, Pricing), Phase 8 (Cash Ledger, MARKET_ON_OPEN), Phase 9 (Re-Eval), Phase 11 (Calibration, Lessons, Storage), Phase 12 (FDE), Phase 13 (Episodic), Phase 14 (Mutation SSE), Phase 15 (Polish, Ops scripts).
- **Risk:** Without tracking which PMACS phase each item belongs to, it is impossible to verify exit tests per phase. The "do not advance" rule from Phases.md 1.3 cannot be enforced.
- **Recommendation:** Tag each plan item with its PMACS build phase number. Verify PMACS phase exit tests in sequence.

### [MEDIUM] D-04: Incorrect Spec Section References

- **Plan refs:** 1.1 (Arch 6.2), 1.2 (Arch 9.3), 2.4 (Arch 8.5)
- **Spec says:**
  - Arch 6.2 is "Universe" schema, not evidence pipeline
  - Arch 9.3 is "SizingEngine", not PricingEngine (that is 9.4)
  - Arch 8.5 is "SQLite tables", not DuckDB analytics (that is 8.6)
- **Plan does:** References wrong section numbers
- **Risk:** Implementers reading the wrong spec section could build something different from what is intended.
- **Recommendation:** Correct references: 1.1 -> Arch 6.1+6.4; 1.2 -> Arch 9.4; 2.4 -> Arch 8.6.

### [MEDIUM] D-05: Plan Omits PMACS Phase 12 Exit Test Item 4 (Dead-Letter Queue)

- **Plan ref:** Wave 3 and exit test
- **Spec says:** Phases.md Phase 12 exit test 4: "Dead-letter queue: simulate a Qdrant write failure -> queued -> retry succeeds on next attempt"
- **Plan does:** Does not include a dead-letter queue test or dead-letter queue wiring task. MISSING.md 12.4-12.5 show dead_letter as DONE, but the exit test specifically requires simulating a failure and verifying retry.
- **Risk:** Phase 12 exit test 4 cannot pass without this test.
- **Recommendation:** Add dead-letter queue integration test to Wave 5.2 test list, or add a Wave 3/4 item to verify dead-letter retry behavior with real storage.

### [LOW] D-06: Plan Says "Files to Create" for Already-Existing Ops Scripts

- **Plan ref:** 5.1 Ops Scripts
- **Plan does:** Lists 7 files to create, including start_inference.sh, install_launchd.sh, install_pf_rules.sh, install_system_users.sh. All 4 already exist on disk.
- **Spec says:** These are required by Phases.md Phase 4 exit test 6 (pf rules), Phase 8 exit test 1 (system users), and Phase 3 exit test 1 (start_inference.sh).
- **Risk:** Lower risk since the plan says "create" but implementer may overwrite existing working scripts.
- **Recommendation:** Change to "verify or update" language. Specifically: audit_chain_verify.py, backup_verify.py, spec_consistency.py are genuinely missing (though backup_verify.py and spec_consistency.py appear in git status as modified, suggesting they exist).

### [LOW] D-07: Plan Item 3.4 STOP_HUNTED Uses Different Thresholds Than Spec

- **Plan ref:** 3.4 FDE STOP_HUNTED Detection
- **Plan says:** "if price recovers above entry + 2% within 48h -> STOP_HUNTED" and "if price stays below stop -> STOP_LOSS_CORRECT"
- **Spec says:** Agents.md 15.1 type 6: "Stopped out, price reversed within 48h to above entry + 2%." Type 7: "Stopped out, price did not recover within 30d."
- **Risk:** Plan says "price stays below stop" for STOP_LOSS_CORRECT but spec says "price did not recover within 30d." These are different conditions. Staying below stop is stricter than not recovering (price could be above stop but below entry).
- **Recommendation:** Align plan language with spec: STOP_LOSS_CORRECT = "price did not recover within 30d" (not "stays below stop").

### [LOW] D-08: Plan Item 4.2 Claims Missing Audit Event, but MISSING.md Shows Partial Status

- **Plan ref:** 4.2 Episodic Context Real Data
- **Plan says:** Wire build_context_brief() to read real data
- **Spec says:** Agents.md 18.4 requires episodic_context_injected audit event with content_hash
- **MISSING.md says:** 13.1 DONE, 13.2 STUB, 13.3 STUB, 13.5 MISSING (audit event)
- **Risk:** Plan correctly identifies the work needed but does not explicitly call out the audit event requirement from Agents.md 18.4.
- **Recommendation:** Add explicit sub-item: "Log episodic_context_injected audit event with content_hash (Agents.md 18.4)."

## Anti-Pattern Risk Assessment

| Anti-Pattern (Arch 16) | Risk Level | Notes |
|---|---|---|
| 16.1 Direct state mutation | LOW | Plan correctly references state_machine.transition() in items 3.2 (re-eval transitions) |
| 16.2 Non-canonical JSON | LOW | Storage migration items (2.2-2.4) should use canonical_json for any audit writes |
| 16.4 Packet mutation in staleness | LOW | Plan item 1.1 mentions staleness filtering -- must return FreshnessResult, not mutate |
| 16.5 cycle_id optionality | MEDIUM | Plan does not mention cycle_id requirements for any of the new audit-emitting operations (evidence fetching, resolution, storage writes). Implementers could forget this. |
| 16.6 Bootstrap mishandling | LOW | Not directly in scope, but EV/Pricing changes in 1.4 interact with bootstrap |
| 16.9 Mutation writing production state | LOW | Plan item 4.3 (Mutation SSE) only adds events, does not touch promotion logic |
| 16.11 Runtime prompt edits | LOW | Plan does not propose runtime prompt editing |
| 16.13 Logging secrets | LOW | Storage activation items (2.1-2.4) must not log connection strings or credentials |
| 16.14 Missing error_code on WARN+ | MEDIUM | Plan's storage migration and error handling paths need explicit error_code assignment |

**Highest risk anti-patterns:**
1. **16.5 (cycle_id)** -- New storage writes, evidence fetching, and resolution flows all need cycle_id. Plan does not mention this requirement.
2. **16.14 (error_code)** -- Error paths in new code (storage failures, price fetch failures) need canonical error codes from Arch 5.5.

## Pydantic v2 Compliance

**No concerns found.** The plan does not propose specific Pydantic model changes. The storage schema items (2.2-2.4) work with existing schemas from Arch 8.3-8.7, which already use `model_config = ConfigDict(...)` and `@model_validator(mode="after")`. The plan should ensure any new Pydantic models follow the same patterns and live in `pmacs/schemas/`.

One note: Plan item 1.3 mentions creating resolver files with new schemas. If these files already exist (per MISSING.md), any schema additions should follow the v2 patterns from Arch 1.1.

## Overall Verdict

**PARTIALLY_COMPLIANT**

The plan's high-level structure is sound: wave ordering follows correct dependencies, the exit test captures the right spirit, and the scope covers the right general areas. However, it has two systemic problems:

1. **Stale inventory:** At least 8 plan items describe implementing components that are already DONE per MISSING.md and exist on disk. The plan was likely written against an earlier version of MISSING.md and not updated. This risks wasted effort and broken existing code.

2. **Exit test mismatch:** The plan's exit test does not directly correspond to any specific PMACS build phase exit test from Phases.md. The plan should either adopt the PMACS Phase 12 exit test verbatim or explicitly map each plan item to its PMACS phase and list the corresponding exit tests.

**Required fixes before implementation:**
1. Reconcile every plan item against current MISSING.md status; mark DONE items as "verify" rather than "implement"
2. Add PMACS Phase 12 exit test items 1-5 from Phases.md as explicit acceptance criteria
3. Correct the 3 wrong spec section references (D-04)
4. Add cycle_id and error_code requirements to storage and evidence items (anti-patterns 16.5, 16.14)

---

_Reviewed: 2026-05-19T12:09:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
