# Phase 8 Context — Polish / LIVE-READY

## PMACS Phases Covered
- Phase 15: Polish, performance, operator experience

## Milestone: LIVE-READY

## Spec References
- Source.md §13 (design system: chrome, components, states, notifications, shortcuts, accessibility)
- Source.md §14-§20 (7 UI page specifications)
- Source.md §21 (8 operator workflows)
- Source.md §23 (first-30-days experience)
- Architecture.md §20 (performance budget: throughput + RAM)
- Architecture.md §16 (anti-patterns — must not violate)

## Current Dashboard State

**Stack:** FastAPI + Jinja2Templates + Tailwind CSS (CDN). No HTMX, no D3.js.

**7 pages built** (Dashboard, Agents, Pipeline, Universe, Cortex, Debug, Settings):
- Base template: topbar (56px), left sidebar (240px), TOTP modal, toast container
- All routes return static/stub mock data — no real data plumbing from stores
- Components: card, statblock, ticker_chip, totp_modal
- Static files: app.js (basic), style.css
- SSE client exists but not wired to UI updates

**Missing (Phase 15 scope):**
- HTMX integration for real-time SSE-driven updates
- D3.js Sankey diagram (currently placeholder div)
- Cmd-K command palette
- Keyboard shortcuts (Cmd-1..7, Cmd-K, Cmd-R, /, Esc, ?)
- Notification policy (event → surface/sound mapping from §13.5)
- Empty/Loading/Error state components (§13.4)
- Accessibility (aria-labels, reduced-motion, keyboard nav, focus states)
- Sparklines on dashboard
- Cycle compare feature
- "Copy for Claude Code" button on debug events
- Pipeline drag-drop refinement
- Agents page progress bars and Math view

## Auto-Decisions (--auto mode)

### D1: Client-side interactivity
- **Add HTMX** for SSE-driven partial updates and URL state management (spec requires)
- **Add D3.js** (CDN) for Sankey visualization in Agents page
- **Pure vanilla JS** for Cmd-K palette, keyboard shortcuts, notifications (no framework)
- Rationale: Spec explicitly names HTMX and D3. No React/Vue mentioned.

### D2: Data plumbing
- Routes remain stub-driven for this phase (real stores not yet populated in development)
- Create **test fixtures** that simulate real SSE events and store data
- Add API endpoints that return fixture data for development/verification
- Exit test 1 (workflows ≤3 clicks) verified with fixture-driven UI

### D3: Performance/memory profiling (exit tests 2-3)
- Create profiling harness scripts in `ops/` that simulate cycle load
- Verify throughput against Architecture.md §20.1 budgets
- Verify RAM against Architecture.md §20.2 budgets
- These are **verification tools**, not automated CI gates (need real hardware + model)

### D4: Accessibility (exit test 7)
- Add aria-labels, role attributes, keyboard nav to all interactive elements
- Add `prefers-reduced-motion` media query handling
- axe-core scan via `npx axe-core` in a test script (not full CI pipeline)
- Manual checklist for the 7 WCAG requirements from §13.7

### D5: Scope prioritization
Phase 15 has 16 deliverables. Prioritized in build order:
1. **Ops tools** (independent, testable immediately): spec_consistency.py, backup_verify.py, audit_chain_verify.py
2. **HTMX + SSE wiring** (foundation for all UI polish)
3. **State components** (empty/loading/error) — needed by all pages
4. **Notification policy** — toasts, modals from SSE events
5. **Keyboard shortcuts + Cmd-K** — global JS behaviors
6. **Dashboard sparklines + time-window selector**
7. **Agents page** — progress bars, Sankey (D3), Math view, cycle compare
8. **Pipeline page** — drag-drop refinement, priority bands
9. **Copy for Claude Code button** on debug events
10. **Accessibility audit** — aria-labels, reduced-motion, focus states, axe-core
11. **Performance profiling** — cycle throughput, RAM verification
12. **Documentation** — operator_runbook.md

### D6: Test strategy
- Ops tools get their own unit tests
- UI features verified via test fixtures + manual checklist
- Exit tests documented as pass/fail checklist with evidence
- Previous phase regression: 824 tests must still pass

## Exit Tests Mapping

| Exit Test | How Verified |
|---|---|
| 1. 8 workflows ≤3 clicks | Manual walkthrough with fixture-driven UI |
| 2. 16-ticker cycle ≤3h | Profiling harness against §20.1 budget table |
| 3. RAM <50GB peak | Memory profiling harness against §20.2 budget table |
| 4. Audit chain after 100+ cycles | audit_chain_verify.py with synthetic chain fixture |
| 5. spec_consistency.py passes | Cross-reference checker, automated |
| 6. Backup + restore | backup_verify.py end-to-end test |
| 7. Accessibility zero critical | axe-core scan + manual checklist |
| 8. Toasts/modals/shortcuts | Manual checklist per §13.5 + §13.6 |

## Anti-Patterns Checklist (Architecture.md §16)
All prior-phase anti-patterns remain enforced. Phase 15 adds no new anti-patterns but must verify existing code doesn't violate them.
