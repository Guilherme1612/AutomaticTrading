# Phase 11: Polish, Performance, Operator Experience — Context

## Goal
Make the system production-quality for paper trading. All operator workflows from `Source.md §21` work smoothly. Performance is within budget. The first-30-days experience from `Source.md §23` is pleasant. After this phase, the system is LIVE-READY.

## PMACS Phase Covered
- Phase 15: Polish, performance, operator experience

## Origin
Final GSD phase. All 14 PMACS phases are complete. This is the polish pass that closes deferred UI items, adds profiling verification, and ensures the operator experience meets spec.

## What Phase 10 Deferred Here
Per Phase 10 CONTEXT out-of-scope list:
- Cmd-K command palette — "deferred to Phase 11"
- Sankey/D3 visualization — "deferred to Phase 11"
- Keyboard shortcuts — "deferred to Phase 11"

Per Phase 8 SUMMARY deferred findings:
- S2-1: Performance/memory exit tests need real 16-ticker cycle + measurement
- S3-1: Hardcoded sparkline SVG — acceptable until DuckDB data arrives
- S3-8: HTMX page transitions — full-reload nav acceptable for LIVE-READY
- S3-10: axe-core empirical scan needs running server + playwright

## What Already Exists (Phase 8 built foundations)

### Already Built
- `pmacs/web/templates/base.html` — Cmd-K palette HTML shell, keyboard shortcut overlay, blocking modal, viewport guard, sidebar collapse toggle, aria-labels, landmark roles
- `pmacs/web/static/app.js` (~44KB) — Notification policy (§13.5), Cmd-K with ticker/action/audit search, keyboard shortcuts (§13.6), kill switch handler, SSE-to-notification mapping, copyForClaudeCode, copyErrorForClaude, reduced-motion detection, runCycleNow wired, cycle compare modal, sidebar persistence
- `pmacs/web/static/style.css` (~10KB) — Reduced-motion media query, focus-visible styles, drag-drop styles, sparkline styles, persona progress bars, skeleton loading, toast animations, sidebar collapse transitions
- `pmacs/web/static/sankey.js` (~24KB) — D3 Sankey with Process/Network/Math views
- `pmacs/web/templates/dashboard.html` — Sparklines (hardcoded SVG), time-window selector, pre-first-cycle state, mutation summary card
- `pmacs/web/templates/agents.html` — Persona progress bars, 3-view communication layer, no-active-cycle state
- `pmacs/web/templates/pipeline.html` — Drag-drop kanban, priority bands (P1-P4), "Run again now" on SKIP cards
- `pmacs/web/templates/debug.html` — Copy for Claude Code button, data-event-level filtering, spec refs, repro hints
- `pmacs/web/templates/settings.html` — Notification levels (7 per-event dropdowns), dark mode toggle (System/Light/Dark)
- `pmacs/web/templates/universe.html` — TOTP-gated Add/Remove/Bulk Actions, sub-sector tagging, group-by tabs
- `pmacs/web/templates/cortex.html` — Kill switch engage/disengage, audit chain, cross-DB integrity, process status
- `pmacs/web/components/empty_state.html` — Pre-first-cycle and post-cycle empty states
- `pmacs/web/components/loading_state.html` — No-spinner loading with ETA and cancel
- `pmacs/web/components/error_state.html` — Error code, description, "What this means", "Copy for Claude Code"
- `ops/spec_consistency.py` (~9.5KB) — Cross-file reference checker
- `ops/backup_verify.py` (~12KB) — Backup + restore tested
- `ops/audit_chain_verify.py` (~4KB) — Standalone verification tool
- `ops/profile_cycle.py` (~6.7KB) — Cycle throughput profiler
- `ops/profile_memory.py` (~4.6KB) — Memory usage profiler
- `docs/operator_runbook.md` (~8.4KB) — Complete operator guide

### Known Gaps (what Phase 11 must address)

#### G1. Hardcoded sparkline SVG data (S3-1 from Phase 8)
Dashboard sparklines use static polyline points. Need to read actual time-series from DuckDB `rolling_metrics`.

#### G2. HTMX push-state navigation (S3-8 from Phase 8)
Full-reload on page navigation. Spec requires `htmx.pushUrl` for back/forward. Low priority but spec-compliance item.

#### G3. axe-core empirical accessibility scan (S3-10 from Phase 8)
CSS-level accessibility is in place (reduced-motion, focus-visible, aria-labels). Need Playwright + axe-core automated scan of all 7 pages.

#### G4. Performance/memory profiling validation (S2-1 from Phase 8)
`ops/profile_cycle.py` and `ops/profile_memory.py` exist but need to be run against a real 16-ticker cycle. Exit tests 2-3 from Phase 15 spec.

#### G5. Error states on all pages
Error state component exists but is not integrated into every page. Need per-page error boundaries.

#### G6. Notification level persistence
Settings page has dropdowns but changes are console.log only. Need backend `POST /api/settings/notifications` endpoint.

## Key Spec References
- Source.md §13.1 — Visual identity tokens (color, typography, spacing)
- Source.md §13.4 — State design philosophy (empty, loading, error states)
- Source.md §13.5 — Notification policy (14 event types with surface/sound)
- Source.md §13.6 — Keyboard shortcuts (9 shortcuts)
- Source.md §13.7 — Accessibility (WCAG AA, reduced-motion, keyboard nav)
- Source.md §15.5 — Agents page animations (Sankey, Math view, progress bars)
- Source.md §15.9 — Cycle compare feature
- Source.md §21 — 8 operator workflows (3-click max)
- Source.md §23 — First 30 days experience
- Architecture.md §20.1 — Per-cycle time budget (~2.5h typical, 3h max)
- Architecture.md §20.2 — Memory budget (~49GB used, 15GB headroom, <50GB peak)

## Exit Tests (from Phases.md Phase 15)
1. All 8 operator workflows from Source.md §21 complete in ≤ 3 clicks (excluding TOTP input)
2. Full cycle on 16-ticker universe completes within 3 hours on M1 Max 64GB
3. RAM usage under 50GB during cycle peak
4. Audit chain verifies after 100+ cycles of accumulated data
5. `ops/spec_consistency.py` passes
6. Backup + restore: back up all 5 DBs → wipe → restore → audit chain verifies → system resumes cycling
7. Accessibility: axe-core scan on all 7 pages returns zero critical violations
8. All toast notifications, modal dialogs, and keyboard shortcuts function per spec

## Duration Estimate
7-10 days (per Phases.md)
