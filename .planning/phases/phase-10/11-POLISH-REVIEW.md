---
phase: 11-polish-performance-operator-experience
reviewed: 2026-05-13T20:11:00Z
depth: standard
files_reviewed: 13
files_reviewed_list:
  - pmacs/web/data.py
  - pmacs/web/routes/dashboard.py
  - pmacs/web/routes/settings.py
  - pmacs/web/templates/dashboard.html
  - pmacs/web/templates/base.html
  - pmacs/web/templates/agents.html
  - pmacs/web/templates/cortex.html
  - pmacs/web/templates/debug.html
  - pmacs/web/templates/pipeline.html
  - pmacs/web/templates/settings.html
  - pmacs/web/templates/universe.html
  - pmacs/web/templates/components/error_state.html
  - pmacs/web/static/app.js
findings:
  critical: 1
  warning: 5
  info: 5
  total: 11
status: issues_found
---

# Phase 11 (Polish): Code Review Report -- Wave 1 + Wave 2

**Reviewed:** 2026-05-13T20:11:00Z
**Depth:** standard
**Files Reviewed:** 13
**Status:** issues_found

## Summary

Reviewed all Wave 1 (Sparklines + HTMX) and Wave 2 (Error Boundaries + Notifications) files for Phase 11 Polish. The error boundary pattern is correctly applied across all 7 page templates with consistent structure. The sparkline data loader handles missing DuckDB gracefully (returns empty lists on any exception). HTMX afterSwap properly reinitializes SSE and sidebar state.

One Critical XSS finding in the Cmd-K palette where user-controlled search text is injected into innerHTML without sanitization. Five Warnings: inconsistent non-disableable event lists across three layers, innerHTML with server-sourced data in cycle comparison, a tautological portfolio value calculation, a stubbed kill switch backend call, and sparkline window buttons that use HTMX to inject raw JSON instead of HTML. Five Info items cover hardcoded URLs, dead code, console.log artifacts, hardcoded disk values, and port display inconsistencies.

## Critical Issues

### CR-01: XSS via innerHTML in Cmd-K Palette with User-Controlled Query

**File:** `pmacs/web/static/app.js:412-419`
**Issue:** The `renderCmdKResults` function builds HTML by concatenating `item.name` directly into an innerHTML assignment. While `CMD_K_ALL` items have hardcoded names, the dynamic search items added at lines 373 and 382 include `query.toUpperCase()` in the name field, which flows unsanitized into innerHTML. A user typing HTML tags (e.g., `<img onerror=...>`) in the search box would get arbitrary HTML injected into the DOM. This is an authenticated-local-only tool, so the blast radius is limited, but it violates the project's security posture.

**Fix:**
```javascript
// Add a text escaping utility at the top of app.js
function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

// Then in renderCmdKResults, at lines 372-373 and 381-382:
filtered.unshift({
    name: 'Go to Pipeline filtered: ' + escapeHtml(query.toUpperCase()),
    href: '/pipeline?ticker=' + encodeURIComponent(query.toUpperCase()),
    category: "ticker",
});
// Similarly for the audit search at line 382:
filtered.unshift({
    name: 'Search audit: ' + escapeHtml(query),
    href: '/debug?q=' + encodeURIComponent(query),
    category: "audit",
});
```

Alternatively, replace the innerHTML approach with `document.createElement` + `textContent` for all dynamic parts, which is the safer pattern already used elsewhere in the file (e.g., toast creation at lines 121-123).

## Warnings

### WR-01: Inconsistent Non-Disableable Event Lists Between Backend, Frontend, and Template

**File:** `pmacs/web/routes/settings.py:83` vs `pmacs/web/static/app.js:202-204` vs `pmacs/web/templates/settings.html:68`

**Issue:** Three different lists define non-disableable events, and they disagree:
- **Backend** (settings.py line 83): blocks `("kill_switch", "audit_chain_failure")`
- **Frontend JS** (app.js lines 202-204): hardcodes `{kill_switch_engaged, audit_chain_failure}`
- **Template** (settings.html line 68): disables `"kill_switch"` and `"error"`

The backend blocks `kill_switch` and `audit_chain_failure`. The template disables `kill_switch` and `error`. The JS hardcodes `kill_switch_engaged` (with `_engaged` suffix). The event keys must be consistent across all three layers or an operator could save a level for `kill_switch` from the JS (different key `kill_switch_engaged`), the backend would not block it, and the notification system would suppress the kill switch alert.

**Fix:** Unify the event keys. The backend blocklist should match what the frontend policy uses. At minimum:
1. Change the template disabled check (settings.html line 68) to include `audit_chain_failure` alongside `kill_switch`
2. Remove `error` from the template disabled set (or add it to the backend blocklist if it should be non-disableable)
3. Consider renaming the JS policy key from `kill_switch_engaged` to `kill_switch` for consistency, or adjust the backend to block both variants

### WR-02: innerHTML Used with Server-Sourced Data in fetchCycleComparison

**File:** `pmacs/web/static/app.js:522,528-529,531`

**Issue:** `fetchCycleComparison` injects cycle IDs (`a`, `b`), error messages (`err.message`), and raw JSON (`JSON.stringify(data)`) directly into innerHTML. Cycle IDs come from user text input and are only whitespace-trimmed (line 517). If a cycle ID contains HTML, it executes. Similarly, `err.message` from a server response could contain HTML if the server is compromised or returns unexpected content.

**Fix:** Use `textContent` for all dynamic content:
```javascript
resultDiv.textContent = '';
var p = document.createElement('p');
p.className = 'text-sm text-zinc-600';
p.textContent = 'Comparing ' + a + ' vs ' + b + '...';
resultDiv.appendChild(p);

// For JSON display:
var pre = document.createElement('pre');
pre.className = 'text-xs font-mono bg-zinc-50 p-3 rounded overflow-auto';
pre.textContent = JSON.stringify(data, null, 2);
resultDiv.appendChild(pre);

// For error:
var errP = document.createElement('p');
errP.className = 'text-sm text-red-600';
errP.textContent = 'Comparison failed: ' + err.message;
resultDiv.appendChild(errP);
```

### WR-03: Hardcoded Portfolio Value Calculation Is a Tautology

**File:** `pmacs/web/routes/dashboard.py:44`
**Issue:** Portfolio value is calculated as `5000.0 - position_value + position_value`, which always equals `5000.0` regardless of actual cash balance or realized P/L. The comment says "cash + positions" but the arithmetic cancels out. After any trade, the displayed portfolio value will be wrong.

**Fix:** Either read the actual cash balance from the database, or compute it correctly:
```python
invested = sum(h.get("position_size_usd") or 0 for h in holdings)
cash_remaining = 5000.0 - invested  # TODO: read from cash_ledger when available
portfolio_value = cash_remaining + invested  # same as 5000 until ledger exists
```
At minimum, remove the tautological arithmetic and add a TODO noting this needs the cash ledger for accurate tracking after trades.

### WR-04: Kill Switch Engage Has No Backend Call (Stub)

**File:** `pmacs/web/static/app.js:720`
**Issue:** The kill switch engage action has a TODO comment (`// TODO: POST to pmacs-nervous /api/kill-switch/engage`) and only updates the button color locally. The kill switch is one of the Five Non-Negotiables ("Operator owns the kill switch"). Without this backend call, clicking "Engage" in the UI does nothing beyond a visual change. The operator could believe the kill switch is engaged when it is not.

**Fix:** Replace the TODO with an actual fetch call:
```javascript
action: function () {
    fetch("/api/kill-switch/engage", {
        method: "POST",
        headers: { "Content-Type": "application/json" }
    }).then(function(resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
    }).then(function(data) {
        document.getElementById("kill-switch-btn").classList.add("bg-red-600");
        document.getElementById("kill-switch-btn").classList.remove("bg-zinc-700");
        showToast("Kill switch ENGAGED. To disengage: Cortex page.", "error", 0);
    }).catch(function(err) {
        showToast("Kill switch engage failed: " + err.message, "critical", 0);
    });
}
```

### WR-05: Sparkline Window Buttons Use HTMX but API Returns Raw JSON

**File:** `pmacs/web/templates/dashboard.html:42-56` and `pmacs/web/routes/dashboard.py:82-95`

**Issue:** The sparkline window buttons use HTMX attributes (`hx-get`, `hx-target="#sparkline-metrics"`, `hx-swap="innerHTML"`) to fetch from `/api/dashboard/sparkline`. But the API endpoint returns raw JSON (`JSONResponse` with `[{"t":..., "v":...}]`), not HTML. When clicked, HTMX would inject raw JSON text into the `#sparkline-metrics` div, replacing the entire SVG-based sparkline grid with unreadable JSON text. The window buttons do not work as designed.

**Fix:** Either: (a) change the sparkline API to return an HTML fragment (a Jinja2 partial rendering the sparkline grid) when the request comes from HTMX, or (b) remove the HTMX attributes from the window buttons and handle the swap entirely in JavaScript (similar to the SSE `sparkline_update` handler at app.js lines 1198-1241). Option (b) is more consistent with the existing SSE refresh pattern and requires less backend change.

## Info

### IN-01: Hardcoded SSE and API URLs with IP Address

**File:** `pmacs/web/static/app.js:15,464,523`
**Issue:** `SSE_URL = "http://127.0.0.1:8000/events"` and `runCycleNow` at line 464 and `fetchCycleComparison` at line 523 also hardcode `http://127.0.0.1:8000`. Meanwhile, the notification level fetch at line 212 and sparkline fetch at line 1209 use relative paths correctly. If pmacs-nervous ever binds to a different address, the hardcoded URLs break.
**Fix:** Use relative URLs throughout: `SSE_URL = "/events"`, `"/api/cycle/start"`, `"/api/cycle/compare"`. The dashboard is served from the same origin as pmacs-nervous (per Architecture.md, pmacs-dashboard proxies or shares the same host). If they are on different ports, derive the base URL from `window.location`.

### IN-02: Hardcoded Disk Free Value in Two Locations

**File:** `pmacs/web/data.py:671` and `pmacs/web/routes/dashboard.py:60`
**Issue:** `disk_free_gb` is hardcoded to `50` in both `get_cortex_status` and the dashboard route. This should read from the actual filesystem. The value will always show "50 GB" regardless of actual disk space.
**Fix:** Use `shutil.disk_usage("/")` to get the real value. Low priority since this is display-only.

### IN-03: Dead Code -- "error" Event Key Has No Notification Handler

**File:** `pmacs/web/templates/settings.html:60,68`
**Issue:** The settings template lists `"error"` as a notification event with a disabled select. But `"error"` does not appear in the `NOTIFICATION_POLICY` object in app.js (lines 185-199). The event is listed in the UI but has no corresponding notification handler in the frontend. The backend does not block `"error"` either (only `kill_switch` and `audit_chain_failure`). Changing this select saves a level to SQLite that nothing consumes.
**Fix:** Either add `"error"` to the NOTIFICATION_POLICY in app.js, or remove it from the template's event list.

### IN-04: console.log in Production Code

**File:** `pmacs/web/templates/pipeline.html:243`
**Issue:** `console.log('Move', ticker, 'to verdict', verdict)` in the kanban onDrop handler. This is a debug artifact that should not ship in production.
**Fix:** Remove the console.log or replace with `showToast()`.

### IN-05: Port 8001/8000 Display Inconsistency in Cortex

**File:** `pmacs/web/data.py:639`
**Issue:** `port_map` lists `"dashboard": 8001` but pmacs-dashboard serves HTML while SSE comes from pmacs-nervous on port 8000. The SSE URL in app.js points to port 8000. An operator checking the Cortex process list might try to access port 8001 for events. The port mapping is technically correct (pmacs-dashboard does serve on 8001) but could confuse operators.
**Fix:** Low priority. Consider adding a note in the Cortex panel that pmacs-nervous provides SSE on 8000 while pmacs-dashboard serves the web UI on 8001.

## Passed Checks

- **Error boundaries:** All 7 page templates (dashboard, agents, pipeline, universe, cortex, debug, settings) use the identical error boundary pattern: `{% if error %}` block at the top of content, setting `error_code`, `error_description`, `error_explanation`, `error_actions`, `error_spec_ref`, then including `components/error_state.html`. Consistent and correct.
- **Sparkline empty data handling:** `get_sparkline_data` and `get_all_sparkline_data` in data.py return empty lists/dicts on any exception (broad except at lines 154, 208). The template handles `< 2` points with "No data yet" fallback (dashboard.html line 89-91). The JS sparkline_update handler also handles `null`/`<2` points (app.js lines 1214-1218).
- **HTMX afterSwap reinit:** app.js lines 1274-1310 correctly reinitializes SSE connection, sidebar active state, and Sankey diagram after HTMX content swaps on `#main-content`.
- **HTMX push-state:** Sidebar links use `hx-push-url="true"` (base.html line 150) and `hx-target="#main-content"` for proper navigation without full page reloads.
- **Jinja2 auto-escaping:** No `{{{` (unsafe) templates found. All dynamic content uses `{{ }}` (auto-escaped by Jinja2). The error_state component correctly uses `textContent` in JS (line 122 of app.js `copyErrorForClaude`).
- **Notification validation:** Backend validates event levels against a strict whitelist (`{"toast", "toast+sound", "modal", "none"}`) at settings.py line 89-94. Non-disableable events blocked at line 83.
- **Pydantic v2:** `NotificationLevelRequest` uses `BaseModel` correctly (settings.py line 14-16).
- **SQLite injection:** All data.py queries use parameterized SQL (`?` placeholders).
- **No `eval()` usage:** No `eval` or `Function` constructor calls found in app.js.
- **Accessiblity:** Toast container has `role="status"` and `aria-live="polite"` (base.html line 318). Blocking modal has `role="alertdialog"` and `aria-modal="true"` (base.html line 309). Viewport guard has `role="alert"` (base.html line 113).

---

_Reviewed: 2026-05-13T20:11:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
