# ADR-001: Server-Rendered Dashboard Separate from Nervous

## Status

Superseded — the dashboard and nervous were later merged into a single FastAPI
process serving on port 8000 (one launchd plist, no inter-process SSE hop). The
read-only design (no dashboard write endpoints; all writes via nervous's operator-confirmed
POST API) and the server-rendered Jinja2 + HTMX choice below still hold. Only the
process/port separation was reversed.

## Context

PMACS originally had two FastAPI processes serving HTTP on localhost: `pmacs-nervous` (port 8000) handled orchestration, write API, and SSE event streaming; a separate `pmacs-dashboard` process (port 8001) rendered the web UI. They were later merged into a single FastAPI process on port 8000 to simplify deployment (one plist, one port, shared state).

The dashboard uses Jinja2 server-side templates with HTMX for interactivity. It subscribes to `pmacs-nervous` via SSE for real-time data and exposes no write endpoints of its own. All write actions are proxied through nervous's operator-confirmed POST API.

Merging the two processes would reduce operational overhead (one launchd plist instead of two, no inter-process SSE subscription) and eliminate the latency of the SSE hop between processes on the same machine.

## Decision

Merge `pmacs-dashboard` and `pmacs-nervous` into a single FastAPI process served on port 8000. The combined process remains read-only for dashboard pages; all write actions are still routed through nervous's operator-confirmed POST API.

Use server-rendered Jinja2 templates with HTMX for the dashboard UI rather than a SPA framework (React, Vue, etc.).

## Consequences

**Positive:**

- Single launchd plist and a single operator-facing port (8000) simplify deployment and reduce confusion.
- No inter-process SSE hop; real-time updates stream from the same process serving the UI.
- Jinja2 autoescape provides XSS protection by default. Combined with strict CSP disallowing inline scripts and external resources, the attack surface is minimal.
- No JavaScript build pipeline. Templates live in `pmacs/web/templates/` and `pmacs/web/components/`. Changes are file-level, no bundling step.
- Dashboard reads SQLite directly (read-only connection) and subscribes to nervous SSE for push updates, providing low-latency data without polling.

**Negative:**

- Dashboard and nervous share a process; a severe vulnerability in template rendering theoretically has access to the orchestration process. Mitigated by keeping dashboard routes read-only and requiring CSRF + operator confirmation on every write endpoint.
- HTMX partials require server round-trips for every interaction. Acceptable for an operator-facing tool, but would not scale to a multi-user product.

**References:** spec/Architecture.md §4.3 (pmacs-dashboard), §4.4 (SSE), §18.6 (CSRF/XSS), spec/Source.md §14-20 (page specifications).
