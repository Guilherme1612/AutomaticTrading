# Code Review: Web Routes (settings, wizard, pipeline, cortex, universe)

**Reviewed:** 2026-05-27T13:06:00Z
**Depth:** standard
**Files Reviewed:** 10
**Status:** issues_found

## Summary

Reviewed five web route modules and their corresponding test files. Found two critical bugs (status value mismatch makes mutation promote/rollback always fail; wizard mode transition silently ignores errors), several security concerns (TOTP bypass surface, credentials logged in error messages, no rate limiting on TOTP verification), and multiple code quality issues. Test coverage is surface-level only -- E2E tests check HTML rendering but none verify API behavior for TOTP-gated write endpoints or error paths.

---

## Critical Issues

### CR-01: Mutation status value mismatch -- promote/rollback always fail silently

**File:** `pmacs/web/routes/settings.py:426,507`
**Also:** `pmacs/web/data.py:523`

**Issue:** The mutation daemon (`pmacs/mutation/daemon.py:217`) inserts proposals with status `'PROPOSED'` (uppercase). The settings routes query for `status = 'pending'` (lowercase). The `mutation_proposals` table default is also `'PROPOSED'`. This means:

- `mutation_promote()` at line 426 queries `WHERE status = 'pending'` -- will match zero rows every time.
- `mutation_rollback()` at line 507 queries `WHERE status = 'approved'` -- this succeeds only after a prior promote, but promote itself never succeeds.
- `data.py:523` `get_mutation_candidates()` uses `WHERE status IN ('pending', 'approved', 'rejected')` -- also returns nothing.
- The diff endpoint at line 537 queries `mutation_proposals` by `id` alone, so it works, but all state-mutating operations are broken.

The operator would see "Candidate not found or not pending" on every attempt to promote or rollback a mutation. The mutation engine is an advisor-only system per spec, but if the operator cannot approve mutations through the dashboard, the flywheel is non-functional.

**Fix:**
```python
# settings.py line 426: change 'pending' to 'PROPOSED'
"WHERE id = ? AND status = 'PROPOSED'",

# settings.py line 456: change to include 'PROPOSED'
"WHERE id = ? AND status IN ('PROPOSED', 'approved')",

# settings.py line 507: also allow rolling back from 'PROPOSED'
# (keep 'approved' since that is set by promote, which now works)

# data.py line 523: change to match canonical status values
WHERE status IN ('PROPOSED', 'approved', 'rejected')
```

Alternatively, normalize all status values to lowercase everywhere. The canonical source is `pmacs/storage/sqlite.py:161` which uses `'PROPOSED'`.

---

### CR-02: Wizard mode promotion silently ignores failure, shows success

**File:** `pmacs/web/routes/wizard.py:545-587`

**Issue:** The `transition_mode()` call at line 553 is inside a try/except that catches all exceptions at line 584 (`except Exception as exc:`). If `transition_mode()` raises `ValueError` (e.g., invalid transition, current mode is not INSTALLING), the exception is caught, `promotion_result["promoted"]` stays `False`, and the error is stored in `promotion_result["error"]`. However, the caller at line 587 returns `{"ok": promotion_result.get("promoted", False)}`. If the DB file exists but the transition fails, the wizard still advances to step 11 (the complete screen) because `next_step` is already set to `TOTAL_STEPS` before the result is checked (lines 178-179). The user sees the "Setup Complete" screen even though the mode was never actually promoted to PAPER.

Additionally, the `mode_history` table INSERT at lines 563-569 runs after `transition_mode()` succeeds, but if this INSERT fails, the mode transition happened without an audit record -- violating Non-Negotiable #3 (every state transition must be hash-chained).

**Fix:**
```python
# Wrap mode transition + mode_history insert in a single transaction
# or verify mode_history write succeeded before marking wizard complete
try:
    mt = transition_mode(...)
    # Verify transition returned valid result
    if mt is None:
        promotion_result["error"] = "transition_mode returned None"
        return {"ok": False, "context": {"promotion_result": promotion_result}}

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO mode_history (from_mode, to_mode, reason, triggered_by, changed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (mt.from_mode.value, mt.to_mode.value, mt.reason, mt.triggered_by,
             mt.changed_at.isoformat()),
        )
        conn.commit()
    except Exception as db_exc:
        promotion_result["error"] = f"Mode transition succeeded but audit failed: {db_exc}"
        return {"ok": False, "context": {"promotion_result": promotion_result}}
    finally:
        conn.close()
except ValueError as exc:
    promotion_result["error"] = f"Invalid mode transition: {exc}"
    return {"ok": False, "context": {"promotion_result": promotion_result}}
```

---

## Warnings

### WR-01: TOTP verification endpoint has no rate limiting

**File:** `pmacs/web/routes/cortex.py:171-221`

**Issue:** The `/api/totp/verify` endpoint accepts unlimited verification attempts with no rate limiting or lockout mechanism. An attacker with access to the local dashboard (loopback only, per spec) could brute-force the 6-digit TOTP code. With 1,000,000 possible codes and a 30-second window, automated attempts at even modest rates could find the valid code.

The PMACS spec says the system is "local-only" and "loopback only", which limits the attack surface. However, the CSRF middleware only checks POST requests have matching cookie/header tokens, which any XSS in the dashboard templates could provide. Combined with no rate limit, this is a real risk.

**Fix:** Add attempt tracking (e.g., count per IP or per session, lock out after 5 failed attempts for 60 seconds). This should use the project's rate-limiting mechanism (`BUCKETS["source"].acquire()` per Architecture.md anti-pattern rules -- custom rate-limit logic is forbidden).

```python
# In totp_verify(), before calling verify_totp:
# Use the canonical rate-limiting mechanism
from pmacs.ratelimit import BUCKETS
if not BUCKETS["totp_verify"].acquire():
    return JSONResponse(
        {"verified": False, "error": "Too many attempts, please wait"},
        status_code=429,
    )
```

---

### WR-02: Exception messages may leak sensitive information in API responses

**File:** `pmacs/web/routes/settings.py:415,499`
**Also:** `pmacs/web/routes/cortex.py:158`, `pmacs/web/routes/universe.py:130,153,178,198`

**Issue:** Multiple endpoints return `str(exc)` in JSON error responses. In settings.py lines 415 and 499, TOTP verification failures include the exception message: `f"TOTP verification failed: {exc}"`. If the underlying keychain or TOTP module throws an exception containing the secret path, key ID, or other internal details, these would be exposed in the HTTP response body.

In universe.py lines 130, 153, 178, 198, `str(exc)` is returned directly from catch-all handlers. Database errors could leak schema information.

Per CLAUDE.md anti-patterns: "Logging secrets -- never log API keys, TOTP secrets, signing keys." The same principle applies to returning them in HTTP responses.

**Fix:**
```python
# Replace generic exception leak with safe error messages
except Exception as exc:
    # Log the full error server-side
    import logging
    logging.getLogger("pmacs.web").error("TOTP verification failed: %s", exc, exc_info=True)
    # Return generic message to client
    return JSONResponse(
        {"ok": False, "error": "TOTP verification failed"},
        status_code=403,
    )
```

---

### WR-03: `mutation_reject` does not require TOTP -- inconsistent with other mutation actions

**File:** `pmacs/web/routes/settings.py:448-467`

**Issue:** `mutation_promote` (line 386) and `mutation_rollback` (line 470) both require TOTP verification, but `mutation_reject` (line 448) does not. While rejecting a mutation is arguably less dangerous than promoting one, an unauthorized rejection of a valid mutation candidate would prevent the operator from seeing it. This is inconsistent with the spec's principle that ALL mutation actions require operator TOTP (CLAUDE.md: "ALL mutations require operator TOTP. No exceptions.").

**Fix:** Add TOTP verification to `mutation_reject`:
```python
@router.post("/api/mutation/reject")
async def mutation_reject(req: MutationActionRequest):
    """Reject a mutation candidate (TOTP-gated per CLAUDE.md)."""
    ok, err = _verify_totp(req.totp_code)
    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=403)
    # ... rest of handler
```

---

### WR-04: `cycle_start` endpoint has no TOTP gate, no authorization check

**File:** `pmacs/web/routes/pipeline.py:248-272`

**Issue:** The `/api/cycle/start` endpoint triggers a new analysis cycle with no authentication or TOTP verification. Anyone who can reach the dashboard (even via CSRF if the token is obtained) can trigger cycles. While the spec says "manual trigger" is an operator action (Source.md S15), this endpoint accepts arbitrary `trigger` values from the request body (`trigger: str = "manual"`), allowing the caller to misrepresent the trigger source.

**Fix:** Either TOTP-gate this endpoint (consistent with other write actions) or at minimum validate the trigger value against an allowed set.

```python
class CycleStartRequest(BaseModel):
    trigger: str = "manual"

    @model_validator(mode="after")
    def validate_trigger(self):
        if self.trigger not in ("manual",):
            raise ValueError(f"Invalid trigger: {self.trigger}")
        return self
```

---

### WR-05: `_get_cost_state` returns cached data from `db` that was already closed

**File:** `pmacs/web/routes/settings.py:618-646`

**Issue:** In `settings_page()` at line 113-118, the function opens a `db` connection, then closes it in the finally block at line 118. Then at lines 153-154, it calls `_get_cost_state(cfg)` and `_get_pricing_table(db)`. The `_get_cost_state` function opens its own connection internally (line 619), so that works. But `_get_pricing_table(db)` at line 153 receives the already-closed `db` from the outer try/finally block. This will fail at runtime because `db.close()` was already called.

Looking more carefully, the `db` is closed inside the `finally` at line 118, but `_get_pricing_table(db)` is called at line 153 which is inside the same `try` block starting at line 109, before the `finally`. So the ordering is: open db (113) -> close db (118 finally) -> _get_pricing_table(db) (153). Wait -- the `finally` at line 118 closes `db`, but lines 120-154 are AFTER the finally block, outside the inner try/finally. So `db` is closed when `_get_pricing_table` is called. This is a use-after-close bug.

**Fix:** Move `_get_pricing_table(db)` inside the try/finally block, or pass `cfg.sqlite_path` and open a new connection.

```python
# Move pricing table query inside the db lifecycle
try:
    mutation_candidates = data_layer.get_mutation_candidates(db)
    recent_mutations = data_layer.get_recent_mutations(db)
    pricing_table = _get_pricing_table(db)  # <-- moved here
finally:
    db.close()
```

---

### WR-06: `_save_registry` atomic write race condition

**File:** `pmacs/web/routes/settings.py:31-41`

**Issue:** The `_save_registry` function acquires the file lock AFTER opening the temp file for writing. The lock is on the temp file descriptor, not on the target file. Two concurrent processes could both open different temp files, write to them, and then race on `.replace()`. The `fcntl.flock` on the temp file does not protect the target file.

**Fix:** Lock the target file (or a separate lock file) instead of the temp file:
```python
def _save_registry(registry: dict) -> None:
    lock_path = _REGISTRY_PATH.with_suffix(".lock")
    with open(lock_path, "w") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            tmp_path = _REGISTRY_PATH.with_suffix(".tmp")
            with open(tmp_path, "w") as f:
                _json.dump(registry, f, indent=2)
                f.flush()
            tmp_path.replace(_REGISTRY_PATH)
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
```

---

## Info

### IN-01: Test suite lacks API-level security tests

**Files:** `tests/unit/test_web_routes.py`, `tests/e2e/test_settings_page.py`, `tests/e2e/test_cortex_page.py`

**Issue:** The E2E tests only verify HTML rendering (section headings present, CSS classes exist). No test verifies:
1. Mutation promote/reject/rollback API endpoints with TOTP codes
2. Kill switch engage/disengage API behavior
3. Cost caps update with TOTP verification
4. Universe add/remove with TOTP verification
5. That write endpoints reject requests without valid TOTP
6. That the status mismatch bug (CR-01) would be caught

The unit test `test_web_routes.py` only checks HTTP 200 on GET routes. There are zero tests for any POST endpoint in the web routes.

**Fix:** Add integration tests that:
- POST to mutation endpoints with invalid/missing TOTP, verify 403
- POST to kill switch disengage without TOTP, verify 403
- Test the full TOTP flow with a known secret
- Verify status transitions in mutation_proposals table

---

### IN-02: Duplicate TOTP verification logic across three files

**Files:** `pmacs/web/routes/settings.py:767-781`, `pmacs/web/routes/cortex.py:130-158`, `pmacs/web/routes/universe.py:99-109`

**Issue:** TOTP verification is implemented independently in three different route files. `settings.py` has `_verify_totp()` returning a tuple, `universe.py` has `_verify_totp()` returning a bool, and `cortex.py` has inline verification. If the TOTP implementation changes, all three must be updated. This violates DRY and increases the risk of inconsistent behavior.

**Fix:** Extract a shared `verify_totp_for_request(code: str)` utility in a common module (e.g., `pmacs/web/security.py`) and import it in all route files.

---

### IN-03: Hardcoded default cost caps in `_get_cost_state`

**File:** `pmacs/web/routes/settings.py:597-598`

**Issue:** Default caps `$2.00` daily and `$30.00` monthly are hardcoded. These should come from a shared constant (likely in `pmacs/constants.py` or `config/risk.toml`).

---

### IN-04: `test_pipeline_page.py:119` uses assignment expression in assert

**File:** `tests/e2e/test_pipeline_page.py:119`

**Issue:** `assert "border-dashed" in html if (html := resp.text) else False` -- the walrus operator assignment is used inside the assert. This works but is harder to read than assigning `html = resp.text` on a separate line. If `resp.text` is empty (which shouldn't happen but defensively), the assert becomes `assert False` with no diagnostic message.

---

### IN-05: Wizard step 5 does not close DuckDB adapter

**File:** `pmacs/web/routes/wizard.py:364-368`

**Issue:** `DuckDBAdapter` is created and `initialize()` is called, but the adapter is never closed. DuckDB holds a file lock and may not release it if the process continues.

---

## Findings Summary

| Severity | Count | Key Issues |
|----------|-------|------------|
| Critical | 2 | Mutation status value mismatch (all promote/rollback broken); Wizard mode transition failure hidden |
| Warning | 6 | TOTP brute-force possible; exception leaks; mutation_reject missing TOTP; cycle_start ungated; db use-after-close; registry write race |
| Info | 5 | No API security tests; duplicate TOTP logic; hardcoded caps; test readability; unclosed DuckDB adapter |

**Priority fix order:**
1. CR-01 (mutation status mismatch) -- renders mutation engine UI completely non-functional
2. CR-02 (wizard silent failure) -- user sees success when mode was not promoted
3. WR-05 (db use-after-close) -- likely causes runtime errors in settings page
4. WR-01 (TOTP rate limiting) -- security hardening
5. WR-03 (mutation_reject TOTP) -- spec compliance

---

_Reviewed: 2026-05-27T13:06:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
