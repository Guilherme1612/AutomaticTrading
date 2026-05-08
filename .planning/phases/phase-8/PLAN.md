# Phase 8 Plan — Polish / LIVE-READY

## Overview

Phase 15 (PMACS) → GSD Phase 8. Production-quality polish for paper trading readiness. 16 deliverables across ops tools, UI polish, accessibility, performance, and documentation.

## Dependencies

All previous phases (1-7) complete. 824 tests pass.

## Build Waves

### Wave 1: Ops Tools (3 files + tests)

Independent Python scripts. No UI dependencies. Testable immediately.

**T1.1: `ops/spec_consistency.py`** — Cross-file reference checker
- Parse Source.md section headers and operator promises
- Parse Architecture.md implementation pointers
- Verify every Source.md operator-promise has an Architecture.md § reference
- Exit test 5: `python ops/spec_consistency.py` passes
- Test: `tests/unit/test_spec_consistency.py`

**T1.2: `ops/audit_chain_verify.py`** — Standalone audit chain verification
- Read audit.log entries
- Verify `prev_sha256` hash chain integrity
- Report break points with line numbers
- Support `--after N` flag to verify last N entries
- Test: `tests/unit/test_audit_chain_verify.py`

**T1.3: `ops/backup_verify.py`** — Backup and restore verification
- Backup command: snapshot all 5 stores (SQLite, KuzuDB, Qdrant, DuckDB, audit.log)
- Restore command: wipe + restore from snapshot
- Verify: audit chain still intact after restore
- Test: `tests/unit/test_backup_verify.py`

**T1.4: Wave 1 tests** — Unit tests for all 3 ops tools

---

### Wave 2: UI Foundation (HTMX + SSE + States + Notifications)

Add client-side infrastructure that all page polish depends on.

**T2.1: Add HTMX + D3 to base template**
- Add `<script src="https://unpkg.com/htmx.org">` to base.html
- Add `<script src="https://d3js.org/d3.v7.min.js">` to base.html
- Add HTMX config: `hx-ext="sse"`, SSE connect to `/events`
- Verify: pages still render, no console errors

**T2.2: SSE → HTMX wiring in app.js**
- Extend existing `connectSSE()` to dispatch HTMX events
- Add `onSSE()` handlers for each stream type (cycle, trade, debug, health)
- SSE events trigger `htmx.ajax()` partial updates on target elements
- Add `hx-sse="connect:/events"` attribute support

**T2.3: State components (empty/loading/error)**
- Create `components/empty_state.html` — pre-first-cycle and post-cycle-empty variants
- Create `components/loading_state.html` — shows what's loading + ETA, no spinners
- Create `components/error_state.html` — error code, description, "What this means" expander, "What to try", "Copy for Claude Code" button, spec link
- Integrate into all 7 page templates where data is empty/loading/errored

**T2.4: Notification policy (§13.5)**
- Extend `showToast()` in app.js with severity levels (info/warning/error/critical)
- Add persistent vs auto-dismiss (5s info, sticky warning/error)
- Add modal system for kill switch + audit chain failure (blocks UI)
- Sound: system click/alert for stop-loss/kill-switch/audit-chain events
- Map SSE event types to notification behavior per §13.5 table
- Kill switch and audit chain modals are non-disableable

**T2.5: Cmd-K command palette**
- Add palette HTML to base.html (hidden by default)
- JS: Cmd-K opens palette, fuzzy search across:
  - Tickers (jumps to Pipeline filtered)
  - Pages (Dashboard, Agents, Pipeline, etc.)
  - Quick actions ("run cycle now", "engage kill switch", "promote NBIS")
  - Audit search (cycle IDs, error codes)
- API endpoint: `GET /api/search?q=` returns categorized results
- Esc closes palette

**T2.6: Keyboard shortcuts (§13.6)**
- Add event listeners in app.js:
  - Cmd-1..7: page navigation
  - Cmd-R: refresh current page
  - Cmd-/: show shortcut overlay
  - /: focus search/filter on current page
  - Esc: close modal/drawer/dismiss toast
  - Cmd-Shift-K (Agents page): engage kill switch with confirmation
  - Cmd-T (no input focus): open TOTP modal
  - ?: contextual help overlay
- Add shortcut overlay modal to base.html

---

### Wave 3: Page Polish

Apply Wave 2 infrastructure to each page's spec requirements.

**T3.1: Dashboard — sparklines + time-window selector**
- Add SVG sparkline component (24px tall, single line, no axes, hover reveals values)
- Sparklines on: portfolio value history, P&L history, drawdown history
- Time-window selector: 1D / 1W / 1M / 3M / ALL toggle
- Data endpoint: `GET /api/dashboard/sparkline?metric=X&window=Y`
- StatBlocks get sparklines below values

**T3.2: Dashboard — mutation summary card**
- Wire mutation card to real data (mutation_summary from route)
- Show "Activates after 50 cycles (current: N)" when dormant
- Show pending candidate count with badge
- "1 pending operator review" → click → Settings → Mutation section

**T3.3: Agents — persona progress bars**
- Add progress bar to each PersonaCard (CSS animated)
- Show persona status: idle → running → complete with color transition
- Show output summary after completion (p_up, p_flat, p_down)
- Respects `prefers-reduced-motion`: static bar instead of animated

**T3.4: Agents — Sankey diagram (D3.js)**
- Replace placeholder div with D3 Sankey rendering
- Evidence sources (left) → Personas (center) → Arbitrated output (right)
- Flow widths = evidence relevance weights
- Hover reveals specific evidence pieces
- After arbitration: second smaller Sankey (Arbitrated → Crucible)
- Smooth D3 enter/update/exit transitions (200ms)

**T3.5: Agents — Math view**
- Toggle between Process/Network/Math views (chip group)
- Math view: per-persona p_up, p_flat, p_down, weight (after Brier adjustment)
- Arbitration formula computed step-by-step
- Conviction formula computed step-by-step
- Progressive fill as data arrives from SSE

**T3.6: Agents — cycle compare (§15.9)**
- Cmd-K → "Compare cycles" action
- Select two cycle IDs → side-by-side view
- Shows: evidence diff, persona output diff, Crucible result diff, verdict diff
- Route: `GET /agents/compare?cycle_a=X&cycle_b=Y`

**T3.7: Pipeline — kanban refinement**
- Smooth drag-drop (HTMX + HTML5 drag API)
- Priority bands within columns (HIGH/MEDIUM/LOW)
- "Run again now" button on SKIP cards → toast confirmation
- Card detail drawer with failure history
- HTMX partial updates for card movements

**T3.8: Debug — "Copy for Claude Code" button**
- Add copy button to every expanded debug event
- Copies paste-ready prompt with: error code, context, stack trace (if any)
- Uses `navigator.clipboard.writeText()`
- Toast confirmation: "Debug context copied to clipboard"

---

### Wave 4: Accessibility + Performance + Documentation

**T4.1: Accessibility — aria-labels + roles**
- Every icon paired with `aria-label`
- Live regions (`aria-live="polite"`) for toasts and SSE-driven updates
- Meaningful tab order on all pages
- Focus states: 2px accent outline, never `outline: none` without replacement
- Works at 200% zoom without horizontal scroll
- Viewport ≥1024px (below: "use a wider window" message)

**T4.2: Accessibility — reduced-motion**
- `@media (prefers-reduced-motion: reduce)` CSS rules
- Disable all animations: Sankey, progress bars, Math view transitions
- Show static equivalents
- Add to style.css

**T4.3: Accessibility — keyboard navigation**
- All interactive elements keyboard-accessible
- Tab order is meaningful (topbar → sidebar → content)
- Focus trap in modals (TOTP, Cmd-K, kill switch confirmation)
- Browser back/forward: HTMX pushes URL state

**T4.4: Performance profiling scripts**
- `ops/profile_cycle.py` — simulate cycle phases, measure time per phase
- `ops/profile_memory.py` — measure RSS of each process, verify <50GB total
- Compare against Architecture.md §20.1 (throughput) and §20.2 (RAM) budgets
- Output: pass/fail table with actual vs budget

**T4.5: Documentation — `docs/operator_runbook.md`**
- System startup sequence (launchd dependency order)
- Daily workflow: pre-market inspection, cycle monitoring, evening shutdown
- TOTP setup guide
- Mode promotion guide with gate requirements
- Kill switch procedures (engage + disengage)
- Mutation Engine review workflow
- Backup/restore procedures
- Troubleshooting common issues

**T4.6: Exit test verification**
- Run all 8 exit tests from Phase 15 spec
- Document pass/fail with evidence
- Verify 824+ existing tests still pass

---

## File Manifest

### New files
```
ops/spec_consistency.py
ops/audit_chain_verify.py
ops/backup_verify.py
ops/profile_cycle.py
ops/profile_memory.py
docs/operator_runbook.md
tests/unit/test_spec_consistency.py
tests/unit/test_audit_chain_verify.py
tests/unit/test_backup_verify.py
```

### Modified files
```
pmacs/web/templates/base.html          — HTMX/D3 scripts, Cmd-K palette, shortcut overlay
pmacs/web/static/app.js                — SSE→HTMX, Cmd-K, keyboard shortcuts, notifications
pmacs/web/static/style.css             — Reduced-motion, focus states, accessibility
pmacs/web/templates/dashboard.html     — Sparklines, time-window, states
pmacs/web/templates/agents.html        — Progress bars, Sankey, Math view, cycle compare
pmacs/web/templates/pipeline.html      — Drag-drop, priority bands
pmacs/web/templates/debug.html         — Copy button
pmacs/web/templates/cortex.html        — States
pmacs/web/templates/universe.html      — States
pmacs/web/templates/settings.html      — States
pmacs/web/routes/dashboard.py          — Sparkline API, fixture data
pmacs/web/routes/agents.py             — Cycle compare route
pmacs/web/routes/pipeline.py           — Card re-order actions
pmacs/web/routes/debug.py              — Copy endpoint
pmacs/web/app.py                       — Search API endpoint
```

### New components
```
pmacs/web/components/empty_state.html
pmacs/web/components/loading_state.html
pmacs/web/components/error_state.html
```

## Execution Strategy

Waves are sequential (each depends on previous). Within each wave, tasks can be parallelized where independent.

- Wave 1 tasks are fully parallelizable (T1.1, T1.2, T1.3, T1.4)
- Wave 2 tasks are mostly sequential (T2.1 first, then T2.2-T2.6)
- Wave 3 tasks are parallelizable after Wave 2 completes
- Wave 4 tasks are parallelizable after Wave 3 completes

## Risk Mitigation

- **No real data yet**: All UI work uses fixture data. Real data plumbing is out of scope (requires live system).
- **Performance tests are verification tools**: Can't fully validate throughput/RAM without M1 Max + model loaded. Scripts verify logic correctness; numbers are budgets.
- **axe-core scan**: Requires headless browser (Playwright). If unavailable, manual accessibility checklist suffices.
- **D3 Sankey complexity**: If D3 Sankey proves too complex for inline implementation, use a simplified static visualization with CSS flexbox as fallback.

## Exit Test Verification

| # | Test | Wave | Evidence |
|---|---|---|---|
| 1 | 8 workflows ≤3 clicks | W3 | Manual walkthrough checklist |
| 2 | 16-ticker cycle ≤3h | W4 | profile_cycle.py output |
| 3 | RAM <50GB peak | W4 | profile_memory.py output |
| 4 | Audit chain after 100+ cycles | W1 | audit_chain_verify.py with fixture |
| 5 | spec_consistency.py passes | W1 | Automated test pass |
| 6 | Backup + restore works | W1 | backup_verify.py with fixture |
| 7 | Accessibility zero critical | W4 | axe-core scan + manual checklist |
| 8 | Toasts/modals/shortcuts work | W2-W3 | Manual checklist per §13.5 + §13.6 |
