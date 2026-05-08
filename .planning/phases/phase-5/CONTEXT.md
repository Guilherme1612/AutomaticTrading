# Phase 5 Context — Monitoring + Dashboard

## PMACS Phases Covered
- Phase 9: StopLossMonitor + trailing stop + thesis re-evaluation
- Phase 10: Dashboard — all 7 pages

## Spec References
- Source.md §13 (visual identity, chrome, components, state design, keyboard shortcuts)
- Source.md §14-20 (7 UI page specs: Dashboard, Agents, Pipeline, Universe, Cortex, Debug, Settings)
- Source.md §21-22 (operator workflows, day-in-the-life)
- Architecture.md §11 (StopLossMonitor two-layer architecture)

## Key Design Decisions
- Notion-aesthetic: zinc surfaces, Inter + JetBrains Mono, Tailwind CSS
- 7 pages: Dashboard, Agents, Pipeline, Universe, Cortex, Debug, Settings
- SSE-driven real-time updates (no polling)
- HTMX for interactivity, D3 for Sankey diagrams
- Cmd-K command palette, TOTP modals for gated actions
- StopLossMonitor runs every 30min during RTH, handoff via SQLite to Nervous

## Exit Tests
1. Stop-loss: price breach → StopTrigger → TradePlan → fill → STOPPED_OUT
2. Trailing stop: arms at 1.5R, ratchets up only
3. Gap-down: MARKET_ON_OPEN selected
4. Weekly re-eval: position re-run, thesis validated/invalidated
5. 90-day thesis aging: THESIS_AGING_REVIEW triggered
6. All 7 pages render with real data
7. SSE drives real-time updates on Agents page
8. TOTP modal appears on gated actions
