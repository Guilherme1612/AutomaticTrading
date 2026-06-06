# Phase 11: Plan-to-Implementation Cross-Review

**Reviewer:** Claude (gsd-code-reviewer)
**Date:** 2026-05-26T12:36:00Z
**Scope:** PLAN.md, 11-REVIEW.md, spec Source.md SS13-14, app.js, style.css, dashboard.html, data.py, dashboard routes, settings routes, base.html, error_state.html, test_a11y.py

---

## Plan-Spec Alignment (score: 3/5)

The plan (Phase 15: Polish, Performance, Operator Experience) maps to spec Source.md SS13 (dashboard application), SS21 (workflows), and SS23 (first 30 days). Alignment is moderate with several concrete gaps:

**Aligned:**
- Visual identity tokens (SS13.1): `base.html` defines all color tokens correctly for light/dark mode via CSS custom properties. Typography (Inter, JetBrains Mono), spacing (page-gutter, card-pad), and font sizes match spec exactly.
- Keyboard shortcuts (SS13.6): All 9 shortcuts implemented in `app.js` lines 676-779 (Cmd-K, Cmd-1..7, Cmd-R, Cmd-/, /, Esc, Cmd-Shift-K, Cmd-T, ?). Shortcut overlay in base.html matches spec table.
- Notification policy (SS13.5): `app.js` NOTIFICATION_POLICY object (lines 267-281) maps all 13 event types from the spec table. NON_DISABLEABLE_EVENTS correctly enforces kill switch and audit chain modals. Backend persistence in `settings.py` lines 168-204 with server-side enforcement at line 177.
- Accessibility (SS13.7): Skip link, viewport guard at 1024px, `prefers-reduced-motion` media query, WCAG AA focus-visible styles, aria-live regions on toast container and cycle indicator -- all present.
- Component library (SS13.3): Sparkline at 24px height, TOTPField 6-digit with auto-advance, toast stack max 5 -- all implemented.

**Gaps:**
1. **Risk metrics row (SS14.3) -- 2 of 5 metrics wrong.** Spec requires: Sharpe, Sortino, Max Drawdown, Win Rate, Avg R/R. Dashboard template (lines 73-79) renders: Max Drawdown, Sharpe, Win Rate, Open Positions, Capital Used. Sortino and Avg R/R are missing; replaced by Open Positions and Capital Used which are NOT in the spec.
2. **Time-window selector (SS14.1) -- missing YTD option.** Spec says "1D / 1W / 1M / 3M / YTD / All". Dashboard template (lines 53-69) and `data.py` SPARKLINE_WINDOWS (lines 199-205) have: 1D, 1W, 1M, 3M, ALL. YTD is absent.
3. **Mode and cycle status card (SS14.2) -- not implemented as a separate card.** Spec describes a dedicated card with: large mode badge, last cycle summary with verdict counts, next cycle trigger description, "Run cycle now" button. The dashboard has mode badge only in the top bar (base.html line 128-129), cycle timing inline in the portfolio card (line 34), and "Run smoke-test cycle" only in pre-first-cycle state.
4. **Portfolio sparkline (SS14.1) -- no full-width portfolio value sparkline.** Spec describes a full-width sparkline directly below the portfolio value card. The implementation has sparklines only on the 5 risk metric StatBlocks (lines 80-108), not on the portfolio summary card itself.
5. **Window delta (SS14.1) -- missing.** Spec requires `+$234.67 (+4.7%) since 1M ago` below the sparkline when a window is selected. Not implemented.

---

## Exit Test Coverage (score: 3/5)

The plan defines 8 exit tests. The 11-REVIEW.md evaluated only the test files, not whether they adequately cover the exit criteria:

| # | Exit Test | Coverage Assessment |
|---|-----------|-------------------|
| 1 | 8 operator workflows <= 3 clicks | **Weak.** test_operator_workflows.py checks for HTML substring existence, not actual click interaction or DOM structure. 11-REVIEW.md WR-05 documents that assertions like `"run again" in html or "override" in html or "skip" in html` pass trivially. |
| 2 | Full cycle <= 3 hours | **Partial.** Profiler test exists but depends on real hardware with model loaded. Plan acknowledges this risk (Risk Note 2). |
| 3 | RAM < 50GB | **Partial.** Same hardware dependency as #2. |
| 4 | Audit chain 100+ entries | **Good.** Tamper detection test is well-designed (breaks at entry 51). |
| 5 | spec_consistency.py passes | **Good.** Script exists and is run. |
| 6 | Backup/restore round-trip | **Good.** Full 5-store round-trip with post-restore chain verify. |
| 7 | axe-core zero critical on 7 pages | **Partial.** test_a11y.py uses FastAPI TestClient for structural checks, not Playwright with axe-core. The test file header (line 11) acknowledges: "Full axe-core automated scan requires Playwright + running server." The structural checks are solid (14 test methods) but do NOT run axe-core. |
| 8 | All notifications/shortcuts per spec | **Weak.** Notification persistence tested indirectly. Shortcut tests in test_keyboard.py check button existence via HTML substring, not key-event dispatch. |

**Critical gap:** Exit test #7 (axe-core WCAG AA) is the spec requirement (SS13.7: "verified in CI via axe-core") but no axe-core test actually runs. The test_a11y.py is a structural check suite, which is valuable but does not satisfy the spec's explicit "axe-core" requirement.

---

## Implementation Quality (score: 4/5)

**Strengths:**

1. **app.js (1672 lines) -- comprehensive and well-structured.** CSRF auto-attachment (lines 46-79), SSE with reconnection resume via lastEventId (lines 105-161), notification policy with non-disableable enforcement (lines 283-379), focus trap system (lines 1500-1545), staggered entrance with reduced-motion guard (lines 1567-1588), HTMX afterSwap reinitialization (lines 1611-1649). The JS is plain ES5-compatible, no build step needed.

2. **style.css (488 lines) -- spec-faithful.** Dark mode tokens, reduced-motion overrides for sankey/progress/toast/sparkline, HTMX transition classes, viewport breakpoints at 1024/1280/1440px. Print styles hide everything (correct for a desktop tool).

3. **base.html -- solid a11y foundation.** Skip link, aria roles on all landmarks, aria-live on dynamic content, keyboard shortcut overlay with all 9 shortcuts, sidebar with semantic nav, viewport guard.

4. **error_state.html -- spec SS13.4 compliant.** All 6 required elements: error code, description, "What this means" expander, "What to try" list, "Copy for Claude Code" button, spec link. Has `role="alert"`.

5. **Dashboard template -- well-structured.** Pre-first-cycle state, error boundary via error_state.html, sparkline data wired to DuckDB via Jinja2, empty state for positions, system health card with audit chain status.

**Issues found in implementation:**

1. **Sparkline SVG renders in HTML, JS `refreshSparkline` rebuilds as innerHTML string.** The `refreshSparkline` function (app.js lines 1371-1408) and `refreshAllSparklines` (lines 1418-1469) construct SVG via string concatenation. This bypasses DOM sanitization. While the data source is DuckDB (not user input), the metric parameter from the URL query string is used unsanitized in a CSS selector: `document.querySelector('[data-sparkline-metric="' + metric + '"]')` (line 1372). A crafted metric value could potentially break out of the attribute selector. In practice, the metric value comes from `data-sparkline-metric` attributes that are server-rendered, so the risk is mitigated.

2. **Duplicate sparkline rendering logic.** The same SVG polyline construction code appears in three places: dashboard.html Jinja2 (lines 93-96), `refreshSparkline` in app.js (lines 1392-1403), and `refreshAllSparklines` in app.js (lines 1451-1463). If the sparkline format changes, all three must be updated. This is a maintenance risk, not a bug.

3. **11-REVIEW.md CR-01 (walrus operator) is genuinely problematic.** `sys.path.insert(0, str(PROFILER_DIR := PROJECT_ROOT / "ops"))` in test_backup_restore.py line 22 is fragile. The fix suggested in the review is correct.

4. **Notification levels endpoint (settings.py line 177) correctly blocks kill_switch_engaged and audit_chain_failure from being modified server-side.** This is a good defense-in-depth measure -- even if app.js were bypassed, the server enforces non-disableable events.

---

## Gaps & Risks

### Gap 1: Spec SS14.3 risk metrics mismatch (Medium)
Dashboard shows `open_positions` and `capital_used_pct` instead of spec-mandated `Sortino` and `Avg R/R`. The data_layer `get_all_sparkline_data` (data.py line 260) hardcodes the same 5 metrics. This is a spec compliance gap. The plan does not address it -- it focuses on wiring sparklines to DuckDB, not on which metrics to show.

### Gap 2: Missing YTD time window (Low)
Spec SS14.1 requires YTD in the time-window chip group. Both the template and `data.py` omit it. Adding it is straightforward (add `"YTD": "INTERVAL ..."` calculated from Jan 1 of current year).

### Gap 3: No separate Mode/Cycle Status card (Medium)
Spec SS14.2 describes a distinct card with cycle verdict breakdown and next-cycle trigger info. The dashboard scatters these across the top bar and inline in the portfolio card. This affects operator workflow (spec SS21.5: "I want to promote PAPER -> PAPER_VALIDATED" starts by clicking the mode badge on the dashboard).

### Gap 4: No portfolio-level sparkline or window delta (Low)
Spec SS14.1 describes a full-width sparkline under the portfolio value with a window delta line. The implementation only has sparklines on metric StatBlocks.

### Gap 5: axe-core CI integration missing (High)
Spec SS13.7 states "verified in CI via axe-core." The plan calls for Playwright + axe-core tests (S3-1) but the existing test_a11y.py uses TestClient-only structural checks. No Playwright/axe-core configuration exists in pyproject.toml or the test infrastructure. The test file itself acknowledges this limitation (line 11-13).

### Risk 1: Test assertion quality (from 11-REVIEW.md)
The review identified 6 warnings about weak assertions (WR-01 through WR-06). The operator workflow tests and a11y tests rely on HTML substring matching rather than DOM-based assertions. This means the tests could pass even when the feature is broken or missing. This undermines exit test reliability.

### Risk 2: HTMX boost reinitialization
The plan (S1-2) and app.js (lines 1611-1649) handle `htmx:afterSwap` for reinitializing page-specific JS. However, the sidebar navigation links in base.html use `hx-target="#main-content" hx-swap="innerHTML"`. The main content area is a `<main>` element, not a `<div>`. HTMX swap with innerHTML on `<main>` could strip the element's attributes (role="main", id="main-content") depending on the response. The SSE reconnect in the handler (line 1624) is a good safety net.

### Risk 3: Duplicate test fixtures (from 11-REVIEW.md WR-06)
Three separate test files define their own TestClient fixtures with duplicated schema setup. Changes to the DB schema require updating all three. This is a maintenance risk that increases with test count.

---

## Recommendations

1. **Fix risk metrics to match spec SS14.3.** Replace `open_positions` and `capital_used_pct` in dashboard.html metric_cards (lines 73-79) and data.py get_all_sparkline_data (line 260) with `sortino` and `avg_r_r`. Ensure DuckDB rolling_metrics table computes these values.

2. **Add YTD to sparkline windows.** Add `"YTD"` to `_SPARKLINE_WINDOWS` in data.py (compute interval from Jan 1), add YTD button to dashboard.html chip group, and update the "ALL" button to match spec ("All" not "ALL" in the UI label).

3. **Implement Mode/Cycle Status card as spec SS14.2.** Extract from top bar into a dedicated dashboard card with: large mode badge, last cycle timestamp + duration + verdict counts, next cycle trigger, "Run cycle now" button always visible (not just pre-first-cycle).

4. **Integrate Playwright + axe-core for exit test #7.** The structural tests in test_a11y.py are good, but the spec explicitly requires axe-core. Add a Playwright test that starts the dashboard, navigates to each page, and runs axe-core assertions. This does not need to replace the structural tests -- it augments them.

5. **Strengthen test assertions (address 11-REVIEW.md WR-01 through WR-06).** Replace HTML substring checks with DOM-based assertions using specific selectors. For example, use `re.search(r'<(?:button|a)[^>]*>[^<]*add\s+ticker', html)` instead of `"add" in html and "ticker" in html`.

6. **Extract sparkline rendering into a shared function.** The Jinja2 template, `refreshSparkline`, and `refreshAllSparklines` all construct the same SVG polyline. Extract the point-computation logic into a single JS function that returns the SVG string, then call it from all three locations.

7. **Consolidate test fixtures.** Create a shared `tests/conftest.py` with a parameterized dashboard_client fixture (empty/populated). Have accessibility, e2e, and performance tests import from it.

---

## Overall Score: 3/5

**Rationale:** The plan is well-structured with clear wave dependencies and realistic risk notes. Implementation quality of the core UI infrastructure (app.js, style.css, base.html) is strong at 4/5. However, the plan-to-spec alignment suffers from several concrete gaps in the dashboard layout (wrong metrics, missing YTD, missing mode card, missing portfolio sparkline). The exit test coverage is undermined by weak assertions and missing axe-core integration. The 11-REVIEW.md correctly identified the critical walrus-operator bug and 6 weak-assertion warnings, but those findings have not been addressed. The system is close to spec-compliant for the chrome (top bar, sidebar, shortcuts, notifications, a11y basics) but has measurable gaps in the dashboard content layout. Fixing the risk metrics (Gap 1) and adding axe-core (Gap 5) would bring the score to 4/5.

---

_Reviewed: 2026-05-26T12:36:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
