# Phase 16 PLAN: Token-Cost Accounting & Efficiency

**PRD:** `docs/prd/Phase_TokenCost.md`
**Goal:** Complete cost-accounting layer for OpenRouter LLM calls with budget enforcement, reconciliation, and dashboard visibility.

---

## Wave 1: Schemas + Core Billing Modules

**Goal:** Pydantic schemas and pure calculation functions that form the billing layer.

### T1.1 Create `pmacs/schemas/billing.py`

Pydantic v2 models per PRD §4, §7, §9:

- `PricingRecord` — model_id, input_price_per_token, output_price_per_token, cached_input_price_per_token, per_request_fee, fetched_at, source
- `EstimatedCost` — persona, model_id, estimated_input_tokens, estimated_output_tokens, estimated_cost_usd, created_at
- `BodyCost` — call_id, cycle_id, persona, model_id, generation_id, prompt_tokens, completion_tokens, body_cost_usd, latency_ms
- `ActualCost` — call_id, actual_cost_usd, reconciled_at, delta_from_body
- `BudgetState` — period ('today'|'this_month'), period_start, total_cost_usd, cap_usd, updated_at
- `BudgetCheckResult` — allowed (bool), reason, cap_type, current_total, estimated_new_total, cap_usd
- `PERSONA_EXPECTED_OUTPUT_TOKENS` dict constant from PRD §6.1

### T1.2 Create `pmacs/billing/cost_calculator.py`

Pure function implementing PRD §4 core equation:

- `compute_cost(prompt_tokens, completion_tokens, input_price_per_token, output_price_per_token) -> float`
- `compute_body_cost(usage: dict, pricing: PricingRecord) -> float`
- Table-driven unit tests with known inputs from PRD §4.1

### T1.3 Create `pmacs/billing/token_estimator.py`

Character-based pre-flight estimation per PRD §6:

- `estimate_tokens(text: str) -> int` — `ceil(len(text) * 0.26)`
- `estimate_call_cost(prompt_text: str, persona: str, pricing: PricingRecord) -> EstimatedCost`
- Unit test: ±15% accuracy validation against sample data

### T1.4 Create `pmacs/billing/__init__.py`

Empty init, re-export key symbols: `compute_cost`, `estimate_tokens`, `estimate_call_cost`.

### T1.5 Unit tests for Wave 1

- `tests/unit/test_cost_calculator.py` — table-driven from PRD §4.1
- `tests/unit/test_token_estimator.py` — character heuristic accuracy, persona output lookup

---

## Wave 2: Pricing Fetch + Storage Schema

**Goal:** OpenRouter pricing fetch, SQLite budget tables, DuckDB api_usage table.

### T2.1 Create `pmacs/billing/pricing.py`

Pricing table CRUD + live fetch per PRD §3, §9.1:

- `fetch_pricing_from_openrouter(model_id: str) -> PricingRecord` — GET `https://openrouter.ai/api/v1/models`, parse pricing for given model
- `get_pricing(sqlite_conn, model_id: str) -> PricingRecord` — cache lookup, fetch on miss, refresh if stale (>24h)
- `refresh_pricing_table(sqlite_conn)` — startup + scheduled refresh
- Handle: model not found, API failure (return cached with warning), network timeout

### T2.2 Extend SQLite schema — `pmacs/storage/sqlite.py`

Add to `SCHEMA_SQL`:

```sql
CREATE TABLE IF NOT EXISTS pricing_table (
    model_id TEXT PRIMARY KEY,
    input_price_per_token REAL NOT NULL,
    output_price_per_token REAL NOT NULL,
    cached_input_price_per_token REAL,
    per_request_fee REAL NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'openrouter'
);

CREATE TABLE IF NOT EXISTS budget_state (
    period TEXT PRIMARY KEY,
    period_start TEXT NOT NULL,
    total_cost_usd REAL NOT NULL DEFAULT 0,
    cap_usd REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS budget_history (
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    period_type TEXT NOT NULL,
    total_cost_usd REAL NOT NULL DEFAULT 0,
    cap_usd REAL NOT NULL,
    breached INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (period_type, period_start)
);
```

Add seed rows in migration: `budget_state` rows for 'today' ($2.00 cap) and 'this_month' ($30.00 cap).

### T2.3 Extend DuckDB schema — `pmacs/storage/duckdb.py`

Add to `init_tables()`:

```sql
CREATE TABLE IF NOT EXISTS api_usage (
    call_id VARCHAR PRIMARY KEY,
    cycle_id VARCHAR NOT NULL,
    persona VARCHAR NOT NULL,
    model_id VARCHAR NOT NULL,
    generation_id VARCHAR,
    called_at TIMESTAMP NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    cached_tokens INTEGER DEFAULT 0,
    estimated_cost_usd DOUBLE NOT NULL,
    body_cost_usd DOUBLE NOT NULL,
    actual_cost_usd DOUBLE,
    latency_ms INTEGER NOT NULL,
    succeeded BOOLEAN NOT NULL,
    retry_count INTEGER DEFAULT 0,
    error_code VARCHAR
);
```

Add `insert_api_usage()` method following existing DuckDB pattern (stub-mode safe: check `_get_conn()` returns None, log stub write, return).
Add indexes on `cycle_id`, `called_at`, `persona`.

### T2.4 Create `pmacs/billing/usage_logger.py`

Writes to both stores. **Must handle DuckDB stub mode** (duckdb not installed) — follows `duckdb.py` pattern: check conn, log stub write on None, return gracefully.

- `log_usage(sqlite_conn, duckdb_conn, call_record: BodyCost, estimated: EstimatedCost)` — INSERT into api_usage via `duckdb.insert_api_usage()`, falls back to stub log if DuckDB unavailable. Budget state update (SQLite) always succeeds.
- `update_budget_state(sqlite_conn, cost_usd: float)` — atomic UPDATE of today + this_month totals in explicit SQLite transaction
- `update_actual_cost(sqlite_conn, duckdb_conn, call_id: str, actual_cost: float)` — UPDATE api_usage SET actual_cost_usd, adjust budget_state by delta

### T2.5 Tests for Wave 2

- `tests/unit/test_pricing.py` — cache hit/miss, stale refresh, API failure fallback
- Integration: SQLite migration creates tables + seed rows

---

## Wave 3: Budget Enforcement + Period Rollover + Drift Detection

**Goal:** Three-tier budget checks, runaway detection, estimate drift monitoring, UTC period rollover.

### T3.1 Create `pmacs/billing/budget_enforcer.py`

Per PRD §8:

- `check_per_cycle_soft_cap(sqlite_conn, estimated_total: float, cap: float) -> BudgetCheckResult`
- `check_daily_hard_cap(sqlite_conn, estimated_call_cost: float, cap: float) -> BudgetCheckResult`
- `check_monthly_hard_cap(sqlite_conn, estimated_call_cost: float, cap: float) -> BudgetCheckResult`
- `check_runaway(sqlite_conn, cycle_id: str, actual_cumulative: float, estimated_cumulative: float) -> BudgetCheckResult` — triggers at 1.5x
- `enforce_budgets(sqlite_conn, estimated_call_cost: float, cycle_id: str | None) -> BudgetCheckResult` — runs all three checks in order

Budget cap defaults from config. Configurable in Settings (Wave 6).

### T3.2 Create `pmacs/billing/period_roller.py`

Per PRD §8.5:

- `roll_daily(sqlite_conn)` — archive today → budget_history, reset today.total_cost_usd = 0
- `roll_monthly(sqlite_conn)` — archive this_month → budget_history, reset this_month.total
- `check_and_roll(sqlite_conn)` — called by orchestrator at cycle end, checks UTC boundaries

### T3.3 Create `pmacs/billing/reconciler.py`

Per PRD §11:

- `reconcile_call(call_id: str, generation_id: str, sqlite_conn, duckdb_conn)` — GET `/api/v1/generation?id={id}`, update actual_cost_usd, adjust budget_state by delta
- `reconcile_cycle(cycle_id: str, duckdb_conn, sqlite_conn)` — force-reconcile any NULL actual_cost_usd for cycle
- `reconcile_daily(duckdb_conn, sqlite_conn)` — daily sweep for unreconciled records
- Retry logic: 3 attempts, exponential backoff (5s, 30s, 5min)
- Drift thresholds from PRD §11.4

### T3.4 Create `pmacs/billing/drift_monitor.py` — Estimate Drift Detection

Per PRD §6.3:

- `check_estimate_drift(persona: str, duckdb_conn)` — query last 100 calls for persona, compute p90 of completion_tokens, compare against `PERSONA_EXPECTED_OUTPUT_TOKENS[persona]`
- If `observed_p90 > configured * 1.20`: log `ESTIMATE_DRIFT` debug event with persona, configured, observed, delta_pct
- Do NOT auto-update — flag as Mutation Engine candidate
- Called from usage_logger after each call (lightweight: only triggers check every 100 calls per persona using a counter in SQLite or in-memory)

### T3.5 Retry economics tracking — in `pmacs/billing/usage_logger.py`

Per PRD §12:

- `compute_persona_retry_rate(persona: str, duckdb_conn) -> float` — `SUM(CASE WHEN retry_count > 0 THEN 1 ELSE 0 END) / COUNT(*)` over last N calls
- `check_quality_regression(persona: str, duckdb_conn) -> None` — if retry_rate > 0.10 AND persona's Brier delta > 0.05 vs no-retry baseline, emit `PERSONA_QUALITY_REGRESSION` alert
- Called after each call (lightweight: only triggers check every 100 calls per persona)
- `PERSONA_QUALITY_REGRESSION` surfaced as SSE event and logged to audit

### T3.6 Tests for Wave 3

- `tests/unit/test_budget_enforcer.py` — soft cap triggers, hard cap rejects, runaway detection
- `tests/unit/test_period_roller.py` — daily rollover, monthly rollover, archive correctness
- `tests/unit/test_drift_monitor.py` — drift detection triggers at 20%, no false positives within tolerance
- `tests/integration/test_reconciliation.py` — body=actual (no delta), body≠actual (budget correction), 404 retry, persistent failure

---

## Wave 4: Agent Integration — Capture Tokens in LLM Calls

**Goal:** Modify `pmacs/agents/base.py` to capture token usage and route through the billing layer.

### T4.1 Introduce `LlmCallResult` dataclass and change `_call_llm()` dispatcher

**Current**: All `_call_llm_*` methods return bare `str`. `_call_llm()` dispatcher (L370-396) returns their result directly. `run()` at L117 assigns to `raw_output` and treats it as `str`.

**Migration strategy — side-channel approach to minimize blast radius:**

1. Add `self._last_call_usage: dict | None = None` instance attribute on `PersonaRunner`
2. Each `_call_llm_*` method stores usage data in `self._last_call_usage` instead of changing return type
3. `_call_llm()` dispatcher unchanged — still returns `str`
4. `run()` reads `self._last_call_usage` after the call completes

This avoids changing the return type of 4 methods and all their callers. The usage data rides on an instance attribute, not the return value.

### T4.2 Modify `_call_llm_openai()` (L514-560)

After `data = response.json()` (L555):
1. Extract `usage = data.get("usage", {})` → `{"prompt_tokens": N, "completion_tokens": N}`
2. Extract `generation_id = data.get("id", "")`
3. Store in `self._last_call_usage = {"prompt_tokens": ..., "completion_tokens": ..., "generation_id": ...}`
4. Return value stays `str` (unchanged)

### T4.3 Modify `_call_llm_anthropic()` (L466-512)

After `data = response.json()` (L507):
1. Extract `usage = data.get("usage", {})` → `{"input_tokens": N, "output_tokens": N}`
2. Store in `self._last_call_usage = {"prompt_tokens": input_tokens, "completion_tokens": output_tokens, "generation_id": data.get("id", "")}`
3. Return value stays `str` (unchanged)

### T4.4 Modify `_call_llm_local()` (L433-464)

After `data = response.json()` (L463):
1. Extract from llama-server response: `data.get("timings", {}).get("prompt_n", 0)` and `data.get("timings", {}).get("predicted_n", 0)` — these are the actual field names
2. Store in `self._last_call_usage = {"prompt_tokens": prompt_n, "completion_tokens": predicted_n, "generation_id": None}` — no generation_id for local calls (no reconciliation)
3. Return value stays `str` (unchanged)

### T4.5 Wire billing pipeline into `run()` method

After `raw_output = self._call_llm(...)` (L117):

1. **Pre-flight** (before L117): call `estimate_call_cost()`, run `enforce_budgets()`. If rejected: log `cost_cap_breached`, skip to simulation mode or abort
2. **Post-call** (after L117): read `self._last_call_usage`. If not None:
   - Compute body-cost via `compute_body_cost(usage, pricing)`
   - Call `usage_logger.log_usage()` to persist to DuckDB + update budget_state
   - Call `drift_monitor.check_estimate_drift()` (lightweight, counter-gated)
   - Call `usage_logger.check_quality_regression()` (counter-gated)
   - Publish SSE `cost.call_completed`
   - Spawn async reconciliation (if generation_id present — skip for local calls)
3. **Mid-cycle runaway** (after each call): track cumulative actual vs estimated. If actual > 1.5x estimated: publish SSE `cost.runaway_detected`, log audit `COST_RUNAWAY_DETECTED`

### T4.6 Extend `_audit_llm_call()` (L562-589)

Add to audit payload (conditionally, only if usage available):
- `prompt_tokens`, `completion_tokens`, `generation_id`
- `estimated_cost_usd`, `body_cost_usd`

### T4.7 Integration test for full call lifecycle

- `tests/integration/test_full_call_lifecycle.py` — pre-flight estimate → in-flight body-cost → usage logged → SSE events fire → post-flight reconciliation populates actual_cost. All three phases verified.

---

## Wave 5: Kill Switch Triggers + SSE Events + Debug Page

**Goal:** Wire budget breaches to kill switch. Add cost SSE channel. Verify debug page shows cost events.

### T5.1 Register new kill switch triggers

In `pmacs/cortex/kill_switch.py`:
- Add `CYCLE_BLOCKED_BUDGET_DAILY` and `CYCLE_BLOCKED_BUDGET_MONTHLY` to `TRIGGER_IDS` tuple (append)
- These are evaluated from `budget_enforcer.py`, not from `check_all_triggers()` — but registration ensures they appear in any trigger enumeration

### T5.2 Wire kill switch engagement in budget_enforcer

In `budget_enforcer.py`:
- Daily hard cap breach → call `engage_kill_switch("CYCLE_BLOCKED_BUDGET_DAILY", f"Daily spend ${total:.4f} exceeds cap ${cap:.2f}")`
- Monthly hard cap breach → call `engage_kill_switch("CYCLE_BLOCKED_BUDGET_MONTHLY", ...)`

### T5.3 Add cost SSE events

In the billing layer (usage_logger, reconciler, budget_enforcer), publish via `SSEPublisher`:
- `cost.call_completed` — after each LLM call's body-cost computed
- `cost.cycle_total` — after cycle closes (sum all call costs)
- `cost.budget_update` — when budget_state totals change
- `cost.cap_breached` — when any cap exceeded
- `cost.runaway_detected` — actual > 1.5 × estimated mid-cycle
- `cost.reconciled` — after reconciliation (only if delta significant, > $0.001)

### T5.4 Verify debug page shows cost events

PRD §13.5: the debug page shows all `cost.*` events when filtered. Verify existing debug page subscribes to all SSE streams (it should — the pattern is `stream: "*"` or per-channel subscription). If not, add `cost` channel subscription to the debug page's SSE listener.

### T5.5 Tests for Wave 5

- Kill switch engages on simulated $0.01 budget breach
- SSE events fire in correct order during a test cycle
- Debug page renders `cost.*` events (verified in T7.3)

---

## Wave 6: UI — Cost Widget + Settings Panel

**Goal:** Dashboard cost visibility and budget controls.

### T6.1 Dashboard cost widget — `pmacs/web/templates/cost_widget.html`

Top-bar widget per PRD §13.1:
- Three-line display: today, this month, last cycle
- Progress bars
- HTMX/SSE auto-update via `cost.budget_update` and `cost.cycle_total` streams
- Click → opens detail panel

### T6.2 Settings → Cost & Budget panel

In `pmacs/web/routes/settings.py` (extend existing route file):
- GET `/api/settings/cost` — current period totals, caps, pricing table, reconciliation status
- POST `/api/settings/cost/caps` — update budget caps (TOTP-gated for loosening caps)
- GET `/api/settings/cost/history` — daily cost history (last 30 days) for chart
- GET `/api/settings/cost/personas` — per-persona cost breakdown (calls, avg tokens, avg cost, total cost)

Add `pmacs/web/templates/cost_settings.html` partial (included in settings.html):
- Current period totals with progress bars
- Budget cap inputs (TOTP-gated)
- Historical chart (daily cost over last 30 days) — use HTMX + inline SVG or existing chart library
- Per-persona cost table
- Pricing table view (model, prices, last refreshed)
- Reconciliation status (% reconciled)

### T6.3 Agents page integration

In existing agents template, add per-persona cost-per-cycle running average:
- `Avg cost/cycle: $X.XXXX    Avg latency: X.Xs`

### T6.4 Per-cycle cost toast

After cycle close, toast notification per PRD §13.2:
- "Cycle complete. Cost: $X.XXX (across N calls). Most expensive: Persona ($X.XX)."
- Auto-dismiss after 10s, first 14 days post-install, then opt-in

### T6.5 Tests for Wave 6

- Cost widget renders with live SSE updates
- Settings cap change requires TOTP for loosening
- Per-persona costs display correctly

---

## Wave 7: Exit Validation

**Goal:** All exit tests from PRD §17 pass.

### T7.1 Run full unit test suite

```
pytest tests/unit/test_token_estimator.py
pytest tests/unit/test_cost_calculator.py
pytest tests/unit/test_budget_enforcer.py
pytest tests/unit/test_period_roller.py
pytest tests/unit/test_drift_monitor.py
```

### T7.2 Run integration tests

```
pytest tests/integration/test_pricing_fetch.py
pytest tests/integration/test_full_call_lifecycle.py
pytest tests/integration/test_reconciliation.py
```

### T7.3 Operational verification

- Cost widget renders on dashboard with live SSE updates
- Per-persona cost displays on Agents page
- Settings → Cost & Budget panel renders with current state
- Debug page shows `cost.*` events when filtered
- Audit log contains `cost_*` events from recent cycles
- Kill switch engages on `CYCLE_BLOCKED_BUDGET_DAILY` (test with $0.01 budget)

### T7.4 Smoke run — Cost accounting consistency

Run 5+ consecutive cycles. Verify:
- Sum of `api_usage.body_cost_usd` matches cycle close totals within $0.01
- All calls have `actual_cost_usd` populated after reconciliation (or body_cost if reconciliation pending)
- `budget_state` totals are consistent
- Zero `RECONCILIATION_LARGE_DELTA` warnings

### T7.5 Quality baseline — PRD §17.1

Run 30 SHADOW-mode cycles on OpenRouter backend. Measure and verify:

| Metric | Threshold | Pass/Fail |
|---|---|---|
| Average per-cycle cost | < $0.20 | (verify against actual, not PRD projection) |
| Per-persona retry rate | < 10% each | query `api_usage` WHERE retry_count > 0 |
| Per-persona latency (analysis) | < 5s | exclude Crucible |
| Per-persona latency (Crucible) | < 15s | Crucible only |
| Per-persona Brier score | Record baseline | no threshold yet — establish for future comparison |

If ANY persona's retry rate exceeds 10% after 30 cycles: surface `PERSONA_QUALITY_REGRESSION` alert, pause phase completion. System cannot ship with chronically failing persona.

---

## File Dependency Graph

```
schemas/billing.py  ←  cost_calculator.py, token_estimator.py, pricing.py
                        budget_enforcer.py, usage_logger.py, reconciler.py
                        drift_monitor.py

pricing.py          ←  storage/sqlite.py (pricing_table)
storage/sqlite.py   ←  pricing_table, budget_state, budget_history DDL
storage/duckdb.py   ←  api_usage DDL + insert_api_usage() method

cost_calculator.py  ←  schemas/billing.py (pure)
token_estimator.py  ←  schemas/billing.py, cost_calculator.py

budget_enforcer.py  ←  schemas/billing.py, storage/sqlite.py, kill_switch.py
period_roller.py    ←  schemas/billing.py, storage/sqlite.py
reconciler.py       ←  schemas/billing.py, storage/duckdb.py, storage/sqlite.py, pricing.py
usage_logger.py     ←  schemas/billing.py, storage/duckdb.py, storage/sqlite.py, sse_publisher.py
drift_monitor.py    ←  schemas/billing.py, storage/duckdb.py (query api_usage)

agents/base.py      ←  billing/* (instance attribute side-channel, return types unchanged)
kill_switch.py      ←  TRIGGER_IDS extended with 2 new triggers
settings route      ←  billing/* for API endpoints
templates/          ←  settings route, SSE events
```

## Risks

| Risk | Mitigation |
|---|---|
| V4 Flash pricing changes | Dynamic fetch from OpenRouter; never hardcoded |
| Token estimate drift | ±15% is advisory; reconciled actual is authoritative. Drift monitor warns at 20% deviation. |
| `base.py` changes break existing agents | Side-channel approach (`self._last_call_usage` instance attribute) — no return type change, no caller changes needed |
| Local llama-server field names | Use `timings.prompt_n` / `timings.predicted_n` (not `tokens_evaluated` / `tokens_predicted`). No generation_id for local — skip reconciliation. |
| Kill switch trigger registration order | Append to existing tuple; no reordering |
| DuckDB unavailable (stub mode) | `usage_logger` follows existing DuckDB stub pattern: check conn, log stub, return. Budget state in SQLite always works. |
| PRD dollar amounts may be inaccurate | Pricing fetched dynamically. Budget caps are 10-27x expected cost — robust regardless of exact pricing. 30-cycle baseline (T7.5) validates projections empirically. |

## What's NOT in scope

- Multi-model adapter pattern
- v2 telemetry (PRD §14)
- Cost optimization recommendations
- Provider failover logic
- Output caching
