phase: 11
plan: polish-operator-experience
type: execute
based_on: CONTEXT.md
waves: 5
depends_on: [phase-10]
autonomous: false

# Phase 11: Polish, Performance, Operator Experience
## PMACS Phase 15 — LIVE-READY

---

## Meta

- **Spec authority:** Source.md §13 (design system), §21 (workflows), §23 (first 30 days); Architecture.md §20 (performance)
- **Exit test count:** 8 (all must pass)
- **Previous phase:** Phase 10 (Broker Integration + Operational Gaps)
- **Non-negotiables unchanged:** LLMs never sign/math, hash-chained audit, local-only, operator kill switch

---

## Pre-Flight

Before starting any wave, verify:
- [ ] `pytest tests/unit/ -x` passes (regression guard)
- [ ] Phase 10 exit tests pass (Alpaca paper, wizard, dead-letter, SSE resume)
- [ ] All 7 pages render in browser without errors
- [ ] `ops/spec_consistency.py` runs (may have findings — that is expected)

---

## Wave 1: Dynamic Sparklines + HTMX Navigation
**Priority:** High (operator sees data immediately)

### S1-1: Wire sparklines to DuckDB rolling_metrics

Dashboard sparklines currently use hardcoded SVG polyline points. Replace with actual time-series data.

**Files:**
- `pmacs/web/data.py` — Add `get_sparkline_data(db, metric, window)` reading from DuckDB `rolling_metrics` table. Returns list of `(timestamp, value)` tuples for the selected window (1D/1W/1M/3M/ALL).
- `pmacs/web/templates/dashboard.html` — Replace static `<polyline points="...">` with Jinja2 loop over `sparkline_data` points. Add `hx-get="/api/dashboard/sparkline?metric=sharpe&window=1W"` for dynamic window switching.
- `pmacs/web/routes/dashboard.py` — Add `/api/dashboard/sparkline` endpoint returning JSON `[{t, v}]`.

**Verify:**
- Time-window buttons (1D/1W/1M/3M/ALL) fetch new data and update SVG.
- Pre-first-cycle state still shows "Run smoke-test cycle" (no broken sparklines).
- `prefers-reduced-motion` hides sparkline hover tooltips (CSS already handles this).

### S1-2: HTMX push-state for page navigation

Replace full-page reload navigation with HTMX boosted links.

**Files:**
- `pmacs/web/templates/base.html` — Add `hx-boost="true"` to the `<main>` content area. Sidebar links get `hx-push-url="true"`. Add `hx-indicator` for loading state.
- `pmacs/web/static/style.css` — Ensure `.htmx-swapping` / `.htmx-settling` transitions work (already defined).
- `pmacs/web/static/app.js` — Add `htmx:afterSwap` handler to reinitialize page-specific JS (sankey, event listeners).

**Verify:**
- Clicking a sidebar link updates URL without full reload.
- Browser back/forward navigates correctly.
- Drawer open/close pushes URL state (spec: Source.md §13.7).
- Page-specific JS reinitializes after swap (sankey, SSE filters).

### S1-3: HTMX sparkline refresh via SSE

Sparklines update in real-time during cycle execution.

**Files:**
- `pmacs/web/static/app.js` — SSE handler already exists. Add `sparkline_update` event handler that calls the sparkline endpoint and swaps SVG content.

**Verify:**
- During cycle execution, sparkline metrics update without page reload.

---

## Wave 2: Error State Integration + Notification Persistence
**Priority:** High (spec compliance for Source.md §13.4 and §13.5)

### S2-1: Per-page error boundaries

Integrate error_state.html component into every page as a conditional error display.

**Files:**
- `pmacs/web/templates/dashboard.html` — Add `{% if error %}{% include "components/error_state.html" %}{% endif %}` at top of content block.
- `pmacs/web/templates/agents.html` — Same pattern.
- `pmacs/web/templates/pipeline.html` — Same pattern.
- `pmacs/web/templates/universe.html` — Same pattern.
- `pmacs/web/templates/cortex.html` — Same pattern.
- `pmacs/web/templates/settings.html` — Same pattern.
- `pmacs/web/templates/debug.html` — Same pattern.
- `pmacs/web/routes/*.py` — Each route handler catches exceptions and returns `error` context variable with error_code, description, explanation, spec_ref.

**Verify:**
- Force an error (e.g., corrupt DB path) → error state renders with all 5 components (code, description, "What this means", "What to try", "Copy for Claude Code").
- Error state respects dark mode.

### S2-2: Notification level backend persistence

Settings notification dropdowns currently `console.log` only. Wire to backend.

**Files:**
- `pmacs/web/routes/settings.py` — Add `POST /api/settings/notifications` accepting `{event: string, level: string}`. Writes to SQLite `settings` table (key: `notif.{event}`, value: level).
- `pmacs/web/static/app.js` — Replace `console.log` in notification level change handler with `fetch POST /api/settings/notifications`.
- `pmacs/web/data.py` — Add `save_notification_level(db, event, level)` and `get_notification_levels(db)`.
- `pmacs/web/routes/settings.py` — Load saved levels when rendering settings page, pass to template.

**Verify:**
- Change "Trade filled" from Toast to Silent → reload page → level persists.
- Kill switch and audit chain failure modals remain non-disableable (enforced in app.js, not just settings).

### S2-3: Notification level consumption in SSE handler

app.js SSE handler reads saved notification levels to decide surface/sound.

**Files:**
- `pmacs/web/static/app.js` — On page load, fetch saved notification levels from `/api/settings/notifications`. SSE handler checks levels before showing toast/modal. Kill switch and audit chain events bypass level check (always modal).

**Verify:**
- Set "Cycle complete" to Silent → run cycle → no toast for cycle complete.
- Set "Kill switch engaged" to Silent → engage kill switch → modal still shows (non-disableable).

---

## Wave 3: Accessibility Audit + Keyboard Shortcuts Validation
**Priority:** Medium (spec compliance for Source.md §13.6, §13.7)

### S3-1: Playwright + axe-core automated accessibility scan

Create a test suite that runs axe-core against all 7 pages.

**Files:**
- `tests/accessibility/test_a11y.py` — Playwright-based test that:
  1. Starts pmacs-dashboard on test port
  2. Navigates to each of 7 pages
  3. Runs `axe-core` via Playwright accessibility extension
  4. Asserts zero critical violations
  5. Reports WCAG AA contrast failures
- `tests/accessibility/conftest.py` — Dashboard server fixture
- `pyproject.toml` or `requirements-dev.txt` — Add `playwright`, `axe-playwright-python` (or `@axe-core/playwright` equivalent)

**Verify:**
- `pytest tests/accessibility/` passes with zero critical violations on all 7 pages.
- All color combinations meet WCAG AA contrast.
- Every interactive element is keyboard-accessible (tab order test).
- Focus states visible (2px accent outline).
- All icons have `aria-label`.

### S3-2: Keyboard shortcuts functional validation

Validate all 9 shortcuts from Source.md §13.6 work.

**Files:**
- `tests/accessibility/test_keyboard.py` — Playwright tests for:
  - Cmd-K opens command palette
  - Cmd-1 through Cmd-7 navigate to correct pages
  - Cmd-R refreshes current page
  - Cmd-/ shows keyboard shortcut overlay
  - `/` focuses search/filter on current page
  - Esc closes modal/drawer/dismisses toast
  - Cmd-Shift-K (Agents page) opens kill switch confirmation
  - Cmd-T opens TOTP modal
  - `?` shows contextual help

**Verify:**
- All 9 shortcuts produce the expected behavior.
- Shortcuts do not fire when text input is focused (except Esc).

### S3-3: Reduced-motion static equivalents

Verify all animated elements have proper static fallbacks.

**Files:**
- `tests/accessibility/test_reduced_motion.py` — Playwright test with `prefers-reduced-motion: reduce` emulation:
  - Sankey diagram renders without D3 transitions
  - Progress bars show final state (no animation)
  - Toast notifications appear/disappear without animation
  - Sparkline hover tooltips are instant (no transition)

**Verify:**
- `prefers-reduced-motion: reduce` → zero CSS animations fire.

### S3-4: Viewport width guard

Test the 1024px minimum width guard.

**Files:**
- `tests/accessibility/test_viewport.py` — Playwright test:
  - Viewport 1024px → dashboard renders normally
  - Viewport 1023px → "PMACS requires a wider window" overlay appears
  - Viewport 1920px → no horizontal scrollbar at 200% zoom

**Verify:**
- Guard works at exactly 1024px boundary.
- 200% zoom at 1920px viewport: no horizontal scrollbar.

---

## Wave 4: Performance Profiling + Ops Tool Validation
**Priority:** Medium (exit tests 2-6)

### S4-1: Cycle throughput profiling harness

Run `ops/profile_cycle.py` against a synthetic 16-ticker cycle and verify results.

**Files:**
- `ops/profile_cycle.py` — Already exists. Review and update assertions for:
  - Per-symbol persona time ≤ 270s (Architecture.md §20.1)
  - Crucible total ≤ 900s
  - Full cycle ≤ 10,800s (3 hours)
  - Gatekeeper ≤ 5s
- `tests/performance/test_cycle_throughput.py` — Integration test that runs the profiler against a synthetic cycle and asserts budgets.

**Verify:**
- Profiler completes and reports per-phase timing.
- All phase times within budget or profiler explains why (e.g., "no model loaded, skipping inference").

### S4-2: Memory profiling harness

Run `ops/profile_memory.py` and verify RAM usage.

**Files:**
- `ops/profile_memory.py` — Already exists. Review and update assertions for:
  - Total system RAM < 50GB during peak
  - Python processes combined < 3GB
  - DB buffers < 6GB
- `tests/performance/test_memory_budget.py` — Integration test that runs memory profiler.

**Verify:**
- Memory profiler reports per-process RSS.
- Peak RAM < 50GB (or profiler documents why hardware is different).

### S4-3: Audit chain verification at scale

Test `ops/audit_chain_verify.py` with 100+ entries.

**Files:**
- `tests/performance/test_audit_chain_scale.py` — Generate 100+ synthetic audit entries via `canonical_json` + hash-chain. Run verifier. Assert chain is intact. Tamper with entry 50 → assert chain breaks at entry 51.

**Verify:**
- Chain verifies with 100+ entries.
- Tamper detection works (chain breaks at first modified entry).

### S4-4: Backup and restore round-trip test

Test `ops/backup_verify.py` end-to-end.

**Files:**
- `tests/performance/test_backup_restore.py` — Integration test:
  1. Create 5 DBs with test data
  2. Run backup
  3. Wipe all DBs
  4. Run restore
  5. Verify audit chain intact
  6. Verify SQLite holdings, DuckDB metrics, KuzuDB graph, Qdrant vectors all restored

**Verify:**
- Backup creates tarball with all 5 stores.
- Restore recreates all 5 stores.
- Post-restore audit chain verifies.

### S4-5: Spec consistency checker

Run `ops/spec_consistency.py` and address findings.

**Files:**
- `ops/spec_consistency.py` — Already exists. Run and review output. Fix any broken cross-references in spec files if found (spec files are source of truth, not code).

**Verify:**
- `ops/spec_consistency.py` exits 0 or documents acceptable findings.

---

## Wave 5: Operator Workflow Validation + Documentation Polish
**Priority:** High (exit test 1 — the most important one)

### S5-1: Operator workflow automated validation

Validate all 8 workflows from Source.md §21 complete in ≤ 3 clicks.

**Files:**
- `tests/e2e/test_operator_workflows.py` — Playwright E2E tests for each workflow:

  **21.1 "I want to add a new ticker"**
  1. Universe page → click "Add Ticker" (1 click)
  2. TOTP modal appears → enter code (not counted) → Submit
  3. Assert: ticker appears in table

  **21.2 "I want to override a SKIP"**
  1. Pipeline page → find SKIP card → click "Run again now" (1 click)
  2. Assert: ticker moves to queue

  **21.3 "I want to investigate why HIMS got stopped out"**
  1. Dashboard → click stopped-out holding row (1 click)
  2. Assert: drawer opens with failure details, FDE classification, timeline
  3. Click "View in Debug" (2nd click) → filtered debug events for that holding

  **21.4 "I want to review and approve a mutation candidate"**
  1. Settings → Mutation Engine section → click candidate row (1 click)
  2. Candidate expands showing diff, stats
  3. Click "Promote" (2nd click) → TOTP modal → Submit
  4. Assert: toast "Mutation promoted. 30-cycle probation active."

  **21.5 "I want to promote PAPER → PAPER_VALIDATED"**
  1. Dashboard → click mode badge (1 click)
  2. Mode management modal shows gate status
  3. Click "Promote" (2nd click) → TOTP modal → Submit

  **21.6 "I want to engage the kill switch immediately"**
  1. Top bar → click kill switch button (1 click)
  2. Confirmation modal → click "Engage" (2nd click)
  3. Assert: button turns red, toast confirms

  **21.7 "I want to inspect the system before market open"**
  1. Cortex page loads → all 6 panels visible (0 clicks beyond nav)
  2. Verify: audit chain status, cross-DB integrity, process status, kill switch, model integrity, disk/clock/network

  **21.8 "I want to add a sub-sector tag"**
  1. Universe page → select checkboxes for 2 tickers (1 click each, but within same interaction)
  2. Bulk Actions → "Tag sub-sector" (1 click) → TOTP → Submit
  3. Assert: tags appear on rows

**Verify:**
- Each workflow completes in ≤ 3 clicks (excluding TOTP input).
- Tests run against a running dashboard with synthetic data.

### S5-2: Operator runbook update

Update `docs/operator_runbook.md` to reflect all Phase 10-11 changes.

**Files:**
- `docs/operator_runbook.md` — Update sections for:
  - Alpaca paper trading (Phase 10)
  - Wizard walkthrough
  - Notification level configuration
  - Dark mode toggle
  - Keyboard shortcuts reference
  - Cycle compare usage
  - Backup/restore procedures

### S5-3: First-30-days checklist validation

Validate the experience described in Source.md §23.

**Files:**
- `tests/e2e/test_first_30_days.py` — Validate:
  - Day 1: Wizard completes, smoke-test cycle runs, dashboard shows results
  - Week 1: Universe populated, pipeline running daily, debug page shows events
  - Month 1: Calibration active, lessons accumulating, mutation engine dormant (under 50 cycles)
  - All empty/loading/error states render correctly at each stage

---

## Exit Test Matrix

| # | Exit Test | Wave | Test File | Status |
|---|-----------|------|-----------|--------|
| 1 | 8 operator workflows ≤ 3 clicks | W5 | `tests/e2e/test_operator_workflows.py` | [ ] |
| 2 | Full cycle ≤ 3 hours (16 tickers) | W4 | `tests/performance/test_cycle_throughput.py` | [ ] |
| 3 | RAM < 50GB during peak | W4 | `tests/performance/test_memory_budget.py` | [ ] |
| 4 | Audit chain verifies 100+ entries | W4 | `tests/performance/test_audit_chain_scale.py` | [ ] |
| 5 | spec_consistency.py passes | W4 | `ops/spec_consistency.py` | [ ] |
| 6 | Backup → wipe → restore → verifies | W4 | `tests/performance/test_backup_restore.py` | [ ] |
| 7 | axe-core zero critical on 7 pages | W3 | `tests/accessibility/test_a11y.py` | [ ] |
| 8 | All notifications/shortcuts per spec | W3+W5 | `tests/accessibility/test_keyboard.py`, `tests/e2e/test_operator_workflows.py` | [ ] |

---

## File Manifest

### New Files
```
tests/accessibility/__init__.py
tests/accessibility/conftest.py
tests/accessibility/test_a11y.py
tests/accessibility/test_keyboard.py
tests/accessibility/test_reduced_motion.py
tests/accessibility/test_viewport.py
tests/performance/__init__.py
tests/performance/test_cycle_throughput.py
tests/performance/test_memory_budget.py
tests/performance/test_audit_chain_scale.py
tests/performance/test_backup_restore.py
tests/e2e/test_operator_workflows.py
tests/e2e/test_first_30_days.py
```

### Modified Files
```
pmacs/web/data.py                                    — sparkline data loader, notification persistence
pmacs/web/routes/dashboard.py                        — sparkline API endpoint
pmacs/web/routes/settings.py                         — notification level persistence endpoint
pmacs/web/templates/base.html                        — HTMX boost, push-state
pmacs/web/templates/dashboard.html                   — dynamic sparklines, error boundary
pmacs/web/templates/agents.html                      — error boundary
pmacs/web/templates/pipeline.html                    — error boundary
pmacs/web/templates/universe.html                    — error boundary
pmacs/web/templates/cortex.html                      — error boundary
pmacs/web/templates/settings.html                    — error boundary, notification level load
pmacs/web/templates/debug.html                       — error boundary
pmacs/web/static/app.js                              — HTMX afterSwap, SSE sparkline, notification level fetch
pmacs/web/static/style.css                           — any new a11y fixes from audit
docs/operator_runbook.md                             — comprehensive update
```

### Reviewed (may need minor fixes)
```
ops/profile_cycle.py                                 — assertion review
ops/profile_memory.py                                — assertion review
ops/spec_consistency.py                              — run and address findings
ops/backup_verify.py                                 — run and verify
ops/audit_chain_verify.py                            — run and verify
```

---

## Dependency Chain

```
W1 (sparklines + HTMX)  ─┐
W2 (errors + notifs)     ─┤── W3 (a11y audit) ── W4 (perf profiling) ── W5 (workflows)
                          │
                          └── W3 needs W2 (error states on pages for axe-core scan)
```

W1 and W2 can run in parallel. W3 depends on W2 (pages must have error boundaries). W4 is independent of W1-W3. W5 depends on everything.

---

## Risk Notes

1. **Playwright infrastructure:** Tests/accessibility requires Playwright installed. If Playwright is not available, W3 tests become manual checklist items.
2. **Real hardware profiling:** W4 exit tests 2-3 require actual M1 Max 64GB with model loaded. Without it, profiler documents what it can and marks timing as "synthetic."
3. **DuckDB rolling_metrics table:** S1-1 depends on DuckDB having actual data. If table is empty, sparklines show "No data yet" state gracefully.
4. **HTMX boost compatibility:** Some page-specific JS (sankey init, SSE handlers) may not reinitialize after HTMX swap. S1-2 must handle this with `htmx:afterSwap` events.

---

## Trust Boundaries

- Dashboard is READ-ONLY (Source.md §13.2). Write actions go through `open_totp_modal()` → POST to nervous API. This phase does not change that boundary.
- Notification level persistence is the one exception: Settings page writes to its own SQLite table. This is acceptable because it is operator preference, not trading state.
- Performance profiling scripts read system state only. No writes to production DBs.
