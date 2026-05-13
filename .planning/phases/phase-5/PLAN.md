# Phase 5 Plan — Monitoring + Dashboard (Addresses Review Feedback)

**Implements PMACS Build Phases 9-10** (spec/Phases.md Phase 9, Phase 10)
**Addresses:** REVIEWS-5.md (5 critical, 7 significant, 5 minor)

> **IMPORTANT:** This is a REVISION pass. All files listed in SUMMARY.md already exist and are functional.
> Every task in this plan is either [EXTEND] (add to existing code), [REWRITE] (replace existing code),
> or [NEW] (create new file). Tasks are marked accordingly. Do NOT recreate files that already exist.

---

## Review Coverage Matrix

| Review ID | Item | Wave | Plan/Task |
|-----------|------|------|-----------|
| C1 | Catastrophe-net cancellation logic | 2 | Plan 02 / Task 1 |
| C2 | OpportunityCostEngine per-holding iteration | 2 | Plan 02 / Task 2 |
| C3 | TOTP modal as reusable component | 4 | Plan 04 / Task 1 (+ Plan 03 / Task 2 for API endpoint) |
| C4 | Nervous SSE `/events` endpoint spec | 3 | Plan 03 / Task 1 |
| C5 | Visual identity Tailwind config | 4 | Plan 04 / Task 2 |
| S1 | Nervous SQLite poller 10s during RTH | 2 | Plan 02 / Task 3 |
| S2 | Trailing stop -> EXIT_TRAILING_STOP + FDE | 2 | Plan 02 / Task 1 |
| S3 | Cmd-K command palette all shortcuts | 5 | Plan 05 / Task 2 |
| S4 | Agents page D3 Sankey diagram | 4 | Plan 04 / Task 3 |
| S5 | Pipeline P1-P4 multi-band priority queue | 5 | Plan 05 / Task 1 |
| S6 | Cycle resume protocol (op_idempotency) | 1 | Plan 01 / Task 1 |
| S7 | Page-by-page exit tests (7 separate) | 6 | Plan 06 / Tasks 1-4 |
| M1 | THESIS_AGING_REVIEW exit paths | 2 | Plan 02 / Task 2 |
| M2 | Weekly re-eval cadence persistence | 1 | Plan 01 / Task 2 |
| M3 | Dashboard responsiveness 1024px min | 4 | Plan 04 / Task 2 |
| M4 | Debug page Copy for Claude Code button | 5 | Plan 05 / Task 3 |
| M5 | Settings mutation candidate display | 5 | Plan 05 / Task 3 |

---

## Wave Structure

| Wave | Plans | Focus | Depends On |
|------|-------|-------|------------|
| 1 | Plan 01 (2 tasks) | Foundation: schemas, DB migrations, cadence | Phase 4 complete |
| 2 | Plan 02 (3 tasks, sequential) | Core engines: stop-loss, catastrophe-net, trailing stop, opportunity cost | Plan 01 |
| 3 | Plan 03 (4 tasks) | Dashboard backend: SSE endpoint, TOTP API, data layer, route wiring | Plan 02 |
| 4 | Plan 04 (3 tasks) | Dashboard frontend: TOTP modal, Tailwind config, Sankey | Plan 03 |
| 5 | Plan 05 (3 tasks) | Integration: P1-P4 queue, Cmd-K, Debug copy, Settings mutation display | Plan 04 |
| 6 | Plan 06 (4 tasks, sequential) | Tests + exit tests: page-by-page verification | Plan 05 |

---

## Plan 01: Foundation — Schemas, DB Tables, Cadence Tracking

### Task 1: Migrate stop_events + op_idempotency schemas; verify state machine transitions [S6, C1-partial]

**Files:**
- `pmacs/schemas/stop_loss.py` — [EXTEND] add `StopEventStatus` enum, add `status` field to `StopTrigger`
- `pmacs/storage/sqlite.py` — [EXTEND] ALTER existing `stop_events` and `op_idempotency` tables
- `pmacs/schemas/contracts.py` — [VERIFY] confirm state transitions exist

**Action:**
- The `stop_events` table ALREADY EXISTS in `pmacs/storage/sqlite.py` (lines 73-83). It uses `id INTEGER PRIMARY KEY AUTOINCREMENT`, `processed INTEGER DEFAULT 0`, `stop_price_usd REAL`, `trigger_price_usd REAL`. **Do NOT create a new table or new file.**
- ALTER TABLE `stop_events` to add: `status TEXT DEFAULT 'PENDING'` column. Add migration logic to convert `processed=1` rows to `status='FILLED'` and `processed=0` rows to `status='PENDING'`.
- ALTER TABLE `stop_events` to add: `stop_type TEXT DEFAULT 'FIXED'` column (FIXED / TRAILING_STOP).
- ALTER TABLE `stop_events` to add: `updated_at TEXT` column.
- The `op_idempotency` table ALREADY EXISTS in `pmacs/storage/sqlite.py` (lines 177-183). It has `cycle_id TEXT, op_seq INTEGER, op_type TEXT, completed_at TEXT`. ALTER to add `result_hash TEXT` column.
- In `pmacs/schemas/stop_loss.py`, add `StopEventStatus(str, Enum)` with PENDING, SUBMITTED, FILLED, CANCELLED values. Extend `StopTrigger` with `status: StopEventStatus = StopEventStatus.PENDING` and `stop_type: str = "FIXED"`.
- **Column name convention:** ALL queries must use `stop_price_usd` and `trigger_price_usd` (not `stop_price` / `trigger_price`) to match existing schema. Update all references in Plans 02-06 accordingly.
- In `pmacs/schemas/contracts.py`, verify VALID_TRANSITIONS includes: `ACTIVE -> EXIT_TRAILING_STOP`, `ACTIVE -> EXIT_OPPORTUNITY_COST`, `ACTIVE -> THESIS_AGING_REVIEW`, `THESIS_AGING_REVIEW -> ACTIVE`, `THESIS_AGING_REVIEW -> EXIT_THESIS_INVALIDATED`. If any are missing, add them.
- Verify `pmacs/nervous/checkpoint.py` already has `save_checkpoint` and `is_completed` that use the `op_idempotency` table.

**Verify:**
```
pytest tests/unit/test_schemas.py -x
```

**Done:** `stop_events` has `status` and `stop_type` columns via ALTER TABLE. `op_idempotency` has `result_hash`. All state transitions valid. No new files created.

### Task 2: Weekly re-eval cadence persistence [M2, S6-partial]

**Files:**
- `pmacs/schemas/contracts.py` — add `last_reeval_at: date | None` field to `Holding`
- `pmacs/engines/thesis_reeval.py` — update `check_weekly_reeval` to use `last_reeval_at`
- `tests/unit/test_thesis_reeval.py` — new test file

**Action:**
- Add `last_reeval_at: date | None = None` to the `Holding` model in `contracts.py`. This field tracks when the last weekly re-eval ran for this holding. Without it, the system cannot distinguish "7 days since entry" from "7 days since last re-eval".
- Rewrite `check_weekly_reeval` to accept `last_reeval_at: date | None` instead of computing from `entry_date`. When `last_reeval_at` is None (never re-evaluated), use `entry_date`. Return True if `(current_date - reference_date).days >= 7`.
- Update `check_thesis_aging` to also return a boolean — no signature change needed, but add docstring clarifying that 90-day is calendar days from `entry_date` (no reset).
- Create `tests/unit/test_thesis_reeval.py` with tests: (a) re-eval not due after 6 days, (b) re-eval due at 7 days from entry when last_reeval_at is None, (c) re-eval due 7 days after last_reeval_at, (d) thesis aging triggered at 90 days, (e) THESIS_AGING_REVIEW returns VALIDATED action [M1], (f) THESIS_AGING_REVIEW returns EXIT_THESIS_INVALIDATED when underwater > 10% [M1].

**Verify:**
```
pytest tests/unit/test_thesis_reeval.py -v
```

**Done:** Weekly re-eval uses `last_reeval_at` field. 90-day aging works from entry date. Both exit paths (VALIDATED -> ACTIVE, EXIT_THESIS_INVALIDATED) tested.

---

## Plan 02: Core Engines — Stop-Loss, Catastrophe-Net, Trailing Stop, Opportunity Cost

> **Intra-plan ordering:** Tasks execute sequentially: Task 1 → Task 2 → Task 3.
> Task 3 (stop_poller) calls `execute_exit` from Task 1. Task 1 must complete first.

### Task 1: Catastrophe-net cancellation + trailing stop state transitions [C1, S2, S1-partial]

**Files:**
- `pmacs/execution/catastrophe_net.py` — add `cancel_catastrophe_net()` function
- `pmacs/engines/stop_loss_monitor.py` — add trailing stop breach detection returning STOP type TRAILING_STOP
- `pmacs/stop_loss_daemon.py` — wire catastrophe-net cancel into exit path, add Nervous poller reference
- `tests/unit/test_catastrophe_cancel.py` — new test file
- `tests/unit/test_stop_loss_monitor.py` — new test file for trailing stop breach detection

**Action:**
- In `pmacs/execution/catastrophe_net.py`, add `cancel_catastrophe_net(order_id: str) -> CancelResult` function that: (a) calls `broker.cancel_order(order_id)`, (b) on success returns `CancelResult(success=True)`, (c) on `BrokerError` logs `CATASTROPHE_CANCEL_FAILED` at CRITICAL level, engages kill switch via `pmacs.cortex.kill_switch.engage(trigger="CATASTROPHE_CANCEL_FAILED")`, and raises. This implements Architecture.md Section 11.5 exactly.
- Add `execute_exit(holding, exit_reason: str)` orchestration function that: (1) cancels catastrophe-net stop, (2) submits primary exit order, (3) audits with `log_audit("catastrophe_net_cancelled", ...)`. The function requires `cycle_id` parameter per anti-pattern Section 16.
- In `pmacs/engines/stop_loss_monitor.py`, add `check_trailing_breach(holding, current_price: float) -> StopCheckResult | None`. When `holding.trailing_stop_armed` is True and `current_price <= holding.trailing_stop_price`, return `StopCheckResult` with `stop_type=TRAILING_STOP`. This is distinct from the fixed-stop check and produces `EXIT_TRAILING_STOP` state [S2].
- Update `stop_loss_daemon.py` `run_stop_loss_loop` to call both `check_stop_breach` and `check_trailing_breach` for each holding. When trailing breach detected, the StopTrigger written to SQLite uses `stop_type=TRAILING_STOP` and `stop_price=holding.trailing_stop_price`.
- In `tests/unit/test_catastrophe_cancel.py`: (a) cancel succeeds, (b) cancel fails triggers kill switch + raises, (c) cancel audits event.
- In `tests/unit/test_stop_loss_monitor.py`: (a) trailing stop breach returns TRAILING_STOP type, (b) trailing stop not breached returns None, (c) fixed stop breach returns FIXED_STOP type, (d) both fixed and trailing breach — trailing takes priority when armed.

**Verify:**
```
pytest tests/unit/test_catastrophe_cancel.py tests/unit/test_stop_loss_monitor.py -v
```

**Done:** Catastrophe-net cancel-before-exit with kill-switch fallback [C1]. Trailing stop produces EXIT_TRAILING_STOP distinct from STOPPED_OUT [S2]. Both tested.

### Task 2: OpportunityCostEngine per-holding iteration [C2, M1]

**Files:**
- `pmacs/engines/opportunity_cost.py` — rewrite with per-holding iteration interface
- `pmacs/nervous/orchestrator.py` — wire opportunity cost call at step 18
- `tests/unit/test_opportunity_cost.py` — new test file

**Action:**
- Rewrite `opportunity_cost.py` to accept a `Holding` object and return `OpportunityCostResult`. The function iterates per active holding (Architecture.md Section 12 step 18: `foreach active holding: OpportunityCostEngine.decide_hold_or_exit()`).
- Add `evaluate_holding(holding, current_conviction, alternative_return_pct) -> OpportunityCostResult` function that: (a) computes PnL, (b) calls existing `decide_hold_or_exit` logic, (c) when action is "EXIT", returns with `exit_state=EXIT_OPPORTUNITY_COST` for state machine transition, (d) always writes audit with `cycle_id` required parameter.
- Add `run_opportunity_cost_scan(active_holdings: list[Holding], ...) -> list[OpportunityCostResult]` that iterates all active holdings and returns results. This is what Nervous calls at step 18.
- For each EXIT result, the audit trail includes: `holding_id`, `conviction_drop`, `opportunity_cost_pct`, `reason`. The state machine transition `ACTIVE -> EXIT_OPPORTUNITY_COST` must be logged.
- In `tests/unit/test_opportunity_cost.py`: (a) holding with high conviction stays HOLD, (b) conviction drop > 0.3 + below 0.2 triggers EXIT, (c) underwater > 5% with better alternatives triggers EXIT, (d) per-holding iteration produces one result per holding, (e) EXIT result includes correct exit_state=EXIT_OPPORTUNITY_COST.

**Verify:**
```
pytest tests/unit/test_opportunity_cost.py -v
```

**Done:** Per-holding opportunity cost iteration with audit trail [C2]. EXIT_OPPORTUNITY_COST state transition tested.

### Task 3: Nervous SQLite poller for PENDING stop events [S1]

**Files:**
- `pmacs/nervous/stop_poller.py` — new file
- `tests/unit/test_stop_poller.py` — new test file

**Action:**
- Create `pmacs/nervous/stop_poller.py` implementing the Nervous-side polling logic from Architecture.md Section 11.2 step 2. This is NOT part of the stop-loss daemon — it runs inside pmacs-nervous.
- Class `StopEventPoller` with method `poll_pending(db_path: Path) -> list[StopTrigger]` that queries SQLite `SELECT * FROM stop_events WHERE status='PENDING'`.
- Method `process_trigger(trigger: StopTrigger, execution_service) -> None` that: (a) constructs TradePlan (side=SELL, order_type from trigger), (b) calls `execute_exit` (which cancels catastrophe-net then submits), (c) updates trigger status to SUBMITTED then FILLED, (d) calls `state_machine.transition(holding, target_state, reason, cycle_id, op_seq)`. Target state is STOPPED_OUT for fixed stop, EXIT_TRAILING_STOP for trailing stop [S2].
- Method `run_poll_loop(db_path, execution_service, interval_s=10)` that runs during RTH only (checks `is_rth()`), polls every 10 seconds, processes all PENDING triggers. The 10-second interval matches Architecture.md Section 11.2 [S1].
- In `tests/unit/test_stop_poller.py`: (a) poll returns empty when no PENDING triggers, (b) poll returns triggers with status=PENDING, (c) process_trigger calls execute_exit (catastrophe-net cancel + SELL), (d) process_trigger updates status to FILLED, (e) process_trigger transitions state to STOPPED_OUT for fixed stop, (f) process_trigger transitions state to EXIT_TRAILING_STOP for trailing stop [S2], (g) poll loop skips non-RTH hours.

**Verify:**
```
pytest tests/unit/test_stop_poller.py -v
```

**Done:** Nervous polls SQLite every 10s during RTH for PENDING stop events [S1]. Trailing stop triggers produce EXIT_TRAILING_STOP [S2].

---

## Plan 03: Dashboard Backend — SSE Endpoint, FastAPI Routes, Data Wiring

### Task 1: Nervous SSE `/events` endpoint specification [C4]

**Files:**
- `pmacs/nervous/api.py` — add SSE `/events` endpoint with stream filtering
- `tests/unit/test_sse_endpoint.py` — new test file

**Action:**
- In `pmacs/nervous/api.py`, add GET `/events` endpoint. This is the server-side SSE endpoint that `pmacs/web/sse_client.py` subscribes to. The SSEPublisher class already exists in `pmacs/nervous/sse_publisher.py` — wire it into the API endpoint.
- Endpoint signature: `GET /events?streams=cycle,agent,decision,trade,mutation,system`. The `streams` query param is a comma-separated filter. Default: all streams.
- Response is `StreamingResponse` with `text/event-stream` content type. Each frame: `id: {event_id}\ndata: {json}\n\n`.
- Support `Last-Event-ID` header for reconnection — client replays from last seen event ID.
- The endpoint calls `publisher.subscribe()` to get a client queue, yields frames from the queue, and calls `publisher.unsubscribe()` on disconnect.
- Streams are: `cycle` (cycle.open, cycle.close, cycle.progress), `agent` (agent.start, agent.complete, agent.failed), `decision` (decision.verdict, decision.trade), `trade` (trade.submitted, trade.filled, trade.rejected), `mutation` (mutation.candidate, mutation.promoted, mutation.rejected), `system` (system.health, system.error, system.heartbeat).
- In `tests/unit/test_sse_endpoint.py`: (a) GET /events returns 200 with text/event-stream, (b) stream filter works, (c) events are JSON-parseable, (d) Last-Event-ID reconnects from correct position, (e) multiple clients receive same events.

**Verify:**
```
pytest tests/unit/test_sse_endpoint.py -v
```

**Done:** Nervous `/events` SSE endpoint serves real-time events to dashboard [C4]. Stream filtering and reconnection supported.

### Task 2: TOTP verification API endpoint [NEW] [C3-prerequisite]

**Files:**
- `pmacs/nervous/api.py` — [EXTEND] add `POST /api/totp/verify` endpoint

**Action:**
- Add `POST /api/totp/verify` endpoint to the existing FastAPI app in `pmacs/nervous/api.py`. This is the server-side endpoint the TOTP modal POSTs to (Plan 04 Task 1).
- Request body: `{totp_code: str, action_id: str}`.
- Endpoint calls `pmacs.cortex.totp.verify(totp_code)` to validate the 6-digit code against the TOTP secret.
- On success: returns `{verified: true, action_id: str}`. The frontend then executes the gated action.
- On failure: returns `{verified: false, error: "Invalid TOTP code"}`. Frontend keeps modal open.
- Rate limiting: max 5 attempts per minute per session (use existing `BUCKETS["totp"].acquire()` rate limiter).
- Audit: `log_audit("totp_verify_attempt", {action_id, success, cycle_id})` on every attempt.
- Requires session auth (same as all write endpoints in nervous).

**Verify:**
```
pytest tests/unit/test_totp_endpoint.py -v
```

**Done:** `POST /api/totp/verify` endpoint validates TOTP codes for dashboard gated actions [C3-prerequisite].

### Task 3: Shared data access layer [NEW] [S7-partial]

**Files:**
- `pmacs/web/data.py` — [NEW] shared data access layer for all routes

**Action:**
- Create `pmacs/web/data.py` as a shared data access layer for all routes. Functions:
  - `get_active_holdings(db)` — from SQLite holdings table
  - `get_recent_decisions(db, limit=20)` — from SQLite/DuckDB cycle results
  - `get_risk_metrics(db)` — from DuckDB rolling_metrics
  - `get_system_health(heartbeat_dir)` — from heartbeat files
  - `get_queue_status(db)` — from SQLite queue table
  - `get_universe(db)` — from SQLite universe table
  - `get_debug_events(db, filters)` — from debug log reader
  - `get_settings(config_dir)` — from TOML + JSON config files
  - `get_cortex_status(db, heartbeat_dir, audit_path)` — aggregated cortex data
  - `get_agent_cycle_data(db, cycle_id)` — persona outputs for agents page
- Each function reads from the appropriate store (SQLite, DuckDB, filesystem).
- Unit tests for each function using synthetic fixtures.

**Verify:**
```
pytest tests/unit/test_web_data.py -v
```

**Done:** Shared data access layer with typed functions for all 7 pages. Unit-tested.

### Task 4: Wire 7 route handlers to data layer [EXTEND] [S7-partial]

**Depends on:** Task 3 (data.py must exist first)

**Files:**
- `pmacs/web/routes/dashboard.py` — [EXTEND] wire to data.py for real portfolio data
- `pmacs/web/routes/agents.py` — [EXTEND] wire to data.py for live persona progress
- `pmacs/web/routes/pipeline.py` — [EXTEND] wire to data.py for kanban columns
- `pmacs/web/routes/cortex.py` — [EXTEND] wire to data.py for heartbeat + audit chain + kill switch status
- `pmacs/web/routes/debug.py` — [EXTEND] wire to data.py for debug log reader
- `pmacs/web/routes/universe.py` — [EXTEND] wire to data.py for universe table
- `pmacs/web/routes/settings.py` — [EXTEND] wire to data.py for config reads

**Action:**
- Update each route to import from `data.py` and pass real data to templates instead of hardcoded values. Routes remain synchronous reads — SSE handles live updates.
- `dashboard.py`: portfolio value from paper ledger, positions from active holdings, risk metrics from DuckDB rolling_metrics, system health from heartbeat files.
- `agents.py`: current cycle status from cycles table, persona outputs from DuckDB scan_records, queue from queue table.
- `pipeline.py`: verdict cards from recent cycle decisions, active positions from holdings with HOLD state.
- `cortex.py`: 6 panels — audit chain status (verify last entry hash), cross-DB consistency, process heartbeats, disk/clock/network, kill switch state, model integrity.
- `debug.py`: event stream from debug log reader with filters.
- `universe.py`: ticker list from universe table with group-by support.
- `settings.py`: config reads from TOML files + JSON files.

**Verify:**
```
pytest tests/unit/test_web_routes.py -v
```

**Done:** All 7 routes return real data from databases via shared data layer. Note: E2E rendering tests run in Plan 06 after templates are updated.

---

## Plan 04: Dashboard Frontend — Templates, TOTP Modal, Tailwind Config, Sankey

### Task 1: Reusable TOTP modal component [C3]

**Files:**
- `pmacs/web/components/totp_modal.html` — rewrite as reusable partial
- `pmacs/web/static/app.js` — add `open_totp_modal(action_id, action_description, callback)` function
- `pmacs/web/templates/base.html` — include TOTP modal in base template

**Action:**
- Rewrite `totp_modal.html` as a generic, parameterizable component. Accept via HTMX `hx-vars` or data attributes: `data-action-id`, `data-action-description`, `data-consequences`, `data-confirm-text` (for destructive actions like "Type KILL to confirm"), `data-callback-url`.
- The modal displays: (a) action description, (b) consequences text, (c) TOTP 6-digit input with auto-advance (already in app.js), (d) optional confirmation text field, (e) Cancel button, (f) Confirm button (disabled until TOTP entered + confirmation text matches if required).
- Register the TOTP modal once in `base.html` so it is available on every page. Any button that needs TOTP gating calls `open_totp_modal(...)` with the specific action context.
- Gated actions across the app (per Source.md Section 13.3):
  - Settings: broker key edit, catastrophe-net % change, kill-switch threshold tuning, mutation promote/reject/rollback, persona enable/disable, audit log replication target change, per-trade approval toggle, mode override.
  - Universe: add ticker, remove ticker (with confirmation if active position), bulk tag/remove.
  - Pipeline: force exit (active positions only).
  - Cortex: kill switch disengage (engage does NOT require TOTP per Source.md Section 18.6).
  - Debug: no TOTP actions.
- In `app.js`, the `open_totp_modal` function: (a) populates modal with action context, (b) shows modal, (c) on TOTP submit, POSTs to `/api/totp/verify` with the TOTP code and action_id, (d) on success, executes the gated action, (e) on failure, shows error and keeps modal open.

**Verify:**
```
pytest tests/e2e/test_dashboard_renders.py -k "totp" -v
```

**Done:** Single TOTP modal component reused across all gated actions [C3]. No page-specific TOTP implementations.

### Task 2: Visual identity Tailwind config + responsiveness [C5, M3]

**Files:**
- `pmacs/web/static/tailwind.config.js` — new Tailwind config file
- `pmacs/web/static/style.css` — update with all visual identity tokens
- `pmacs/web/templates/base.html` — update to use Tailwind classes from config
- `pmacs/web/components/card.html` — update with Tailwind card tokens
- `pmacs/web/components/statblock.html` — update with Tailwind stat tokens

**Action:**
- Create `tailwind.config.js` implementing ALL color tokens from Source.md Section 13.1:
  ```
  theme.extend.colors: surface, surface-elevated, border, text-primary, text-secondary,
  text-muted, accent, positive, negative, warning, strong-buy, crucible
  ```
  Map to Tailwind zinc/blue/green/red/amber/purple palettes per the table in Section 13.1.
- Add typography config: `fontFamily.sans = ['Inter', ...]`, `fontFamily.mono = ['JetBrains Mono', ...]`. Font sizes: 12 (caption), 14 (body), 16 (subhead), 20 (head), 28 (page title).
- Add spacing overrides: page gutter 32px, card padding 24px, section gap 16px, tight numeric tables 12px row padding.
- Add dark mode: `darkMode: 'class'` with system preference detection in base template.
- Update `style.css` to import Tailwind base/components/utilities with the custom config.
- Responsiveness [M3]: minimum viewport 1024px. Below 1024px, show "use a wider window" message (desktop tool, not responsive app per Source.md Section 13.7). Between 1024-1280px: single column layout, Agents persona cards in 3x3 grid. Above 1280px: multi-column layout with right rails. Above 1440px: Dashboard page gets right rail per Source.md Section 14.
- WCAG AA compliance: all color combinations meet 4.5:1 contrast ratio. `prefers-reduced-motion` disables all animations (Sankey, progress bars, transitions). Focus states: 2px accent outline. All interactive elements keyboard-accessible. `aria-label` on all icons. Live regions for toasts and SSE updates per Source.md Section 13.7.

**Verify:**
```
pytest tests/e2e/test_dashboard_renders.py -k "visual_identity or responsiveness or accessibility" -v
```

**Done:** Tailwind config with all Source.md Section 13.1 tokens [C5]. Responsive at 1024px+ [M3]. WCAG AA [Section 13.7].

### Task 3: Agents page D3 Sankey diagram [S4]

**Files:**
- `pmacs/web/static/sankey.js` — new D3 Sankey module
- `pmacs/web/templates/agents.html` — add Sankey container + toggle
- `pmacs/web/routes/agents.py` — add endpoint returning Sankey data as JSON

**Action:**
- Create `sankey.js` implementing the Communication Layer Visualization from Source.md Section 15.5. Three views toggled by chip group: Process / Network / Math.
- **Process view** (default): horizontal timeline `Evidence -> Personas -> Arbitration -> Crucible -> Sizing -> Risk Gate -> Verdict`. Each stage is a D3 node. Lines connect them. Completed stages fill with result (e.g., Arbitration shows `p_up=0.62`). Pending stages are gray. Animates left-to-right as stages complete.
- **Network view**: D3 Sankey diagram. Left: evidence sources (SEC filings, Polygon EOD, Form 4, news, IR pages). Middle: personas. Right: Arbitrated output. Flow widths = evidence relevance weights. Hover reveals specific evidence pieces. After Arbitration, second smaller Sankey: Arbitrated -> Crucible (colored by severity). If Crucible flips decision, arrow shows override. D3 enter/update/exit transitions at 200ms.
- **Math view**: Per persona: `p_up`, `p_flat`, `p_down`, weight. Below: arbitration formula step-by-step. Below: conviction formula step-by-step. Numbers fill progressively.
- All animations respect `prefers-reduced-motion` — static equivalents shown when active per Source.md Section 13.7.
- Add route endpoint `GET /agents/sankey-data` returning JSON with: evidence_sources, personas, arbitration_result, crucible_result, weights, flows.
- Update `agents.html` to include a `<div id="sankey-container">` below the persona row, with chip group toggle: Process | Network | Math.

**Verify:**
```
pytest tests/e2e/test_dashboard_renders.py -k "agents_sankey" -v
```

**Done:** D3 Sankey with three views (Process, Network, Math) on Agents page [S4]. Animated transitions, hover interactions. Respects prefers-reduced-motion.

---

## Plan 05: Integration — P1-P4 Queue, Cmd-K, Debug Copy, Settings Mutation Display

### Task 1: Pipeline page P1-P4 multi-band priority queue [S5]

**Files:**
- `pmacs/web/routes/pipeline.py` — add queue management endpoints
- `pmacs/web/templates/pipeline.html` — add right rail with P1-P4 bands
- `pmacs/web/static/app.js` — add drag-and-drop handlers for queue reordering

**Action:**
- Implement the Pipeline right rail per Source.md Section 16.5. Four priority bands: P1 (highest), P2, P3, P4 (background, runs only if cycle has time). Active holdings always in P1 for re-evaluation per Architecture.md Section 12 step 12.
- Add drag-and-drop between bands using HTMX + minimal JS. Dragging a ticker from P3 to P1 sends `POST /pipeline/queue/reorder` with `{ticker, from_band, to_band}`.
- Each band shows ticker chips sorted by priority_score within the band. Priority score formula per Architecture.md Section 12: `priority_score = (catalyst_imminence * 3.0) + (thesis_strength * 2.0) + (source_brier_avg * 1.5) + (portfolio_fit * 1.0)`. Operator pins override score (pinned tickers sort first within their band).
- Add "Promote all in P1 to head of next cycle" button.
- Add pin/unpin per ticker (toggle, persists across cycles).
- Add saved priority schemes: operator can name and recall queue configurations. Stored in SQLite config table.
- Add per-ticker actions from Section 16.4: Run again now, Promote to next-cycle priority, Pin to queue, Force exit (active only, TOTP-gated).

**Verify:**
```
pytest tests/e2e/test_dashboard_renders.py -k "pipeline_queue" -v
```

**Done:** P1-P4 multi-band priority queue with drag-and-drop [S5]. Pin/unpin, saved schemes, promote button.

### Task 2: Cmd-K command palette with all shortcuts [S3]

**Files:**
- `pmacs/web/static/app.js` — extend Cmd-K with all shortcuts from Source.md Section 13.6
- `pmacs/web/templates/base.html` — add keyboard event listeners

**Action:**
- Extend the existing Cmd-K command palette in `app.js` to support ALL shortcuts from Source.md Section 13.6:
  - `Cmd-K`: open command palette (search tickers, pages, quick actions, audit search)
  - `Cmd-1` through `Cmd-7`: jump to page (Dashboard, Agents, Pipeline, Universe, Cortex, Debug, Settings)
  - `Cmd-R`: refresh current page (re-fetch from server via HTMX)
  - `Cmd-/`: show keyboard shortcut overlay
  - `/`: focus search/filter on current page
  - `Esc`: close modal, close drawer, dismiss toast
  - `Cmd-Shift-K` (Agents page only): engage kill switch (typed confirmation: type "KILL" to confirm)
  - `Cmd-T` (when no text input focused): open TOTP modal pre-emptively
  - `?`: show contextual help for current page
- Command palette search: (a) tickers (jump to Pipeline filtered), (b) pages (jump to), (c) quick actions ("run cycle now", "engage kill switch", "promote NBIS to priority"), (d) audit search (recent cycle IDs, error codes).
- Add keyboard shortcut overlay (Cmd-/) showing all shortcuts in a modal grid.
- Browser back/forward: HTMX pushes URL state for every page navigation and drawer open/close per Source.md Section 13.7.

**Verify:**
```
pytest tests/e2e/test_dashboard_renders.py -k "cmd_k or shortcuts" -v
```

**Done:** All 9 keyboard shortcuts from Source.md Section 13.6 implemented [S3]. Command palette with search, quick actions, audit search.

### Task 3: Debug Copy for Claude Code + Settings mutation display [M4, M5]

**Files:**
- `pmacs/web/templates/debug.html` — add "Copy for Claude Code" button on expanded event rows
- `pmacs/web/templates/settings.html` — add mutation candidate display with effect size, p-value, trending
- `pmacs/web/static/app.js` — add copy-to-clipboard function

**Action:**
- In `debug.html`, when an event row is expanded (click to expand inline showing full payload, traceback, spec_ref), add a "Copy for Claude Code" button [M4] per Source.md Section 19.2. On click, the button copies to clipboard a paste-ready prompt containing: (a) event payload (JSON), (b) spec section reference (spec_ref), (c) repro steps ("Run cycle and look for {error_code} in debug events"). The copied text is formatted as a Claude Code prompt the operator can paste directly.
- In `settings.html`, Section 20.8 (Mutation Engine), add mutation candidate display [M5] per Source.md Section 20.8: per candidate show dimension, target (e.g., "moat_analyst.system_prompt"), proposed_at date, sample_size, current effect_size (Cohen's d), current p-value, trending direction (improving/stable/declining). Add Promote button (TOTP-gated) and Reject button. Add recent promotions log with rollback button (TOTP-gated).
- In `app.js`, add `copyForClaudeCode(eventId)` function that: (a) reads the event data from the expanded row, (b) formats as prompt string, (c) calls `navigator.clipboard.writeText()`, (d) shows toast "Copied to clipboard".

**Verify:**
```
pytest tests/e2e/test_dashboard_renders.py -k "debug_copy or settings_mutation" -v
```

**Done:** Debug page has "Copy for Claude Code" button [M4]. Settings shows mutation candidates with effect size, p-value, trending [M5].

---

## Plan 06: Tests + Exit Tests

### Task 1: Dashboard + Agents page exit tests [S7]

**Files:**
- `tests/e2e/test_dashboard_page.py` — [NEW] Dashboard page exit test
- `tests/e2e/test_agents_page.py` — [NEW] Agents page exit test

**Action:**
- Create 2 page-level exit test files with component-level verification. Each test uses FastAPI `TestClient` to request the page and verify specific content.

**1. `test_dashboard_page.py`** — Dashboard page components:
  - (a) Portfolio summary card: current value, day change, sparkline present
  - (b) Mode + cycle status card: mode badge, last cycle info, "Run cycle now" button
  - (c) Risk metrics row: 5 StatBlocks (Sharpe, Sortino, max drawdown, win rate, avg R/R)
  - (d) Active positions table: column headers (ticker, entry date, entry price, current price, MtM %, conviction, re-eval status, days held)
  - (e) Recent decisions feed: last 20 decisions with timestamp, ticker chip, verdict, reason
  - (f) System health card: audit chain status, disk free, clock drift, heartbeats, severity histogram
  - (g) Mutation Engine summary: active mutations, pending review, approved last 30d
  - (h) Empty state: pre-first-cycle hero card with explanation

**2. `test_agents_page.py`** — Agents page components:
  - (a) Queue strip: horizontal scrollable ticker chips with phase indicator
  - (b) Current ticker panel header: ticker, company name, phase badge, elapsed, ETA
  - (c) Persona row: 9 cards (7 analysis + Crucible + MemoWriter) with status indicator
  - (d) Communication layer viz toggle: Process / Network / Math chip group
  - (e) Decision summary right rail: phase 0-2 result, arbitration, EV, sizing, risk gate, conviction, verdict, trade plan
  - (f) Cycle log strip: collapsible, filterable by severity

**Verify:**
```
pytest tests/e2e/test_dashboard_page.py tests/e2e/test_agents_page.py -v
```

**Done:** Dashboard and Agents pages verified at component level [S7].

### Task 2: Pipeline + Universe + Cortex page exit tests [S7]

**Depends on:** Task 1

**Files:**
- `tests/e2e/test_pipeline_page.py` — [NEW] Pipeline page exit test
- `tests/e2e/test_universe_page.py` — [NEW] Universe page exit test
- `tests/e2e/test_cortex_page.py` — [NEW] Cortex page exit test

**Action:**

**1. `test_pipeline_page.py`** — Pipeline page components:
  - (a) Top filter bar: verdict multi-select, state multi-select, sector, date range, search
  - (b) 4 kanban columns: STRONG_BUY, BUY, HOLD, SKIP
  - (c) Per-ticker card: ticker, price, conviction, memo truncated, cycle date, action buttons on hover
  - (d) Right rail P1-P4 bands with drag-and-drop
  - (e) Single-ticker detail drawer: 60% viewport, full memo, per-persona accordion, historical decisions

**2. `test_universe_page.py`** — Universe page components:
  - (a) Top bar: group-by selector, search, "Add ticker" button
  - (b) Per-ticker row: ticker, name, exchange, sector, market cap, ADV, days of history, status badges
  - (c) Add ticker modal: auto-fill, admittance checks (ADV >= $1M, OHLCV available, not halted)
  - (d) Right rail: universe statistics
  - (e) Bulk actions: checkboxes, tag, remove

**3. `test_cortex_page.py`** — Cortex page components:
  - (a) 2x3 grid layout
  - (b) Audit chain panel: status indicator, last verified, total entries, chain head SHA
  - (c) Cross-DB consistency panel: 4 DB indicators, last reconciled, drift count
  - (d) Process status panel: 8 processes with heartbeat age, restart count, PID
  - (e) Disk/clock/network panel: disk free, NTP drift, source connectivity matrix
  - (f) Kill switch panel: ARMED/ENGAGED state, engage button (no TOTP), disengage button (TOTP)
  - (g) Model integrity panel: GGUF SHA256, model name, backend

**Verify:**
```
pytest tests/e2e/test_pipeline_page.py tests/e2e/test_universe_page.py tests/e2e/test_cortex_page.py -v
```

**Done:** Pipeline, Universe, and Cortex pages verified at component level [S7].

### Task 3: Debug + Settings page exit tests [S7, M4, M5]

**Depends on:** Task 2

**Files:**
- `tests/e2e/test_debug_page.py` — [NEW] Debug page exit test
- `tests/e2e/test_settings_page.py` — [NEW] Settings page exit test

**Action:**

**1. `test_debug_page.py`** — Debug page components:
  - (a) Filter bar: level, process, component, error_code, cycle_id, ticker, time range, search
  - (b) Event rows: timestamp, level badge, process, component, error_code, message
  - (c) Quick filter chips: Errors only, Current cycle, Last hour, LLM events, Trade events
  - (d) Expand inline: full payload JSON, traceback, spec_ref, suggested_fix_keywords
  - (e) "Copy for Claude Code" button on expanded row [M4]

**2. `test_settings_page.py`** — Settings page components:
  - (a) 12 sections with left sub-nav anchors: General, Brokers, Inference, Universe, Risk, Crucible, Mutation Engine, Agent Personas, Queue, Audit & Debug, Operator, (plus "not in Settings" note)
  - (b) Mutation Engine section: pending candidates with dimension, target, sample size, effect size, p-value, trending [M5]
  - (c) TOTP modal appears on gated actions (broker key edit, catastrophe-net change, kill-switch threshold, mutation promote/reject/rollback, persona enable/disable, mode override)
  - (d) All TOTP-gated actions call `open_totp_modal()` — verify same component used

**Verify:**
```
pytest tests/e2e/test_debug_page.py tests/e2e/test_settings_page.py -v
```

**Done:** Debug and Settings pages verified at component level [S7, M4, M5].

### Task 4: Full stop-loss integration test [C1, C2, S1, S2, M1]

**Depends on:** Task 3

**Files:**
- `tests/integration/test_stop_loss_full.py` — [NEW] comprehensive stop-loss + engine integration test
  - Note: This EXTENDS the existing `tests/integration/test_stop_loss.py` (28 tests from Phase 4). The new file adds comprehensive coverage for Phase 5 features. Both files should pass.

**Action:**
- Full stop-loss integration test covering:
  - (a) Price breaches stop -> StopTrigger written to SQLite with status=PENDING [S1]
  - (b) Nervous poller picks up PENDING trigger within 10s polling cycle [S1]
  - (c) Poller calls execute_exit which cancels catastrophe-net first [C1]
  - (d) If catastrophe-net cancel fails, kill switch engages [C1]
  - (e) TradePlan submitted, trigger status -> SUBMITTED -> FILLED
  - (f) State machine transitions to STOPPED_OUT (fixed stop) or EXIT_TRAILING_STOP (trailing) [S2]
  - (g) Audit trail written for every step with cycle_id
  - (h) Trailing stop breach produces distinct EXIT_TRAILING_STOP, not STOPPED_OUT [S2]
  - (i) Trailing stop arms at 1.5R, ratchets up only
  - (j) Gap-down: price opens 5% below stop -> MARKET_ON_OPEN order type selected
  - (k) Weekly re-eval: position re-run, thesis validated -> stays ACTIVE [M1]
  - (l) Weekly re-eval: thesis broken -> EXIT_THESIS_INVALIDATED [M1]
  - (m) 90-day thesis aging: THESIS_AGING_REVIEW triggered, outcome recorded
  - (n) Opportunity cost: per-holding iteration, EXIT_OPPORTUNITY_COST when conviction collapses [C2]

**Verify:**
```
pytest tests/integration/test_stop_loss_full.py tests/integration/test_stop_loss.py -v
```

**Done:** Full stop-loss integration test covers all critical paths [C1, C2, S1, S2, M1]. Existing test_stop_loss.py still passes.

---

## Threat Model

| Boundary | Description |
|----------|-------------|
| Operator -> Dashboard (browser) | Untrusted on the wire; TOTP gates all write actions |
| Dashboard -> Nervous API | Internal HTTP on loopback; session auth |
| Nervous -> Execution (UDS) | Unix domain socket; Ed25519 signing required |
| Nervous -> SQLite | File-system access; process-isolated |
| Stop-loss daemon -> Alpaca API | External API call; API key auth |

| Threat ID | Category | Component | Disposition | Mitigation |
|-----------|----------|-----------|-------------|------------|
| T5-01 | S (Spoofing) | TOTP modal | mitigate | 6-digit TOTP with auto-advance; verify server-side before executing gated action |
| T5-02 | T (Tampering) | stop_events SQLite | mitigate | Status transitions audited; PENDING->SUBMITTED->FILLED sequence enforced |
| T5-03 | T (Tampering) | Catastrophe-net cancel | mitigate | Cancel-before-exit enforced in code; kill switch on cancel failure [C1] |
| T5-04 | R (Repudiation) | All state transitions | mitigate | Hash-chained audit log with cycle_id required on every transition |
| T5-05 | I (Information Disclosure) | Dashboard :8001 | mitigate | Loopback-only binding; no external access |
| T5-06 | D (Denial of Service) | SSE /events endpoint | mitigate | Max 1024 events per client queue; auto-unsubscribe on full |
| T5-07 | E (Elevation of Privilege) | Kill switch disengage | mitigate | TOTP required to disengage; engage does NOT require TOTP (safer direction) |

---

## Success Criteria

### PMACS Phase 9 Exit Tests
1. `pytest tests/integration/test_stop_loss_full.py tests/integration/test_stop_loss.py` — complete stop-loss path including catastrophe-net cancellation [C1]
2. `pytest tests/unit/test_trailing_stop.py` — trailing math correct
3. `pytest tests/unit/test_stop_poller.py` — Nervous 10s polling [S1]
4. Gap-down: MARKET_ON_OPEN selected for non-RTH breaches
5. Trailing stop: EXIT_TRAILING_STOP distinct from STOPPED_OUT [S2]
6. Weekly re-eval: validated -> ACTIVE, broken -> EXIT_THESIS_INVALIDATED [M1]
7. 90-day thesis aging: THESIS_AGING_REVIEW triggered, both exit paths work
8. Opportunity cost: per-holding iteration with EXIT_OPPORTUNITY_COST [C2]
9. Catastrophe-net: cancel-before-exit, kill-switch on cancel failure [C1]

### PMACS Phase 10 Exit Tests
1. Dashboard page: portfolio summary + risk metrics + positions table + system health [S7]
2. Agents page: 9 persona cards + Sankey viz (3 views) + decision rail [S4]
3. Pipeline page: 4 kanban columns + P1-P4 queue with drag-and-drop [S5]
4. Universe page: ticker rows with badges + add ticker modal (TOTP)
5. Cortex page: 2x3 grid with all 6 panels
6. Debug page: event stream + "Copy for Claude Code" button [M4]
7. Settings page: 12 sections + mutation candidate display with stats [M5]
8. TOTP modal: single reusable component, POSTs to `/api/totp/verify` [C3]
9. Cmd-K: all 9 shortcuts from Source.md Section 13.6 work [S3]
10. SSE drives real-time updates on Agents page [C4]
11. Visual identity: all Source.md Section 13.1 tokens in Tailwind config [C5]
12. Responsiveness: works at 1024px minimum [M3]
13. WCAG AA: all color combinations, keyboard nav, prefers-reduced-motion

### Total Test Count Target
- Unit: ~45 new tests
- Integration: ~30 new tests (stop_loss_full)
- E2E: ~80 new tests (7 page files, split across 3 tasks)

---

## Dependency Graph

```
Plan 01 (Wave 1: schemas, tables, cadence) — 2 tasks
    |
    v
Plan 02 (Wave 2: engines, poller, catastrophe-net) — 3 tasks, sequential
    |
    v
Plan 03 (Wave 3: SSE endpoint, TOTP API, data layer, route wiring) — 4 tasks
    |
    v
Plan 04 (Wave 4: TOTP modal, Tailwind config, Sankey) — 3 tasks
    |
    v
Plan 05 (Wave 5: P1-P4 queue, Cmd-K, debug copy, settings mutation) — 3 tasks
    |
    v
Plan 06 (Wave 6: page-by-page exit tests + integration) — 4 tasks, sequential
```

All plans are sequential because each wave builds on the previous wave's artifacts. Plans within the same wave could theoretically parallelize, but this phase has one plan per wave due to shared file dependencies.

---

## Existing Code Leveraged

These files already exist and will be EXTENDED, not rewritten:
- `pmacs/engines/stop_loss_monitor.py` — add `check_trailing_breach()`
- `pmacs/engines/trailing_stop.py` — already complete, no changes needed
- `pmacs/engines/opportunity_cost.py` — add `evaluate_holding()` and `run_opportunity_cost_scan()`
- `pmacs/engines/thesis_reeval.py` — update `check_weekly_reeval` signature
- `pmacs/engines/state_machine.py` — verify transitions, no changes expected
- `pmacs/execution/catastrophe_net.py` — add `cancel_catastrophe_net()`
- `pmacs/nervous/sse_publisher.py` — already complete, used by SSE endpoint
- `pmacs/nervous/api.py` — add `/events` SSE endpoint
- `pmacs/nervous/checkpoint.py` — already complete, verify op_idempotency
- `pmacs/stop_loss_daemon.py` — add trailing stop check call
- `pmacs/web/app.py` — already complete, no changes expected
- `pmacs/web/sse_client.py` — already complete, no changes expected
- `pmacs/web/routes/*.py` — update to use data layer
- `pmacs/web/templates/*.html` — update with Tailwind tokens + new components
- `pmacs/web/components/totp_modal.html` — rewrite as reusable
- `pmacs/web/static/app.js` — extend Cmd-K + add TOTP handler + copy function
- `pmacs/web/static/style.css` — update with Tailwind tokens
- `pmacs/schemas/stop_loss.py` — add StopEventStatus, status field
- `pmacs/schemas/contracts.py` — verify transitions, add last_reeval_at
