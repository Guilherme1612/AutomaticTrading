---
phase: 08-polish
fixed_at: 2026-05-12T23:21:00Z
review_path: .planning/phases/phase-8/08-REVIEW.md
iteration: 1
findings_in_scope: 5
fixed: 5
skipped: 0
status: all_fixed
---

# Phase 8: Code Review Fix Report

**Fixed at:** 2026-05-12T23:21:00Z
**Source review:** .planning/phases/phase-8/08-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 5
- Fixed: 5
- Skipped: 0

## Fixed Issues

### CR-01: XSS via Jinja2 template injection in empty_state.html onclick handler

**Files modified:** `pmacs/web/components/empty_state.html`
**Commit:** dc6db79
**Applied fix:** Replaced `onclick="{{ empty_cta_action }}"` with `data-action="{{ empty_cta_action }}"` and added `empty-cta-btn` class. Removes inline JS injection vector entirely; consumers should bind click handlers via the class selector.

### WR-01: SSE infinite reconnect loop on persistent server failure

**Files modified:** `pmacs/web/static/app.js`
**Commit:** b87af15
**Applied fix:** Added exponential backoff starting at 5s, capped at 60s, with max 20 retries. On `onopen`, retry count resets to 0. After max retries, shows persistent error toast and stops reconnecting.

### WR-04: SSE untracked reconnect timer

**Files modified:** `pmacs/web/static/app.js`
**Commit:** b87af15
**Applied fix:** Combined with WR-01 fix. Added `sseReconnectTimer` variable; `connectSSE` clears any pending timer on entry; `onerror` and catch block store the timer ID for tracking. Prevents stacked reconnect timers.

### WR-02: backup_verify.py do_e2e wipes data directory with no confirmation or safety check

**Files modified:** `ops/backup_verify.py`
**Commit:** 54179ae
**Applied fix:** Added backup emptiness check after backup step -- aborts E2E if backup directory is empty. Wrapped wipe+restore in try/except that attempts recovery restore from backup on failure before re-raising.

### WR-03: toggleEventDetail broad selector matches unrelated children

**Files modified:** `pmacs/web/static/app.js`, `pmacs/web/templates/debug.html`
**Commit:** e8e6b1f
**Applied fix:** Changed selector from `.event-detail, [class*='hidden']` to `.event-detail-row`. Added `event-detail-row` class to the detail div in debug.html. Eliminates false matches on any descendant with "hidden" in its class string.

---

_Fixed: 2026-05-12T23:21:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
