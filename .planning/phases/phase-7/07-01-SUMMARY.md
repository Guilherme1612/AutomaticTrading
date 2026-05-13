# Plan 07-01 Summary — Wire daemon loop + persist ABRunner state

## Status: COMPLETE

## Changes

### Task 1: Wire main_loop() to run_cycle()
- `pmacs/mutation/daemon.py` — Replaced skeleton `while True: sleep(60)` with actual daemon loop that:
  - Generates cycle_id per iteration
  - Queries paper cycle count from SQLite `cycles` table
  - Calls `daemon.run_cycle(cycle_id, paper_cycle_count)`
  - Logs `MUTATION_DAEMON_ITERATION` at INFO level
  - Catches errors per iteration with `MUTATION_DAEMON_LOOP_ERROR` (resilient)

### Task 2: Persist ABRunner outcome data to SQLite
- `pmacs/mutation/ab_runner.py` — Added outcome persistence:
  - `_ensure_outcomes_table()` — creates `mutation_outcomes` table
  - `_persist_outcome()` — INSERTs each recorded outcome
  - `_restore_from_db()` — now loads accumulated outcomes from `mutation_outcomes`
  - `record_outcome()` — accepts optional `cycle_id` kwarg, persists to SQLite
  - `__init__` — calls `_ensure_outcomes_table()` before `_restore_from_db()`

## Test Results
- 314 passed, 0 failures (full suite excluding pre-existing fastapi/hypothesis import errors)
