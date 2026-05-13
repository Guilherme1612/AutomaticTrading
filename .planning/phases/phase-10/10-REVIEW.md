---
phase: 10-broker-wizard-sse
reviewed: 2026-05-13T15:45:00Z
depth: standard
files_reviewed: 13
files_reviewed_list:
  - pmacs/execution/adapter.py
  - pmacs/execution/alpaca_paper.py
  - pmacs/execution/service.py
  - pmacs/storage/dead_letter.py
  - pmacs/nervous/sse_publisher.py
  - pmacs/nervous/api.py
  - pmacs/web/routes/wizard.py
  - pmacs/installer/steps/verify_llm.py
  - pmacs/installer/steps/verify_data.py
  - pmacs/installer/steps/totp_enroll.py
  - pmacs/execution/catastrophe_net.py
  - pmacs/agents/schemas_json/__init__.py
  - pyproject.toml
findings:
  critical: 2
  warning: 5
  info: 6
  total: 13
status: issues_found
---

# Phase 10 Code Review

**Reviewed:** 2026-05-13T15:45:00Z
**Depth:** standard
**Files Reviewed:** 13
**Status:** issues_found

## Summary

Reviewed 13 files across broker integration, wizard UI, SSE resume, dead-letter queue, and Ollama JSON schemas. Found 2 critical issues, 5 warnings, and 6 info items. The two critical issues are runtime crashes: an unregistered error code that will raise `ValueError` whenever a catastrophe-net stop fails, and a missing SYSTEM_EVENT_TYPES entry that will crash on every successful stop placement when `cycle_id` is empty. The broker adapter architecture (ABC + factory + isolation) is well-structured. SSE ring buffer and Last-Event-ID resume are correctly implemented. Dead-letter backoff logic is sound. JSON schemas use proper `additionalProperties: false` throughout.

## Critical (must fix)

### C1: `CATASTROPHE_NET_FAILED` error_code not in registry -- runtime ValueError

**File:** `pmacs/execution/service.py:232-234`
**What:** `service.py:233` uses `error_code="CATASTROPHE_NET_FAILED"` in a `log_debug()` call at CRITICAL level. The error code registry (`pmacs/logsys/error_classifier.py`) only contains `CATASTROPHE_CANCEL_FAILED`. The `log_debug` function validates `error_code` against `VALID_ERROR_CODES` for WARN+ levels and raises `ValueError` if the code is not found. This means every catastrophe-net stop failure will crash with an uncaught `ValueError` instead of logging the failure.
**Risk:** When a catastrophe-net stop fails (the most dangerous scenario -- an unprotected open position), the error handler itself crashes. The operator gets no debug event and no CRITICAL log. This masks the exact situation that needs the most visibility.
**Fix:** Add `CATASTROPHE_NET_FAILED` to the error classifier registry:

```python
# In pmacs/logsys/error_classifier.py, add after line 41:
CATASTROPHE_NET_FAILED = "CATASTROPHE_NET_FAILED"

# And add to VALID_ERROR_CODES frozenset (around line 104):
CATASTROPHE_NET_FAILED,
```

### C2: `CATASTROPHE_NET_PLACED` not in SYSTEM_EVENT_TYPES -- crashes when cycle_id is empty

**File:** `pmacs/execution/service.py:205-216`
**What:** `service.py:206` uses event type `CATASTROPHE_NET_PLACED` in a `log_debug()` call. This event type is NOT in `SYSTEM_EVENT_TYPES` in `pmacs/logsys/debug_log.py`. The `log_debug` function checks `if cycle_id is None and event_type not in SYSTEM_EVENT_TYPES` and raises `ValueError`. While `plan.cycle_id` defaults to `""` (not `None`), the semantic intent is that a non-None empty string bypasses the guard. However, if `cycle_id` is ever explicitly passed as `None`, or if the validation is tightened to check empty strings, this crashes on every successful catastrophe-net placement.
**Risk:** In the current code, `plan.cycle_id` defaults to `""` so this won't crash today. But it violates the architectural contract -- events not in SYSTEM_EVENT_TYPES should always have a meaningful cycle_id. An empty string is semantically equivalent to None.
**Fix:** Add `CATASTROPHE_NET_PLACED` to `SYSTEM_EVENT_TYPES` in `pmacs/logsys/debug_log.py`, since catastrophe-net placement is an infrastructure-level event:

```python
# In pmacs/logsys/debug_log.py SYSTEM_EVENT_TYPES, add:
"CATASTROPHE_NET_PLACED",
```

## High (should fix)

### H1: TOTP secret returned in HTTP response body (phase 1)

**File:** `pmacs/installer/steps/totp_enroll.py:132-136`
**What:** The `run()` function returns `{"secret": secret}` in the response dict on phase 1 (generate). The wizard route at `pmacs/web/routes/wizard.py:200` passes this directly as template context: `{"totp_result": result}`. If the template renders this into HTML, the TOTP secret is exposed in the page source. The CLAUDE.md anti-pattern rule states: "Logging secrets -- never log API keys, TOTP secrets, signing keys."
**Risk:** TOTP secret exposed in HTTP response body and potentially rendered in HTML. While this is a local-only system, the secret should only be conveyed via the QR code data URI, not as a raw string in the response.
**Fix:** Remove `secret` from the returned dict or ensure the template only uses it in a hidden form field (not rendered visibly). At minimum, do not include it in `totp_result` -- pass it separately via a session or encrypted cookie.

### H2: TOTP secret truncated in fallback SVG -- information loss

**File:** `pmacs/installer/steps/totp_enroll.py:69`
**What:** When the `qrcode` library is not installed, the fallback SVG renders `secret[:20]` followed by `...`. A base32 TOTP secret from `secrets.token_bytes(20)` is 32 characters. Truncating to 20 characters makes the secret impossible to manually enter into an authenticator app. If the SVG fallback is shown and the QR code cannot be scanned, the operator has no way to complete enrollment.
**Risk:** Operator cannot complete TOTP enrollment if `qrcode` is not installed and QR scanning fails.
**Fix:** Either render the full secret in the fallback SVG (wrapped across multiple lines), or make `qrcode` a required dependency in `pyproject.toml`.

### H3: Wizard step 4 collects credentials but discards them

**File:** `pmacs/web/routes/wizard.py:152-156`
**What:** Step 4 (keychain) collects form data into `creds` dict at line 152, but then returns `{"ok": True, "context": {}}` without using `creds` at all. The credentials are silently dropped.
**Risk:** Operator enters API keys during wizard setup, believes they are stored, but they are discarded. The system then runs without valid credentials, causing broker adapter failures at runtime.
**Fix:** Pass `creds` to a credential storage function (keyring/security CLI) before returning success. Or return `{"ok": False}` until credential storage is implemented.

### H4: `_store_totp_secret` silently falls back on non-macOS

**File:** `pmacs/installer/steps/totp_enroll.py:176-179`
**What:** When `security` CLI is not found (FileNotFoundError), the code catches the exception and silently passes. The TOTP secret is not stored anywhere. The wizard step returns `{"ok": True}` (line 118) even though the secret was never persisted. On next startup, the system has no TOTP secret and the operator is locked out of TOTP-protected operations.
**Risk:** TOTP enrollment appears successful but secret is not stored on non-macOS systems. Operator cannot use TOTP-gated features (kill switch disengage, mutation promote).
**Fix:** Either (a) raise an error so the wizard reports failure, or (b) implement a file-based fallback (with appropriate permissions warning). At minimum, return `{"ok": False}` from `run()` if storage failed.

### H5: `cancel_catastrophe_net` calls `broker.cancel_order()` synchronously

**File:** `pmacs/execution/catastrophe_net.py:113`
**What:** `cancel_catastrophe_net()` calls `broker.cancel_order(order_id)` as a regular synchronous call, but `BrokerAdapter.cancel_order()` is an `async` method. This will return a coroutine object (which is truthy) rather than actually executing the cancellation. The result will appear successful while the cancel never reaches the broker.
**Risk:** Catastrophe-net stop cancellation silently fails. The old stop remains on the broker while a new position is opened, potentially causing an unwanted sell. This is a correctness bug in the exit path.
**Fix:** Make `cancel_catastrophe_net` async and await the call:

```python
async def cancel_catastrophe_net(order_id: str, broker=None) -> CancelResult:
    # ...
    try:
        await broker.cancel_order(order_id)
        # ...
```

And update `execute_exit` to be async as well, with `await cancel_catastrophe_net(...)`.

## Medium (nice to fix)

### M1: `MockAdapter.poll_fill` returns zero-filled TradeResult

**File:** `pmacs/execution/adapter.py:92-102`
**What:** `poll_fill` returns a `TradeResult` with `ticker="UNKNOWN"`, `filled_quantity=0`, `filled_price_usd=0.0`. The caller in `service.py:140-152` patches this up, but only when `ticker == "UNKNOWN"`. If the plan's price is 0 (edge case), the mock returns `filled_price_usd=0.0` which could pass through.
**Fix:** Have `MockAdapter.poll_fill` accept an optional `plan` parameter, or have `submit_order` store a mapping from order_id to plan data that `poll_fill` can use. Alternatively, always apply the plan-data override regardless of the "UNKNOWN" check.

### M2: SSE event_generator parses JSON twice per frame

**File:** `pmacs/nervous/api.py:206-220` and `pmacs/nervous/api.py:232-256`
**What:** Every SSE frame goes through `json.loads()` in the event generator to parse and filter. The frame was already JSON-serialized by `SSEPublisher.publish()`. This is redundant work for every event on every connected client.
**Fix:** Consider storing the parsed event dict alongside the frame string in the ring buffer, or embedding stream/type/id as SSE fields directly in the frame so the generator can filter without full JSON parse.

### M3: Dead-letter `enqueue` uses `json.dumps` instead of `canonical_json`

**File:** `pmacs/storage/dead_letter.py:77-84`
**What:** The `enqueue` method serializes the payload with `json.dumps(payload, sort_keys=True)`. The CLAUDE.md anti-pattern rule says: "json.dumps(payload) for audit -- MUST use canonical_json(payload)." While dead_letter entries are not audit entries per se, they contain payloads that may be re-processed later. Using non-canonical serialization could cause subtle mismatches if the payload is compared against a canonical version.
**Fix:** Use `from pmacs.data.canonical import canonical_json` and `canonical_json(payload)` for consistency.

### M4: `verify_data.py` sends `"apiKey": "test"` to Polygon

**File:** `pmacs/installer/steps/verify_data.py:75`
**What:** The data source connectivity check sends a hardcoded `"apiKey": "test"` query parameter to Polygon. This will always fail authentication. The intent is to check connectivity (401/403 = reachable), but the parameter name `apiKey` may not match Polygon's expected parameter (`apiKey` is correct for Polygon, so this is actually fine).
**Fix:** No fix needed for correctness. Consider adding a comment explaining this is intentional (401 = connectivity OK).

## Low (cosmetic)

### L1: `TradePlan.created_at` uses deprecated `datetime.utcnow`

**File:** `pmacs/schemas/trade.py:41`
**What:** `default_factory=datetime.utcnow` uses the deprecated `datetime.utcnow()`. Python 3.12+ warns about this.
**Fix:** Use `default_factory=lambda: datetime.now(timezone.utc)` for consistency with the rest of the codebase.

### L2: Wizard uses cookie for state checkpoint

**File:** `pmacs/web/routes/wizard.py:40-44`
**What:** The comment says "production: SQLite" but cookie-based state is currently used. Cookies can be manipulated by the client, allowing the operator to skip steps.
**Fix:** Implement SQLite-backed wizard state before shipping to production. The code comment acknowledges this.

### L3: `api.py` audit logging creates a new `AuditWriter` per TOTP verify call

**File:** `pmacs/nervous/api.py:135-143`
**What:** Each TOTP verification creates a new `AuditWriter("logs/audit.log")`, writes one entry, and closes it. This recovers the hash chain on every call (file scan) and opens/closes the file descriptor. Under load, this is wasteful.
**Fix:** Use a module-level or app-state `AuditWriter` instance that persists for the lifetime of the process.

### L4: JSON schemas lack `$id` for deduplication

**Files:** `pmacs/agents/schemas_json/*.json`
**What:** All 9 JSON schemas have `$schema` but lack `$id`. This makes it harder for schema validators to cache/deduplicate.
**Fix:** Add `"$id": "pmacs://schemas/{persona}.json"` to each schema. Very low priority.

### L5: `alpaca_paper.py` logs plan.direction.value not Alpaca side

**File:** `pmacs/execution/alpaca_paper.py:79`
**What:** The log line logs `plan.direction.value` (which is "BUY"/"SELL") alongside `plan.ticker` and `plan.quantity`. The Alpaca `order.side` is available as `order.side` and would be more useful for debugging broker-side issues.
**Fix:** Log both or switch to logging `side` from the Alpaca order response.

### L6: SSE publisher doesn't prune the ring buffer until it overflows

**File:** `pmacs/nervous/sse_publisher.py:82-83`
**What:** The ring buffer uses `self._event_log = self._event_log[-self.RING_BUFFER_SIZE:]` which creates a new list every time the size exceeds 1000. This is a list copy on every publish after the buffer fills.
**Fix:** Consider using `collections.deque(maxlen=1000)` for O(1) append with automatic pruning. Very low priority -- only matters under high event throughput.

## Passed Checks

- **Architecture compliance:** BrokerAdapter ABC properly isolates broker communication. Only `alpaca_paper.py` imports the alpaca SDK, consistent with Architecture.md S4.1.
- **Ed25519 signing:** `service.py` verifies signatures against a trusted public key before processing trades. LLMs cannot directly cause trades (Non-Negotiable #1).
- **Catastrophe-net percentage:** Uses `CATASTROPHE_NET_PCT` constant (0.15 = 15%), matching the spec.
- **SSE Last-Event-ID resume:** Ring buffer correctly stores `(event_id, frame)` tuples. `get_events_since()` properly filters `eid > last_id`. Keepalive comments sent on 30s timeout.
- **Dead-letter backoff schedule:** Matches Architecture.md S14.1: `[1, 5, 30, 300, 3600, 86400]` with 6 max attempts.
- **Dead-letter exhausted event:** Uses `DEAD_LETTER_EXHAUSTED` which IS in `SYSTEM_EVENT_TYPES`, so `cycle_id=None` is acceptable.
- **Rate limiting:** TOTP verify endpoint uses `BUCKETS["totp_verify"].acquire()` -- no custom rate-limit logic.
- **SQL injection prevention:** All dead_letter queries use parameterized SQL (`?` placeholders).
- **Pydantic v2 compliance:** All models use `ConfigDict`, `BaseModel`, `Field`. No `from pydantic.v1` imports found.
- **JSON schemas:** All 9 schemas use `additionalProperties: false` and `required` arrays. Proper draft-07 format.
- **TOTP implementation:** Uses `hmac.compare_digest` for timing-safe comparison. 30s period, 6 digits, SHA-1 per RFC 6238.
- **Session management:** 256-bit tokens via `secrets.token_hex(32)`. Single active session with 24h expiry.
- **Kill switch integration:** `catastrophe_net.py` engages kill switch on broker cancellation failure, per Architecture.md S11.5.
- **`verify_llm.py`:** Proper timeout handling, health check before completion test, all error paths return structured failures.

---

_Reviewed: 2026-05-13T15:45:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
