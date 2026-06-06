# Phase 13 (UI Polish): Cross-Review

**Reviewed:** 2026-05-26T12:37:00Z
**Scope:** PLAN.md, spec alignment, implementation quality across app.js, style.css, templates, runbook
**Spec references:** Source.md SS13 (dashboard app), SS14-20 (pages), Phases.md Phase 15 (Polish)

---

## Plan-Spec Alignment (score: 4/5)

**Naming mismatch.** The `.planning/phases/phase-13` directory and PLAN.md label this "Phase 13: UI Polish", but per CLAUDE.md GSD mapping, Phase 13 maps to PMACS Build Phase 13 (Episodic context injection). The UI Polish work is PMACS Build Phase 15 (Polish, performance, operator experience), which maps to GSD Phase 8. This naming causes confusion when cross-referencing the spec. The PLAN.md itself acknowledges "Most items were already implemented during Phase 11" and lists only 5 incremental items.

**Content alignment is strong.** Despite the naming issue, the plan covers the right Phase 15 deliverables:

| Phase 15 Spec Item | Covered? | Evidence |
|---|---|---|
| Cmd-K command palette | Yes | app.js L381-543: pages, actions, error codes, ticker search, keyboard nav |
| Keyboard shortcuts (SS13.6) | Yes | app.js L658-780: Cmd-1..7, Cmd-K, Cmd-R, Cmd-/, Cmd-Shift-K, Cmd-T, /, ?, Esc |
| Notification policy (SS13.5) | Yes | app.js L267-379: 12 event types, 5 surfaces, persistent/blocking/modal, sound, saved levels |
| Toast system (SS13.2) | Yes | app.js L166-237: 5 types, auto-dismiss, persistent, max-5 cap |
| Sparklines + time-window | Yes | app.js L1371-1469: SVG polyline, window buttons, SSE refresh, tooltips |
| Accessibility audit (SS13.7) | Partial | See gaps below |
| Operator runbook | Yes | docs/operator_runbook.md: 380 lines covering all workflows |
| Cycle compare (SS15.9) | Yes | app.js L561-627: modal with cycle A/B input, fetch comparison |
| Copy for Claude Code | Yes | app.js L1149-1280: debug event, error state, raw JSON copy |

**Missing from Phase 15 spec but not in plan:**
- Performance profiling per Architecture.md SS20.1/SS20.2 (cycle throughput, RAM budget)
- `ops/spec_consistency.py` cross-file reference checker

These are Phase 15 infrastructure items not related to UI polish, so their absence from this wave is acceptable if tracked separately.

---

## Exit Test Coverage (score: 3/5)

Phase 15 exit test from Phases.md has 8 criteria. Assessment against current implementation:

| # | Exit Test Criterion | Status | Notes |
|---|---|---|---|
| 1 | 8 operator workflows (SS21) complete in <=3 clicks | **Partial** | Workflows 21.1-21.8 are implemented but NOT tested against the 3-click threshold. The runbook documents steps but no click-count verification exists. |
| 2 | 16-ticker cycle completes within 3 hours (M1 Max 64GB) | **Untested** | No evidence of timing benchmarks. |
| 3 | RAM under 50GB during cycle peak | **Untested** | No profiling evidence. |
| 4 | Audit chain verifies after 100+ cycles | **Untested** | `ops/audit_chain_verify.py` exists but no test result showing 100+ cycles. |
| 5 | `ops/spec_consistency.py` passes | **Unknown** | File exists but pass/fail status not recorded. |
| 6 | Backup + restore cycle works | **Unknown** | `ops/backup_verify.py` exists but no E2E test result. |
| 7 | axe-core scan: zero critical violations on 7 pages | **Not verified** | a11y features are implemented (focus states, aria attributes, reduced motion) but no axe-core CI integration evidence. |
| 8 | All toasts, modals, shortcuts function per spec | **Implemented** | All UI surface features are coded. Functional testing is the gap. |

The plan does not reference the Phase 15 exit test at all. It declares "Status: Complete" after listing 5 incremental items without any exit-test verification.

---

## Implementation Quality (score: 4/5)

### Strengths

1. **Cmd-K palette** (app.js L381-543) is thorough: page navigation, actions, ticker search with regex validation, error codes from Architecture.md SS5.5, keyboard arrow/enter nav, escape dismissal, click-outside-close. Category labels and color-coded badges. Well-structured.

2. **Toast system** (app.js L166-237) correctly caps at 5 toasts, handles enter/exit animations, persistent toasts get dismiss buttons, uses `role="status"` and `aria-live` for screen readers.

3. **Notification policy** (app.js L267-379) is spec-accurate: all 12 event types from SS13.5 are present, non-disableable events (kill_switch, audit_chain) bypass saved levels, sound synthesis via Web Audio API respects reduced motion.

4. **Focus trap** (app.js L1500-1545) correctly implements Tab/Shift+Tab cycling, restores previously focused element on release. Wired for Cmd-K palette (L1593-1609).

5. **TOTP modal** (totp_modal.html) is well-designed: auto-advance on digit input, paste distribution across 6 fields, auto-submit on last digit, confirmation text for destructive actions, proper aria labels on each digit.

6. **CSS** (style.css) correctly implements reduced-motion by collapsing all animation/transition durations to near-zero AND providing static fallbacks for animated elements (progress bars, sparklines, sankey).

7. **Runbook** (docs/operator_runbook.md) is comprehensive at 380 lines: startup, daily workflow, notification config, dark mode, shortcuts, cycle timing, compare, TOTP, mode promotion, kill switch, mutation engine, backup/restore, troubleshooting.

### Issues Found

**WR-01: Focus trap not wired for TOTP modal or blocking modal.**
- File: `pmacs/web/static/app.js:1590-1609`
- Issue: The focus trap (`trapFocus`/`releaseFocus`) is wired only for Cmd-K palette. The TOTP modal (`open_totp_modal` at L866) and blocking modal (`showBlockingModal` at L241) do not call `trapFocus`. Per WCAG 2.1 SC 2.4.3, focus must be contained within `role="dialog"` and `role="alertdialog"`. Both modals have `aria-modal="true"` but no programmatic focus containment. A keyboard user can Tab out of these modals into the background page.
- Fix: Add `trapFocus` calls in `open_totp_modal` (after L915) and `showBlockingModal` (after L262). Add `releaseFocus` in `closeTotpModal` and in the blocking modal dismiss handlers.

**WR-02: Blocking modal lacks focus management entirely.**
- File: `pmacs/web/static/app.js:241-263`
- Issue: `showBlockingModal` does not move focus to the modal. The first button is not focused programmatically. Screen reader users may not be aware the modal appeared unless they have live-region support.
- Fix: After `modal.classList.remove("hidden")`, focus the first button in `actionsDiv` (or the modal container itself with `tabindex="-1"`).

**WR-03: Plan claims "Status: Complete" but focus traps are incomplete.**
- File: `.planning/phases/phase-13/PLAN.md:5`
- Issue: The plan lists "Focus trap for modals -- JS focus trap for Cmd-K, blocking modal, TOTP modal" but only Cmd-K has the trap wired. This is a verification gap -- the code for trapFocus exists but the wiring is partial.

**IN-01: LLM Switcher review has unresolved high-severity issues.**
- File: `.planning/phases/phase-13/13-REVIEW-LLM-SWITCHER.md`
- Issue: HI-01 (side-by-side diff view broken: `diff_html` vs `diff_rows` field mismatch) and HI-02 (inference API endpoints lack authentication) remain unresolved. These are in scope for this phase's review but no fix status is recorded.

**IN-02: Runbook shortcut table has incorrect Cmd-7 page mapping.**
- File: `docs/operator_runbook.md:179`
- Issue: Table says "Cmd-1 through Cmd-7" navigate to "(Dashboard, Agents, Pipeline, Universe, Cortex, Settings, Debug)" -- this lists Settings as 6th and Debug as 7th. But in app.js L660 (`PAGE_SHORTCUTS`) and base.html L142-149, the order is: Dashboard(1), Agents(2), Pipeline(3), Universe(4), Cortex(5), Debug(6), Settings(7). The runbook has the order swapped for positions 6 and 7.
- Fix: Change to "(Dashboard, Agents, Pipeline, Universe, Cortex, Debug, Settings)".

---

## Gaps and Risks

### Gap 1: No exit test execution evidence
The Phase 15 exit test has 8 criteria. None have formal pass/fail records. The plan declares completion based on code presence, not functional verification. This is the highest risk for the phase -- the UI surfaces exist but their end-to-end correctness is unverified.

### Gap 2: Partial accessibility compliance
WCAG AA focus states are styled (style.css L476-486), skip-link exists (base.html L113), reduced-motion is respected, and aria attributes are present. But:
- Focus traps are only wired for Cmd-K (not TOTP or blocking modals)
- No axe-core or automated a11y test results
- Exit test #7 (axe-core zero violations) cannot be confirmed

### Gap 3: Performance profiling absent
Phase 15 exit tests #2 and #3 require cycle timing and RAM benchmarks. No profiling data exists. The cycle timing display on the dashboard (app.js L1549-1565) measures individual cycles client-side but this is not the same as the 16-ticker 3-hour benchmark.

### Gap 4: LLM Switcher review issues unresolved
HI-01 (broken diff view) and HI-02 (unauthenticated inference endpoints) from the earlier review are high severity and still open. These affect Settings page functionality and trust boundaries.

### Risk: Naming confusion
The directory `phase-13` contains Phase 15 (Polish) work. Anyone cross-referencing Phases.md Phase 13 (Episodic context) will be confused. This is a planning artifact issue, not a code bug.

---

## Recommendations

1. **Wire focus traps for TOTP and blocking modals** (WR-01, WR-02). This is a WCAG compliance blocker for exit test #7. Estimated effort: 10 lines of code.

2. **Fix runbook Cmd-7 mapping** (IN-02). One-line text fix.

3. **Resolve LLM Switcher HI-01** (diff_html vs diff_rows). The side-by-side mutation diff view is completely broken. This affects the operator's ability to review mutations (workflow 21.4).

4. **Execute exit test #8** manually: verify all toasts, modals, and keyboard shortcuts work per spec across all 7 pages. Create a checklist recording.

5. **Run axe-core scan** on all 7 pages and record results for exit test #7.

6. **Schedule performance profiling** (exit tests #2, #3) as a separate task. The cycle timing display infrastructure exists but benchmarking requires a live system under load.

7. **Consider renaming** the planning directory or adding a clarification in PLAN.md that this is PMACS Build Phase 15 content in a phase-13 directory.

---

## Overall Score: 3.5/5

**Justification:** The implementation quality is solid (4/5) -- the UI surfaces are well-structured, spec-accurate in their event types and keyboard mappings, and the runbook is thorough. However, the exit test coverage is weak (3/5) because none of the 8 Phase 15 exit criteria have formal verification. The focus trap gap for TOTP/blocking modals is a real a11y regression. The unresolved LLM Switcher high-severity issues add risk. The plan declares "Complete" prematurely -- the code is written but not verified against the spec's acceptance criteria.

**The phase is code-complete but not verification-complete.** Closing it requires: (a) wiring focus traps for 2 modals, (b) fixing the diff view field mismatch, (c) executing and recording the 8-point exit test.

---

_Reviewed: 2026-05-26T12:37:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
