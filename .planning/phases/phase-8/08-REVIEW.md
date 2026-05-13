---
phase: 08-polish
reviewed: 2026-05-12T23:14:00Z
depth: standard
files_reviewed: 18
files_reviewed_list:
  - ops/spec_consistency.py
  - ops/audit_chain_verify.py
  - ops/backup_verify.py
  - ops/profile_cycle.py
  - ops/profile_memory.py
  - pmacs/web/components/empty_state.html
  - pmacs/web/components/loading_state.html
  - pmacs/web/components/error_state.html
  - tests/unit/test_spec_consistency.py
  - tests/unit/test_audit_chain_verify.py
  - tests/unit/test_backup_verify.py
  - tests/unit/test_profile_tools.py
  - pmacs/web/templates/base.html
  - pmacs/web/templates/dashboard.html
  - pmacs/web/templates/agents.html
  - pmacs/web/templates/pipeline.html
  - pmacs/web/templates/debug.html
  - pmacs/web/static/app.js
  - pmacs/web/static/style.css
findings:
  critical: 1
  warning: 4
  info: 4
  total: 9
status: issues_found
---

# Phase 8: Code Review Report

**Reviewed:** 2026-05-12T23:14:00Z
**Depth:** standard
**Files Reviewed:** 18
**Status:** issues_found

## Summary

Reviewed 18 files across Phase 8 (Polish / LIVE-READY): 5 ops tools (Python), 4 test files, 6 HTML templates, 1 JS file, and 1 CSS file. The code is generally well-structured, consistent with PMACS spec conventions, and has good test coverage.

Found 1 critical issue (XSS via onclick interpolation in empty_state.html), 4 warnings (missing error handling, inconsistent element targeting, SSE reconnect loop, potential data loss race), and 4 info items (console.log in production, TODOs, unused variable, unreachable logic).

## Critical Issues

### CR-01: XSS via Jinja2 template injection in empty_state.html onclick handler

**File:** `pmacs/web/components/empty_state.html:14`
**Issue:** The `empty_cta_action` variable is injected directly into an `onclick` attribute without sanitization:
```html
<button onclick="{{ empty_cta_action | default('') }}"
```
If `empty_cta_action` contains a value like `'); alert(document.cookie);//`, it executes arbitrary JavaScript. In Jinja2, the default autoescaping does NOT escape inside HTML attribute event handlers when using `{{ }}` -- Jinja2 autoescapes HTML entities but onclick expects JavaScript, so `&quot;` escaping does not prevent breakout from the JS string context.

Even though this is a local-only tool (loopback-only per Architecture.md), it violates the project's security posture and could be exploited if template context ever includes user-supplied data.
**Fix:**
```html
<button data-cta-action="{{ empty_cta_action | default('') | e }}"
        onclick="var fn = this.getAttribute('data-cta-action'); if (fn) { var f = new Function(fn); f(); }"
        class="px-4 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors">
    {{ empty_cta_text }}
</button>
```
Alternatively, avoid dynamic JS entirely and use a data attribute that app.js reads:
```html
<button data-action="{{ empty_cta_action | default('') }}"
        class="empty-cta-btn px-4 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors">
    {{ empty_cta_text }}
</button>
```

## Warnings

### WR-01: SSE infinite reconnect loop on persistent server failure

**File:** `pmacs/web/static/app.js:49-58`
**Issue:** The `onerror` handler calls `connectSSE()` via `setTimeout(connectSSE, 5000)` unconditionally. If the server is permanently down, this retries forever every 5 seconds with no backoff or limit. This is a resource leak and log noise issue on long-running dashboard sessions.
**Fix:** Add exponential backoff with a maximum retry count or reset on successful connection:
```javascript
var sseRetryCount = 0;
var SSE_MAX_RETRIES = 20;

function connectSSE() {
    if (eventSource) {
        eventSource.close();
    }
    if (sseRetryCount >= SSE_MAX_RETRIES) {
        showToast("SSE connection permanently lost. Reload the page.", "error", 0);
        return;
    }
    try {
        eventSource = new EventSource(SSE_URL);
        eventSource.onopen = function () {
            sseRetryCount = 0; // reset on success
        };
        eventSource.onerror = function () {
            console.warn("SSE connection lost, reconnecting in", 5 * Math.pow(1.5, sseRetryCount), "s");
            eventSource.close();
            var delay = Math.min(5000 * Math.pow(1.5, sseRetryCount), 60000);
            sseRetryCount++;
            setTimeout(connectSSE, delay);
        };
        // ... rest unchanged
    } catch (e) { ... }
}
```

### WR-02: backup_verify.py do_e2e wipes data directory with no confirmation or safety check

**File:** `ops/backup_verify.py:222-267`
**Issue:** The `do_e2e` function wipes the production data directory (lines 236-241) before restoring from a temporary backup. If the backup step silently fails (e.g., disk full, permissions), the original data is destroyed. The backup target is a `tempfile.TemporaryDirectory()` which is cleaned up when the `with` block exits -- so if restore fails after wipe, both original and backup are lost.

The `with tempfile.TemporaryDirectory() as tmp:` block on line 230 means the backup directory is deleted when the function returns. But data_dir is wiped inside the `with` block, and if `do_restore` raises an exception, the temp dir still cleans up, leaving data_dir empty.
**Fix:** Add a safety check after backup to verify it succeeded before wiping, and move the backup outside tempdir or add a cleanup guard:
```python
def do_e2e(data_dir: Path, verbose: bool = False) -> None:
    import tempfile

    print("=== E2E: Backup -> Wipe -> Restore -> Verify ===")

    # Step 1: Backup
    print("\n--- Step 1: Backup ---")
    with tempfile.TemporaryDirectory() as tmp:
        backup_dir = do_backup(data_dir, Path(tmp), verbose)

        # Safety: verify backup has content before wiping
        if not any(backup_dir.iterdir()):
            print("ERROR: Backup directory is empty. Aborting E2E to prevent data loss.")
            sys.exit(1)

        # Step 2: Wipe data dir
        # ... rest unchanged, but wrap wipe+restore in try/except
        try:
            # wipe + restore
        except Exception:
            print("ERROR: Restore failed! Attempting to recover from backup...", file=sys.stderr)
            do_restore(backup_dir, data_dir, verbose)
            raise
```

### WR-03: toggleEventDetail targets wrong element, toggles hidden on unrelated children

**File:** `pmacs/web/static/app.js:860-865`
**Issue:** The `toggleEventDetail` function uses a broad selector that matches any element with "hidden" in its class:
```javascript
function toggleEventDetail(el) {
    var detail = el.querySelector(".event-detail, [class*='hidden']");
```
The `[class*='hidden']` selector matches ANY descendant element that has "hidden" anywhere in its class string -- including the parent event row itself if it were hidden. In the debug.html template, the detail container uses `class="hidden mt-3 ml-16 space-y-2"` (line 58), so `[class*='hidden']` works but is fragile. If any child element inside the detail section has `hidden` in its class (e.g., a badge like `bg-red-50` with `overflow-hidden`), it would match incorrectly.
**Fix:** Use a specific class or data attribute instead:
```javascript
function toggleEventDetail(el) {
    var detail = el.querySelector(".event-detail-row");
    if (detail) {
        detail.classList.toggle("hidden");
    }
}
```
And update `debug.html:58` to use `class="event-detail-row hidden mt-3 ml-16 space-y-2"`.

### WR-04: SSE reconnect uses fixed 5s delay with no clean shutdown path

**File:** `pmacs/web/static/app.js:53`
**Issue:** The `setTimeout(connectSSE, 5000)` creates a timer that is never tracked or cleared. If `connectSSE` is called manually (e.g., after the page has been idle), multiple reconnect timers can stack, creating duplicate EventSource connections. The `eventSource.close()` at line 31 handles the old connection but does not cancel the pending timer.
**Fix:** Track the timer ID and clear it on re-entry:
```javascript
var sseReconnectTimer = null;

function connectSSE() {
    if (sseReconnectTimer) {
        clearTimeout(sseReconnectTimer);
        sseReconnectTimer = null;
    }
    if (eventSource) {
        eventSource.close();
    }
    // ... onerror:
    sseReconnectTimer = setTimeout(connectSSE, 5000);
}
```

## Info

### IN-01: console.log left in production code

**File:** `pmacs/web/templates/pipeline.html:235`
**Issue:** `console.log('Move', ticker, 'to verdict', verdict);` is left in the kanban drop handler. This is fine for debugging but should be removed or converted to conditional debug logging before LIVE-READY.
**Fix:** Remove the line or gate it behind a debug flag: `if (window.__PMACS_DEBUG) console.log(...)`.

### IN-02: TODO comments in app.js for unimplemented features

**File:** `pmacs/web/static/app.js:395,399`
**Issue:** Two `// TODO:` comments mark unimplemented backend endpoints (`/api/cycle/start`, cycle compare modal). These are acceptable placeholders for Phase 15 but should be tracked for completion.
**Fix:** No code change needed. Ensure these are tracked in a follow-up task.

### IN-03: Unused _find_project_root result in audit_chain_verify.py

**File:** `ops/audit_chain_verify.py:99`
**Issue:** `_find_project_root()` is called a second time on line 99 even though the project root was already needed (and potentially computed) when resolving the log path. This is minor redundancy, not a bug.
**Fix:** Cache the result:
```python
project_root = _find_project_root()
# ... use project_root for both log_path and sys.path
```

### IN-04: Skeleton loading uses animate-pulse which may not respect reduced-motion from Tailwind CDN

**File:** `pmacs/web/components/loading_state.html:9-16`
**Issue:** The `animate-pulse` Tailwind class is used for skeleton loading placeholders. The `style.css` reduced-motion rule (line 55-87) targets `animation-duration` and `animation-iteration-count` globally, which should cover `animate-pulse`. However, the Tailwind CDN runtime generates `animate-pulse` via keyframe, and the `!important` on `animation-duration: 0.01ms` may not fully suppress the visual flash in all browsers.
**Fix:** Add an explicit override in style.css:
```css
@media (prefers-reduced-motion: reduce) {
    .animate-pulse {
        animation: none !important;
        opacity: 0.6; /* static placeholder */
    }
}
```

---

_Reviewed: 2026-05-12T23:14:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
