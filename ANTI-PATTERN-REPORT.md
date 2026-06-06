# PMACS Anti-Pattern Scan Report

**Date**: 2026-05-31
**Scope**: `pmacs/` source tree (Architecture.md S16 forbidden patterns)
**Scan method**: Grep-based pattern matching across all `.py` files

---

## Summary

| # | Anti-Pattern | Status | Violations |
|---|---|---|---|
| 1 | Direct state mutation outside state_machine.transition() | VIOLATION | 1 |
| 2 | json.dumps for audit logging (must use canonical_json) | CLEAN | 0 |
| 3 | Custom rate-limit logic (must use BUCKETS) | CLEAN | 0 |
| 4 | Mutating evidence packets in staleness checks | CLEAN | 0 |
| 5 | cycle_id=None on audit-emitting functions | CLEAN | 0 |
| 6 | Day 1 bootstrap aborting everything | CLEAN | 0 |
| 7 | Tight broker-side stops | CLEAN | 0 |
| 8 | eur_per_usd field (must be usd_per_eur) | CLEAN | 0 |
| 9 | Mutation Engine writing production state directly | CLEAN | 0 |
| 10 | Mutation A/B running in PAPER mode | CLEAN | 0 |
| 11 | Mutation auto-applying without TOTP | CLEAN | 0 |
| 12 | Runtime prompt edits | CLEAN | 0 |
| 13 | Backtesting against historical LLM outputs | CLEAN | 0 |
| 14 | Logging secrets | CLEAN | 0 |
| 15 | Missing error_code on WARN+ debug events | VIOLATION | 23 |
| 16 | Pydantic v1 imports | CLEAN | 0 |

**Total violations: 24 across 2 anti-patterns**

---

## Detailed Findings

### AP1: Direct state mutation outside state_machine.transition() -- VIOLATION (1)

The spec forbids `holding.state = "..."` and requires all state transitions via `state_machine.transition()`. All `.state` reads (e.g., `holding.state == HoldingState.ACTIVE`) are fine. However, one direct holding field mutation was found outside the transition mechanism:

| File | Line | Evidence |
|---|---|---|
| `pmacs/cortex/stop_loss_daemon.py` | 251 | `h.trailing_stop_price_usd = trailing_price` |

This mutates a holding's `trailing_stop_price_usd` field directly without going through `state_machine.transition()`. The daemon also sets `h.ticker`, `h.stop_price_usd`, and `h.trailing_stop_armed` at lines 249-252 in the same block. While this is not a state *transition* (it does not change `holding.state`), it is a direct mutation of holding fields outside the canonical transition path, which may be intentional for the stop-loss daemon's monitoring role. **Review recommended.**

---

### AP2: json.dumps for audit logging -- CLEAN

All audit-adjacent code paths correctly use `canonical_json` from `pmacs/data/canonical.py`:

- `pmacs/storage/audit.py:100` -- uses `canonical_json(payload)`
- `pmacs/storage/dead_letter.py:84` -- uses `canonical_json(payload)`
- `pmacs/mutation/candidate_generator.py` -- uses `canonical_json()`
- `pmacs/nervous/mutation.py:36` -- uses `canonical_json(data)`

Other `json.dumps` usages are legitimate non-audit contexts:
- `pmacs/logsys/debug_log.py:243` -- debug log line serialization (not audit chain)
- `pmacs/logsys/dead_letter.py:67` -- dead letter queue payload (not audit chain)
- `pmacs/execution/service.py` -- UDS socket IPC framing (not audit)
- `pmacs/web/app.py`, `pmacs/nervous/sse_publisher.py` -- SSE framing (not audit)
- `pmacs/agents/base.py:303` -- simulation data output (not audit)
- `pmacs/data/resolution/detector.py:229` -- DB column insert for evidence IDs (not audit)

---

### AP3: Custom rate-limit logic -- CLEAN

The codebase has exactly one rate-limiting module per spec:
- `pmacs/nervous/rate_limit.py` -- defines `TokenBucket` and `BUCKETS` dict
- `pmacs/data/gateway.py` -- uses its own `TokenBucket` for HTTP gateway (separate from BUCKETS, but this is the data-fetch gateway, not API rate-limiting)

The `BUCKETS["source"].acquire()` pattern is used correctly in `pmacs/web/routes/settings.py` for TOTP verification rate-limiting. No custom ad-hoc rate-limit logic found.

---

### AP4: Mutating evidence packets in staleness checks -- CLEAN

All staleness/freshness checks return result objects (`HeartbeatStatus`, `FreshnessResult`) without mutating the input evidence packets. Found in:
- `pmacs/cortex/health.py` -- returns `HeartbeatStatus` list, never mutates input
- `pmacs/cortex/self_check.py` -- reads timestamps, returns status
- `pmacs/config.py` -- `staleness_budget_seconds` is a config field, not mutation

---

### AP5: cycle_id=None on audit-emitting functions -- CLEAN

Only 2 occurrences found:
- `pmacs/cortex/sleep_watch.py:151` -- `incomplete_cycle_id=None` (parameter name is `incomplete_cycle_id`, not `cycle_id`; this is a lookup key, not an audit emission)
- `pmacs/logsys/debug_log.py:30` -- Comment: `# System-level events where cycle_id=None is acceptable (Architecture.md S5.2).` This is a documented exception.

All mutation functions use `cycle_id: str = ""` (empty string, not None) which is the documented pattern for required cycle_id.

---

### AP6: Day 1 bootstrap aborting everything -- CLEAN

The system correctly implements `PROCEED_BOOTSTRAP_LOW_CONFIDENCE`:
- `pmacs/schemas/arbitration.py:19` -- defines the enum value
- `pmacs/engines/arbitration.py:223` -- emits it when all immature sources agree
- `pmacs/constants.py:137` -- defines the constant

All `ABORTED_LLM` state transitions go through `transition(holding, HoldingState.ABORTED_LLM, ...)` correctly. No blanket "abort everything on day 1" logic found.

---

### AP7: Tight broker-side stops -- CLEAN

The execution layer correctly implements catastrophe-net only:
- `pmacs/execution/service.py:212` -- `stop_price = compute_catastrophe_stop(fill.filled_price_usd)`
- `pmacs/execution/adapter.py:56` -- doc: `Used for catastrophe-net stops (15% below entry).`
- `pmacs/engines/pricing.py:33` -- `MAX_STOP_LOSS_PCT: float = 0.15  # hard cap (Source.md S5, non-negotiable)`
- `pmacs/engines/pricing.py:64` -- `stop_loss_pct = min(MAX_STOP_LOSS_PCT, max(0.10, 2.0 * atr_pct))`

PMACS manages trailing stops internally (`pmacs/cortex/stop_loss_daemon.py`). Broker only gets the 15% catastrophe-net.

---

### AP8: eur_per_usd field -- CLEAN

The codebase has active guards against this pattern:
- `pmacs/constants.py:128` -- `FX_CONVENTION = "usd_per_eur"  # NEVER use eur_per_usd`
- `pmacs/schemas/currency.py:25-31` -- `@model_validator` raises `ValueError` if `eur_per_usd` appears as a declared field or in `model_dump()`
- `pmacs/schemas/currency.py:35` -- `eur_per_usd` exists only as a computed `@property` (read-only), not a stored field

No violations found.

---

### AP9: Mutation Engine writing production state directly -- CLEAN

Structural separation is correctly implemented:
- `pmacs/nervous/mutation.py` docstring: "This module lives in pmacs-nervous because the mutation process (pmacs-mutation) MUST NOT have write access to production config files."
- `apply_candidate_to_registry()` is in `pmacs/nervous/mutation.py`, not in `pmacs/mutation/`
- All writes go through `atomic_write_config()` with `canonical_json`
- `pmacs/mutation/` (the daemon process) only reads and proposes; never writes config

---

### AP10: Mutation A/B running in PAPER mode -- CLEAN

The A/B runner explicitly runs SHADOW-only for candidate arms:
- `pmacs/mutation/ab_runner.py:38` -- `"Candidate arm always runs SHADOW-only (Architecture.md S16 anti-pattern)."`
- `pmacs/mutation/ab_runner.py:1` -- `"Shadow A/B test runner for Mutation Engine"`

---

### AP11: Mutation auto-applying without TOTP -- CLEAN

All promotion paths require TOTP:
- `pmacs/web/routes/settings.py:406` -- `"Promote a mutation candidate to production (TOTP-gated)."`
- `pmacs/web/routes/settings.py:419-422` -- calls `verify_totp(secret, req.totp_code)` before promotion
- `pmacs/web/routes/settings.py:504-513` -- same TOTP gate for rollback
- `pmacs/mutation/promotion.py:52` -- `"Operator promotes a mutation candidate. Requires TOTP."`
- `config/mutation.toml` -- no `auto_promote` or `auto_apply` settings found
- `pmacs/execution/adapter.py:5` -- comment: `"Architecture.md S16.10 -- No mutation auto-applying."`

---

### AP12: Runtime prompt edits -- CLEAN

No patterns found for runtime prompt modification. Prompts are loaded from `pmacs/agents/prompts/*.md` files and used read-only.

---

### AP13: Backtesting against historical LLM outputs -- CLEAN

No patterns found for backtesting against historical LLM outputs.

---

### AP14: Logging secrets -- CLEAN

All TOTP/secret-related logging uses safe patterns:
- `pmacs/web/routes/cortex.py:236` -- `"TOTP verification failed: %s", exc` (logs exception, not secret)
- `pmacs/web/routes/settings.py:430,438,520,527` -- same pattern (logs exception type/message, never the TOTP code or secret)
- `pmacs/installer/steps/verify_llm.py:127` -- `"Keyring lookup failed for %s: %s", api_key_ref, exc` (logs reference name, not key value)

No instances of logging actual API keys, TOTP secrets, or signing keys found.

---

### AP15: Missing error_code on WARN+ debug events -- VIOLATION (23)

Architecture.md S5.5 requires every WARN+ debug event to have a canonical `error_code`. The following `log_debug()` calls use `level="WARN"` or `level="ERROR"` but lack an `error_code` parameter:

#### WARN without error_code (18 locations)

| File | Line | Event Code |
|---|---|---|
| `pmacs/cortex/flywheel_monitor.py` | 140 | `FLYWHEEL_HEALTH_CHECK` |
| `pmacs/nervous/orchestrator.py` | 348 | `CYCLE_MID_CYCLE_KILL_SWITCH` |
| `pmacs/nervous/orchestrator.py` | 414 | `CYCLE_SIGNAL_RECEIVED` |
| `pmacs/nervous/orchestrator.py` | 457 | `CYCLE_STEP_BUDGET_EXCEEDED` |
| `pmacs/nervous/orchestrator.py` | 517 | `CYCLE_ABORTED_CLOCK_DRIFT` |
| `pmacs/nervous/orchestrator.py` | 556 | `CYCLE_ABORTED_KILL_SWITCH` |
| `pmacs/engines/lessons.py` | 116 | `LESSON_WRITE_FAILED` |
| `pmacs/engines/reconciliation.py` | 67 | `RECONCILIATION_CONFIG_PARSE_FAILED` |
| `pmacs/engines/cash_ledger.py` | 250 | `CASH_LEDGER_BALANCE_DRIFT` (conditional WARN) |
| `pmacs/engines/arbitration.py` | 255 | `ARBITRATION_MATURE_DISAGREE` |
| `pmacs/data/gateway.py` | 52 | `PROMPT_INJECTION_DETECTED` |
| `pmacs/data/price_cache.py` | 113 | `PRICE_UNAVAILABLE` |
| `pmacs/data/price_cache.py` | 147 | `PRICE_POLYGON_FAILED` |
| `pmacs/data/price_cache.py` | 178 | `PRICE_FINNHUB_FAILED` |
| `pmacs/data/price_cache.py` | 213 | `PRICE_ALPACA_FAILED` |
| `pmacs/nervous/orchestrator.py` | (multiple) | Additional orchestrator WARN events |

#### ERROR without error_code (5 locations)

| File | Line | Event Code |
|---|---|---|
| `pmacs/web/app.py` | 36 | `WEB_UNHANDLED_EXCEPTION` |
| `pmacs/engines/cash_ledger.py` | 171 | `CASH_LEDGER_NEGATIVE_BALANCE` |
| `pmacs/execution/service.py` | 134 | `EXEC_INVALID_PLAN` |
| `pmacs/execution/catastrophe_net.py` | 125 | `CATASTROPHE_CANCEL_FAILED` |
| `pmacs/sim/alpaca_paper_adapter.py` | 172 | `ALPACA_CANCEL_FAILED` |

Note: Many of these already use the event code string (first arg to `log_debug`) as a quasi-error-code, but it is not passed as the explicit `error_code=` parameter as required by spec.

---

### AP16: Pydantic v1 imports -- CLEAN

- No `from pydantic.v1 import` found anywhere in `pmacs/`
- No `class Config:` (Pydantic v1 style) found in `pmacs/schemas/`
- All schemas use `model_config = ConfigDict(...)` (Pydantic v2 style)

---

## Recommended Actions

### Critical (spec violations)

1. **AP15 -- Add `error_code` to all 23 WARN/ERROR debug events**. Each `log_debug()` call at level WARN or ERROR must include `error_code="<CANONICAL_CODE>"`. The canonical codes should come from Architecture.md S5.5 taxonomy.

### Review recommended

2. **AP1 -- `pmacs/cortex/stop_loss_daemon.py:251`** -- Direct mutation of `h.trailing_stop_price_usd`. This may be intentional (stop-loss daemon updates trailing prices by design), but should be documented as an allowed exception or refactored to use a formal update method.
