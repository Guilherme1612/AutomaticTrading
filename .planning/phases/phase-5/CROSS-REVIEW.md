# Phase 5 Cross-Review: Monitoring + Dashboard (PMACS Phases 9-10)

**Reviewed:** 2026-05-26T12:38:00Z
**Reviewer:** Claude (gsd-code-reviewer)
**Scope:** PLAN.md, SUMMARY.md, spec/Phases.md (Phase 9-10), spec/Source.md (S13-S20), spec/Architecture.md (S4.4, S11, S12)

---

## Plan-Spec Alignment (score: 4/5)

The plan is thorough and addresses all spec sections. It traces every review finding (C1-C5, S1-S7, M1-M5) back to specific tasks with file paths and code-level detail. The wave structure is logical: schemas -> engines -> backend -> frontend -> integration -> tests.

**Strengths:**
- Six-wave dependency graph is correct: schemas before engines, backend before frontend, implementation before tests.
- Every file listed in SUMMARY.md matches the plan's deliverables.
- Review Coverage Matrix maps all 17 review items to specific plan tasks.
- Threat model in PLAN.md is well-structured with STRIDE analysis.

**Gaps:**
- **SSE architecture split unclear.** The spec (Architecture.md S4.4) says `pmacs-nervous` exposes `GET /events` SSE. The dashboard subscribes via `sse_client.py`. But `pmacs/web/app.py` also has its own `/events` endpoint (lines 160-175) that sends only pings with a comment saying "Real event streaming will be proxied from pmacs-nervous once that service is integrated." This is an architectural inconsistency -- the dashboard app has a stub SSE endpoint instead of proxying from nervous. The plan's Task 03.1 addresses this but the current implementation is incomplete.
- **Missing `data.gateway` or price-fetching integration in `opportunity_cost.py`.** `evaluate_holding()` sets `pnl_pct = 0.0` hardcoded (line 121) with a comment "Will be overridden by caller with real data." This is not wired -- it means opportunity cost evaluations always see zero PnL, which can produce incorrect HOLD decisions for underwater positions.
- **Plan does not mention `pmacs/nervous/sse_publisher.py`** -- the SSEPublisher class is used by `nervous/api.py` but never called from the dashboard's SSE forwarding. The plan assumes nervous SSE "just works" but does not wire the dashboard to consume it end-to-end.
- **`stop_loss_daemon.py` uses `_HoldingProxy` inner class** (lines 244-251) rather than importing the real `Holding` model. This is fragile and makes it impossible to use model validators or field constraints.

Score justification: Strong overall, but the SSE proxy gap and PnL wiring gap are meaningful spec deviations that cost one point.

---

## Exit Test Coverage (score: 4/5)

The plan defines 9 exit tests for Phase 9 and 13 exit tests for Phase 10. The implementation delivers:

**Phase 9 exit tests (spec Phases.md lines 366-371):**

| Exit Test | Spec Requirement | Implementation Status |
|---|---|---|
| Stop-loss execution path | `pytest tests/integration/test_stop_loss.py` | 28 tests in file |
| Trailing math correct | `pytest tests/unit/test_trailing_stop.py` | 14 tests |
| Gap-down: MARKET_ON_OPEN | Architecture.md S11.3 | Covered in test_stop_loss_full.py |
| Weekly re-eval: validated -> ACTIVE, broken -> EXIT_THESIS_INVALIDATED | Phases.md line 369 | Covered in test_stop_loss.py |
| 90-day thesis aging: THESIS_AGING_REVIEW triggered | Phases.md line 370 | Covered in test_stop_loss.py |
| Catastrophe-net cancel-before-exit | Plan C1 | Covered in test_stop_loss_full.py |
| Trailing stop: EXIT_TRAILING_STOP | Plan S2 | Covered in test_stop_poller.py + test_stop_loss_full.py |
| Opportunity cost: per-holding iteration | Plan C2 | Covered in test_stop_loss.py |
| Nervous 10s polling | Plan S1 | Covered in test_stop_poller.py |

**Phase 10 exit tests (spec Phases.md lines 396-407):**

| Exit Test | Status |
|---|---|
| All 7 pages render | 51 tests in test_dashboard_renders.py |
| Agents page: persona progress (SSE) | test_dashboard_renders.py checks for persona cards |
| Pipeline kanban columns | test_dashboard_renders.py checks for verdict columns |
| Universe tickers with flags | test_dashboard_renders.py checks for ticker heading, add button |
| Cortex heartbeats + audit chain | test_dashboard_renders.py checks for heartbeat, audit panels |
| Debug streams live events | test_dashboard_renders.py checks for filter bar, event rows |
| Settings + TOTP on gated actions | test_dashboard_renders.py checks for settings sections |
| Dashboard portfolio summary | 29 tests in test_dashboard_page.py |
| Operator reorder queue | Pipeline route API tests exist |
| Add ticker (TOTP-gated) | Universe route tests exist |

**Gaps:**
- **Sankey visualization (Source.md S15.5)** -- Plan Task 04.3 specifies D3 Sankey with Process/Network/Math views. The test_dashboard_renders.py only checks for `sankey-placeholder` (line 81). No actual Sankey implementation or test exists -- just a placeholder div.
- **Cmd-K command palette completeness (Source.md S13.6)** -- Plan specifies 9 shortcuts. The test suite checks for the palette container but does not verify all 9 keybindings individually. The plan's Task 05.2 is ambitious (cycle compare, audit search, quick actions) and the E2E coverage for it is thin.
- **`prefers-reduced-motion` (Source.md S13.7)** -- Plan mentions WCAG AA compliance and motion preference. No dedicated test for this was found in the test files.
- **Drag-and-drop queue reorder (Source.md S16.5)** -- Backend API exists (`/pipeline/queue/reorder`) but no E2E test for the drag-and-drop interaction.

Score justification: Comprehensive test coverage for core engines (175 tests across unit/integration). Dashboard E2E tests are strong for static rendering. Interactive features (Sankey, Cmd-K, drag-and-drop) have weak or missing test coverage.

---

## Implementation Quality (score: 4/5)

**Engine quality -- excellent:**
- `stop_loss_monitor.py`: Clean separation of fixed vs trailing breach. `StopCheckResult` dataclass with `StopBreachType` literal. `check_trailing_breach` uses `getattr` with fallback for armed flag. Correct gap-down -> MARKET_ON_OPEN logic.
- `trailing_stop.py`: Pure functions, no side effects. `compute_profit_r` handles zero-risk edge case. `maybe_ratchet_trailing` correctly never lowers. Matches Architecture.md S11.4 exactly.
- `thesis_reeval.py`: Clean `last_reeval_at` fallback logic. 90-day aging is calendar days from entry (no reset). Both exit paths (VALIDATED, EXIT_THESIS_INVALIDATED) covered.
- `catastrophe_net.py`: `cancel_catastrophe_net` engages kill switch on failure (Architecture.md S11.5). `execute_exit` enforces cycle_id requirement. Audit trail written on every cancel.
- `stop_poller.py`: Correct PENDING -> SUBMITTED -> FILLED lifecycle. RTH-only polling. State machine transitions produce correct states (STOPPED_OUT vs EXIT_TRAILING_STOP).

**Dashboard quality -- good:**
- `app.py`: CSRF double-submit cookie, security headers (CSP, X-Frame-Options), autoescape enabled.
- `data.py`: Comprehensive shared data layer with typed functions. Priority bands with scoring formula matching Architecture.md S12. P1 auto-promotion for active holdings. Error context helpers for Source.md S13.4.
- `cortex.py` route: Kill switch engage (no TOTP) vs disengage (TOTP) -- matches Source.md S18.6 exactly. TOTP verification endpoint with audit logging.
- `pipeline.py` route: P1-P4 reorder, pin/unpin, promote-all, scheme save/load. Cycle start and compare endpoints.

**Issues:**
- **`opportunity_cost.py` PnL hardcoded to 0.0** (line 121). The `evaluate_holding` function never computes actual PnL. The `run_opportunity_cost_scan` attempts to override via `pnl_pcts` dict but the initial `evaluate_holding` call always sees 0.0 PnL. For underwater positions with stable conviction, this means the "underwater > 5% + better alternatives" exit trigger (lines 68-80) can never fire from `evaluate_holding`.
- **`stop_loss_daemon.py` inner `_HoldingProxy` class** (lines 244-251). Creates an anonymous class per holding per cycle. Should use `types.SimpleNamespace` or import `Holding` from schemas. The inner class approach is fragile and prevents type checking.
- **`stop_poller.py` uses `asyncio.run()`** (line 100) inside a synchronous method called from a synchronous loop. This will fail if there is already a running event loop. The `process_trigger` method should be refactored to accept pre-awaited results or the poll loop should be async.
- **`sse_client.py` does not use `Last-Event-ID`** for reconnection. The spec (Architecture.md S4.4) requires reconnecting with `Last-Event-ID` for resume. The client reconnects after 5s but does not send the last event ID.
- **`app.py` SSE endpoint is a stub** (lines 160-175). It sends pings every 30s. The real SSE events come from `nervous/api.py`, but the dashboard's own `/events` endpoint does not proxy from nervous. The `sse_client.py` connects to nervous, but the dashboard's SSE endpoint is what the browser connects to.

Score justification: Core engine implementations are high-quality and spec-compliant. Dashboard backend is well-structured with proper data layer. The PnL wiring gap, sync/async mismatch in stop_poller, and SSE architecture split are quality issues worth fixing but not blockers.

---

## Gaps & Risks

### Gaps

1. **SSE proxy not implemented.** The dashboard's `/events` endpoint sends pings only. The `sse_client.py` subscribes to nervous but is never wired to forward events to the dashboard's SSE clients. This means real-time updates from cycles, agents, and trades do not reach the browser. The spec (Architecture.md S4.4, Source.md S13.2) requires SSE-driven updates, not polling.

2. **D3 Sankey is a placeholder.** Plan Task 04.3 specifies a three-view Sankey (Process/Network/Math) with D3 animations. Only a placeholder div exists. This is a significant visual feature for the Agents page (Source.md S15.5 -- "the most important page in PMACS").

3. **Opportunity cost PnL not computed.** `evaluate_holding()` hardcodes `pnl_pct = 0.0`. This means the opportunity cost engine cannot detect underwater positions, making the "underwater > 5% with better alternatives" exit path unreachable.

4. **Cmd-K palette is partial.** Plan specifies 9 shortcuts with cycle compare, audit search, quick actions. The implementation has a basic palette but the advanced features (audit search, ticker jump with filter) are not wired.

5. **Saved filters on Debug page (Source.md S19.1).** The spec mentions "Saved filters (right rail)" for the Debug page. Not found in the implementation.

### Risks

1. **`asyncio.run()` in synchronous context.** `stop_poller.py` line 100 calls `asyncio.run(execute_exit(...))` from `process_trigger()`. If the poller is ever called from within an async context (e.g., during testing with `pytest-asyncio` or integration with the nervous orchestrator), this will raise `RuntimeError: asyncio.run() cannot be called from a running event loop`.

2. **`_HoldingProxy` class fragility.** The stop-loss daemon creates an anonymous inner class for each holding. If the `Holding` schema adds required fields (e.g., `trailing_stop_armed` as a Pydantic field), the proxy will silently fail to provide them, potentially causing AttributeError at runtime during stop-loss checks.

3. **No price data during daemon checks.** The `_fetch_current_price` function tries Finnhub then Alpaca then SQLite last-known. In a local-only, pf-blocked inference environment (Non-Negotiable 4), Finnhub and Alpaca API calls will fail. The system relies entirely on SQLite last-known price, which could be stale. This is a real operational risk -- stop-loss checks might miss breaches.

4. **TOTP secret as global variable in nervous/api.py** (line 81). `_totp_secret: str = ""` is a module-level global. If nervous restarts, the TOTP secret is lost until `set_totp_secret()` is called again. The cortex route (cortex.py line 187) reads it from keychain each time, which is more resilient.

---

## Recommendations

1. **Wire the SSE proxy.** The dashboard's `/events` endpoint should either (a) proxy from nervous `/events` via the `SSEClient` in `sse_client.py`, or (b) the browser should connect directly to nervous `:8000/events` (CORS allowing). Option (b) is simpler but requires nervous to serve CORS headers for `:8001`. Option (a) is more robust but requires the SSEClient to forward to dashboard SSE clients. This is the highest-priority gap because real-time updates are central to the spec.

2. **Fix PnL computation in opportunity cost.** `evaluate_holding()` should accept `current_price` as a parameter and compute PnL from `entry_price_usd` and `current_price`. Remove the hardcoded `pnl_pct = 0.0`.

3. **Replace `_HoldingProxy` with `types.SimpleNamespace`** or a proper TypedDict/dataclass. This is a minor change that improves type safety and prevents silent field-missing errors.

4. **Fix `asyncio.run()` in stop_poller.** Either make `process_trigger` async and call it from an async event loop, or refactor `execute_exit` to provide a synchronous wrapper. The current approach will fail in any async context.

5. **Add `Last-Event-ID` to SSE client reconnection.** Track the last event ID received and send it as a header on reconnect. This matches Architecture.md S4.4 and prevents event loss during reconnection.

6. **Implement or defer Sankey.** If the Sankey is too complex for this phase, document it as a known gap and create a follow-up task. The current placeholder should at minimum show the Process view (horizontal timeline) as a simpler HTML/CSS implementation that does not require D3.

7. **Stale price handling in stop-loss daemon.** Log a WARN with staleness duration when using last-known price from SQLite. Consider adding a configurable staleness threshold beyond which the stop check is skipped with an explicit audit event.

---

## Overall Score: 4/5

**Summary:** Phase 5 delivers a solid implementation of PMACS Phases 9-10. The core stop-loss engine, trailing stop, thesis re-evaluation, catastrophe-net cancellation, and opportunity cost engine are all implemented and well-tested (175+ tests). The dashboard has all 7 pages rendering with real data, TOTP-gated write actions, P1-P4 priority queue, CSRF protection, and security headers.

The one-point deduction comes from three areas: (1) the SSE proxy is a stub, meaning real-time updates do not actually reach the browser -- this is the spec's central UI communication mechanism, (2) opportunity cost PnL is hardcoded to zero, making one of its two exit paths unreachable, and (3) several interactive features (Sankey, Cmd-K advanced functions, drag-and-drop) are placeholders without test coverage.

These are fixable in a follow-up pass without architectural changes. The foundation is strong and spec-compliant for the deterministic engine layer. The UI layer needs the SSE wiring to be production-ready.
