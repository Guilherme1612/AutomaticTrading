# Phase 16 REVIEWS

## Claude CLI Review (independent session)

### Summary

Phase 16 implements a solid billing layer with good module decomposition and proper PMACS patterns (Pydantic v2, stub-safe DuckDB, hash-chained audit). However, there are several critical gaps: the billing pipeline is **not wired into the orchestrator cycle**, the reconciler uses synchronous `time.sleep` blocking, `duckdb_adapter.execute()` is called but doesn't exist as a generic method, and the dashboard queries reference a `body_costs` table instead of `api_usage`.

### Critical (must fix)

- **[CRIT-1]** Billing not wired into orchestrator cycle. `pmacs/nervous/orchestrator.py` has zero imports from `pmacs.billing.*`. The `_last_call_usage` side-channel in `base.py` captures data, but nobody reads it and routes it through `usage_logger.log_usage()`.
- **[CRIT-2]** `duckdb_adapter.execute()` called but doesn't exist. `usage_logger.py`, `drift_monitor.py`, `reconciler.py` all call `duckdb_adapter.execute("SELECT ...", [...])` but DuckDB adapter only has specific methods — no generic `execute()`. Will `AttributeError` at runtime.
- **[CRIT-3]** Dashboard/settings query `body_costs` table that doesn't exist. The actual DuckDB table is `api_usage`.
- **[CRIT-4]** Reconciler uses blocking `time.sleep` (2s, 30s, 300s). Blocks entire cycle if called synchronously.

### Warning (should fix)

- **[WARN-1]** `check_per_cycle_soft_cap` ignores `sqlite_conn` parameter — can't account for cumulative cycle spend.
- **[WARN-2]** `enforce_budgets` skips cycle soft cap check — only runs daily + monthly.
- **[WARN-3]** `pricing.py:97` timezone-naive `fromisoformat` comparison.
- **[WARN-4]** `update_budget_state` manual `BEGIN IMMEDIATE` may conflict with sqlite3 autocommit.
- **[WARN-5]** `_engage_budget_kill_switch` error_code `"KILL_SWITCH_ENGAGED"` is semantically wrong for a failure.
- **[WARN-6]** Drift monitor reuses `COST_RUNAWAY_DETECTED` error code — should use distinct code.
- **[WARN-7]** Pricing tests require network — will fail in air-gapped CI.
- **[WARN-8]** DuckDB row access pattern assumes dict-like rows — needs explicit config.

### Info

- **[INFO-1]** `estimate_call_cost` generates unused `call_id` in `EstimatedCost`.
- **[INFO-2]** Crucible persona in `PERSONA_EXPECTED_OUTPUT_TOKENS` — multi-iteration may not match.
- **[INFO-3]** Missing integration tests (full call lifecycle, reconciliation, pricing fetch).

### Architecture Assessment

**Good:** Module decomposition, side-channel approach, DuckDB stub-safe pattern, Pydantic v2 schemas, schema-first design.

**Bad:** Pipeline disconnected — modules exist but aren't composed. Imaginary `duckdb_adapter.execute()` API. Dashboard queries wrong table.

### Completeness vs PRD

~65% implemented, ~50% functional at runtime. Key gaps: orchestrator integration, generic DuckDB query method, dashboard table names, integration tests.
