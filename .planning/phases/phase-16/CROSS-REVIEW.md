# Phase 16 Cross-Review: Token-Cost Accounting & Efficiency

**Reviewer:** Claude (gsd-code-reviewer)
**Date:** 2026-05-26T12:36:00Z
**Depth:** Standard (full file reads, cross-file analysis)
**Files Reviewed:** 13 source files, 1 test file, 3 planning files

---

## Plan-Spec Alignment (score: 4/5)

The plan aligns well with PMACS architecture patterns and the four-file spec. Module decomposition follows the established convention (schemas in `pmacs/schemas/`, engines/logic in `pmacs/billing/`, storage extensions in `pmacs/storage/`). Kill switch integration, audit logging, and TOTP-gating all respect the Five Non-Negotiables.

**Strengths:**
- Side-channel approach for `base.py` integration is minimally invasive -- no return type changes, no caller disruption.
- DuckDB stub-safe pattern consistently followed across all billing modules.
- Three-tier budget hierarchy (cycle soft, daily hard, monthly hard) matches CLAUDE.md kill switch philosophy.
- Pydantic v2 models use `ConfigDict`, `frozen=True`, correct import paths.

**Gap:** The PRD (`docs/prd/Phase_TokenCost.md`) referenced by the plan does not exist. The plan, CONTEXT, and REVIEWS all cite section numbers from it (PRD section 4, 6, 8, 11, 17), making external validation impossible. The plan is self-consistent but cannot be verified against its stated source of truth.

## Exit Test Coverage (score: 3/5)

The plan defines thorough exit tests in Wave 7 (T7.1-T7.5), including a 30-cycle smoke run with cost consistency verification. However, several gaps exist:

**Covered:**
- Unit tests exist for all core modules (cost_calculator, token_estimator, budget_enforcer, pricing, period_roller, drift_monitor).
- Integration test (`test_billing_lifecycle.py`) covers the full lifecycle: estimate, body cost, usage logging, budget enforcement, reconciliation, rollover, drift detection.
- Kill switch engagement on budget breach tested via budget_enforcer unit tests.

**Not covered or weak:**
- No E2E test running actual cycles with billing capture (T7.4 smoke run is manual).
- No test for the settings routes (`/api/settings/cost/*`).
- No test for the cost widget HTML rendering or SSE event subscription.
- The reconciler's background thread behavior (thread safety, connection lifecycle) has no test.
- The `_check_budget_monthly` kill switch trigger passes wrong period string (see Gaps), so the trigger test would silently pass with zero spend.

## Implementation Quality (score: 3/5)

The billing module suite is well-structured and follows PMACS conventions, but contains several runtime-breaking bugs that prevent correct operation.

**What works well:**
- `cost_calculator.py` -- clean, pure, well-documented, unit-testable.
- `token_estimator.py` -- correct heuristic, proper persona lookup with fallback.
- `pricing.py` -- proper cache-first/fetch-on-miss pattern, stale fallback with warning.
- `period_roller.py` -- correct UTC boundary logic, proper archive+reset transaction.
- `DuckDBAdapter` -- generic `execute()` method now exists (line 268), returns `list[dict]`. This resolves CRIT-2 from REVIEWS.md.
- Orchestrator integration (`_log_call_billing`) is wired correctly at line 2206, called after each successful persona run.
- Kill switch triggers registered (lines 63-64 of kill_switch.py).

**What is broken:**

### Bug 1 (Critical): Kill switch monthly check queries wrong period key

**File:** `pmacs/cortex/kill_switch.py:638`
**Issue:** `_get_period_total(conn, "month")` but the SQLite `budget_state` table uses `"this_month"` as the period key. The `_get_period_total` function queries `WHERE period = ?`, so passing `"month"` returns zero always. The monthly budget kill switch trigger will never fire.
**Fix:** Change line 638 to `_get_period_total(conn, "this_month")`.

### Bug 2 (Critical): Settings routes query nonexistent `actual_costs` table

**File:** `pmacs/web/routes/settings.py:715`
**Issue:** `_get_reconciliation_status()` queries `FROM actual_costs`. This table does not exist in either SQLite or DuckDB. The actual cost data lives in `api_usage.actual_cost_usd` in DuckDB. This will raise a SQL error at runtime.
**Fix:** Rewrite the query to use DuckDB `api_usage` table with appropriate filtering for reconciled rows.

### Bug 3 (Warning): Settings routes query `created_at` but `api_usage` uses `called_at`

**File:** `pmacs/web/routes/settings.py:623,629,634,640,670`
**Issue:** All cost queries in `_get_cost_state()` and `_get_persona_costs()` reference `created_at`, but the DuckDB `api_usage` schema (duckdb.py:222) uses `called_at`. Every cost-related settings API call will fail with a column-not-found error.
**Fix:** Replace all `created_at` references with `called_at` in the settings route queries.

### Bug 4 (Warning): Drift monitor reuses `COST_RUNAWAY_DETECTED` error code

**File:** `pmacs/billing/drift_monitor.py:49`
**Issue:** Estimate drift uses `error_code="COST_RUNAWAY_DETECTED"`, which is also used by the actual runaway detector in `budget_enforcer.py:152`. These are semantically different events (drift is p90 token deviation over time, runaway is instantaneous cost exceedance). Conflating them makes log analysis and alert routing unreliable.
**Fix:** Use a distinct error code such as `ESTIMATE_DRIFT` (which is already the event name on line 41).

### Bug 5 (Warning): Pricing stale cache comparison is timezone-naive

**File:** `pmacs/billing/pricing.py:96-97`
**Issue:** `datetime.fromisoformat(fetched_at)` returns a timezone-naive datetime when `fetched_at` lacks timezone info (it won't -- it's always stored with timezone). However, if the database contains a row written by a previous version or manual edit without timezone suffix, `fromisoformat` returns naive, and the subtraction `datetime.now(timezone.utc) - fetched_dt` raises `TypeError`.
**Fix:** Wrap in try/except (already done at line 108) -- this is a minor robustness concern, not a crash risk in practice.

### Bug 6 (Info): `EstimatedCost` schema has no `call_id` field

**File:** `pmacs/schemas/billing.py:52-61`
**Issue:** `token_estimator.py:62` sets `call_id=uuid.uuid4().hex[:16]` but the `EstimatedCost` model has no `call_id` field. This would raise a `ValidationError` at runtime. The field exists in `BodyCost` (line 72) and `ActualCost` (line 87) but not in `EstimatedCost`.
**Fix:** Either add `call_id: str = ""` to `EstimatedCost` or remove the `call_id` assignment from `estimate_call_cost()`.

## Gaps & Risks

1. **PRD missing** -- The plan cites `docs/prd/Phase_TokenCost.md` sections throughout, but this file does not exist. Without it, there is no authoritative source for dollar amounts, thresholds, or API contract details.

2. **No pre-flight budget check in orchestrator** -- The plan (T4.5) specifies a pre-flight `enforce_budgets()` check before each LLM call, but the orchestrator's `_log_call_billing()` only runs post-call (line 2206). There is no budget gate before `runner.run()` at line 2202. A runaway persona will burn through the entire budget before the post-call billing captures the spend.

3. **No SSE cost events** -- The plan specifies 6 SSE event types (`cost.call_completed`, `cost.cycle_total`, etc.) but none of the billing modules publish SSE events. The `SSEPublisher` is never imported or called from any billing module. The cost widget's HTMX auto-update will receive no data.

4. **Latency not captured** -- `BodyCost` has a `latency_ms` field, but the orchestrator's `_log_call_billing()` constructs `BodyCost` without setting it (defaults to 0). The latency data is available from the runner but not wired through.

5. **Thread safety in reconciler** -- `spawn_reconcile_call` opens new SQLite/DuckDB connections in a background thread, which is correct. However, `update_actual_cost()` in `usage_logger.py` adjusts `budget_state` from the background thread while the orchestrator may be updating it from the main thread. Both use `sqlite3.connect()` without WAL mode or explicit locking, risking `database is locked` errors under concurrent access.

6. **Cost widget template exists but SSE wiring unclear** -- `cost_widget.html` exists but the billing modules do not emit SSE events. Without the events, the widget cannot receive live updates.

7. **30-cycle smoke run (T7.5) requires OpenRouter** -- The quality baseline requires 30 SHADOW-mode cycles on OpenRouter. This is an external dependency that cannot be tested offline or in CI.

## Recommendations

1. **Fix Bug 1 immediately** -- `_check_budget_monthly` passing `"month"` instead of `"this_month"` means the monthly budget kill switch is completely non-functional. This is a safety-critical defect.

2. **Fix Bug 2 and Bug 3 immediately** -- All settings cost routes will fail at runtime due to wrong table and column names. The cost settings UI panel and the `/api/settings/cost` endpoint are dead code until fixed.

3. **Add pre-flight budget check** -- Wire `enforce_budgets()` into the orchestrator before `runner.run()` (around line 2201). Without it, the three-tier budget system only reports spend after the fact and cannot prevent overspend.

4. **Add SSE event publishing** -- The plan specifies 6 SSE event types. At minimum, `cost.call_completed` and `cost.budget_update` must be emitted from `usage_logger.py` for the dashboard cost widget to function.

5. **Assign distinct error codes** -- `ESTIMATE_DRIFT` should use its own error code, not `COST_RUNAWAY_DETECTED`. These are different failure modes requiring different operator responses.

6. **Fix `EstimatedCost` schema** -- Either add the `call_id` field to the model or stop passing it in `estimate_call_cost()`. Current code will raise `ValidationError`.

7. **Capture latency in billing** -- Time the `runner.run()` call and pass `latency_ms` to the `BodyCost` record. The data is already available; it just needs to be wired.

8. **Use WAL mode for SQLite in reconciler threads** -- Open the SQLite connection with `PRAGMA journal_mode=WAL` in the reconciler's background thread to prevent locking conflicts with the main orchestrator thread.

## Overall Score: 3/5

The architecture and module decomposition are solid. The core calculation, estimation, and pricing modules are clean and well-tested. The integration story -- wiring billing into the orchestrator, kill switch, settings routes, and SSE events -- is partially complete but contains three runtime-breaking bugs (wrong period key, wrong table name, wrong column name) and two missing critical features (pre-flight budget check, SSE event publishing). The billing layer captures data correctly after each call but cannot prevent overspend before calls fire, and the dashboard/UI cannot display live cost data because the SSE channel is not wired. Fixing the six identified bugs and completing the two missing integrations would bring this to a 4/5.
