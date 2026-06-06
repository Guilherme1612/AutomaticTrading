---
phase: 13-llm-provider-switcher
reviewed: 2026-05-24T22:32:00Z
depth: standard
files_reviewed: 3
files_reviewed_list:
  - pmacs/web/templates/settings.html
  - pmacs/web/routes/settings.py
  - pmacs/installer/steps/verify_llm.py
findings:
  critical: 0
  high: 2
  medium: 4
  low: 3
  total: 9
status: issues_found
---

# Phase 13: Code Review Report -- LLM Provider Switcher

**Reviewed:** 2026-05-24T22:32:00Z
**Depth:** standard
**Files Reviewed:** 3
**Status:** issues_found

## Summary

Reviewed the new LLM provider switcher feature across the settings template, API routes, and installer verification step. The architecture is sound -- CSRF is handled globally, keyring is used for secrets, and TOTP gating is properly enforced for mutation operations. However, there are two high-severity bugs and several medium-severity issues worth addressing.

The most impactful bug is a broken side-by-side diff view: the JS reads `data.diff_html` (line 604 of settings.html) but the API returns `data.diff_rows` (line 550 of settings.py). The side-by-side view will silently show blank content for all mutation diffs.

The second high-severity issue is that the inference API endpoints lack any authentication or session binding. While CSRF tokens protect against cross-origin attacks, any same-origin script (or the browser console) can switch the LLM provider, save API keys, or change models without operator confirmation. For a system where "LLMs never sign trades" is a non-negotiable, silently swapping the inference backend is a meaningful trust boundary violation.

## High Issues

### HI-01: Side-by-side diff view broken -- `diff_html` field does not exist

**File:** `pmacs/web/templates/settings.html:604`
**Issue:** The `renderDiff('side-by-side')` branch assigns `container.innerHTML = data.diff_html`, but the API endpoint (`/api/mutation/{id}/diff`) returns `diff_rows` (an array of objects), not `diff_html`. The `diff_html` property will be `undefined`, rendering as blank text. The side-by-side diff view is completely non-functional.

The API docstring on line 499 of `settings.py` also incorrectly documents the return as containing `diff_html`.

**Fix (template JS):**
```javascript
// Replace line 604:
} else {
    // Build side-by-side HTML from diff_rows
    var rows = data.diff_rows || [];
    var html = '<table class="w-full text-xs font-mono">';
    html += '<tr class="text-zinc-500"><th class="text-left pb-2 w-1/2">Baseline</th><th class="text-left pb-2 w-1/2">Candidate</th></tr>';
    for (var i = 0; i < rows.length; i++) {
        var r = rows[i];
        html += '<tr class="' + r["class"] + '"><td class="pr-4 whitespace-pre">' + r.baseline + '</td><td class="whitespace-pre">' + r.candidate + '</td></tr>';
    }
    html += '</table>';
    container.innerHTML = html;
}
```

Also update the docstring at `settings.py:499` to say `diff_rows` instead of `diff_html`.

---

### HI-02: Inference API endpoints have no authentication or authorization

**File:** `pmacs/web/routes/settings.py:201-263`
**Issue:** The four inference endpoints (`/api/settings/inference/provider`, `/api/settings/inference/api-key`, `/api/settings/inference/model`, `/api/settings/inference/test`) accept unauthenticated POST requests. Any same-origin request can:
1. Switch the active LLM provider to a cloud provider (potentially routing LLM calls externally)
2. Save arbitrary API keys to the system keychain
3. Change the model configuration

While CSRF middleware prevents cross-origin attacks, there is no session check, no TOTP gate, and no authorization. Given PMACS Non-Negotiable #4 (local-only execution) and the trust implications of silently switching from a local llama-server to a cloud provider, these endpoints should at minimum verify the request originates from the dashboard session.

**Fix:** Add at minimum a session cookie check. For TOTP-gating (matching the pattern used for mutations), wrap the provider-switch and API-key endpoints with TOTP verification:
```python
@router.post("/api/settings/inference/provider")
async def set_inference_provider(request: Request, req: InferenceProviderRequest):
    # Verify session/TOTP for provider changes
    session = request.cookies.get("pmacs_session")
    if not session:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    # ... existing logic
```

## Medium Issues

### ME-01: Template event handlers embed unescaped variables in JS string contexts

**File:** `pmacs/web/templates/settings.html:210,214,218,248,282`
**Issue:** Template variables are embedded directly into `onclick`/`onchange` attributes inside single-quoted JS strings:
```html
onclick="viewDiff('{{ candidate.candidate_id }}')"
onchange="switchProvider('{{ provider.id }}')"
```

Jinja2's auto-escaping HTML-encodes `<` and `&` but does NOT escape single quotes or backslashes. If a candidate ID or provider ID ever contained a single quote (`'`) or backslash (`\`), the JS string literal would break, potentially allowing attribute injection. Currently the IDs in `model_registry.json` (e.g., `llama_server`, `openai`) are safe, and UUID-based candidate IDs are unlikely to contain quotes, but this is a defense-in-depth gap.

**Fix:** Use `| tojson` filter to produce properly JS-escaped strings, or use `data-*` attributes with `addEventListener`:
```html
<button data-candidate-id="{{ candidate.candidate_id }}"
        onclick="viewDiff(this.dataset.candidateId)">
```

---

### ME-02: `Keychain error: {exc}` may leak keyring implementation details

**File:** `pmacs/web/routes/settings.py:242`
**Issue:** Line 242 returns `f"Keychain error: {exc}"` to the client. The exception message from keyring may include file paths, backend names (e.g., macOS Keychain, KDE Wallet), or other internal details. Per CLAUDE.md anti-pattern: "Logging secrets -- never log API keys, TOTP secrets, signing keys" -- while this is not logging, exposing keyring internals in the HTTP response is a similar information leak.

**Fix:** Return a generic error message to the client and log the full exception server-side:
```python
except Exception as exc:
    import logging
    logging.getLogger("pmacs.web.settings").error("Keychain write failed: %s", exc)
    return JSONResponse(
        {"ok": False, "error": "Failed to save API key to system keychain"},
        status_code=500,
    )
```

---

### ME-03: Race condition on concurrent `model_registry.json` writes

**File:** `pmacs/web/routes/settings.py:24-31, 201-263`
**Issue:** `_load_registry()` and `_save_registry()` have no file locking. If two requests hit `/api/settings/inference/provider` and `/api/settings/inference/model` concurrently, one write can overwrite the other's changes (read-modify-write race). The registry file is small and contention is unlikely (single-operator system), but the window exists.

**Fix:** Use a threading lock for the registry file access:
```python
import threading
_registry_lock = threading.Lock()

def _save_registry(registry: dict) -> None:
    with _registry_lock:
        _REGISTRY_PATH.write_text(_json.dumps(registry, indent=2))
```

---

### ME-04: `resp.text[:200]` in error responses may leak API response bodies

**File:** `pmacs/web/routes/settings.py:342` and `pmacs/installer/steps/verify_llm.py:142`
**Issue:** When a cloud provider returns a non-200 response, the first 200 characters of the response body are forwarded to the client/caller. Cloud API error responses sometimes include request IDs, account identifiers, or rate limit details that should not be surfaced.

**Fix:** Return only the HTTP status code and a generic message:
```python
return JSONResponse(
    {"ok": False, "error": f"HTTP {resp.status_code} from {active}"},
    status_code=502,
)
```

## Low Issues

### LO-01: `renderDiff` unified view only escapes `<` but not `>` or `&`

**File:** `pmacs/web/templates/settings.html:599`
**Issue:** The unified diff rendering does `.replace(/</g,'&lt;')` but does not escape `>` to `&gt;` or `&` to `&amp;`. While `>` alone is not dangerous in HTML context, it is technically malformed HTML and could cause rendering issues with diff lines containing XML/HTML content (mutation targets could be prompt templates containing HTML-like syntax).

**Fix:**
```javascript
var escaped = lines[i].replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
html += '<span class="' + cls + '">' + escaped + '</span>\n';
```

---

### LO-02: `testConnection` does not disable button on network error path

**File:** `pmacs/web/templates/settings.html:707-733`
**Issue:** The `testConnection()` function sets `btn.disabled = true` at the start but there is no `finally`-equivalent in the Promise chain. If `fetch` itself throws (network error before getting a response), the `.catch` handler does reset the button, so this is actually handled. However, if the response is not valid JSON (`.json()` throws), the `.catch` handler also resets. This is fine, but worth noting that the error handling path is split across `.then` and `.catch` rather than using `async/await` with try/catch, which would be more maintainable.

**Fix:** This is low priority. Consider refactoring to `async/await` with `try/catch/finally` for clarity if the file is touched for other reasons.

---

### LO-03: `verify_llm.py` swallows keyring exceptions silently

**File:** `pmacs/installer/steps/verify_llm.py:95-96`
**Issue:** The bare `except Exception: pass` on line 95-96 silently swallows any keyring error, including configuration errors that would help the user diagnose why their API key is not found. The function then returns a generic "API key not found" message that does not distinguish between "key not set" and "keyring broken".

**Fix:**
```python
except Exception as exc:
    return {
        "ok": False,
        "message": f"Keyring access error for {active}: {exc}",
        "model_path": "",
    }
```

---

_Reviewed: 2026-05-24T22:32:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
