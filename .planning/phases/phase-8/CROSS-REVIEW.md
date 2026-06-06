# Phase 8 Cross-Review: Polish / LIVE-READY (PMACS Phase 15)

**Reviewer:** Claude (gsd-code-reviewer)
**Date:** 2026-05-26T12:36:00Z
**Scope:** PLAN.md, 08-REVIEW.md, REVIEWS.md, SUMMARY.md, spec/Phases.md Phase 15, spec/Architecture.md 16/19/20
**Depth:** Standard (spec-conformance cross-check)

---

## Plan-Spec Alignment (score: 4/5)

The plan maps directly to Phase 15's deliverables list (Phases.md lines 543-559). Every item in the spec's "What gets built" has a corresponding wave in the plan:

| Spec Deliverable | Plan Coverage | Status |
|---|---|---|
| Agents page animations (Source.md 15.5) | Wave 3 -- persona progress bars, 3-view communication | Implemented |
| Pipeline kanban refinement | Wave 3 -- drag-drop, priority bands | Implemented |
| Dashboard sparklines + time-window | Wave 3 -- sparklines (hardcoded SVG placeholder), selector | Partial |
| Cmd-K command palette | Wave 2 -- full palette with tickers/actions/audit | Implemented |
| Keyboard shortcuts (13.6) | Wave 2 -- all 9 shortcuts | Implemented |
| Accessibility audit (13.7) | Wave 4 -- structural (aria, focus-visible, reduced-motion) | Partial |
| Performance profiling 20.1 | Wave 4 -- profile_cycle.py (framework only) | Deferred |
| Memory profiling 20.2 | Wave 4 -- profile_memory.py (framework only) | Deferred |
| spec_consistency.py | Wave 1 -- 22 tests | Implemented |
| backup_verify.py | Wave 1 -- 15 tests including E2E | Implemented |
| audit_chain_verify.py | Wave 1 -- 6 tests | Implemented |
| operator_runbook.md | Wave 4 -- complete (245 lines) | Implemented |
| Empty/loading/error states (13.4) | Wave 2 -- all three components | Implemented |
| Notification policy (13.5) | Wave 2 -- full event mapping | Implemented |
| Cycle compare (15.9) | Replan Wave 1 -- modal with dual input | Implemented |
| Copy for Claude Code | Wave 2 + Replan fix (S3-3) | Implemented |

**Gap: 3 exit tests marked "framework only" or "structural" instead of empirically validated.** The spec requires actual measurements (exit tests 2, 3, 7). The plan acknowledges this and defers with documented rationale, which is honest but means the system cannot be declared LIVE-READY by the spec's own definition (Phases.md 7.4: "Phase 15 complete + PAPER_VALIDATED mode gates pass").

**Deduction:** -1 for deferred empirical validation that the spec explicitly requires.

---

## Exit Test Coverage (score: 3/5)

Phase 15 defines 8 exit tests (Phases.md lines 563-571). Assessment against actual evidence:

| # | Exit Test | Spec Requirement | Actual Status | Verdict |
|---|---|---|---|---|
| 1 | 8 workflows <=3 clicks | All 8 from Source.md 21 | Cmd-K + TOTP modal + page routes | PASS |
| 2 | 16-ticker cycle <=3h | Architecture.md 20.1: empirical | profile_cycle.py threshold logic only | FAIL (not measured) |
| 3 | RAM <50GB peak | Architecture.md 20.2: empirical | profile_memory.py threshold logic only | FAIL (not measured) |
| 4 | Audit chain 100+ cycles | Hash chain integrity | Tested with 200-entry synthetic chain | PASS |
| 5 | spec_consistency.py passes | Cross-file references | 22 unit tests against real spec files | PASS |
| 6 | Backup + restore works | All 5 DBs | E2E tested including wipe-restore-verify | PASS |
| 7 | axe-core zero critical | Actual scan on rendered pages | aria-labels, focus-visible, keyboard nav in code | FAIL (not scanned) |
| 8 | Toasts/modals/shortcuts | Per spec | All 13.6 shortcuts, notification policy, blocking modals | PASS |

**5 of 8 pass. 3 fail the spec's own criteria.** The SUMMARY.md correctly marks these as DEFERRED/PASS (framework)/PASS (structural), which is transparent but does not satisfy the exit test definition from Phases.md 7.1: "The exit test scenario passes end-to-end."

The spec is unambiguous: "Full cycle on 16-ticker universe completes within 3 hours" and "Accessibility: axe-core scan on all 7 pages returns zero critical violations." These are not structural assertions -- they require a running system. Without them, Phase 15 is structurally complete but not exit-test complete.

**Deduction:** -2 for 3 of 8 exit tests not meeting the spec's empirical bar.

---

## Implementation Quality (score: 4/5)

### Strengths

1. **Anti-pattern enforcement is thorough.** All 14 Architecture.md 16.* anti-patterns have runtime or CI guards:
   - `canonical_json` used throughout audit path (pmacs/storage/audit.py, pmacs/mutation/candidate_generator.py)
   - `state_machine.transition()` is the sole mutation path (pmacs/engines/state_machine.py)
   - `BUCKETS["source"].acquire()` used for all rate limiting (pmacs/nervous/rate_limit.py, pmacs/data/gateway.py)
   - `usd_per_eur` enforced via Pydantic validator (pmacs/schemas/currency.py)
   - `PROCEED_BOOTSTRAP_LOW_CONFIDENCE` implemented in arbitration engine
   - `error_code` required for WARN+ debug events with validation against registry (pmacs/logsys/debug_log.py)
   - `cycle_id=None` raises ValueError for non-system events (debug_log.py line 217)

2. **Dual-stream logging (audit + debug) is implemented.** The debug_log module enforces error_code and cycle_id requirements per Architecture.md 5.2 and 5.5. The audit log uses hash-chained canonical_json. Both streams are independent and append-only.

3. **TOTP gating is comprehensive after replan.** The cross-review found TOTP on: kill switch disengage, universe add/remove/bulk-tag/bulk-remove, promoteAllP1Global, settings changes, and the Cmd-K palette properly routes destructive actions through `open_totp_modal()`.

4. **Replan was disciplined.** The original code review (08-REVIEW.md) found 1 critical + 4 warnings. The cross-AI peer review (REVIEWS.md) found 5 SEV-2 + 10 SEV-3. The replan addressed all SEV-2 and 7/10 SEV-3, with documented rationale for 4 deferrals. All 5 replan commits verified in SUMMARY.md.

5. **Test coverage is solid.** 714 tests passing (50 new), 43 tests for ops tools alone. Zero regressions from replan changes.

### Weaknesses

1. **Hardcoded sparkline SVG** (S3-1, deferred). Dashboard sparklines are static placeholder shapes. The spec (Source.md 13.3) says "Hover reveals point values" -- the hover is implemented but the data is fake. Acceptable as a visual placeholder but the dashboard is not data-driven yet.

2. **HTMX loaded but unused** (S3-8, deferred). Full page reloads on navigation. The spec mentions HTMX for real-time updates. Currently SSE drives toasts only, not DOM patches. Acceptable for LIVE-READY but a functional gap.

3. **Universe workflow 21.8 incomplete.** Source.md 21.8 specifies: select checkboxes -> Bulk Actions -> Tag sub-sector -> type label -> Submit. The replan added the Bulk Actions dropdown with TOTP gating, but there is no checkbox selection mechanism and no text input for the sub-sector label. The `bulkTagSubsector()` function opens a TOTP modal but has no UI for entering the tag value.

4. **Workflow 21.2 "Run again now" has no TOTP.** Source.md 21.2 specifies clicking "Run again now" on a SKIP card. The pipeline.html has this button but it does not route through TOTP. This is a decision-rights question -- the Source.md 6 matrix may or may not require TOTP for re-queuing a single ticker. Worth clarifying.

5. **Notification settings are display-only.** The 7 per-event dropdowns in settings.html render but the change handler only logs to console.log (line 513). No POST to backend. This is acknowledged in the code comment but means the operator cannot actually change notification behavior through the UI.

**Deduction:** -1 for partial implementation of spec workflows and non-functional notification settings.

---

## Gaps & Risks

### Gap 1: Exit tests 2, 3, 7 cannot pass without running system [HIGH]

The spec requires empirical measurement of cycle throughput (20.1), memory usage (20.2), and accessibility (axe-core). These are impossible to validate without a running inference server with a loaded model + 16-ticker universe. This is a fundamental blocker for declaring "LIVE-READY" per Phases.md 7.4.

**Risk:** The system could pass all structural checks but fail empirically (e.g., OOM on 16 tickers, cycle takes 5 hours). Discovering this late is costly.

**Mitigation:** The plan correctly identifies this and defers with rationale. The profiling tools (profile_cycle.py, profile_memory.py) are ready to run when the system is bootable. This is a sequencing problem, not a quality problem.

### Gap 2: PAPER_VALIDATED mode gates are not verified [MEDIUM]

Phases.md 7.4 says LIVE-READY requires "Phase 15 complete + PAPER_VALIDATED mode gates pass." The mode promotion gates (90 cycles, 200 trades, Brier <= 0.30, Sharpe >= 0.0, drawdown <= 15%) require extended paper trading. This is outside Phase 8's scope but worth noting: Phase 8 produces the production-quality system, but LIVE-READY also requires operational track record.

### Gap 3: Workflow 21.8 sub-sector tagging is partially implemented [MEDIUM]

The bulk actions dropdown exists with TOTP gating, but the operator cannot select tickers via checkboxes or type a sub-sector label. The Source.md 21.8 workflow specifies: "Select RKLB, ASTS (checkboxes) -> Bulk actions -> 'Tag sub-sector' -> type 'space + satellite' -> Submit." The current implementation skips checkbox selection and label input.

### Gap 4: Notification settings are cosmetic [LOW]

The settings.html notification level dropdowns fire console.log on change but do not persist changes. The operator cannot actually adjust notification behavior from the UI. The spec (Source.md 13.5) says "The operator can adjust notification levels in Settings -> General." The backend endpoint does not exist yet.

### Gap 5: Cycle compare modal has no backend [LOW]

`fetchCycleComparison()` calls `/api/cycle/compare?cycle_a=...&cycle_b=...` but this endpoint may not exist on pmacs-nervous. The client-side modal is well-built (Esc closes, proper error handling, loading state), but it will return HTTP errors until the backend is wired. Same for `runCycleNow()` calling `/api/cycle/start`.

---

## Recommendations

1. **Do not declare LIVE-READY until exit tests 2, 3, 7 are empirically validated.** The SUMMARY.md claims "COMPLETE -- LIVE-READY" but 3 of 8 exit tests are not measured. Recommend changing to "COMPLETE -- LIVE-READY (pending empirical validation of exit tests 2, 3, 7)" or "STRUCTURALLY COMPLETE."

2. **Complete workflow 21.8 (sub-sector tagging).** Add checkbox selection to universe.html rows and a text input modal for the sub-sector label before the TOTP gate fires. This is one of the 8 operator workflows the spec requires to work in 3 clicks.

3. **Wire notification settings to backend.** Either implement `POST /api/settings/notifications` on pmacs-nervous, or remove the dropdowns and display the current values from notification.toml as read-only. Currently they create a false affordance.

4. **Clarify TOTP requirement for workflow 21.2.** The "Run again now" button on SKIP cards in pipeline.html may or may not need TOTP per the decision rights matrix. If it does, add the gate. If not, document the exemption.

5. **Run the profiling tools at first opportunity.** When the inference server boots with a real GGUF model, immediately run profile_cycle.py and profile_memory.py against a 16-ticker synthetic universe. Record actuals. This is the single highest-value validation action remaining.

6. **Schedule an axe-core scan.** Even a manual run via browser extension against the 7 rendered pages would surface real WCAG issues. Structural accessibility (aria-labels, focus-visible) is good but not sufficient -- rendered-page testing catches color contrast, heading hierarchy, and ARIA role mismatches that static analysis misses.

---

## Overall Score (3.5/5)

Phase 8 is a well-executed polish phase. The codebase has strong anti-pattern enforcement, comprehensive TOTP gating, dual-stream logging with hash-chained audit, and 714 passing tests. The replan was disciplined -- all SEV-2 findings were addressed, deferrals are documented with clear rationale.

The score reflects that 3 of 8 spec-defined exit tests cannot be empirically validated without a running system. This is an honest structural limitation, not a quality deficit. The profiling tools are ready; the system is not yet bootable with real data. When those 3 exit tests pass with actual measurements, Phase 8 will be a clean 4.5/5.

The declared status of "COMPLETE -- LIVE-READY" should be amended to acknowledge the empirical gap. Per Phases.md 7.4, LIVE-READY requires Phase 15 complete (exit tests passing) plus PAPER_VALIDATED mode gates. Neither condition is fully met yet. The system is structurally complete and production-quality in its code; it needs runtime validation.

### Score Breakdown

| Dimension | Score | Rationale |
|---|---|---|
| Plan-Spec Alignment | 4/5 | All spec deliverables addressed; 3 exit tests deferred |
| Exit Test Coverage | 3/5 | 5/8 pass, 3 require running system |
| Implementation Quality | 4/5 | Strong anti-patterns, logging, TOTP; partial workflow 21.8, cosmetic notification settings |
| **Overall** | **3.5/5** | Solid polish, honest deferrals, needs empirical validation to close |

---

_Reviewed: 2026-05-26T12:36:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Scope: PLAN.md, 08-REVIEW.md, REVIEWS.md, SUMMARY.md vs spec/Phases.md Phase 15 + spec/Architecture.md 16/19/20_
