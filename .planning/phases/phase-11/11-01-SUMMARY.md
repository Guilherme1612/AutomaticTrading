---
phase: 11
plan: 01
subsystem: dashboard
tags: [sparkline, htmx, sse, duckdb, navigation]
dependency_graph:
  requires: [phase-5, phase-8]
  provides: [dynamic-sparklines, htmx-navigation, sse-sparkline-refresh]
  affects: [dashboard, base-template, app-js]
tech_stack:
  added: [htmx-boost, htmx-push-url, htmx-indicator]
  patterns: [data-layer-sparkline, jinja2-sparkline-rendering, sse-sparkline-handler]
key_files:
  created: []
  modified:
    - pmacs/web/data.py
    - pmacs/web/routes/dashboard.py
    - pmacs/web/templates/dashboard.html
    - pmacs/web/templates/base.html
    - pmacs/web/static/style.css
    - pmacs/web/static/app.js
    - tests/unit/test_web_data.py
    - tests/unit/test_web_routes.py
decisions:
  - Sparkline SVG points computed server-side via Jinja2 template loop from DuckDB data
  - HTMX boosted navigation targets #main-content innerHTML swap
  - SSE sparkline_update handler fetches from API endpoint rather than embedding data in SSE event
metrics:
  duration: 13m
  completed: 2026-05-13
  tasks: 3
  files: 8
  tests_added: 12
---

# Phase 11 Plan 01: Dynamic Sparklines + HTMX Navigation Summary

Replaced hardcoded sparkline SVG polylines with DuckDB-backed time-series data, added HTMX SPA-style navigation with push-state, and wired real-time SSE sparkline updates during cycle execution.

## Completed Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| S1-1 | Wire sparklines to DuckDB rolling_metrics | e4d9a11 | data.py, dashboard.py, dashboard.html, test_web_data.py, test_web_routes.py |
| S1-2 | HTMX push-state for page navigation | b3fb416 | base.html, style.css, app.js |
| S1-3 | HTMX sparkline refresh via SSE | 4065dc6 | app.js |

## Key Changes

### S1-1: Sparkline Data Layer
- Added `get_sparkline_data(db_path, metric, window)` to `pmacs/web/data.py` reading from DuckDB `rolling_metrics` table
- Added `get_all_sparkline_data(db_path, window)` to fetch all 5 dashboard metrics at once
- Added GET `/api/dashboard/sparkline` endpoint returning JSON `[{t, v}]` arrays
- Replaced 5 hardcoded SVG `<polyline points="...">` with Jinja2 loop computing points from actual data
- "No data yet" text shown for pre-first-cycle state (empty data)

### S1-2: HTMX Navigation
- Added `hx-boost="true"` to `<body>` for SPA-style navigation
- Added `hx-push-url="true"` and `hx-target="#main-content"` to sidebar nav links
- Added `.htmx-indicator` loading spinner overlay in top-right
- Added CSS transitions for `.htmx-indicator`, `.htmx-swapping`, `.htmx-settling`
- Added `htmx:afterSwap` handler to reinit SSE, Sankey visualization, sidebar active state

### S1-3: SSE Sparkline Refresh
- Added `sparkline_update` SSE event handler that fetches fresh data from `/api/dashboard/sparkline`
- Rebuilds SVG polyline dynamically from JSON response
- Respects current active time-window selection
- Graceful degradation: no-op on fetch failure

## Deviations from Plan

None -- plan executed exactly as written.

## Tests Added

- 9 new tests in `test_web_data.py`: sparkline data retrieval, window filtering, all-metrics dict
- 4 new tests in `test_web_routes.py`: sparkline API endpoint, default params, dashboard data attributes
- 40 total web tests pass (excluding 1 pre-existing DuckDB env failure)

## Self-Check: PASSED

- All 8 modified files found on disk
- All 3 commit hashes found in git log
- 40/40 web unit tests pass
- 0 accidental file deletions in any commit
