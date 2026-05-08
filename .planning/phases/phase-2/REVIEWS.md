# Phase 2 Cross-AI Peer Reviews

## Reviewer Availability

| Reviewer | Available | Status |
|---|---|---|
| Claude Sonnet 4.6 | Yes | Reviewed below |
| Gemini | No | `gemini` CLI not found |
| Codex | No | `codex` CLI not found |
| OpenCode | No | `opencode` CLI not found |
| Qwen | No | `qwen` CLI not found |
| Cursor | No | `cursor` CLI not found |

---

## Reviewer: Claude Sonnet 4.6

### Dimension 1: Spec Compliance — **4/5**

**Strengths:**
- `PersonaRunner` correctly implements the three-layer contract from `Agents.md §3`: grammar → Pydantic → sanity validator
- Kill switch implementation matches `Architecture.md §13` with all 10 triggers, TOTP-gated disengage, SQLite persistence
- `pmacs-execution` UDS service follows `Architecture.md §4.3` protocol (Ed25519 signature verification)
- SSE publisher with 6 streams matches `Architecture.md §4.4` spec
- Session auth uses 256-bit tokens, 24h expiry, HttpOnly, SameSite=Strict as specified
- Checkpoint/idempotency system implements `Architecture.md §4.6` correctly
- Crash loop detector uses 5 restarts / 60s window per `Architecture.md §4.7`

**Weaknesses:**
- `test_kill_switch.py` integration test referenced in plan does not exist in `tests/integration/` — only unit test exists
- Some Phase 3-4 exit tests cannot be verified without running llama-server (tests skip gracefully, but actual verification is deferred)

---

### Dimension 2: Statistical Correctness — **N/A**

Not applicable to Phase 2. This phase implements infrastructure (inference, processes, kill switch). Statistical correctness is evaluated in Phase 6+.

---

### Dimension 3: Security and Safety — **5/5**

**Excellent security implementation:**

- **TOTP implementation** (`pmacs/cortex/totp.py`): RFC 6238 compliant, 30s window, 6 digits, stdlib only (`hmac`, `hashlib.sha1`). Secret stored in macOS Keychain per `Architecture.md §1.3`.
- **Ed25519 signing** (`pmacs/execution/signing.py`): Correct keypair generation, tamper detection works, file permissions 0600/0644.
- **Process isolation** (`launchd/*.plist`): 8 separate processes with dedicated `_pmacs_*` users. `pmacs-inference` least privilege. `pmacs-dashboard` read-only. `pmacs-execution` ONLY process that can sign.
- **pf rules** (`ops/install_pf_rules.sh`): Blocks `_pmacs_inference` from internet egress. Allows loopback only.
- **Kill switch triggers** (`pmacs/cortex/kill_switch.py`): All 10 triggers from `Architecture.md §13.1`. Engage no TOTP (safer over-trigger). Disengage REQUIRES TOTP. Flags recent mutations for review.
- **Session security** (`pmacs/nervous/auth.py`): 256-bit random tokens, single active session, 24h expiry. Write endpoints require session + TOTP.

---

### Dimension 4: Test Coverage and Quality — **3/5**

**Strengths:**
- `test_llm_call.py` — comprehensive 4-test suite covering grammar pipeline, audit fields, no-grammar failure, model integrity
- `test_cycle_stub.py` — 10+ test cases covering open/close, audit, SSE, kill switch blocking, checkpoints, session manager
- `test_signing.py` — complete coverage of keypair generation, signing/verification, tampering, file persistence
- `test_kill_switch.py` (unit) — 20+ tests covering engage/disengage, TOTP, state persistence, all 10 triggers

**Weaknesses:**
- **Missing integration test**: `tests/integration/test_kill_switch.py` does not exist despite being in PLAN.md
- `test_heartbeats.py` referenced in PLAN.md — verify it exists and covers the required scenarios
- **No actual test run results** in review — cannot confirm exit tests pass

---

### Dimension 5: Code Quality — **4/5**

**Strengths:**
- Pydantic v2 used correctly throughout
- Proper error handling: try/finally blocks ensure DB connections closed
- Good logging: all audit events include `cycle_id`, appropriate `error_code` per `Architecture.md §5.5`
- Clean abstractions: `PersonaRunner` base class well-designed with clear extension points
- Comprehensive type hints and docstrings with spec references

**Weaknesses:**
- Some magic strings (`"ARMED"`, `"ENGAGED"`, `"OPEN"`, `"CLOSED"`) could be enums
- `initiate_cycle._publisher` attribute assignment is a hack for test wiring
- Boot detector uses simple weekday check instead of `pandas_market_calendars`

---

### Dimension 6: Completeness Gaps — **3/5**

**Implemented from PLAN.md Waves 1-6:** All tasks across all 6 waves have corresponding files.

**Missing items:**
- `tests/integration/test_kill_switch.py` — PLAN.md Wave 3 explicitly lists this file
- `tests/integration/test_heartbeats.py` — PLAN.md Wave 6 lists this file
- Grammar version tracking in GBNF files — spec suggests explicit version comments (`Agents.md §3` Layer 1)

---

### Overall: **4/5**

Phase 2 delivers a solid foundation for PMACS inference and process infrastructure. The code quality is high, security implementation is excellent, and spec compliance is strong. Main gaps are test infrastructure (missing integration tests) and unverified exit test execution.

### Critical Issues: **2**

1. **Missing `test_kill_switch.py` integration test** — The kill switch is the primary safety mechanism; an integration test verifying engage → no cycles start → TOTP disengage → cycles resume is essential.
2. **Unverified exit tests** — Without actual pytest execution, cannot confirm Phase 2's 6 exit tests pass.

### Recommendations (Prioritized)

**HIGH:**
1. Create `tests/integration/test_kill_switch.py` with engage→block→TOTP→resume flow
2. Run and capture pytest results for all Phase 2 exit tests
3. Add `test_heartbeats.py` integration test per PLAN.md

**MEDIUM:**
4. Extract magic strings to enums (CycleState, KillSwitchState string values)
5. Improve `initiate_cycle` test wiring — use dependency injection
6. Add unit tests for `pmacs/cortex/self_check.py` and `pmacs/cortex/daemon.py`

**LOW:**
7. Replace simple weekday check in `boot_detector.py` with `pandas_market_calendars`
8. Add explicit version comments to GBNF grammar files
