# GSD Phase 5: Monitoring + Dashboard

**Implements PMACS Build Phases 9-10** (spec/Phases.md §2)

## Milestone

Stop-loss, re-eval, all 7 UI pages.

---

## PMACS Phase 9: StopLossMonitor + trailing stop + thesis re-evaluation

**Goal:** Active positions are monitored during RTH. Stop-losses fire. Trailing stops arm and ratchet. Weekly re-evaluation and 90-day thesis aging run.

**What gets built:**
- `pmacs/cortex/stop_loss_daemon.py` — the `pmacs-stoploss` process body (`Architecture.md §11`)
- `pmacs/engines/stop_loss_monitor.py` — detection logic, gap-down handling
- Trailing stop arming and ratcheting (`Architecture.md §11.4`)
- Weekly re-evaluation: Nervous step 14 (`Architecture.md §12`)
- Thesis aging review: Nervous step 15 + `THESIS_AGING_REVIEW` state (`Architecture.md §8.2`)
- `pmacs/engines/opportunity_cost.py` — hold-or-exit decision (`Architecture.md §12` step 18)
- SQLite `stop_events` table
- `tests/integration/test_stop_loss.py` — price breaches stop → StopTrigger written → Nervous polls → TradePlan → fill → STOPPED_OUT
- `tests/unit/test_trailing_stop.py` — arms at 1.5R, ratchets up, never down

**Exit test:**
1. `pytest tests/integration/test_stop_loss.py` — complete stop-loss execution path works
2. `pytest tests/unit/test_trailing_stop.py` — trailing math is correct
3. Gap-down: price opens 5% below stop → `MARKET_ON_OPEN` order type selected
4. Weekly re-eval: a held position gets full pipeline re-run; thesis validated → stays ACTIVE; thesis broken → EXIT_THESIS_INVALIDATED
5. 90-day thesis aging: a position held 90+ days triggers THESIS_AGING_REVIEW state; re-eval runs; outcome recorded

**Dependencies:** Phase 8 (active positions exist in PAPER mode).

---

## PMACS Phase 10: Dashboard — all 7 pages

**Goal:** The operator-facing web application is functional. All 7 pages render real data. SSE drives real-time updates. The Agents page shows persona progress in real time.

**What gets built:**
- `pmacs/web/app.py` — FastAPI dashboard
- `pmacs/web/sse_client.py` — subscribes to Nervous `/events`
- `pmacs/web/routes/*.py` — all 7 page routes (dashboard, agents, pipeline, universe, cortex, debug, settings)
- `pmacs/web/templates/*.html` — Jinja2 + HTMX
- `pmacs/web/components/*.html` — reusable partials (card, statblock, persona_card, ticker_chip, totp_field, etc.)
- `pmacs/web/static/` — Tailwind CSS, D3 for Sankey, minimal JS
- Visual identity tokens from `Source.md §13.1`
- Cmd-K command palette
- TOTP modal
- Toast notifications
- All empty states and loading states per `Source.md §13.4`
- `tests/e2e/test_dashboard_renders.py` — each page returns 200 with expected content

**Exit test:**
1. All 7 pages render at `localhost:8001` with real cycle data
2. Agents page shows persona progress during an active cycle (SSE-driven, not polling)
3. Pipeline page shows verdict cards in kanban columns
4. Universe page shows all seeded tickers with correct flags
5. Cortex page shows heartbeats and audit chain status
6. Debug page streams live events
7. Settings page renders all sections; TOTP modal appears on gated actions
8. Dashboard page shows portfolio summary, risk metrics, and recent decisions
9. Operator can reorder queue from Pipeline right rail
10. Operator can add a ticker from Universe page (TOTP-gated)

**Dependencies:** Phase 8 (data exists in DBs), Phase 9 (stop-loss events to display).

---

## Next-phase dependency

GSD Phase 6 requires:
- All PMACS Phase 9-10 exit tests pass
- Stop-loss monitoring works
- All 7 dashboard pages render with real data
- Operator workflows accessible via UI
