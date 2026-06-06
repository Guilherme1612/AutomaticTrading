---
reviewed: 2026-06-06T20:08:00+01:00
depth: standard
files_reviewed: 20
files_reviewed_list:
  - pmacs/web/app.py
  - pmacs/web/config.py
  - pmacs/web/data.py
  - pmacs/web/templating.py
  - pmacs/web/routes/agents.py
  - pmacs/web/routes/cortex.py
  - pmacs/web/routes/dashboard.py
  - pmacs/web/routes/debug.py
  - pmacs/web/routes/pipeline.py
  - pmacs/web/routes/settings.py
  - pmacs/web/routes/universe.py
  - pmacs/web/routes/wizard.py
  - pmacs/web/static/app.js
  - pmacs/web/static/sankey.js
  - pmacs/web/static/style.css
  - pmacs/web/templates/agents.html
  - pmacs/web/templates/settings.html
  - pmacs/web/templates/universe.html
  - pmacs/execution/adapter.py
  - pmacs/execution/service.py
  - pmacs/sim/alpaca_paper_adapter.py
findings:
  critical: 3
  warning: 5
  info: 4
  total: 12
status: issues_found
---

# Code Review Report

**Reviewed:** 2026-06-06T20:08:00+01:00
**Depth:** standard
**Files Reviewed:** 20
**Status:** issues_found

## Summary

Reviewed 20 files across the web layer (routes, templates, static JS/CSS), execution service, and simulation adapter. Three critical issues found: a runtime crash from a missing `_verify_totp` function in universe.py, an unguarded force-exit endpoint that bypasses TOTP, and an XSS vector in settings.html error rendering. Five warnings cover missing TOTP on cycle start, un-TOTP-gated queue mutations, insufficient escaping in SSE-driven innerHTML, and stale in-memory state across cycles. Four info items note code quality concerns.

## Critical Issues

### CR-01: Missing `_verify_totp` function in universe.py causes NameError crash

**File:** `pmacs/web/routes/universe.py:297,324`
**Issue:** Lines 297 and 324 call `_verify_totp(req.totp_code)` but this function is never defined or imported in the file. The settings route defines its own `_verify_totp` but it is not shared. This will raise `NameError: name '_verify_totp' is not defined` at runtime when `bulk-tag` or `bulk-remove` endpoints are called, returning a 500 error instead of the expected 403 TOTP failure. The catch-all exception handler in app.py will mask the root cause in logs.
**Fix:**
```python
# At the top of universe.py, add:
from pmacs.web.routes.settings import _verify_totp

# OR define a local helper:
def _verify_totp(totp_code: str) -> bool:
    from pmacs.cortex.totp import verify_totp
    from pmacs.storage.keychain import get_api_key
    if not totp_code or len(totp_code) != 6:
        return False
    try:
        secret = get_api_key("pmacs.system.totp_secret", "operator")
        return verify_totp(secret, totp_code)
    except Exception:
        return False
```

### CR-02: Force-exit endpoint accepts but ignores TOTP -- no verification performed

**File:** `pmacs/web/routes/pipeline.py:1779-1818`
**Issue:** The `force_exit` endpoint accepts a `totp_code` field in `ForceExitRequest` (line 1776) but never validates it. The handler proceeds directly to `UPDATE holdings SET state = 'EXIT_THESIS_INVALIDATED'` without any TOTP check. Per spec (Non-Negotiable #5, Source.md section 15), force-exiting a position is an operator action that must be TOTP-gated. Any HTTP client can force-exit holdings without authentication.
**Fix:**
```python
@router.post("/api/pipeline/force-exit")
async def force_exit(req: ForceExitRequest):
    # Add TOTP verification before proceeding
    from pmacs.web.routes.settings import _verify_totp
    ok, err = _verify_totp(req.totp_code)
    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=403)
    # ... rest of handler
```

### CR-03: XSS via unescaped API error in settings.html diff viewer

**File:** `pmacs/web/templates/settings.html:626`
**Issue:** When the mutation diff API returns an error, the response `data.error` string is injected directly into `innerHTML` without escaping:
```javascript
container.innerHTML = '<p ...>Error: ' + data.error + '</p>';
```
If an attacker can influence the error message (e.g., via a crafted `candidate_id` that triggers a reflected error), this is an XSS vector. While the candidate_id comes from DB, the error message construction in the handler at `settings.py:577` returns user-influenced strings from `difflib` output. The same pattern exists at `universe.html:303` where `dd.innerHTML = html` builds from API data -- but that case uses `escapeHtml()` correctly.
**Fix:**
```javascript
// Line 626 in settings.html:
if (!data.ok) { container.innerHTML = '<p class="text-xs text-negative">Error: ' + escapeHtml(data.error) + '</p>'; return; }
```

## Warnings

### WR-01: Cycle start endpoint removed TOTP verification -- no auth required

**File:** `pmacs/web/routes/pipeline.py:1701-1708`
**Issue:** `CycleStartRequest` has `totp_code: str = ""` with the comment "kept for backward compat, no longer checked." The `cycle_start` handler never validates TOTP. Starting a cycle triggers real API calls (Finnhub, OpenRouter) that consume rate limits and API credits, and creates real paper trades. While Source.md section 15 does not explicitly gate cycle start with TOTP, the removal of the check means any loopback client (or any client if the firewall is misconfigured) can trigger cycles at will.
**Fix:** Either restore TOTP verification or add a rate limiter to prevent cycle spam. At minimum, document that cycle start is intentionally un-gated.

### WR-02: Queue mutation endpoints lack TOTP gating

**File:** `pmacs/web/routes/pipeline.py:189-297`
**Issue:** Five queue mutation endpoints (`reorder`, `pin`, `remove`, `add`, `promote_all`) accept POST requests with no TOTP verification. These modify the pipeline queue (SQLite writes) that determines which tickers get analyzed and traded. While queue management is lower-risk than trade execution, a malicious caller could reorder the queue to front-run analysis, remove all tickers to prevent trading, or add junk tickers to waste API credits. The `scheme/save` endpoint also has no TOTP gate.
**Fix:** Add TOTP verification to queue write endpoints, or document that queue management is considered non-security-critical for the loopback-only deployment model.

### WR-03: In-memory agent results stale across cycles

**File:** `pmacs/web/routes/pipeline.py:322-326`
**Issue:** Module-level dicts `_last_cycle_agent_results`, `_last_cycle_crucible_results`, `_last_cycle_arbitration` accumulate data across cycles without cleanup. Over multiple cycles, only the last cycle's data is kept per ticker, but old tickers no longer in the cycle remain in memory. The agents route reads from these dicts (via `list(_last_cycle_agent_results.keys())[-1]`) to pick the "last ticker," which could be stale data from a previous cycle if the current cycle has not finished yet.
**Fix:** Clear the in-memory dicts at the start of each cycle in `_run_demo_cycle`:
```python
_last_cycle_agent_results.clear()
_last_cycle_crucible_results.clear()
_last_cycle_arbitration.clear()
```

### WR-04: SSE event ID uses `time.time()` -- non-monotonic, collisions possible

**File:** `pmacs/web/routes/pipeline.py:347`
**Issue:** `_emit_event` generates event IDs via `str(time.time())` which produces values like `"1749237680.123456"`. If two events fire within the same float precision window (common with parallel agent execution), they get the same ID. The SSE client at `app.py:266` skips events with `int(evt_id) <= last_id`, so duplicate IDs could cause event loss. Additionally, `time.time()` can go backwards on NTP adjustments.
**Fix:**
```python
import uuid
# Replace:
"id": str(time.time()),
# With:
"id": str(uuid.uuid4()),
```
Or use a monotonic counter.

### WR-05: `openAgentModal` passes analysis via onclick string -- fragile escaping

**File:** `pmacs/web/templates/agents.html:1155-1158`
**Issue:** The SSE-driven card builder constructs an `onclick` handler by escaping analysis text with only `.replace(/\\/g,"\\\\").replace(/'/g,"\\'")`. This fails for analysis text containing backticks, double quotes, newlines, or other JS-special characters. If LLM output contains e.g. `O'Brien's "growth" strategy`, the escaping is incomplete. The Jinja2 template version (line 175) uses `| tojson | safe` which is correct, but the JS-generated version at line 1157 does not.
**Fix:** Use `data-*` attributes and read them in the click handler instead of string-interpolating into onclick:
```javascript
btn.setAttribute('data-persona', _data.persona || '');
btn.setAttribute('data-analysis', _data.analysis || '');
btn.setAttribute('data-evidence', JSON.stringify(evidence));
btn.setAttribute('data-confidence', ((_data.scores && _data.scores.confidence) || 0));
btn.addEventListener('click', function() {
    openAgentModal(
        this.getAttribute('data-persona'),
        this.getAttribute('data-analysis'),
        JSON.parse(this.getAttribute('data-evidence')),
        parseFloat(this.getAttribute('data-confidence'))
    );
});
```

## Info

### IN-01: Duplicate backend-type check functions across files

**File:** `pmacs/web/routes/dashboard.py:15-30` and `pmacs/web/routes/wizard.py:105-119`
**Issue:** `_check_backend_type()` in dashboard.py and `_get_backend_type()` in wizard.py both query `wizard_state` for the `backend_type` key. Three separate implementations of "check backend type from wizard_state" exist. This is a DRY violation that could drift over time.
**Fix:** Extract to a shared helper in `pmacs/web/config.py` or `pmacs/web/data.py`.

### IN-02: `app.on_event("startup")` is deprecated in FastAPI/Starlette

**File:** `pmacs/web/app.py:46`
**Issue:** `@app.on_event("startup")` is deprecated since Starlette 0.26 / FastAPI 0.100. The recommended replacement is the `lifespan` context manager pattern. This will not break today but will emit deprecation warnings on newer FastAPI versions.
**Fix:** Migrate to `lifespan`:
```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    await _close_stuck_cycles()
    yield

app = FastAPI(title="PMACS", lifespan=lifespan)
```

### IN-03: Bare `except Exception: pass` swallows errors across pipeline.py

**File:** `pmacs/web/routes/pipeline.py` (lines 94, 289, 488, 525, 629, 1489, 1510, 1540, 1675)
**Issue:** Nine instances of `except Exception: pass` silently swallow errors in decision persistence, memo persistence, holding creation, and audit writing. While these are defensive (the cycle should continue), some could indicate real problems (schema mismatch, disk full) that should at minimum be logged. The spec requires audit chain integrity (Non-Negotiable #3) -- silently failing audit writes could break the chain without detection.
**Fix:** At minimum, log the exception at WARNING level:
```python
except Exception as exc:
    import logging
    logging.getLogger("pmacs.web").warning("Persistence failed: %s", exc)
```

### IN-04: Credential leakage suppression in alpaca adapter is reasonable but narrow

**File:** `pmacs/sim/alpaca_paper_adapter.py:43-47`
**Issue:** The credential leakage suppression covers `httpx`, `httpcore`, and `urllib3` loggers but not other potential HTTP libraries. The suppression runs in `__init__`, so it only applies when the adapter is instantiated.
**Fix:** Minor -- the current approach is reasonable for the Alpaca adapter scope. No action needed unless `requests` library is also used in this adapter.

---

_Reviewed: 2026-06-06T20:08:00+01:00_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
