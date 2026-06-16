# PMACS Security Audit Report

**Date:** 2026-05-28
**Scope:** Full codebase -- Five Non-Negotiables, 15 Anti-Patterns, additional security checks
**Auditor:** Claude Code (security audit, gsd-secure-phase)
**Spec references:** Architecture.md S5, S13, S16; Source.md S4, S5; CLAUDE.md (Five Non-Negotiables)

---

## Executive Summary

Full source code audit of `pmacs/` against the Five Non-Negotiables, all 15 Anti-Patterns from
Architecture.md S16, and supplementary security checks. **Previous CRITICAL and HIGH findings
(SEC-CRIT-01, SEC-HIGH-01) from the 2026-05-17 audit have been remediated.** One new HIGH
finding: Non-Negotiable #4 (local-only execution) is violated by the active model registry
configuration routing LLM calls through OpenRouter (cloud). One MEDIUM finding persists.
**22 of 25 findings PASS.**

---

## HIGH Findings

### SEC-HIGH-02: Active LLM Backend Routes Through Cloud (Non-Negotiable #4 Violation)

**Severity:** HIGH
**Category:** Architecture violation -- cloud LLM calls
**Files:** `config/model_registry.json:39`, `pmacs/agents/base.py:389-392`
**Spec violation:** Architecture.md S4.1 -- "Local-only execution. No cloud LLM calls."
CLAUDE.md Non-Negotiable #4.

**Description:**
The model registry has `"active": "openrouter"` which routes all LLM inference through
`https://openrouter.ai/api/v1` via the `_call_llm_openai` code path (base.py:551).

```json
"openrouter": {
  "default_model": "deepseek/deepseek-v4-pro",
  "structured_output": "json_schema",
  "api_key_ref": "pmacs.credentials.openrouter_api_key",
  "base_url": "https://openrouter.ai/api/v1"
},
"active": "openrouter"
```

The dispatch logic (base.py:386-392) uses `structured_output` to select the code path:
`json_schema` -> `_call_llm_openai()` -> cloud HTTP request.

The pf firewall rules (ops/install_pf_rules.sh) only block the `_pmacs_inference` user
(llama-server process). The agent code making the cloud LLM calls runs in `pmacs-nervous`,
which is NOT pf-blocked from internet. The firewall is structurally incomplete for the
current active backend configuration.

**Impact:** All LLM inference payloads (including evidence, thesis, analysis) are sent to a
third-party cloud service. This violates the "local-only execution" non-negotiable and
contradicts the spec requirement that inference be pf-blocked from internet.

**Remediation:** Change `model_registry.json` active backend to `"llama_server"` or `"ollama"`
(both local). If cloud backends are needed for development/testing, gate them behind a mode
flag that prevents use in PAPER or higher modes.

---

## MEDIUM Findings

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

---

### SEC-MED-03: Write Endpoints Not TOTP-Gated (Settings, Pipeline, Universe)

**Severity:** MEDIUM
**Category:** Missing authentication
**Files:**
- `pmacs/web/routes/settings.py:234` (`/api/settings/inference/provider`)
- `pmacs/web/routes/settings.py:251` (`/api/settings/inference/api-key`)
- `pmacs/web/routes/settings.py:282` (`/api/settings/inference/model`)
- `pmacs/web/routes/pipeline.py:174-248` (queue reorder/pin/promote/save, cycle start)
- `pmacs/web/routes/universe.py:112-210` (universe add/remove/bulk-tag/bulk-remove)

**Description:**
Multiple write endpoints that modify system state lack TOTP verification:

1. **Inference provider switching** -- allows changing the active LLM backend without TOTP.
   Combined with SEC-HIGH-02, an attacker on localhost could switch to a malicious backend.
2. **API key storage** -- allows writing arbitrary API keys to keychain without TOTP.
3. **Cycle start** -- allows triggering trading cycles without TOTP.
4. **Queue management** -- allows reordering the trading pipeline without TOTP.

The following endpoints ARE correctly TOTP-gated (verified):
- `/api/mutation/promote`, `/api/mutation/reject`, `/api/mutation/rollback`
- `/api/cortex/kill-switch/disengage`
- `/api/totp/verify`
- `/api/settings/cost/caps`

**Risk assessment:** All endpoints are loopback-only (127.0.0.1 binding), reducing
exposure to local privilege escalation. However, any process running on the same machine
can call these endpoints. In a single-operator local system this is acceptable for
operational endpoints (queue, universe), but inference provider switching and API key
storage should be TOTP-gated.

**Remediation:** Add TOTP verification to inference provider/API key endpoints at minimum.

---

## LOW Findings

### SEC-LOW-01: Exception Message Leaking in Web Responses

**Severity:** LOW
**Category:** Information disclosure
**Files:**
- `pmacs/web/routes/settings.py:381` (`str(exc)` in inference test response)
- `pmacs/web/routes/wizard.py:290, 360-408, 466-509, 585` (multiple `str(exc)` in responses)

**Description:**
Several web endpoints return `str(exc)` in JSON error responses. This can leak internal
implementation details (file paths, module names, stack information) to the client.

```python
return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
```

**Risk:** Low because the dashboard is loopback-only and single-operator. But it violates
defense-in-depth principles.

**Remediation:** Replace `str(exc)` with generic error messages in production. Log the
full exception server-side.

---

### SEC-LOW-02: In-Memory Session Store Loses Sessions on Restart

**Severity:** LOW
**Category:** Availability
**Files:** `pmacs/nervous/auth.py:36-61`

**Description:**
`SessionManager` stores the active session in a Python `dataclass` attribute. If the
`pmacs-nervous` process restarts, the session is lost and the operator must re-authenticate.

**Risk:** Acceptable for a single-operator local system.

---

### SEC-LOW-03: Hardcoded NTP Host

**Severity:** LOW
**Category:** Network exposure
**Files:** `pmacs/cortex/clock_monitor.py`

**Description:**
NTP drift check connects to `time.google.com:123`. The host is hardcoded and cannot be
configured. The module is in `pmacs-cortex` which has limited network access per spec.
Fails silently if network is unavailable.

**Risk:** Minimal. Acceptable.

---

### SEC-LOW-04: Wizard Credential Storage Lacks Key Allowlist

**Severity:** LOW
**Category:** Privilege escalation
**Files:** `pmacs/web/routes/wizard.py:152-158`

**Description:**
Step 4 of the first-run wizard stores arbitrary form data into macOS Keychain without
validating that the keys are expected credential names. A crafted form submission could
store arbitrary key-value pairs in the Keychain under `pmacs.credentials`.

**Risk:** Low because the wizard is one-time, localhost-only, and the form is generated
server-side.

---

## Previously Reported Findings -- Status Update

| Previous ID | Description | Status |
|-------------|-------------|--------|
| SEC-CRIT-01 | Mutation promote/rollback TOTP bypass (broken import path) | **FIXED** -- all imports now use `pmacs.storage.keychain` |
| SEC-HIGH-01 | difflib.HtmlDiff XSS in mutation diff endpoint | **FIXED** -- now uses `difflib.unified_diff()` (line 572) |
| SEC-MED-01 | Finnhub API key from environment variable | **FIXED** -- now uses `read_key("pmacs.finnhub.api_key")` from Keychain |

---

## PASS Findings (Verified Secure)

### SEC-PASS-01: NN#1 -- LLMs Never Sign Trades

**Evidence:**
- `pmacs/execution/signing.py`: Ed25519 signing isolated in execution module only.
- `pmacs/execution/service.py`: UDS server verifies Ed25519 signature before accepting trade.
  Client-side `sign_and_send()` requires the private key (line 290-291).
- `pmacs/agents/` directory: Zero imports of `execution.signing`, `execution.service`, or
  any trade submission code. Agents produce structured JSON via GBNF grammars and Pydantic
  schemas only.
- No code path from agent output to trade signing without passing through the deterministic
  engines (arbitration, sizing) and the UDS execution service.

**Verdict:** PASS

---

### SEC-PASS-02: NN#2 -- LLMs Never Math

**Evidence:**
- All probability combination, sizing, conviction, and arbitration logic is in `pmacs/engines/`:
  - `arbitration.py`: Brier-inverse weighted combination
  - `sizing.py`: Position sizing
  - `conviction.py`: Conviction scalar computation
- `pmacs/agents/` contains zero functions for `combine`, `arbitrate`, `size`, or `prob`.
- Three-layer validation (Grammar -> Pydantic -> Sanity) per Agents.md S3 ensures LLM
  outputs are structured data, never used directly for calculations.

**Verdict:** PASS

---

### SEC-PASS-03: NN#3 -- Every State Transition Is Hash-Chained

**Evidence:**
- `pmacs/engines/state_machine.py:88-105`: Every state transition writes to audit log via
  `AuditWriter.append()` with `prev_sha256` chain.
- `pmacs/storage/audit.py`: Hash chain with `sha256(ts || prev_sha || event_type || canonical_json)`.
  `os.fsync()` after every write. Rotation preserves chain across files.
- `pmacs/data/canonical.py`: `canonical_json()` with `sort_keys=True`, `separators=(",",":")`,
  `allow_nan=False` for deterministic serialization.
- `pmacs/storage/audit.py:AuditVerifier`: Full and incremental chain verification implemented.
- Kill switch state changes, trade executions, mutation promotions all write to audit chain.

**Verdict:** PASS

---

### SEC-PASS-04: NN#4 -- Local-Only Execution (Infrastructure)

**Evidence:**
- `ops/install_pf_rules.sh`: pf firewall blocks `_pmacs_inference` user from all outbound
  except loopback. Properly implemented.
- `pmacs/cli.py`: All web servers start with `--host 127.0.0.1` (loopback only).
- No `0.0.0.0` bindings found anywhere.
- `pmacs-execution`: Unix Domain Socket (no network at all).
- Qdrant: localhost default.
- Zero telemetry, analytics, or phone-home code.
- **NOTE:** The active backend config violates this at the application level (SEC-HIGH-02).
  The infrastructure (pf rules, binding) is correct. The config is wrong.

**Verdict:** PASS (infrastructure) / FAIL (active configuration -- see SEC-HIGH-02)

---

### SEC-PASS-05: NN#5 -- Operator Owns Kill Switch

**Evidence:**
- `pmacs/cortex/kill_switch.py:engage()`: No TOTP required (safer to over-trigger).
- `pmacs/cortex/kill_switch.py:disengage()`: Requires `verify_totp()` with valid code.
  Also re-checks that the underlying trigger condition has resolved before allowing
  disengagement (lines 234-253).
- `pmacs/cortex/totp.py:verify_totp()`: Uses `hmac.compare_digest()` (timing-safe).
  Window of +/-1 period (30s). Proper base32 decoding.
- Kill switch state persisted in SQLite singleton with `CHECK (id = 1)` constraint.
- `pmacs/web/routes/cortex.py:133-166`: Disengage endpoint verifies TOTP server-side.
- `pmacs/web/routes/cortex.py:111-130`: Engage endpoint requires NO TOTP (correct).
- Mutation flagging on kill switch engagement (kill_switch.py:160-179).

**Verdict:** PASS

---

### SEC-PASS-06: Anti-Pattern #1 -- holding.state = "ABORTED_LLM" (forbidden direct mutation)

**Evidence:**
- Grep for `holding.state = "ABORTED_LLM"`: Zero matches.
- Grep for `.state =` in all `pmacs/` Python files: Only match is `state_machine.py:71`
  (`holding.state = new_state`), which is the ONE allowed location.
- All other `.state` references are reads (`==`), not writes (`=`).
- Orchestrator uses `transition(holding, HoldingState.ABORTED_LLM, ...)` correctly.

**Verdict:** PASS

---

### SEC-PASS-07: Anti-Pattern #2 -- json.dumps(payload) for audit

**Evidence:**
- `pmacs/storage/audit.py:100`: Uses `canonical_json(payload)` for all audit writes.
- The two `json.dumps(payload)` usages found are:
  - `stop_loss_daemon.py:179`: heartbeat file (not audit)
  - `logsys/dead_letter.py:67`: dead letter storage (not audit)
- Both are for non-audit purposes. Audit chain uses canonical_json exclusively.

**Verdict:** PASS

---

### SEC-PASS-08: Anti-Pattern #3 -- Custom rate-limit logic

**Evidence:**
- `pmacs/nervous/rate_limit.py`: Centralized `BUCKETS` dict with `TokenBucket` implementation.
- `pmacs/data/gateway.py`: Per-source `TokenBucket` with `DEFAULT_RATES`.
- All rate limiting uses `BUCKETS["source"].acquire()` pattern.
- No ad-hoc `time.sleep()` or custom counter-based rate limiting found.

**Verdict:** PASS

---

### SEC-PASS-09: Anti-Pattern #4 -- Mutating evidence packets in staleness checks

**Evidence:**
- `pmacs/agents/base.py:345-369`: `_sanitize_evidence_packets()` creates new packets via
  `model_copy(update=...)` rather than mutating originals.
- Evidence sanitization returns new string values; original packets are not modified.

**Verdict:** PASS

---

### SEC-PASS-10: Anti-Pattern #5 -- cycle_id=None on audit-emitting functions

**Evidence:**
- `pmacs/logsys/debug_log.py:220-224`: `log_debug()` raises `ValueError` if `cycle_id is None`
  for any event type not in `SYSTEM_EVENT_TYPES`. This is enforced at runtime.
- `pmacs/execution/catastrophe_net.py:168-171`: `execute_exit()` raises `ValueError` if
  `cycle_id` is empty.
- System-level events (process lifecycle, health, storage) are properly exempted in
  `SYSTEM_EVENT_TYPES` frozenset (160 entries).

**Verdict:** PASS

---

### SEC-PASS-11: Anti-Pattern #6 -- Day 1 bootstrap aborting everything

**Evidence:**
- `pmacs/engines/arbitration.py:223`: Uses `ArbitrationDecision.PROCEED_BOOTSTRAP_LOW_CONFIDENCE`
  when all immature personas agree on direction.
- `pmacs/schemas/arbitration.py:19`: Proper enum value.
- `pmacs/nervous/orchestrator.py:1487`: Checks for bootstrap decision value.

**Verdict:** PASS

---

### SEC-PASS-12: Anti-Pattern #7 -- Tight broker-side stops

**Evidence:**
- `pmacs/execution/catastrophe_net.py`: Broker receives ONLY catastrophe-net stop at 15% below
  entry (CATASTROPHE_NET_PCT constant).
- Docstring explicitly states: "PMACS manages tight stops internally. The broker receives only
  a catastrophe-net stop" (line 4-5).
- `pmacs/engines/trailing_stop.py`: Tight stops managed internally by PMACS.

**Verdict:** PASS

---

### SEC-PASS-13: Anti-Pattern #8 -- eur_per_usd field

**Evidence:**
- Grep for `eur_per_usd`: Only in guard/validator code.
- `pmacs/schemas/currency.py:25-31`: `_no_eur_per_usd_field()` model validator rejects the
  field and raises `ValueError`. Also checks `model_dump()` output.
- `pmacs/constants.py:127`: `FX_CONVENTION = "usd_per_eur"` with comment "NEVER use eur_per_usd".
- `eur_per_usd` exists only as a computed property (inverse of `usd_per_eur`), not a stored field.

**Verdict:** PASS

---

### SEC-PASS-14: Anti-Pattern #9 -- Mutation Engine writing production state directly

**Evidence:**
- `pmacs/mutation/daemon.py`: Mutation daemon reads data, generates proposals, writes to
  `mutation_proposals` table. Does NOT write to `model_registry.json`.
- `pmacs/mutation/promotion.py:103`: Imports `apply_candidate_to_registry` from
  `pmacs.nervous.mutation` -- separate process (structural isolation).
- `pmacs/nervous/mutation.py:1-8`: Explicitly documents structural separation:
  "This module lives in pmacs-nervous because the mutation process MUST NOT have write
  access to production config files."
- `pmacs/nervous/mutation.py:21-60`: `atomic_write_config()` with temp-file + rename (POSIX
  atomicity).

**Verdict:** PASS

---

### SEC-PASS-15: Anti-Pattern #10 -- Mutation A/B running in PAPER

**Evidence:**
- `pmacs/mutation/ab_runner.py:38`: Docstring explicitly states "Candidate arm always runs
  SHADOW-only (Architecture.md S16 anti-pattern)."

**Verdict:** PASS

---

### SEC-PASS-16: Anti-Pattern #11 -- Any mutation auto-applying

**Evidence:**
- `pmacs/mutation/promotion.py:81-82`: `operator_promote()` requires `totp_code` parameter,
  raises `PermissionError` if TOTP verification fails.
- `pmacs/mutation/daemon.py:7`: "All promotions require operator TOTP. No auto-promote."
- `pmacs/web/routes/settings.py:398-427`: Server-side TOTP verification enforced on promote.
- No `auto_promote` or `auto-apply` logic found anywhere in codebase.

**Verdict:** PASS

---

### SEC-PASS-17: Anti-Pattern #12 -- Runtime prompt edits

**Evidence:**
- Prompt templates loaded from static `.md` files at agent initialization.
- `pmacs/agents/memo_writer.py:47`: Template loaded from `prompts/memo_writer.md`.
- No code that writes to or modifies prompt template files at runtime.
- Mutation Engine proposes prompt changes as candidates, which require operator TOTP to
  promote (see SEC-PASS-16).

**Verdict:** PASS

---

### SEC-PASS-18: Anti-Pattern #13 -- Backtesting against historical LLM outputs

**Evidence:**
- No `backtest.*llm`, `llm.*backtest`, or `historical.*llm.*output` patterns found.
- No code that replays historical LLM outputs through the pipeline for evaluation.

**Verdict:** PASS

---

### SEC-PASS-19: Anti-Pattern #14 -- Logging secrets

**Evidence:**
- No logging of API keys, TOTP secrets, or signing keys found.
- `pmacs/installer/steps/verify_llm.py:99`: Logs `api_key_ref` (key name like
  `pmacs.credentials.anthropic_api_key`), not the key value.
- `pmacs/storage/keychain.py`: Has `_scrub_secrets()` that redacts secrets from error messages.
- `pmacs/logsys/debug_log.py`: No secret filtering needed because secrets are never passed
  to the logging system.

**Verdict:** PASS

---

### SEC-PASS-20: Anti-Pattern #15 -- Missing error_code on WARN+ debug events

**Evidence:**
- `pmacs/logsys/debug_log.py:207-217`: `log_debug()` raises `ValueError` if `error_code` is
  `None` for WARN or ERROR levels. Also validates that `error_code` is in `VALID_ERROR_CODES`
  registry.
- `pmacs/logsys/error_classifier.py:120-172`: Comprehensive registry of 70+ canonical error
  codes covering all subsystems.

**Verdict:** PASS

---

### SEC-PASS-21: No SQL Injection Vectors

**Evidence:**
- All SQLite queries use parameterized `?` placeholders.
- `pmacs/storage/sqlite.py:311-314`: Table name interpolation has regex guard
  `re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table)`.
- No string formatting of SQL queries with user input.

**Verdict:** PASS

---

### SEC-PASS-22: No Hardcoded Secrets

**Evidence:**
- All API keys retrieved from macOS Keychain via `keyring` library at runtime.
- Config files contain zero credentials (verified: `model_registry.json`, `risk.toml`,
  `crucible.toml`, `mutation.toml`, `resources.toml`).
- `.gitignore` excludes `.env`, `*.pem`, `*.key`, `credentials.json`.

**Verdict:** PASS

---

### SEC-PASS-23: Prompt Injection Defense

**Evidence:**
- `pmacs/data/gateway.py:sanitize_evidence()`: 7 compiled regex patterns detect injection
  attempts. Matches replaced with `[SANITIZED]`.
- `pmacs/agents/base.py:345-369`: All evidence packets sanitized before passing to LLM.
- Detection logged with `PROMPT_INJECTION_DETECTED` error code.

**Verdict:** PASS

---

### SEC-PASS-24: TOTP Implementation RFC 6238 Compliant

**Evidence:**
- `pmacs/cortex/totp.py`: 30s period, 6 digits, SHA-1, `hmac.compare_digest()` (timing-safe).
- Secret generation uses `secrets.token_bytes(20)` (cryptographic RNG).
- +/-1 period window for clock skew tolerance.

**Verdict:** PASS

---

### SEC-PASS-25: Network Services Bound to Localhost Only

**Evidence:**
- No `0.0.0.0` bindings found.
- `pmacs-nervous`: `127.0.0.1:8000`
- `pmacs-dashboard`: `127.0.0.1:8000` (served by pmacs-nervous)
- `pmacs-inference`: `127.0.0.1:8080`
- `pmacs-execution`: Unix Domain Socket (no network)
- Security headers middleware present (`X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`).

**Verdict:** PASS

---

## Summary Table

| ID | Finding | Severity | Status |
|----|---------|----------|--------|
| SEC-CRIT-01 | Mutation TOTP bypass (broken import) | CRITICAL | **FIXED** |
| SEC-HIGH-01 | difflib.HtmlDiff XSS | HIGH | **FIXED** |
| SEC-MED-01 | Finnhub API key from env var | MEDIUM | **FIXED** |
| SEC-HIGH-02 | Active LLM backend routes through cloud | HIGH | **OPEN** |
| SEC-MED-02 | Alpaca credentials in httpx headers | MEDIUM | **OPEN** |
| SEC-MED-03 | Write endpoints not TOTP-gated | MEDIUM | **OPEN** |
| SEC-LOW-01 | Exception message leaking | LOW | ACCEPTED |
| SEC-LOW-02 | In-memory session store | LOW | ACCEPTED |
| SEC-LOW-03 | Hardcoded NTP host | LOW | ACCEPTED |
| SEC-LOW-04 | Wizard credential storage lacks allowlist | LOW | ACCEPTED |
| SEC-PASS-01 through SEC-PASS-25 | (25 verified controls) | -- | PASS |

---

## Five Non-Negotiables Verification

| NN | Requirement | Status | Evidence |
|----|-------------|--------|----------|
| 1 | LLMs never sign trades | PASS | SEC-PASS-01 |
| 2 | LLMs never math | PASS | SEC-PASS-02 |
| 3 | Every state transition hash-chained | PASS | SEC-PASS-03 |
| 4 | Local-only execution | **FAIL** | SEC-PASS-04 (infra) + SEC-HIGH-02 (config) |
| 5 | Operator owns kill switch | PASS | SEC-PASS-05 |

---

## Anti-Pattern Compliance (Architecture.md S16)

| # | Anti-Pattern | Status | Evidence |
|---|-------------|--------|----------|
| 1 | holding.state = "ABORTED_LLM" | PASS | SEC-PASS-06 |
| 2 | json.dumps(payload) for audit | PASS | SEC-PASS-07 |
| 3 | Custom rate-limit logic | PASS | SEC-PASS-08 |
| 4 | Mutating evidence in staleness checks | PASS | SEC-PASS-09 |
| 5 | cycle_id=None on audit-emitting functions | PASS | SEC-PASS-10 |
| 6 | Day 1 bootstrap aborts everything | PASS | SEC-PASS-11 |
| 7 | Tight broker-side stops | PASS | SEC-PASS-12 |
| 8 | eur_per_usd field | PASS | SEC-PASS-13 |
| 9 | Mutation writes production state | PASS | SEC-PASS-14 |
| 10 | Mutation A/B in PAPER | PASS | SEC-PASS-15 |
| 11 | Any mutation auto-applying | PASS | SEC-PASS-16 |
| 12 | Runtime prompt edits | PASS | SEC-PASS-17 |
| 13 | Backtesting against historical LLM outputs | PASS | SEC-PASS-18 |
| 14 | Logging secrets | PASS | SEC-PASS-19 |
| 15 | Missing error_code on WARN+ | PASS | SEC-PASS-20 |

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
   in config files, environment variables (Finhhub fixed), or logs.

5. **TOTP correctness:** `hmac.compare_digest()` (timing-safe), proper window, cryptographic
   RNG for secret generation.

6. **Error code enforcement:** Runtime validation that all WARN+ events carry valid error
   codes from the canonical registry. `cycle_id` required for non-system events.

7. **No telemetry or phone-home:** Zero external calls beyond required data/broker APIs.

---

*Audit complete. 3 open findings (1 HIGH, 2 MEDIUM). Previous CRITICAL finding remediated.
Non-Negotiable #4 violation (SEC-HIGH-02) requires config change before LIVE-READY status.*
