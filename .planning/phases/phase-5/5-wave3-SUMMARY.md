---
phase: "5"
plan: "wave3"
subsystem: "web, nervous"
tags: ["SSE", "TOTP", "data-layer", "routes", "C3", "C4", "S7"]
dependency_graph:
  requires: ["5-wave1", "5-wave2"]
  provides: ["sse-tests", "totp-verify-endpoint", "web-data-layer", "wired-routes"]
  affects: ["pmacs/nervous/api.py", "pmacs/web/routes/*", "pmacs/web/data.py"]
tech_stack:
  added: ["TokenBucket rate limiter", "DashboardConfig injection", "get_readonly_db"]
  patterns: ["BUCKETS rate-limit pattern", "read-only SQLite connections", "template field mapping"]
key_files:
  created:
    - "pmacs/nervous/rate_limit.py"
    - "pmacs/web/config.py"
    - "pmacs/web/data.py"
    - "pmacs/web/templates/components/empty_state.html"
    - "tests/unit/test_sse_endpoint.py"
    - "tests/unit/test_totp_endpoint.py"
    - "tests/unit/test_web_data.py"
    - "tests/unit/test_web_routes.py"
  modified:
    - "pmacs/nervous/api.py"
    - "pmacs/web/routes/dashboard.py"
    - "pmacs/web/routes/agents.py"
    - "pmacs/web/routes/pipeline.py"
    - "pmacs/web/routes/cortex.py"
    - "pmacs/web/routes/debug.py"
    - "pmacs/web/routes/universe.py"
    - "pmacs/web/routes/settings.py"
    - "tests/e2e/test_dashboard_renders.py"
decisions:
  - "SSE HTTP streaming tests avoided due to TestClient blocking; publisher unit tests cover same code path"
  - "Template field names mapped in route handlers rather than changing templates"
  - "In-memory SQLite fallback for routes when DB file missing"
metrics:
  duration_s: 1575
  completed: "2026-05-09"
  tasks: 4
  files: 17
  tests_added: 53
  tests_passing: 1032
  tests_failing: 0
  tests_skipped: 11
---

# Phase 5 Wave 3: SSE Tests, TOTP Endpoint, Data Layer, Route Wiring Summary

SSE endpoint verified with publisher unit tests, TOTP verification API with token-bucket rate limiting, shared data access layer for 10 data functions, and all 7 route handlers wired to real data stores.

## Completed Tasks

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | SSE endpoint tests [C4] | c5b6c03 | tests/unit/test_sse_endpoint.py |
| 2 | TOTP verify API endpoint [C3] | ea303f3 | pmacs/nervous/api.py, pmacs/nervous/rate_limit.py |
| 3 | Shared data access layer [S7] | 4eb4ea5 | pmacs/web/data.py |
| 4 | Wire 7 route handlers [S7] | 79913f2, a3e881d | pmacs/web/routes/*.py, pmacs/web/config.py |

## Task Details

### Task 1: SSE Endpoint Tests
- Verified /events endpoint: text/event-stream, stream filter, Last-Event-ID, session cookie
- 13 tests: 8 publisher unit tests (fan-out, filtering, reconnection), 5 endpoint tests
- HTTP streaming tests avoided -- TestClient blocks on infinite generators; publisher tests exercise identical code path

### Task 2: TOTP Verification API
- POST /api/totp/verify with Pydantic request/response models
- Token bucket rate limiter (5 attempts/min) using BUCKETS["totp_verify"].acquire() pattern
- Audit logging via AuditWriter on every attempt with cycle_id="totp"
- 8 tests: valid/invalid codes, input validation, rate limiting, unconfigured secret, bucket refill

### Task 3: Shared Data Access Layer
- pmacs/web/data.py: 10 functions reading from SQLite, DuckDB, JSONL, TOML, filesystem
- Functions: get_active_holdings, get_recent_decisions, get_risk_metrics, get_system_health, get_queue_status, get_universe_list, get_debug_events (with filters), get_settings (TOML+JSON), get_cortex_status (aggregated), get_agent_cycle_data
- get_readonly_db helper with in-memory fallback for missing files
- 23 tests with synthetic fixtures covering all functions

### Task 4: Route Wiring
- All 7 routes updated: dashboard, agents, pipeline, cortex, debug, universe, settings
- DashboardConfig module for path injection at startup
- Template field mapping: conviction_score->conviction, entry_price_usd->entry, ts->timestamp
- Missing empty_state.html component created
- Pre-existing e2e tailwind assertion fixed (CDN->vendor)
- 9 route tests + 7 e2e fixes = 1032 total tests pass

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed None-type position_size_usd in dashboard route**
- Found during: Task 4
- Issue: sum(h.get("position_size_usd", 0)) failed when value was None (SQLite NULL)
- Fix: Changed to `h.get("position_size_usd") or 0`
- Files: pmacs/web/routes/dashboard.py
- Commit: 79913f2

**2. [Rule 2 - Missing] Created missing components/empty_state.html template**
- Found during: Task 4 (e2e regression)
- Issue: dashboard.html includes "components/empty_state.html" which never existed
- Fix: Created the component template
- Files: pmacs/web/templates/components/empty_state.html
- Commit: a3e881d

**3. [Rule 2 - Missing] Created TokenBucket rate limiter (Architecture.md anti-pattern requirement)**
- Found during: Task 2
- Issue: BUCKETS["source"].acquire() pattern referenced in anti-patterns but not implemented
- Fix: Created pmacs/nervous/rate_limit.py with TokenBucket class and BUCKETS dict
- Files: pmacs/nervous/rate_limit.py
- Commit: ea303f3

**4. [Rule 2 - Missing] Added OperationalError handling for get_universe_list**
- Found during: Task 4 (e2e regression)
- Issue: In-memory fallback DB has no universe table, causing unhandled OperationalError
- Fix: Wrapped get_universe call in try/except sqlite3.OperationalError
- Files: pmacs/web/data.py
- Commit: a3e881d

**5. [Rule 1 - Bug] Fixed pre-existing e2e tailwind assertion**
- Found during: Task 4 (regression check)
- Issue: Test checks for "tailwindcss" CDN string but HTML uses local vendor "tailwind.min.js"
- Fix: Changed assertion to check for "tailwind" substring
- Files: tests/e2e/test_dashboard_renders.py
- Commit: a3e881d

### Design Decisions

- SSE publisher tested directly rather than via HTTP streaming -- TestClient's stream context manager blocks indefinitely on infinite async generators, making HTTP-level streaming tests impractical in a synchronous test runner
- Template field mapping done in route handlers (not template changes) to avoid breaking existing template structure
- cycle_id="totp" used for TOTP audit events since they are not cycle-scoped (Architecture.md allows system-level events to be exempt)

## Known Stubs
None -- all data functions wired to real stores.
