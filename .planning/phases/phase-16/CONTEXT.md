# Phase 16: Token-Cost Accounting & Efficiency

## Source

PRD: `docs/prd/Phase_TokenCost.md`

## Goal

Complete cost-accounting layer that estimates cost before LLM calls, tracks actual cost after, reconciles against OpenRouter's authoritative cost, enforces three-tier budgets, persists cost data, surfaces real-time cost in dashboard, and engages kill switch on hard-cap breach.

## Key Decisions from PRD

1. **Single inference backend**: DeepSeek V4 Flash via OpenRouter. No adapter pattern. No fallback.
2. **Dynamic pricing**: Fetched live from OpenRouter `/api/v1/models`, cached, refreshed every 24h. Never hardcoded.
3. **Three-tier budget**: Per-cycle soft cap ($1.00 default), daily hard cap ($2.00), monthly hard cap ($30.00). All configurable in Settings.
4. **Three-phase call lifecycle**: Pre-flight (estimate + budget check) → In-flight (actual tokens + body-cost) → Post-flight (async reconciliation against OpenRouter `/generation`).
5. **Cost insertion point**: `pmacs/agents/base.py` `_call_llm_openai()` — extract `usage` from response, route through cost layer.
6. **Storage**: SQLite for `pricing_table`, `budget_state`, `budget_history`. DuckDB for `api_usage` analytics.
7. **SSE**: New `cost` channel with 6 event types for real-time dashboard updates.
8. **Kill switch**: New triggers `CYCLE_BLOCKED_BUDGET_DAILY`, `CYCLE_BLOCKED_BUDGET_MONTHLY`.

## Caveats (operator note)

> "The dollar numbers could be wrong, other things could be changeable as well."

- All dollar amounts (pricing, caps, projections) are **defaults** to be verified at implementation time
- Pricing is always fetched dynamically — snapshot in PRD §3 is planning-time only
- Budget cap defaults are 10-27x expected cost — conservative regardless of exact pricing
- Token estimation heuristic (0.26 ratio) should be validated empirically

## Dependencies

- Phase 1-13 complete (system is PRODUCTION-READY)
- `pmacs/agents/base.py` — LLM call flow exists, needs cost instrumentation
- `pmacs/storage/sqlite.py` — schema init + migrations
- `pmacs/storage/duckdb.py` — analytics tables
- `pmacs/nervous/sse_publisher.py` — event bus
- `pmacs/cortex/kill_switch.py` — trigger registration
- `pmacs/web/routes/settings.py` — budget controls UI
- `config/model_registry.json` — backend configuration (openrouter entry exists)

## New Files (from PRD §15)

```
pmacs/billing/__init__.py
pmacs/billing/pricing.py          # pricing_table CRUD + OpenRouter fetch
pmacs/billing/token_estimator.py  # character-based pre-flight estimation
pmacs/billing/cost_calculator.py  # core equation
pmacs/billing/budget_enforcer.py  # three-tier check + runaway detection
pmacs/billing/reconciler.py       # async post-flight reconciliation
pmacs/billing/usage_logger.py     # writes to api_usage + budget_state
pmacs/billing/period_roller.py    # UTC midnight + month-boundary rollover
pmacs/schemas/billing.py          # Pydantic models
pmacs/web/routes/settings_cost.py # /settings/cost endpoints
pmacs/web/templates/cost_widget.html
pmacs/web/templates/cost_settings.html
tests/unit/test_token_estimator.py
tests/unit/test_cost_calculator.py
tests/unit/test_budget_enforcer.py
tests/unit/test_period_roller.py
tests/integration/test_pricing_fetch.py
tests/integration/test_full_call_lifecycle.py
tests/integration/test_reconciliation.py
```

## Exit Test (from PRD §17)

All unit + integration tests pass. Cost widget renders. Kill switch engages on budget breach. 30-cycle smoke run: sum of actual costs matches cycle totals within $0.01.
