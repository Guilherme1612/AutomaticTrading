# ADR-001: Server-Rendered Dashboard Separate from Nervous

## Status

Accepted

## Context

PMACS has two FastAPI processes serving HTTP on localhost: `pmacs-nervous` (port 8000) handles orchestration, write API, and SSE event streaming; `pmacs-dashboard` (port 8001) renders the web UI that the operator interacts with. Both could be merged into a single process to simplify deployment (one plist, one port, shared state).

The dashboard uses Jinja2 server-side templates with HTMX for interactivity. It subscribes to `pmacs-nervous` via SSE for real-time data and exposes no write endpoints of its own. All write actions are proxied through nervous's TOTP-gated POST API.

Merging the two processes would reduce operational overhead (one launchd plist instead of two, no inter-process SSE subscription) and eliminate the latency of the SSE hop between processes on the same machine.

## Decision

Keep `pmacs-dashboard` and `pmacs-nervous` as separate processes. Dashboard is read-only; nervous holds all write access and orchestration logic.

Use server-rendered Jinja2 templates with HTMX for the dashboard UI rather than a SPA framework (React, Vue, etc.).

## Consequences

**Positive:**

- Attack surface isolation: a vulnerability in the dashboard's template rendering or browser-facing code cannot escalate to write access on nervous. Dashboard has no database write paths.
- Dashboard can be restarted independently without disrupting an active cycle or trade execution.
- Jinja2 autoescape provides XSS protection by default. Combined with strict CSP disallowing inline scripts and external resources, the attack surface is minimal.
- No JavaScript build pipeline. Templates live in `pmacs/web/templates/` and `pmacs/web/components/`. Changes are file-level, no bundling step.
- Dashboard reads SQLite directly (read-only connection) and subscribes to nervous SSE for push updates, providing low-latency data without polling.

**Negative:**

- Two launchd plists to manage.
- Inter-process SSE adds ~1-2ms latency for real-time updates (negligible at operator-interaction timescales).
- HTMX partials require server round-trips for every interaction. Acceptable for an operator-facing tool, but would not scale to a multi-user product.

**References:** spec/Architecture.md §4.3 (pmacs-dashboard), §4.4 (SSE), §18.6 (CSRF/XSS), spec/Source.md §14-20 (page specifications).
