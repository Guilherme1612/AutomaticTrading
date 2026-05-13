---
phase: 08-polish
reviewed: 2026-05-12T23:28:00Z
reviewer: Claude Opus 4.6 (cross-AI peer review)
status: completed
---

# Phase 8: Cross-AI Peer Review

**Reviewer:** Claude Opus 4.6 (independent cross-AI review)
**Date:** 2026-05-12T23:28:00Z
**Phase:** 08 -- Polish / LIVE-READY (PMACS Phase 15)

## Files Examined

**Spec files:**
- `spec/Source.md` sections 13-23 (design system, pages, workflows)
- `spec/Architecture.md` sections 16, 20 (anti-patterns, performance budgets)
- `spec/Phases.md` Phase 15 exit tests

**Planning docs:**
- `.planning/phases/phase-8/PLAN.md`
- `.planning/phases/phase-8/CONTEXT.md`
- `.planning/phases/phase-8/SUMMARY.md`

**Implementation files:**
- `pmacs/web/static/app.js` (1047 lines)
- `pmacs/web/static/style.css`
- `pmacs/web/static/sankey.js` (24K)
- `pmacs/web/templates/base.html` (311 lines)
- `pmacs/web/templates/dashboard.html` (230 lines)
- `pmacs/web/templates/agents.html` (157 lines)
- `pmacs/web/templates/pipeline.html`
- `pmacs/web/templates/debug.html`
- `pmacs/web/templates/cortex.html`
- `pmacs/web/templates/universe.html`
- `pmacs/web/templates/settings.html`
- `pmacs/web/components/error_state.html`
- `pmacs/web/components/empty_state.html`
- `pmacs/web/components/loading_state.html`
- `pmacs/web/components/totp_modal.html`
- `ops/spec_consistency.py` (292 lines)
- `ops/backup_verify.py` (353 lines)
- `ops/audit_chain_verify.py`
- `ops/profile_cycle.py`
- `ops/profile_memory.py`
- `ops/verify_isolation.py` (437 lines)
- `docs/operator_runbook.md` (245 lines)

## Scores

| Dimension | Score | Summary |
|---|---|---|
| Spec Compliance | 4/5 | Strong alignment with Source.md 13-21. Notification policy matches exactly. Minor gaps in workflow depth and sidebar collapsibility. |
| Architecture Compliance | 4/5 | No anti-pattern violations detected. Performance budgets addressed via profiling tools. HTMX+SSE wiring correct. |
| Completeness | 3/5 | All 8 exit tests nominally pass but 3 are framework-only (not empirically validated). Missing: cycle compare modal, sidebar collapse, notification settings UI. |
| Code Quality | 4/5 | Clean, well-structured code with good separation. No security issues. A few unimplemented TODO stubs and a potential null-ref in keyboard handler. |
| Design & UX | 4/5 | Excellent Notion-aesthetic implementation. State components match spec. Minor: sparklines are hardcoded SVG, not data-driven. |
| **Overall** | **3.8/5** | Solid Polish phase. A few items need follow-through before the system is truly LIVE-READY. |

## Findings

### [SEV-1] Critical Issues

**None found.** No anti-pattern violations, no security issues, no broken non-negotiables.

### [SEV-2] Significant Issues

#### S2-1: Exit tests 2 and 3 are "framework only" -- not empirically validated

**Files:** `ops/profile_cycle.py`, `ops/profile_memory.py`, `.planning/phases/phase-8/SUMMARY.md` lines 44-45

The summary marks exit tests 2 (16-ticker cycle <= 3h) and 3 (RAM < 50GB) as "PASS (framework)". This means the profiling tools exist and have correct threshold logic, but no actual 16-ticker cycle has been timed against the Architecture.md 20.1 budget, and no actual memory measurement has been taken against the 49GB budget from 20.2.

The spec is explicit: "Full cycle on 16-ticker universe completes within 3 hours on M1 Max 64GB" and "RAM usage under 50GB during cycle peak." These are empirical requirements, not structural ones. The Phase 15 exit test demands actual measurement.

**Recommendation:** Before declaring LIVE-READY, run `profile_cycle.py` and `profile_memory.py` against a real (even synthetic) 16-ticker cycle and record the empirical numbers. Update the summary with actuals.

#### S2-2: Cycle compare feature is a stub

**File:** `pmacs/web/static/app.js` lines 418-421

```javascript
function openCycleCompare() {
    showToast("Select two cycles to compare", "info");
    // TODO: Open cycle compare modal
}
```

Source.md 15.9 specifies: "Cmd-K -> Compare cycles -> select two cycle IDs -> side-by-side view of the same ticker across both cycles. Shows what changed (different evidence available, different persona outputs, different Crucible result, different verdict)." The Cmd-K palette entry exists but the actual compare UI is not implemented. This is listed in Phase 15's deliverables.

**Recommendation:** Either implement the cycle compare modal or explicitly defer it and note the deferral in the summary.

#### S2-3: Sidebar not collapsible to 64px

**File:** `pmacs/web/templates/base.html` lines 134-176

Source.md 13.2 specifies: "Fixed, 240px wide, collapsible to 64px." The current implementation is fixed at 240px (`w-60`) with no collapse toggle. This is a minor UX issue but the spec is explicit.

**Recommendation:** Add a collapse/expand toggle to the sidebar. At 64px, show only icons (no labels).

#### S2-4: Notification level adjustment not implemented in Settings

**File:** `pmacs/web/templates/settings.html`

Source.md 13.5 states: "The operator can adjust notification levels in Settings -> General." The Settings page renders configuration sections but does not include a notification-level adjustment UI. Architecture.md 17.7 references `config/notification.toml` as editable via Settings -> General.

**Recommendation:** Add a notification settings section to the Settings page with per-event level controls (or at minimum a placeholder with a TODO noting it reads from `config/notification.toml`).

#### S2-5: "Run cycle now" is a TODO stub

**File:** `pmacs/web/static/app.js` lines 413-416

```javascript
function runCycleNow() {
    showToast("Starting new cycle...", "info");
    // TODO: POST to pmacs-nervous /api/cycle/start
}
```

This function is used by the pre-first-cycle "Run smoke-test cycle" CTA (dashboard.html line 10) and the "Run new cycle" button on the Agents page. It shows a toast but does not actually trigger a cycle. For a LIVE-READY system, core operational functions should not be stubs.

**Recommendation:** Wire to `POST /api/cycle/start` on pmacs-nervous. Even if the endpoint doesn't exist yet in the pipeline, the client-side call should be ready.

### [SEV-3] Minor Issues / Suggestions

#### S3-1: Sparkline data is hardcoded SVG, not data-driven

**File:** `pmacs/web/templates/dashboard.html` lines 48-53 (and repeated 4 more times)

The sparkline `<polyline points="...">` values are hardcoded in the template, not generated from actual time-series data. This means sparklines will always show the same shape regardless of actual metrics. The spec (13.3) says "Hover reveals point values" which is implemented, but the underlying data is static.

**Recommendation:** When real data plumbing arrives, replace with D3-rendered sparklines or server-rendered SVG from actual DuckDB analytics data. Acceptable for now as a visual placeholder.

#### S3-2: Potential null reference in keyboard shortcut handler

**File:** `pmacs/web/static/app.js` lines 462-465

```javascript
var activeModal = !document.getElementById("cmd-k").classList.contains("hidden") ||
                  !document.getElementById("totp-modal").classList.contains("hidden") ||
                  !document.getElementById("shortcut-overlay").classList.contains("hidden") ||
                  !document.getElementById("blocking-modal").classList.contains("hidden");
```

If any of these elements don't exist on a page (e.g., `totp-modal` is included via `{% include %}` which could fail silently), `getElementById` returns null and `.classList` throws. This runs on every keypress.

**Recommendation:** Add null checks: `(document.getElementById("cmd-k") || {}).classList?.contains("hidden") !== false` or use optional chaining.

#### S3-3: Error state "Copy for Claude Code" uses wrong data attributes

**File:** `pmacs/web/components/error_state.html` lines 42-46

The error state component passes `data-error-code`, `data-error-description`, `data-error-explanation` to `copyForClaudeCode()`. But the `copyForClaudeCode()` function in app.js reads `data-error-code`, `data-message`, `data-level`, `data-stream`, `data-cycle-id`, `data-timestamp`, `data-spec-ref` (lines 893-901). The attributes don't align, so the copied prompt will have empty fields for most data points.

**Recommendation:** Align the error_state component's data attributes with what `copyForClaudeCode()` expects, or create a dedicated `copyErrorForClaude()` function for error state components.

#### S3-4: Universe "Add Ticker" button not TOTP-gated

**File:** `pmacs/web/templates/universe.html` line 16

The "Add Ticker" button has no `onclick` handler calling `open_totp_modal()`. Source.md 21.1 and the Decision Rights Matrix (Source.md 6) specify that adding a ticker requires TOTP. The operator_runbook.md correctly documents the workflow as "Cmd-K -> add ticker -> type symbol -> TOTP."

**Recommendation:** Wire the "Add Ticker" button to `open_totp_modal()` with `actionId: "add_ticker"` and a callback URL for the POST.

#### S3-5: Universe "Remove" button not TOTP-gated

**File:** `pmacs/web/templates/universe.html` line 36

Same as S3-4: the "Remove" button per ticker row has no TOTP gating. Per Source.md 6, ticker removal requires TOTP.

#### S3-6: "Bulk Actions" button on Universe has no implementation

**File:** `pmacs/web/templates/universe.html` line 18

The "Bulk Actions" button has no handler. Source.md 21.8 specifies a bulk-tag workflow with checkbox selection and sub-sector tagging.

**Recommendation:** Wire to a dropdown with "Tag sub-sector" option that opens a TOTP-gated modal.

#### S3-7: Dark mode toggle not present in Settings

**File:** `pmacs/web/templates/settings.html`

Source.md 13.1 states: "Theme follows system preference by default. Manual toggle in Settings." The dark mode detection is implemented correctly in base.html (lines 96-108) with localStorage persistence, but there is no Settings UI toggle to switch themes manually.

**Recommendation:** Add a theme toggle (Light/Dark/System) in the Settings General section.

#### S3-8: HTMX not actively used for page transitions

**File:** `pmacs/web/templates/base.html` line 8

HTMX is loaded (`htmx.min.js` in vendor) but page navigation uses standard `<a href>` links. The spec mentions HTMX for real-time updates and SSE-driven UI changes, and the CONTEXT.md D1 decision chose HTMX for client-side interactivity. Currently, HTMX is included but not wired -- all navigation causes full page reloads.

This is acceptable if the intent is to add HTMX-driven partial updates incrementally, but worth noting that SSE data updates currently only trigger toasts, not DOM patching.

**Recommendation:** For LIVE-READY, consider at minimum wiring HTMX `hx-get` on the sidebar links so page transitions don't require full reloads. Or document that full-reload navigation is an intentional design choice.

#### S3-9: `promoteAllP1Global()` has no TOTP gating

**File:** `pmacs/web/static/app.js` lines 428-441

The "Promote all P1 queue" action in the Cmd-K palette POSTs directly to `/pipeline/queue/promote` without TOTP. Per Source.md 6 Decision Rights Matrix, queue priority changes are in the "Operator via TOTP" column.

**Recommendation:** Wrap in `open_totp_modal()` with the POST as the callback.

#### S3-10: Accessibility exit test not empirically validated

**File:** `.planning/phases/phase-8/SUMMARY.md` line 49

Exit test 7 is marked "PASS (structural)" -- meaning aria-labels, focus-visible, reduced-motion, and keyboard nav are implemented in code, but no actual axe-core scan has been run on the rendered pages. The Phase 15 exit test requires: "Accessibility: axe-core scan on all 7 pages returns zero critical violations."

**Recommendation:** Run an actual axe-core audit (e.g., via `playwright` + `@axe-core/playwright`) against all 7 pages and record results. Many WCAG AA issues only surface at render time.

## Recommendations for Replan

1. **Empirical validation of exit tests 2, 3, 7.** These are currently framework/structural passes. Run actual measurements. If the system can't be started yet (no GGUF model, no real data), explicitly note these as "deferred to integration validation" rather than PASS.

2. **Wire the remaining TODO stubs.** `runCycleNow()`, `openCycleCompare()`, and `promoteAllP1Global()` should have real implementations or at minimum real fetch calls to the nervous API, even if those endpoints return mock data.

3. **TOTP-gate all destructive actions.** Audit every button that modifies state (Add Ticker, Remove Ticker, Bulk Actions, Promote P1) and ensure they route through `open_totp_modal()`. Currently only the Kill Switch and Settings changes are gated.

4. **Implement sidebar collapse.** Small UX gap but the spec is explicit about 64px collapse mode.

5. **Add notification settings to Settings page.** Even a read-only display of `config/notification.toml` values would close the spec gap.

6. **Align error_state data attributes with copyForClaudeCode expectations.** These are two different call sites with incompatible attribute schemas.

## Strengths

1. **Design system implementation is excellent.** The color tokens, typography (Inter + JetBrains Mono), spacing, and Notion-aesthetic are faithfully implemented in base.html. Light/dark mode with CSS variables and system preference detection is clean and correct.

2. **Notification policy is a near-perfect match to spec.** The `NOTIFICATION_POLICY` object in app.js maps every event from Source.md 13.5 to the correct surface, type, duration, and sound. The sound synthesis (oscillator-based "click" and "alert") is a thoughtful touch that avoids external audio files.

3. **State components are spec-compliant.** Empty state (pre-first-cycle + post-cycle), loading state (no spinners, ETA, cancel button at 30s), and error state (error code, description, "What this means" expander, "What to try", copy for Claude Code, spec link) all match Source.md 13.4 requirements.

4. **Ops tools are well-engineered.** `spec_consistency.py` with range-aware back-reference parsing, `backup_verify.py` with full E2E cycle and safety checks (empty backup abort), `audit_chain_verify.py` -- all have good test coverage (22 + 15 + 6 = 43 tests) and proper CLI interfaces.

5. **Keyboard shortcuts implementation is comprehensive.** All 9 shortcuts from Source.md 13.6 are implemented: Cmd-K, Cmd-1..7, Cmd-R, Cmd-/, Cmd-Shift-K (kill switch), Cmd-T (TOTP), / (search), Esc (dismiss), ? (help). The Cmd-K palette supports pages, actions, tickers, audit search, and error codes -- exceeding the spec minimum.

6. **TOTP modal is properly parameterizable.** The `open_totp_modal(opts)` function with `actionId`, `description`, `consequences`, `confirmText`, `callbackUrl`, and `onSuccess` is well-designed for the varied TOTP-gated actions across the system. Auto-advance on digits, auto-submit on 6th digit, and confirmation text validation (for destructive actions) are all correct.

7. **Operator runbook is thorough.** Covers all 8 workflows from Source.md 21, mode promotion gates, kill switch procedures, mutation review, backup/restore, and troubleshooting. The quick-reference table is a useful operator-facing summary.

8. **Accessibility foundations are solid.** `prefers-reduced-motion` disables all animations, focus-visible outlines are 2px accent, viewport guard at 1024px, `aria-live` on toast container and cycle indicator, `role` attributes on landmarks, dialogs, and lists. The skeleton loading uses the correct Tailwind `animate-pulse` class.

---

_Reviewed: 2026-05-12T23:28:00Z_
_Reviewer: Claude Opus 4.6 (independent cross-AI peer review)_
_Depth: comprehensive_
