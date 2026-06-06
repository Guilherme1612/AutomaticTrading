# PMACS Security Audit Report

**Date:** 2026-05-30
**Scope:** Full codebase -- Five Non-Negotiables, 15 Anti-Patterns, injection vectors, secrets handling, process isolation, TOTP gating, pre-commit hooks
**Auditor:** Claude Code (security audit, gsd-secure-phase)
**Previous audit:** 2026-05-28 (SECURITY.md)
**Spec references:** Architecture.md S5, S13, S16; Source.md S4, S5; CLAUDE.md (Five Non-Negotiables)

---

## Executive Summary

Full source code audit of `pmacs/` against the Five Non-Negotiables, all 15 Anti-Patterns from
Architecture.md S16, and supplementary security checks. **Two previously HIGH/MEDIUM findings
have been remediated since the 2026-05-28 audit:**

- SEC-HIGH-02 (cloud LLM routing): **FIXED** -- `model_registry.json` active backend is now `"llama_server"` (local).
- SEC-MED-03 (write endpoints not TOTP-gated): **FIXED** -- all write endpoints now require TOTP verification.

**Two findings remain OPEN (1 MEDIUM, 1 LOW). 25 of 27 total findings PASS.**

All Five Non-Negotiables now **PASS**. All 15 Anti-Patterns **PASS**.

---

## OPEN Findings

### SEC-MED-02: Alpaca Credentials Passed as HTTP Headers (Logging Risk)

**Severity:** MEDIUM
**Category:** Secret exposure
**Files:** `pmacs/cortex/stop_loss_daemon.py:146-155`

**Description:**
The stop-loss daemon reads Alpaca API key and secret from Keychain (correct) but then
passes them as HTTP headers to `httpx.get()`:

```python
headers={
    "APCA-API-KEY-ID": api_key,
    "APCA-API-SECRET-KEY": secret,
}
```

If `httpx` logging is enabled at DEBUG level, these headers will appear in logs. The
keychain correctly scrubs secrets from error messages, but `httpx` has its own logging
that is not controlled by PMACS scrubbing.

**Remediation:** Configure httpx client with a custom logging filter that redacts
`APCA-API-KEY-ID` and `APCA-API-SECRET-KEY` from logged request headers.

**Status:** OPEN

---

### SEC-LOW-01: Exception Message Leaking in Web Responses

**Severity:** LOW
**Category:** Information disclosure
**Files:**
- `pmacs/web/routes/settings.py:396` (`str(exc)` in inference test response)
- `pmacs/web/routes/wizard.py:290, 360-408, 466-509, 587` (multiple `str(exc)` in responses)

**Description:**
Several web endpoints return `str(exc)` in JSON error responses. This can leak internal
implementation details (file paths, module names, stack information) to the client.

```python
return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
```

**Risk:** Low because the dashboard is loopback-only and single-operator. Defense-in-depth
violation only.

**Status:** OPEN (ACCEPTED)

---

## Previously OPEN -- Now FIXED

| Previous ID | Description | Status | Evidence |
|-------------|-------------|--------|----------|
| SEC-HIGH-02 | Active LLM backend routes through cloud | **FIXED** | `config/model_registry.json:39` now shows `"active": "llama_server"` |
| SEC-MED-03 | Write endpoints not TOTP-gated | **FIXED** | All write endpoints now verify TOTP (see SEC-PASS-26) |

---

## Previously FIXED (Confirmed Still Fixed)

| Previous ID | Description | Status |
|-------------|-------------|--------|
| SEC-CRIT-01 | Mutation promote/rollback TOTP bypass (broken import path) | **FIXED** -- confirmed imports use `pmacs.storage.keychain` |
| SEC-HIGH-01 | difflib.HtmlDiff XSS in mutation diff endpoint | **FIXED** -- confirmed uses `html.escape()` + unified_diff |
| SEC-MED-01 | Finnhub API key from environment variable | **FIXED** -- confirmed uses `read_key()` from Keychain |

---

## PASS Findings (Verified Secure)

### NN#1 -- LLMs Never Sign Trades

**Evidence:**
- `pmacs/execution/signing.py`: Ed25519 signing isolated in execution module only.
- `pmacs/execution/service.py`: UDS server verifies Ed25519 signature before accepting trade.
  Client-side `sign_and_send()` requires the private key (lines 290-291).
- `pmacs/agents/` directory: Zero imports of `execution.signing`, `execution.service`, or
  any trade submission code.
- No code path from agent output to trade signing without passing through deterministic
  engines (arbitration, sizing) and the UDS execution service.

**Verdict:** PASS

---

### NN#2 -- LLMs Never Math

**Evidence:**
- All probability combination, sizing, conviction, and arbitration logic in `pmacs/engines/`:
  `arbitration.py`, `sizing.py`, `conviction.py`.
- `pmacs/agents/` contains zero functions for `combine`, `arbitrate`, `size`, or `prob`.
- Three-layer validation (Grammar -> Pydantic -> Sanity) per Agents.md S3.

**Verdict:** PASS

---

### NN#3 -- Every State Transition Is Hash-Chained

**Evidence:**
- `pmacs/engines/state_machine.py`: Every transition writes via `AuditWriter.append()`.
- `pmacs/storage/audit.py`: Hash chain with `sha256(ts || prev_sha || event_type || canonical_json)`.
  `os.fsync()` after every write. Rotation preserves chain across files.
- `pmacs/data/canonical.py`: `canonical_json()` with `sort_keys=True`, `separators=(",",":")`,
  `allow_nan=False`.
- `pmacs/storage/audit.py:AuditVerifier`: Full and incremental chain verification.

**Verdict:** PASS

---

### NN#4 -- Local-Only Execution

**Evidence:**
- `ops/install_pf_rules.sh`: pf firewall blocks `_pmacs_inference` user from all outbound
  except loopback.
- `pmacs/cli.py`: All web servers start with `--host 127.0.0.1` (loopback only).
- No `0.0.0.0` bindings found anywhere in codebase.
- `pmacs-execution`: Unix Domain Socket (no network at all).
- `config/model_registry.json`: `"active": "llama_server"` -- routes to local `127.0.0.1:8080`.
- Zero telemetry, analytics, or phone-home code.

**Verdict:** PASS

---

### NN#5 -- Operator Owns Kill Switch

**Evidence:**
- `pmacs/cortex/kill_switch.py:engage()`: No TOTP required (safer to over-trigger).
- `pmacs/cortex/kill_switch.py:disengage()`: Requires `verify_totp()` with valid code.
  Re-checks underlying trigger condition before allowing disengagement.
- `pmacs/cortex/totp.py:verify_totp()`: Uses `hmac.compare_digest()` (timing-safe).
  Window of +/-1 period (30s). Proper base32 decoding. `secrets.token_bytes(20)` for generation.
- Kill switch state persisted in SQLite singleton with `CHECK (id = 1)` constraint.
- `pmacs/web/routes/cortex.py`: Disengage endpoint verifies TOTP server-side.
- Mutation flagging on kill switch engagement (kill_switch.py:160-179).

**Verdict:** PASS

---

### Anti-Pattern #1 -- holding.state = "ABORTED_LLM"

**Evidence:** `grep 'holding\.state\s*=(?!\s*=)' pmacs/` -- zero matches outside `state_machine.py`.

**Verdict:** PASS

---

### Anti-Pattern #2 -- json.dumps(payload) for audit

**Evidence:** `pmacs/storage/audit.py:100` uses `canonical_json(payload)` exclusively. No `json.dumps` in audit.py.

**Verdict:** PASS

---

### Anti-Pattern #3 -- Custom rate-limit logic

**Evidence:** `pmacs/nervous/rate_limit.py`: centralized `BUCKETS` dict with `TokenBucket`.
`pmacs/data/gateway.py`: per-source `TokenBucket`. No ad-hoc rate limiting.

**Verdict:** PASS

---

### Anti-Pattern #4 -- Mutating evidence packets in staleness checks

**Evidence:** `pmacs/agents/base.py:345-369`: `_sanitize_evidence_packets()` uses `model_copy(update=...)`.

**Verdict:** PASS

---

### Anti-Pattern #5 -- cycle_id=None on audit-emitting functions

**Evidence:** `pmacs/logsys/debug_log.py:220-224`: `log_debug()` raises `ValueError` if `cycle_id is None`
for non-SYSTEM_EVENT_TYPES. Only two matches for `cycle_id=None`: debug_log.py exempt list and
sleep_watch.py non-cycle context.

**Verdict:** PASS

---

### Anti-Pattern #6 -- Day 1 bootstrap aborting everything

**Evidence:** `pmacs/engines/arbitration.py:223`: `PROCEED_BOOTSTRAP_LOW_CONFIDENCE`.

**Verdict:** PASS

---

### Anti-Pattern #7 -- Tight broker-side stops

**Evidence:** `pmacs/execution/catastrophe_net.py`: broker receives ONLY catastrophe-net at 15%.

**Verdict:** PASS

---

### Anti-Pattern #8 -- eur_per_usd field

**Evidence:** `pmacs/schemas/currency.py:25-31`: `_no_eur_per_usd_field()` validator rejects it.

**Verdict:** PASS

---

### Anti-Pattern #9 -- Mutation Engine writing production state directly

**Evidence:**
- `pmacs/mutation/daemon.py`: reads data, generates proposals, writes to `mutation_proposals` table only.
- `pmacs/nervous/mutation.py:1-8`: "This module lives in pmacs-nervous because the mutation process
  MUST NOT have write access to production config files."
- `pmacs/nervous/mutation.py:21-60`: `atomic_write_config()` with temp-file + rename (POSIX atomicity).

**Verdict:** PASS

---

### Anti-Pattern #10 -- Mutation A/B running in PAPER

**Evidence:** `pmacs/mutation/ab_runner.py:38`: "Candidate arm always runs SHADOW-only."

**Verdict:** PASS

---

### Anti-Pattern #11 -- Any mutation auto-applying

**Evidence:**
- `pmacs/mutation/promotion.py:81-82`: `operator_promote()` requires `totp_code`, raises `PermissionError` if fails.
- `pmacs/mutation/daemon.py:7`: "All promotions require operator TOTP. No auto-promote."
- No `auto_promote` or `auto_apply` patterns found anywhere.

**Verdict:** PASS

---

### Anti-Pattern #12 -- Runtime prompt edits

**Evidence:** Prompt templates loaded from static `.md` files at initialization. No code writes to prompt files at runtime.

**Verdict:** PASS

---

### Anti-Pattern #13 -- Backtesting against historical LLM outputs

**Evidence:** No `backtest.*llm`, `llm.*backtest`, or `historical.*llm.*output` patterns found.

**Verdict:** PASS

---

### Anti-Pattern #14 -- Logging secrets

**Evidence:**
- No logging of API keys, TOTP secrets, or signing keys found.
- `pmacs/storage/keychain.py`: `_scrub_secrets()` redacts secrets from error messages.
- `pmacs/installer/steps/verify_llm.py:99`: logs `api_key_ref` (key name), not value.

**Verdict:** PASS

---

### Anti-Pattern #15 -- Missing error_code on WARN+ debug events

**Evidence:** `pmacs/logsys/debug_log.py:207-217`: raises `ValueError` if `error_code` is `None` for WARN/ERROR.
`pmacs/logsys/error_classifier.py`: 70+ canonical error codes.

**Verdict:** PASS

---

### SEC-PASS-21: No SQL Injection Vectors

**Evidence:**
- All SQLite queries use parameterized `?` placeholders.
- `pmacs/storage/sqlite.py:311`: Table name interpolation guarded by `re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table)`.
- No string formatting of SQL queries with user input.

**Verdict:** PASS

---

### SEC-PASS-22: No Command Injection Vectors

**Evidence:**
- All `subprocess.run()` / `subprocess.Popen()` calls use list form (not shell=True).
- Zero instances of `shell=True` in codebase.
- Arguments are hardcoded or derived from config, not user input.

**Verdict:** PASS

---

### SEC-PASS-23: No XSS Vectors

**Evidence:**
- `pmacs/web/app.py:168`: Jinja2 autoescape enabled via `select_autoescape(["html", "htm"])`.
- `pmacs/web/routes/settings.py:595-596`: Mutation diff uses `html.escape()` for line-by-line escaping.
- CSP header set: `default-src 'self'; script-src 'self' 'unsafe-inline'` (HTMX requires inline).

**Verdict:** PASS

---

### SEC-PASS-24: No Hardcoded Secrets

**Evidence:**
- All API keys retrieved from macOS Keychain via `keyring` / `security` CLI at runtime.
- Config files (`model_registry.json`, `risk.toml`, `crucible.toml`, `mutation.toml`, `resources.toml`)
  contain zero credentials. Verified via grep for `api_key|secret|password|token|credential`.
- `.gitignore` excludes `.env`, `*.pem`, `*.key`, `credentials.json`.

**Verdict:** PASS

---

### SEC-PASS-25: Prompt Injection Defense

**Evidence:**
- `pmacs/data/gateway.py:16-26`: 7 compiled regex patterns detect injection attempts.
- `pmacs/agents/base.py:98`: All evidence packets sanitized before passing to LLM.
- Detection logged with `PROMPT_INJECTION_DETECTED` error code.

**Verdict:** PASS

---

### SEC-PASS-26: TOTP Gating on All Write Endpoints (NEW -- fixes SEC-MED-03)

**Evidence:**
All previously ungated write endpoints now require TOTP verification:
- `/api/settings/inference/provider` -- `_verify_totp(req.totp_code)` at settings.py:241
- `/api/settings/inference/api-key` -- `_verify_totp(req.totp_code)` at settings.py:262
- `/api/settings/inference/model` -- `_verify_totp(req.totp_code)` at settings.py:297
- `/api/cycle/start` -- `_verify_totp(req.totp_code)` at pipeline.py:253
- `/api/universe/add` -- `_verify_totp(req.totp_code)` at universe.py:115
- `/api/universe/remove` -- `_verify_totp(req.totp_code)` at universe.py:138
- `/api/universe/bulk-tag` -- `_verify_totp(req.totp_code)` at universe.py:163
- `/api/universe/bulk-remove` -- `_verify_totp(req.totp_code)` at universe.py:190

TOTP verification uses rate-limited `_verify_totp()` with `BUCKETS["totp_verify"].acquire()`.

**Verdict:** PASS

---

### SEC-PASS-27: CSRF Protection

**Evidence:**
- `pmacs/web/app.py:34-76`: CSRFMiddleware using double-submit cookie pattern.
- Cookie: `pmacs_csrf`, Header: `x-csrf-token`.
- Validated with `secrets.compare_digest()` (timing-safe) for all unsafe methods.
- Disabled in test mode via `_CSRF_ENABLED = "pytest" not in _sys.modules`.

**Verdict:** PASS

---

### SEC-PASS-28: Security Headers

**Evidence:**
- `pmacs/web/app.py:83-99`: SecurityHeadersMiddleware adds:
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Referrer-Policy: same-origin`
  - `Content-Security-Policy: default-src 'self'; ...`

**Verdict:** PASS

---

### SEC-PASS-29: Session Management

**Evidence:**
- `pmacs/nervous/auth.py`: Single active session, 256-bit random token (`secrets.token_hex(32)`).
- 24h expiry. New creation invalidates old session.

**Verdict:** PASS

---

### SEC-PASS-30: Signing Key File Permissions

**Evidence:**
- `pmacs/execution/signing.py:29-30`: Private key written with `chmod(0o600)` (owner-only).
- Public key with `chmod(0o644)`. Private key never logged.

**Verdict:** PASS

---

### SEC-PASS-31: Pydantic v2 Compliance

**Evidence:**
- Zero `from pydantic.v1` imports in entire codebase.
- Zero `class Config:` in `pmacs/schemas/` (verified via grep).
- `model_config = ConfigDict(...)` pattern used throughout.

**Verdict:** PASS

---

## Pre-Commit Hook Verification

`.pre-commit-config.yaml` enforces 6 anti-pattern checks:

| Hook | Anti-Pattern | Status |
|------|-------------|--------|
| `no-holding-state-mutation` | AP#1: no `holding.state =` outside state_machine.py | ACTIVE |
| `no-json-dumps-audit` | AP#2: no `json.dumps` in audit.py | ACTIVE |
| `no-secrets-in-logs` | AP#14: no secret logging | ACTIVE |
| `no-pydantic-v1` | Pydantic v2: no `from pydantic.v1` | ACTIVE |
| `no-class-config` | Pydantic v2: no `class Config:` | ACTIVE |
| `no-eur-per-usd-field` | AP#8: no `eur_per_usd` field | ACTIVE |

**Missing from pre-commit (enforced at runtime instead):**
- AP#3 (custom rate-limit): enforced by centralized rate_limit.py, no hook needed
- AP#5 (cycle_id=None): enforced by debug_log.py runtime ValueError
- AP#7 (tight broker stops): architectural, not detectable by grep
- AP#9-11 (mutation safety): enforced by TOTP gating at runtime
- AP#15 (missing error_code): enforced by debug_log.py runtime ValueError

**Verdict:** Adequate. Runtime enforcement covers what pre-commit cannot detect.

---

## Five Non-Negotiables Verification

| NN | Requirement | Status | Evidence |
|----|-------------|--------|----------|
| 1 | LLMs never sign trades | **PASS** | Ed25519 isolated in execution/, no agent imports |
| 2 | LLMs never math | **PASS** | All math in engines/, agents produce structured data only |
| 3 | Every state transition hash-chained | **PASS** | AuditWriter with sha256 chain, fsync, canonical JSON |
| 4 | Local-only execution | **PASS** | pf blocks inference, localhost bindings, active=llama_server |
| 5 | Operator owns kill switch | **PASS** | TOTP-gated disengage, timing-safe compare |

---

## Anti-Pattern Compliance (Architecture.md S16)

| # | Anti-Pattern | Status | Evidence |
|---|-------------|--------|----------|
| 1 | holding.state = "ABORTED_LLM" | **PASS** | grep: zero matches outside state_machine.py |
| 2 | json.dumps(payload) for audit | **PASS** | audit.py uses canonical_json exclusively |
| 3 | Custom rate-limit logic | **PASS** | Centralized BUCKETS/TokenBucket |
| 4 | Mutating evidence in staleness checks | **PASS** | model_copy(update=...) |
| 5 | cycle_id=None on audit-emitting functions | **PASS** | Runtime ValueError enforcement |
| 6 | Day 1 bootstrap aborts everything | **PASS** | PROCEED_BOOTSTRAP_LOW_CONFIDENCE |
| 7 | Tight broker-side stops | **PASS** | catastrophe_net at 15% only |
| 8 | eur_per_usd field | **PASS** | Validator rejects, constants forbid |
| 9 | Mutation writes production state | **PASS** | Structural separation, nervous-only writes |
| 10 | Mutation A/B in PAPER | **PASS** | SHADOW-only enforced |
| 11 | Any mutation auto-applying | **PASS** | TOTP required, no auto_promote |
| 12 | Runtime prompt edits | **PASS** | Static templates, mutation TOTP-gated |
| 13 | Backtesting against historical LLM outputs | **PASS** | No such code found |
| 14 | Logging secrets | **PASS** | Keychain scrubbing, no secret logging |
| 15 | Missing error_code on WARN+ | **PASS** | Runtime ValueError + 70+ canonical codes |

---

## Summary Table

| ID | Finding | Severity | Status |
|----|---------|----------|--------|
| SEC-CRIT-01 | Mutation TOTP bypass (broken import) | CRITICAL | **FIXED** (confirmed) |
| SEC-HIGH-01 | difflib.HtmlDiff XSS | HIGH | **FIXED** (confirmed) |
| SEC-HIGH-02 | Active LLM backend routes through cloud | HIGH | **FIXED** -- active=llama_server |
| SEC-MED-01 | Finnhub API key from env var | MEDIUM | **FIXED** (confirmed) |
| SEC-MED-02 | Alpaca credentials in httpx headers | MEDIUM | **OPEN** |
| SEC-MED-03 | Write endpoints not TOTP-gated | MEDIUM | **FIXED** -- all endpoints TOTP-gated |
| SEC-LOW-01 | Exception message leaking | LOW | **OPEN** (ACCEPTED) |
| SEC-PASS-01 through SEC-PASS-31 | (31 verified controls) | -- | **PASS** |

---

## Architectural Strengths Observed

1. **Defense in depth on trade execution:** Ed25519 signing -> UDS transport -> signature
   verification -> broker adapter -> catastrophe-net stop. Five layers before a trade
   reaches a broker.

2. **Structural isolation of mutation engine:** Mutation process cannot write production
   config. Promotion goes through nervous (separate process). All mutations require
   operator TOTP.

3. **Audit chain integrity:** `fsync` after every write, canonical JSON, hash recomputation,
   full and incremental verification, rotation preserves chain.

4. **Secret management:** Keychain-based with `_scrub_secrets()` for error paths. No secrets
   in config files, environment variables, or logs.

5. **TOTP correctness:** `hmac.compare_digest()` (timing-safe), proper window, cryptographic
   RNG for secret generation.

6. **Error code enforcement:** Runtime validation that all WARN+ events carry valid error
   codes from the canonical registry. `cycle_id` required for non-system events.

7. **No telemetry or phone-home:** Zero external calls beyond required data/broker APIs.

8. **CSRF protection:** Double-submit cookie with timing-safe comparison on all unsafe methods.

9. **Security headers:** CSP, X-Frame-Options DENY, nosniff, same-origin referrer policy.

---

*Audit complete. 2 open findings (1 MEDIUM, 1 LOW). All Five Non-Negotiables PASS. All 15 Anti-Patterns PASS. Previous HIGH finding (SEC-HIGH-02) remediated.*
