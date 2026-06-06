# Re-Audit: Security and Trading Pipeline Fixes

**Date:** 2026-05-30
**Scope:** Verify 3 previously-identified fixes + fresh security scan
**Auditor:** Claude Code (security re-audit, gsd-secure-phase)
**Baseline:** SECURITY.md (2026-05-28), 09-REVIEW.md, 04-REVIEW.md

---

## Executive Summary

Re-audited three targeted fixes and performed a fresh full-scope security scan against the Five Non-Negotiables and 15 Anti-Patterns. **Two of three targeted fixes are VERIFIED. One has a residual gap.** One new HIGH finding discovered. All Five Non-Negotiables remain in the same state as the 2026-05-28 audit (NN#4 still FAIL via SEC-HIGH-02). No new anti-pattern violations introduced.

---

## Targeted Fix Verification

### FIX-1: Cash Ledger TOCTOU -- VERIFIED

**File:** `pmacs/engines/cash_ledger.py:136-137`
**Status:** PASS

The `apply_flow()` method now uses `BEGIN IMMEDIATE` before the read-modify-write sequence:

```python
# BEGIN IMMEDIATE serializes concurrent writers, preventing TOCTOU races
conn.execute("BEGIN IMMEDIATE")
```

**Verification that seed() and get_balance() are NOT broken:**
- `seed()` (line 65-93): Uses `SELECT COUNT(*)` then `INSERT` + `commit()`. This is a single-writer idempotent operation. No concurrent writer can insert between the check and the insert because the INSERT only fires when COUNT is 0, and the method runs at boot. `BEGIN IMMEDIATE` is correctly NOT applied here -- it would be unnecessary overhead for an idempotent bootstrap.
- `get_balance()` (line 95-104): Read-only query. No transaction isolation needed.
- `get_snapshot()` (line 106-126): Read-only query. No transaction isolation needed.
- `validate_total()` (line 195-246): Read + INSERT. Could benefit from `BEGIN IMMEDIATE` but is less critical than `apply_flow()` since it only writes a snapshot row.

**Verdict:** FIX VERIFIED. No regression to seed() or get_balance().

---

### FIX-2: CATASTROPHE_CANCEL_FAILED trigger -- VERIFIED

**File:** `pmacs/cortex/kill_switch.py:74`
**Status:** PASS

`TRIGGER_IDS` tuple now includes `"CATASTROPHE_CANCEL_FAILED"` at line 74:

```python
TRIGGER_IDS: tuple[str, ...] = (
    "AUDIT_CHAIN_INTEGRITY",
    "ROLLING_5D_LOSS",
    "SINGLE_DAY_MTM_LOSS",
    ...
    "MANUAL",
    "CATASTROPHE_CANCEL_FAILED",
)
```

**Cross-references verified:**
- `pmacs/execution/catastrophe_net.py:123-132`: Engages kill switch with `trigger="CATASTROPHE_CANCEL_FAILED"`.
- `pmacs/logsys/error_classifier.py:42`: Error code `CATASTROPHE_CANCEL_FAILED` registered in canonical registry.
- `pmacs/logsys/debug_log.py:88`: Listed in `VALID_ERROR_CODES`.

**Verdict:** FIX VERIFIED. TRIGGER_IDS validation still works -- `engage()` does not validate the trigger parameter against TRIGGER_IDS (it logs whatever it receives), so the addition is purely for documentation and any future validation.

---

### FIX-3: Holdings Persistence on Exit Paths -- PARTIALLY VERIFIED

**Files:** `pmacs/nervous/orchestrator.py` (multiple locations)
**Status:** PARTIAL -- all abort paths fixed, but interrupt path has a gap

**Verified abort paths (8 of 8 now have `_upsert_holding` + `_symbol_holdings.pop`):**

| Path | Line | `_upsert_holding` | `_symbol_holdings.pop` |
|------|------|-------------------|------------------------|
| Normal completion | 1169 | YES | YES |
| Antipattern abort | 1190-1191 | YES | YES |
| Persona timeout/all-fail | 1365-1366 | YES | YES |
| No valid probs | 1397-1398 | YES | YES |
| Crucible abort | 1533-1534 | YES | YES |
| Sizing abort | 1607-1608 | YES | YES |
| Verdict SKIP | 1644-1645 | YES | YES |
| Risk gate | 1675-1676 | YES | YES |

**Residual gap -- Interrupt path does NOT persist holdings:**

`_interrupt_remaining_holdings()` (lines 2240-2299) transitions holdings in-memory but does NOT call `_upsert_holding()` after each transition. The method:
1. Iterates `_symbol_holdings` (line 2262)
2. Transitions each holding via `transition()` (line 2276)
3. Appends to `interrupted` list (line 2283)
4. Calls `_symbol_holdings.clear()` (line 2286)

No `_upsert_holding()` call exists between the transition and the clear. If the process crashes after `_interrupt_remaining_holdings` but before `_close_cycle_aborted` completes, the interrupted/aborted state changes are lost -- the holdings table retains their pre-interrupt state.

**Severity:** MEDIUM -- The process is in a controlled shutdown path (SIGTERM or kill switch). The abbreviated post-cycle runs immediately after (steps 26-28), and `_close_cycle_aborted` follows. The window for data loss is narrow but non-zero.

**Verdict:** PARTIAL. All per-symbol abort paths fixed. Interrupt batch path missing persistence.

---

## State Machine Verification (ABORTED_PRE_LLM Change)

### ABORTED_PRE_LLM Transitions

`ABORTED_PRE_LLM` is a terminal state (contracts.py:47-48, 57). It is a valid transition target from:
- `CANDIDATE -> ABORTED_PRE_LLM` (contracts.py:65)
- `APPROVED_PENDING -> ABORTED_PRE_LLM` (contracts.py:79)

Both transitions are used in the orchestrator:
- Line 1187: Antipattern check (`CANDIDATE -> ABORTED_PRE_LLM`)
- No code currently transitions from `APPROVED_PENDING -> ABORTED_PRE_LLM`, but the transition is valid

### INTERRUPTED State Reachability

**Finding (RESIDUAL from 09-REVIEW C1 -- partially fixed):**

The 09-REVIEW C1 flagged that INTERRUPTED was unreachable from any state. The current state of VALID_TRANSITIONS shows:

- `ACTIVE -> INTERRUPTED` is valid (contracts.py:88)
- `INTERRUPTED -> ACTIVE | PANIC_EXIT | DELISTED` is valid (contracts.py:96-98)

The `_interrupt_remaining_holdings` method correctly works around the lack of INTERRUPTED transitions from pre-decision states by mapping them to abort states instead:
- ACTIVE -> INTERRUPTED (valid)
- Pre-decision states -> ABORTED_* (valid)
- HALTED -> PANIC_EXIT (valid)
- THESIS_AGING_REVIEW -> EXIT_THESIS_INVALIDATED (valid)

However, the original 09-REVIEW C1 recommendation to add INTERRUPTED as a valid target from CANDIDATE, PHASE1_RESEARCH, etc. was NOT implemented. The interrupt handler's workaround is functionally correct -- pre-decision holdings are aborted rather than interrupted, which is arguably the safer behavior since they have no open position to preserve.

**Verdict:** No state machine regression. The INTERRUPTED transition from ACTIVE works correctly. Pre-decision holdings are properly aborted instead.

---

## Execution Pipeline Verification

### Trade Signing Chain (Intact)

The full signing chain verified:

1. **Key Generation** (`execution/signing.py:10-36`): Ed25519 via `cryptography` library. Keys written with `chmod 0o600`.
2. **Signing** (`execution/signing.py:39-42`): `sign_bytes()` signs arbitrary bytes with Ed25519 private key.
3. **Client-side send** (`execution/service.py:287-322`): `sign_and_send()` derives public key from private, constructs `{payload, signature, public_key}` envelope, sends via Unix Domain Socket.
4. **Server-side verify** (`execution/service.py:105-108`): Verifies `client_pub == self._public_key` (key pinning) AND `verify_signature()`.
5. **Trade submission** (`execution/service.py:148`): Only after signature verification passes.
6. **Catastrophe stop** (`execution/service.py:168-170`): Placed after fill, 15% below entry.
7. **Audit logging** (`execution/service.py:186`): Every accepted trade written to audit.

**Agent isolation verified:** Zero imports of `execution.signing` or `execution.service` found in `pmacs/agents/` directory. LLMs cannot trigger trades directly.

**Verdict:** PASS -- signing chain intact, no regression.

---

## Fresh Security Scan

### Five Non-Negotiables

| NN | Requirement | Status | Change Since 2026-05-28 |
|----|-------------|--------|------------------------|
| 1 | LLMs never sign trades | PASS | No change |
| 2 | LLMs never math | PASS | No change |
| 3 | Every state transition hash-chained | PASS | No change |
| 4 | Local-only execution | FAIL (SEC-HIGH-02) | No change -- active backend still `openrouter` |
| 5 | Operator owns kill switch | PASS | No change |

### Anti-Pattern Compliance (15/15 PASS)

| # | Anti-Pattern | Status | Evidence |
|---|-------------|--------|----------|
| 1 | holding.state direct mutation | PASS | Only `state_machine.py:71` writes `holding.state =` |
| 2 | json.dumps for audit | PASS | All audit writes use `canonical_json()` |
| 3 | Custom rate-limit logic | PASS | Centralized `TokenBucket` via `rate_limit.py` |
| 4 | Mutating evidence in staleness | PASS | `model_copy(update=...)` used |
| 5 | cycle_id=None | PASS | Enforced by `log_debug()` for non-system events |
| 6 | Day 1 bootstrap aborts all | PASS | `PROCEED_BOOTSTRAP_LOW_CONFIDENCE` used |
| 7 | Tight broker-side stops | PASS | Catastrophe-net only (15%) |
| 8 | eur_per_usd field | PASS | Validator rejects it; `usd_per_eur` used |
| 9 | Mutation writes production state | PASS | Structural separation enforced |
| 10 | Mutation A/B in PAPER | PASS | SHADOW-only |
| 11 | Mutation auto-applying | PASS | TOTP required on all promotions |
| 12 | Runtime prompt edits | PASS | Static templates, mutation is propose-only |
| 13 | Backtesting LLM outputs | PASS | No such code found |
| 14 | Logging secrets | PASS | Keychain scrubbing, no secrets in logs |
| 15 | Missing error_code on WARN+ | PASS | Runtime validation enforced |

### SQL Injection Check

All SQLite queries use parameterized `?` placeholders. The `_column_exists` function in `sqlite.py:304-314` has:
- Regex guard: `re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table)` (line 311)
- Safety comment warning against user input (line 307-309)
- All callers use hardcoded table name strings

**Verdict:** PASS -- no injection vectors.

### Network Binding Check

- No `0.0.0.0` bindings found anywhere in codebase
- All services bound to `127.0.0.1` or UDS

**Verdict:** PASS.

---

## New Finding

### SEC-MED-04: Interrupted Holdings Not Persisted to SQLite

**Severity:** MEDIUM
**Category:** Data loss on crash
**File:** `pmacs/nervous/orchestrator.py:2240-2299`

**Description:**
`_interrupt_remaining_holdings()` transitions holdings to INTERRUPTED or ABORT states in memory but does not call `_upsert_holding()` to persist the state change to SQLite. If the process crashes between the interrupt transitions and the cycle close, the holdings table retains stale pre-interrupt states.

The state transition IS recorded in the audit chain (via `transition()` at line 2276, which calls `AuditWriter.append()` at state_machine.py:99), so the audit trail is preserved. But the `holdings` SQLite table would be inconsistent with the audit log.

**Impact:** In a mid-cycle crash during kill switch engagement, the operator would see holdings in their pre-interrupt state (e.g., PHASE1_RESEARCH) in the dashboard, even though the audit log correctly shows the interrupt transition. This is a consistency issue, not a safety issue -- the holdings were never in an ACTIVE/open-position state.

**Remediation:** Add `self._upsert_holding(holding, cycle_id)` after each successful transition in the loop:

```python
if is_valid_transition(holding.state, target):
    holding = transition(holding, target, "mid_cycle_abort", cycle_id, op)
    self._upsert_holding(holding, cycle_id)  # <-- missing
    interrupted.append(ticker)
    op += 1
```

---

## Previously Open Findings -- Status

| ID | Description | Status |
|----|-------------|--------|
| SEC-HIGH-02 | Active LLM backend routes through cloud | **OPEN** -- no config change |
| SEC-MED-02 | Alpaca credentials in httpx headers | **OPEN** |
| SEC-MED-03 | Write endpoints not TOTP-gated | **OPEN** |
| SEC-MED-04 | Interrupted holdings not persisted (NEW) | **OPEN** |
| SEC-LOW-01 | Exception message leaking | ACCEPTED |
| SEC-LOW-02 | In-memory session store | ACCEPTED |
| SEC-LOW-03 | Hardcoded NTP host | ACCEPTED |
| SEC-LOW-04 | Wizard credential storage lacks allowlist | ACCEPTED |

---

## 09-REVIEW Issue Status

| ID | Description | Status |
|----|-------------|--------|
| C1 | INTERRUPTED state unreachable | **WORKED AROUND** -- ACTIVE->INTERRUPTED works; pre-decision states map to aborts |
| C2 | Direct Holding field mutation | **UNFIXED** -- `holding.entry_price_usd = entry_price` still present |
| C3 | SQL injection in _column_exists | **FIXED** -- regex guard added |
| H1 | Unregistered error codes | **UNFIXED** -- DATA_UNAVAILABLE, STEP_OVER_BUDGET, etc. still not in registry |
| H2 | WHERE state = 'OPEN' | **FIXED** -- all queries now use 'ACTIVE' |
| H5 | Missing _symbol_holdings.pop on abort paths | **FIXED** -- all 8 paths now have pop |

---

## Summary

| Category | Count |
|----------|-------|
| Fixes verified | 2 of 3 (FIX-1, FIX-2 pass; FIX-3 partial) |
| Five Non-Negotiables | 4 PASS, 1 FAIL (same as baseline) |
| Anti-pattern violations | 0 new |
| New findings | 1 (SEC-MED-04) |
| Total open findings | 4 (1 HIGH, 3 MEDIUM) |
| Injection vectors | 0 |

---

*Re-audit complete. 1 new MEDIUM finding (SEC-MED-04: interrupt holdings not persisted). No regressions. No new anti-patterns. NN#4 violation (SEC-HIGH-02) requires config change before LIVE-READY.*
