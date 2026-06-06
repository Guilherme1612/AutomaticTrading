# Phase 2 Cross-Review: Plan vs Spec Alignment

**Reviewer:** Claude (gsd-code-reviewer)
**Date:** 2026-05-26T12:37:00Z
**Scope:** GSD Phase 2 (PMACS Phases 3-4) -- Inference + Processes
**Documents Reviewed:** PLAN.md, SUMMARY.md, spec/Phases.md S2 (Phase 3-4), spec/Architecture.md S4, S13

---

## Plan-Spec Alignment (score: 4/5)

The PLAN.md maps cleanly onto PMACS Phases 3 and 4 from spec/Phases.md S2. Every file listed in the spec's "What gets built" section for Phase 3 and Phase 4 has a corresponding task in the plan. The six-wave structure is logical: Inference first (Phase 3), then Crypto, Cortex, Execution, Nervous, and Ops (Phase 4).

**Strengths:**
- All 8 process plists are specified (matches Architecture.md S4.1 table exactly)
- Kill switch state machine matches Architecture.md S13.4 pseudocode (ARMED/ENGAGED enum, engage without TOTP, disengage with TOTP)
- PersonaRunner three-layer pipeline matches Agents.md S3 contract (grammar -> Pydantic -> sanity)
- TOTP implementation matches Architecture.md S18 requirements (RFC 6238, 30s period, 6 digits)
- SSE streams match Architecture.md S4.4 table (6 streams with correct event types)
- Checkpoint system matches Architecture.md S4.6 (op_idempotency resume)
- Task dependency graph is acyclic and respects build order

**Gaps (minor):**
1. **Architecture.md S4.3 IPC table**: The spec lists `Local API key (Keychain)` as auth for Nervous->Inference calls. The plan mentions this indirectly via PersonaRunner but does not have a dedicated task for the local API key mechanism. Implementation in `base.py` reads from config/model_registry.json rather than Keychain for the inference URL. Low severity -- the inference process is pf-blocked from internet, so the API key is defense-in-depth, not primary auth.
2. **Architecture.md S4.5.1 session management**: The spec requires `HttpOnly, SameSite=Strict` cookies with 24h expiry and single-session enforcement. The plan covers this in Task 5.3 but the spec also mentions `Last-Event-ID` support for SSE reconnection. The implementation (sse_publisher.py) would need to confirm this is wired up.
3. **Architecture.md S4.6 sleep_watch.py**: The spec explicitly defines `cortex/sleep_watch.py` with IOKit sleep/wake handlers. This file exists in the codebase (`pmacs/cortex/sleep_watch.py`) but is not mentioned in the Phase 2 PLAN.md. It appears to have been added in a later phase. Not a blocker for Phase 2, but the plan should acknowledge it as "deferred" if it was intentionally excluded.
4. **Architecture.md S4.1 boot order**: The spec assigns explicit boot order numbers (1-7) to processes. The plan mentions launchd but does not specify how boot ordering is enforced in the plist files. Launchd handles this via KeepAlive and dependency ordering, but the plists should be checked for `WaitForDebugger` or queue-based ordering if strict sequencing is needed.

---

## Exit Test Coverage (score: 4/5)

### Phase 3 Exit Tests (4 required)

| # | Exit Test | Plan Coverage | Implementation Status |
|---|-----------|---------------|----------------------|
| 1 | llama-server starts with GGUF, responds on :8080 | Task 1.5 (start_inference.sh) | Script exists. Deferred -- needs actual GGUF file. |
| 2 | LLM integration test: prompt+GBNF -> JSON -> Pydantic -> audit event | Task 1.6 (test_llm_call.py) | 6 tests created, skip without server. |
| 3 | Model integrity check passes | Task 1.4 (model_integrity.py) | Unit test passes. |
| 4 | GBNF violation produces Pydantic failure | Task 1.6 sub-test | Test created, skip without server. |

**Assessment:** Tests 1, 2, and 4 are gated on having an actual GGUF model file and running llama-server. This is a known limitation documented in the plan. The test infrastructure is correct (skip-if-no-server pattern). Test 3 passes. The exit test structure matches the spec exactly.

### Phase 4 Exit Tests (6 required)

| # | Exit Test | Plan Coverage | Implementation Status |
|---|-----------|---------------|----------------------|
| 1 | 8 processes start, heartbeat within 10s | Tasks 6.1, 6.3 | 8 plists validated. 12 heartbeat tests pass. |
| 2 | Kill switch engage/disengage with TOTP | Task 3.3, 7.1 | 22 unit + integration tests pass. |
| 3 | Stub cycle open/close with SSE | Task 5.2 | 20 integration tests pass. |
| 4 | Ed25519 sign/verify/tamper | Task 2.2 | 8 unit tests pass. |
| 5 | Crash loop: 5 restarts/60s -> BROKEN_CRASH_LOOP | Task 3.5 | 8 unit tests pass. |
| 6 | pf rules block inference egress | Task 6.2 | Script exists, needs root to verify live. |

**Assessment:** All 6 exit tests have corresponding test files that pass. Tests 1 and 6 require live launchd/pf environment. This is expected for a development machine vs production deployment.

### Checkpoint A Coverage (7 items from Phases.md S6.1)

| # | Checkpoint Item | Test File | Status |
|---|----------------|-----------|--------|
| 1 | Kill switch engages on all 10 triggers | test_kill_switch.py + test_risk_checkpoint_a.py | 22+15 tests pass. All 10 spec triggers + 2 budget triggers implemented. |
| 2 | Disengagement requires TOTP | test_risk_checkpoint_a.py (TestDisengageRequiresTOTP) | 3 tests pass |
| 3 | Audit chain break -> immediate kill switch | test_risk_checkpoint_a.py (TestAuditChainBreakDetection) | 3 tests pass |
| 4 | llama-server cannot reach external IP | install_pf_rules.sh | Script exists, needs root |
| 5 | Execution is only process with broker imports | Structural (plist isolation) | Enforced by filesystem permissions |
| 6 | Ed25519 signing + tamper detection | test_risk_checkpoint_a.py (TestEd25519Signing + TestEd25519TamperDetection) | 6 tests pass |
| 7 | Crash loop detection works | test_risk_checkpoint_a.py (TestCrashLoopDetection) | 3 tests pass |

**Assessment:** Checkpoint A has comprehensive test coverage. Item 4 (pf rules) requires live verification that cannot be fully automated in unit tests. Item 5 is architectural (enforced by launchd user isolation, not a test).

**Deduction:** The LLM integration tests (Phase 3 exit tests 1, 2, 4) are skipped without a running llama-server. This is pragmatic but means Phase 3 exit tests have not been truly "passed" -- they are "ready to pass." The spec says exit tests are binary pass/fail. Strictly, Phase 3 is not complete until a GGUF model is available and those tests run green.

---

## Implementation Quality (score: 4/5)

### PersonaRunner (`pmacs/agents/base.py`)

**Strengths:**
- Clean three-layer validation pipeline (Layer 1: grammar HTTP call, Layer 2: Pydantic model_validate, Layer 3: sanity validator)
- Retry logic with +0.05 temperature bump matches Agents.md S3
- Audit logging on every LLM call (success and failure) with prompt_hash, output_hash, model_hash, grammar_version, retry_count, latency_ms
- Evidence sanitization (anti-pattern S16.4 compliant -- returns new packets, never mutates)
- Simulation fallback mode for testing without LLM
- Multi-backend support (GBNF local, OpenAI-compatible JSON schema, Anthropic tool use) -- extensible without code changes

**Issues:**
1. `_call_llm_openai` (line 532-586): Uses `response_format: {"type": "json_object"}` for OpenAI backend. This is correct for ensuring JSON output, but it does NOT provide the same structural guarantee as GBNF grammar. A malformed but valid JSON object would pass. The Pydantic layer catches this, but the grammar-value exit test (#4) cannot be meaningfully tested against this backend. Minor -- the spec targets llama-server as primary.
2. `_get_model_hash` (line 625-644): Falls back gracefully when config is unavailable, but the fallback path (line 642-643) returns empty string silently. If model hash verification is a kill switch trigger, an empty hash could bypass the integrity check. The `check_all_triggers` function handles this correctly (returns "No model hash configured"), but the empty-string pattern is worth noting.
3. `_extract_json` (line 660-673): Simple `{` to `}` extraction. This will fail on nested JSON where the last `}` is the outer object but there are other `}` characters in string values. In practice, GBNF grammars produce clean output, so this is a fallback-only concern.

### Kill Switch (`pmacs/cortex/kill_switch.py`)

**Strengths:**
- KillSwitchState enum matches Architecture.md S13.4 exactly
- engage() does NOT require TOTP (correct per spec: safer to over-trigger)
- disengage() requires valid TOTP code (correct per spec)
- SQLite singleton table with CHECK(id=1) ensures single row
- 10 triggers from Architecture.md S13.1 all implemented plus 2 budget triggers
- Mutation rollback flagging on engage (Architecture.md S13.3)
- Audit logging on both engage and disengage
- Debug event emission with canonical error_codes

**Issues:**
1. `disengage()` (line 178-251): The spec (Architecture.md S13.2) says Cortex must "confirm underlying condition resolved" before allowing disengage. The implementation does NOT check whether the trigger condition is still active. It only requires TOTP + typed reason. This is a spec deviation. In practice, an operator could disengage while a crash loop is still active, allowing cycles to start on an unstable system.
2. `engage()` opens and closes a new SQLite connection on every call (line 109). The `_get_db()` function creates a fresh connection each time. For a safety-critical hot path (crash loop -> engage -> halt), connection pooling or a persistent connection would reduce latency. The current pattern works but is not optimal.
3. `_check_rolling_loss()` (line 440-472): The comment says "simplified check" and "full implementation needs 5-day window calculation." The current code reads the latest `total_value_usd` but does NOT actually compare to the value 5 days ago. It always returns `triggered=False` unless an exception occurs. This means the ROLLING_5D_LOSS trigger is effectively a no-op. The test in test_risk_checkpoint_a.py verifies the trigger is wired but does not verify it actually detects a 5-day loss scenario.

### TOTP (`pmacs/cortex/totp.py`)

**Strengths:**
- Clean RFC 6238 implementation using stdlib only (hmac, hashlib.sha1, struct)
- Uses `hmac.compare_digest` for constant-time comparison (resistant to timing attacks)
- Uses `secrets.token_bytes` for secret generation (cryptographically secure)
- Window-based verification (configurable, default +-1 period)

**No issues found.** Implementation is spec-compliant and secure.

### Ed25519 Signing (`pmacs/execution/signing.py`)

**Strengths:**
- Uses `cryptography` library (standard, well-audited)
- File permissions 0600 on private key, 0644 on public key
- Clean sign/verify API with proper exception handling
- `verify_signature` catches `InvalidSignature` and returns bool (no unhandled exceptions)

**No issues found.** Implementation is spec-compliant.

### Orchestrator (`pmacs/nervous/orchestrator.py`)

**Strengths:**
- Full 30-step cycle pipeline matching Architecture.md S12
- CycleLock via fcntl.flock (non-blocking, auto-release on crash)
- Kill switch guard before cycle start (raises KillSwitchEngagedError)
- Checkpoint/resume via op_idempotency (crash recovery)
- SIGTERM/SIGINT signal handlers for graceful shutdown
- Persona slot map for parallel dispatch (3 slots)
- Per-step timing budgets

**Note:** The orchestrator has grown well beyond the Phase 4 "stub" scope described in the plan. It implements the full pipeline including execution adapter, post-cycle steps, and hardening that belongs to later phases. This is not a defect -- it shows progressive implementation -- but it means the "stub cycle" exit test is actually testing a much more capable system than the plan describes.

### Process Topology

All 8 processes from Architecture.md S4.1 are represented:
1. pmacs-inference (:8080) -- plist valid
2. pmacs-cortex (daemon) -- plist valid
3. pmacs-cortex-self-check (daemon) -- plist valid
4. pmacs-execution (UDS) -- plist valid
5. pmacs-nervous (:8000) -- plist valid
6. pmacs-stoploss (daemon, RTH) -- plist valid
7. pmacs-mutation (daemon) -- plist valid
8. pmacs-dashboard (:8001, loopback) -- plist valid

All 8 plists pass `plutil -lint` validation.

---

## Gaps & Risks

### Gap 1: ROLLING_5D_LOSS trigger is a stub (MEDIUM)

The `_check_rolling_loss()` function in kill_switch.py reads the latest `total_value_usd` from paper_account but never compares it to the value from 5 days prior. It always returns `triggered=False`. This means one of the 10 spec-required kill switch triggers is effectively disabled. The test coverage verifies wiring but not actual detection.

**Risk:** A sustained >10% loss over 5 days would not trigger the kill switch, allowing continued trading on a deteriorating account.

### Gap 2: Kill switch disengage does not verify condition resolution (MEDIUM)

Architecture.md S13.2 states: "Cortex confirms underlying condition resolved." The current `disengage()` implementation only checks TOTP validity, not whether the trigger condition is still active. An operator could disengage while a crash loop continues or while disk is still below 2GB.

**Risk:** Premature disengagement could allow cycles to resume under unsafe conditions. In practice, the operator is expected to verify conditions before disengaging, but the spec explicitly requires system verification.

### Gap 3: Phase 3 exit tests not runnable without GGUF model (LOW -- known)

Tests 1, 2, and 4 of Phase 3 exit tests are skipped when llama-server is not running. The plan acknowledges this. The test infrastructure is correct (skip pattern). But strictly, Phase 3 exit tests have not passed.

**Risk:** Low -- this is a known infrastructure dependency. The inference pipeline code is well-tested at the unit level.

### Gap 4: No pf rules live verification (LOW -- known)

Exit test #6 (pf rules block inference egress) requires root access and a live pf firewall. The script exists but has not been tested against actual network traffic.

**Risk:** Low -- the script follows standard macOS pf patterns. Live verification is a deployment-time concern.

---

## Recommendations

1. **Implement ROLLING_5D_LOSS properly.** Add a query that compares `total_value_usd` from 5 days ago to the current value. Calculate percentage loss. Trigger if >10%. Add a test that inserts data spanning 5+ days with a >10% drop and verifies the trigger fires.

2. **Add condition-resolved check to disengage().** After TOTP verification, re-evaluate the trigger that caused engagement. If the trigger condition is still active (e.g., disk still <2GB, crash loop still detected), raise RuntimeError or return False with a reason. At minimum, log a WARN if condition is still active but allow override with a second confirmation.

3. **Document Phase 3 exit test prerequisites.** The SUMMARY.md should explicitly note which exit tests require a GGUF model and llama-server running. Consider adding a Phase 3.5 "inference verification" step that runs when the operator first installs the GGUF model.

4. **Consider connection caching for kill_switch.** The engage() function is on the crash-loop hot path. Opening a new SQLite connection per call adds latency when the system is already under stress. A module-level connection with reconnection logic would be more robust.

5. **Wave 7 review patches are well-targeted.** Task 7.1 (kill switch integration test), Task 7.2 (cortex daemon unit tests), and Task 7.3 (grammar version comments) address real gaps. These should be considered required, not optional.

---

## Overall Score: 4/5

The plan is well-structured, spec-aligned, and thoroughly implemented. 152 tests pass (89 unit + 63 integration). All 8 processes have valid launchd plists. The kill switch is correctly implemented with TOTP-gated disengagement, audit logging, and all 10 spec triggers wired (12 total with budget triggers). The PersonaRunner correctly implements the three-layer validation pipeline from Agents.md S3.

The two medium-severity gaps (ROLLING_5D_LOSS stub implementation, disengage without condition resolution) prevent a score of 5/5. These are fixable within the current phase scope and should be addressed before promoting to Phase 5.

**Plan-Spec Alignment:** 4/5 -- All spec requirements mapped, minor gaps in IPC auth detail and sleep_watch deferment.
**Exit Test Coverage:** 4/5 -- All tests exist and pass where runnable. Phase 3 LLM tests deferred on GGUF availability.
**Implementation Quality:** 4/5 -- Clean, well-documented, spec-compliant code with two functional gaps in kill switch triggers.

---

_Reviewed: 2026-05-26T12:37:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
