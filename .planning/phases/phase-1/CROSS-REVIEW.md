---
phase: 1-foundation-data
reviewed: 2026-05-26T11:35:00Z
depth: deep
files_reviewed: 14
files_reviewed_list:
  - .planning/phases/phase-1.md
  - spec/Phases.md
  - spec/Architecture.md
  - spec/Source.md
  - pmacs/storage/audit.py
  - pmacs/engines/state_machine.py
  - pmacs/schemas/contracts.py
  - pmacs/data/canonical.py
  - pmacs/config.py
  - pmacs/storage/sqlite.py
  - pmacs/constants.py
  - pmacs/logsys/debug_log.py
  - pmacs/logsys/error_classifier.py
  - pmacs/storage/keychain.py
  - .pre-commit-config.yaml
tests_verified:
  - tests/unit/test_schemas.py (32 passed)
  - tests/unit/test_audit_chain.py (6 passed)
  - tests/unit/test_state_machine.py (10 passed)
  - config load test (passed)
status: issues_found
findings:
  critical: 2
  warning: 4
  info: 3
  total: 9
---

# Phase 1 Cross-Review: Plan vs Spec vs Implementation

**Reviewed:** 2026-05-26T11:35:00Z
**Depth:** deep
**Files Reviewed:** 14
**Tests Verified:** 4 exit tests (all pass)
**Status:** issues_found

## Plan-Spec Alignment (score: 4/5)

The plan in `.planning/phases/phase-1.md` is a faithful 1:1 transcription of `spec/Phases.md Section 2 (Phase 1 and Phase 2)`. Every file listed, every exit test, and every dependency matches. The plan correctly:

- Enumerates all Pydantic model files, storage modules, config loader, canonical JSON, state machine, audit chain
- Specifies the exact exit test battery (schemas compile, audit chain genesis+100+verify+tamper, state machine valid/invalid transitions, config loads, anti-pattern grep)
- Calls out Phase 2 data layer dependencies correctly (rate limiting, staleness, FX convention, 13 sources)

**Minor gap:** The plan does not mention `spec/Architecture.md Section 5.2`'s requirement that cycle_id is required on every cycle-scoped audit event. The implementation handles this in `audit.py:56` but the plan does not explicitly call it out as an exit criterion.

## Exit Test Coverage (score: 5/5)

All five Phase 1 exit tests pass:

1. **`pytest tests/unit/test_schemas.py`** -- 32 passed. All Pydantic models compile with cross-field validators.
2. **`pytest tests/unit/test_audit_chain.py`** -- 6 passed. Genesis, 100 appends, verify, tamper detection, incremental verify, SHA recovery all work.
3. **`pytest tests/unit/test_state_machine.py`** -- 10 passed (18 total including helpers). Every valid transition succeeds; every invalid transition raises `InvalidStateTransition`.
4. **`python -c "from pmacs.config import load_config; load_config()"`** -- succeeds. All 7 config files load cleanly.
5. **Anti-pattern grep checks** -- `.pre-commit-config.yaml` contains hooks for all 6 anti-patterns from `Architecture.md Section 16`.

## Implementation Quality (score: 4/5)

Implementation is strong overall. The code is clean, well-documented with spec references, and follows Pydantic v2 conventions throughout. However, there are spec divergences documented in the findings below.

## Critical Issues

### CR-01: TERMINAL_STATES includes INTERRUPTED -- spec says it should NOT

**File:** `pmacs/schemas/contracts.py:46-54`
**Issue:** The implementation's `TERMINAL_STATES` frozenset includes `HoldingState.INTERRUPTED`. The spec (`Architecture.md Section 8.2`, line 1212-1221) does NOT include `INTERRUPTED` in `TERMINAL_STATES`. Furthermore, the spec defines `INTERRUPTED` as having valid transitions to `ACTIVE`, `PANIC_EXIT`, and `DELISTED` -- meaning it is a resumable state, not terminal.

The implementation also does NOT define `VALID_TRANSITIONS` for `INTERRUPTED` at all (line 62-95), creating a dead-end: a holding that enters `INTERRUPTED` state can never transition out because (a) there are no valid transitions defined from it, and (b) it is marked terminal, so the state machine raises `InvalidStateTransition` immediately.

**Fix:**
```python
# In pmacs/schemas/contracts.py

# Remove INTERRUPTED from TERMINAL_STATES (it is resumable per spec)
TERMINAL_STATES = frozenset({
    HoldingState.RESOLVED_UP, HoldingState.RESOLVED_FLAT,
    HoldingState.RESOLVED_DOWN, HoldingState.RESOLVED_MIXED,
    HoldingState.STOPPED_OUT, HoldingState.EXIT_THESIS_INVALIDATED,
    HoldingState.EXIT_OPPORTUNITY_COST, HoldingState.EXIT_TRAILING_STOP,
    HoldingState.EXIT_FAILED, HoldingState.DELISTED,
    HoldingState.RESOLUTION_TIMEOUT, HoldingState.PANIC_EXIT,
    # INTERRUPTED is NOT terminal per spec -- it can resume to ACTIVE
})

# Add INTERRUPTED transitions per spec
VALID_TRANSITIONS[HoldingState.INTERRUPTED] = frozenset({
    HoldingState.ACTIVE,
    HoldingState.PANIC_EXIT,
    HoldingState.DELISTED,
})
```

### CR-02: VALID_TRANSITIONS diverges from spec in multiple places

**File:** `pmacs/schemas/contracts.py:62-95`
**Issue:** The implementation's `VALID_TRANSITIONS` table has several divergences from the spec (`Architecture.md Section 8.2`, lines 1163-1210):

1. **CANDIDATE:** Implementation adds `HALTED` and `INTERRUPTED` transitions. Spec only allows `PHASE1_RESEARCH` and `ABORTED_PRE_LLM`.
2. **PHASE1_RESEARCH:** Implementation adds `INTERRUPTED`. Spec does not list it.
3. **PHASE1_TIMEOUT:** Implementation adds `INTERRUPTED`. Spec does not list it.
4. **PHASE2_CRUCIBLE:** Implementation adds `INTERRUPTED`. Spec does not list it.
5. **APPROVED_PENDING:** Implementation is MISSING `ABORTED_LLM` and `ABORTED_PRE_LLM` which the spec explicitly includes (lines 1183-1184).
6. **ACTIVE:** Implementation is MISSING `DELISTED` from the valid set (spec line 1193 includes it). The implementation does include it, but the resolution states (`RESOLVED_UP/FLAT/DOWN/MIXED`) are present in the implementation but not in the spec's ACTIVE transitions -- however these are reached via the resolution sub-flow, not directly, so this may be intentional.
7. **HALTED:** Implementation allows `CANDIDATE` and `ACTIVE`. Spec allows `ACTIVE`, `DELISTED`, and `PANIC_EXIT` (but NOT `CANDIDATE`).

These divergences mean the state machine either allows transitions the spec forbids, or forbids transitions the spec requires. Both are correctness risks.

**Fix:** Align `VALID_TRANSITIONS` exactly with `spec/Architecture.md Section 8.2` lines 1163-1210. The extra `INTERRUPTED` transitions in pre-decision states may be intentional for cycle hardening (Architecture.md Section 9) -- if so, document this as an intentional deviation with a spec reference.

## Warnings

### WR-01: State machine missing idempotency check present in spec

**File:** `pmacs/engines/state_machine.py:25-112`
**Issue:** The spec's `transition()` pseudocode (Architecture.md Section 8.2, lines 1245-1247) includes an idempotency guard:
```python
if op_already_completed(cycle_id, op_seq, "state_transition"):
    return holding
```
The implementation does not perform this check. This means re-running a cycle could apply duplicate state transitions. While the `op_idempotency` table exists in SQLite, the `transition()` function never queries it.

**Fix:** Add idempotency check at the start of `transition()`, or document that idempotency is handled at the orchestrator layer above the state machine.

### WR-02: State machine does not trigger FDE on terminal transitions

**File:** `pmacs/engines/state_machine.py:87-102`
**Issue:** The spec's `transition()` pseudocode (Architecture.md Section 8.2, lines 1267-1270) triggers the Failure Diagnostic Engine on terminal transitions:
```python
if new_state in TERMINAL_STATES:
    from pmacs.engines.failure_diagnostic import classify_and_record
    classify_and_record(holding, cycle_id)
```
The implementation does not call FDE. This is acceptable for Phase 1 (FDE is built in Phase 6) but should be tracked as a Phase 6 integration requirement.

**Fix:** Add a comment placeholder in `state_machine.py` noting the FDE integration point for Phase 6.

### WR-03: AuditWriter creates a new AuditWriter instance on every transition call

**File:** `pmacs/engines/state_machine.py:96-99`
**Issue:** Inside `transition()`, when `audit_path` is provided, a new `AuditWriter` instance is created every time:
```python
if audit_path is not None:
    from pmacs.storage.audit import AuditWriter
    writer = AuditWriter(audit_path)
    writer.append(...)
```
`AuditWriter.__init__` scans the entire file to recover the last SHA (line 31-44). Under high throughput, this creates O(n^2) file reads. Additionally, the writer is never `.close()`d, meaning the file descriptor leaks.

**Fix:** Either inject a long-lived `AuditWriter` instance, or cache it per path.

### WR-04: APPROVED_PENDING in spec allows ABORTED_LLM and ABORTED_PRE_LLM -- implementation omits them

**File:** `pmacs/schemas/contracts.py:78-81`
**Issue:** See CR-02 point 5. The spec explicitly states that `APPROVED_PENDING` can transition to `ABORTED_LLM` (for conviction < 0.3 after sizing/risk gate). The implementation only allows `ACTIVE`, `ABORTED_RISK`, and `INTERRUPTED`. This means a holding that passes the Crucible but fails conviction at the risk gate cannot be properly aborted -- it would raise `InvalidStateTransition` instead.

**Fix:** Add `HoldingState.ABORTED_LLM` and `HoldingState.ABORTED_PRE_LLM` to `VALID_TRANSITIONS[HoldingState.APPROVED_PENDING]`.

## Info

### IN-01: Keychain service naming convention diverges from spec

**File:** `pmacs/storage/keychain.py:40` and `pmacs/storage/keychain.py:123-146`
**Issue:** `Architecture.md Section 1.3` specifies service names as `pmacs.<category>.<key>`. The `read_key()` helper splits on the last dot, which means `pmacs.finnhub.api_key` becomes service=`pmacs.finnhub`, account=`api_key`. This works but `get_api_key()` is called directly elsewhere with service names like `pmacs-polygon` (hyphen format). Two naming conventions coexist.

**Fix:** Standardize on dotted format per spec and add a comment in `get_api_key` documenting the convention.

### IN-02: canonical_json _default handler catches float before datetime

**File:** `pmacs/data/canonical.py:25-36`
**Issue:** The `_default` function checks `isinstance(obj, float)` before checking datetime. Since `datetime` is not a subclass of `float`, this is fine functionally, but the ordering could be more readable if datetime/date (more specific) were checked first, matching the spec's pseudocode ordering (lines 687-713).

**Fix:** Cosmetic -- reorder to match spec pseudocode for readability.

### IN-03: pre-commit secret-detection grep may produce false negatives

**File:** `.pre-commit-config.yaml:33-34`
**Issue:** The `no-secrets-in-logs` hook uses a broad grep for `api_key|secret|password|token` combined with `log|print|debug`. This can miss cases where secrets are logged via format strings (e.g., `logger.info(f"Using {key}")`) where the variable name is not literally `api_key`. It can also produce false positives on legitimate code like error code definitions.

**Fix:** Consider using a dedicated secret-scanning tool (detect-secrets, trufflehog) in CI for more robust coverage.

## Gaps & Risks

### Gap 1: INTERRUPTED state deadlock risk

As detailed in CR-01, `INTERRUPTED` is marked terminal but has no valid outgoing transitions. If any code path places a holding into `INTERRUPTED`, it can never leave that state. The current `INTERRUPTED` state appears to be used for cycle hardening (graceful shutdown), but without valid outgoing transitions, any interrupted holding becomes permanently stuck.

**Risk:** Medium. If the system is interrupted mid-cycle (power loss, crash), holdings in pre-decision states would become unrecoverable.

### Gap 2: Spec deviations are untested

The state machine tests (`tests/unit/test_state_machine.py`) cover the implementation's current transition table but do not verify against the spec's transition table. The divergences in CR-02 were not caught because tests validate what IS, not what SHOULD BE.

**Risk:** High. Future phases that depend on the spec's transition table will encounter unexpected `InvalidStateTransition` errors (or missing errors).

### Gap 3: Missing PHASE1_TIMEOUT -> ABORTED_LLM transition in implementation

The spec (line 1174) shows `PHASE1_TIMEOUT` can transition to `ABORTED_LLM`. The implementation adds `INTERRUPTED` instead. If phase 1 research times out and then the system tries to abort it via the LLM abort path, it will fail.

**Risk:** Medium. This is a real operational path that will be exercised when LLM calls time out.

## Recommendations

1. **Reconcile VALID_TRANSITIONS with spec** -- This is the highest priority fix. Create a diff between the implementation's transition table and `Architecture.md Section 8.2` lines 1163-1210, and bring them into alignment. Document any intentional deviations as spec amendments.

2. **Fix INTERRUPTED state** -- Either remove it from TERMINAL_STATES and add spec-compliant outgoing transitions, or document it as a cycle-hardening addition with explicit spec reference.

3. **Add spec-conformance test** -- Add a test that programmatically verifies the implementation's `VALID_TRANSITIONS` and `TERMINAL_STATES` match the spec's definitions. This prevents silent drift.

4. **Fix APPROVED_PENDING transitions** -- Add the missing `ABORTED_LLM` and `ABORTED_PRE_LLM` transitions per spec, as these are needed for conviction < 0.3 aborts.

5. **Track Phase 6 integration points** -- FDE trigger on terminal transitions, idempotency check in state machine. Add `# TODO(phase-6)` comments now.

## Overall Score: 4/5

The Phase 1 foundation is solid: all exit tests pass, the audit chain is correctly implemented with fsync and tamper detection, canonical JSON matches the spec exactly, config loading works, and the anti-pattern pre-commit hooks are in place. The code quality is high with thorough docstrings and spec references.

The score is held back by the state machine transition table divergences from the spec (CR-01, CR-02, WR-04). These are not theoretical concerns -- they represent real operational paths that will break in later phases when the orchestrator attempts transitions that the spec defines but the implementation forbids (or vice versa). The fixes are mechanical and low-risk, but they should be applied before Phase 3 work begins.

---

_Reviewed: 2026-05-26T11:35:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
