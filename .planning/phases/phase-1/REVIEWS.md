# Phase 1 Cross-AI Peer Reviews

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
- Exit tests match Phases.md §2 exactly for both Phase 1 and Phase 2
- Schema coverage includes all models from Architecture.md §8-9
- Configuration files match Architecture.md §17 structure
- Anti-pattern grep checks cover Architecture.md §16 items that are grep-able
- Five Non-Negotiables are referenced and architecture enforces them structurally

**Issues:**
1. **Architecture.md §8.2 (HoldingState enum)**: Plan references "all 22 states" but doesn't enumerate them in Task 2.1. Must verify implementation includes exact enum values from spec.
2. **FX convention (Architecture.md §16.8)**: Plan correctly specifies `usd_per_eur` convention but needs explicit test case that rejects `eur_per_usd` field.
3. **Staleness budgets**: Plan references `config/source_criticality.toml` but doesn't specify budget values (hours/days) for CRITICAL/IMPORTANT/NICE_TO_HAVE sources. Spec implies budgets exist but doesn't define them - plan should make this explicit.

**Fixable before implementation:** Yes. Add explicit test case for FX convention field rejection. Define example staleness budget values in config section.

---

### Dimension 2: Statistical Correctness — **3/5**

**Strengths:**
- DirectionalProbability sum constraint is enforced: `sum ≈ 1.0 ± 1e-6`
- Conviction thresholds match Source.md §7.2: STRONG_BUY ≥ 0.6, BUY ≥ 0.3
- FX round-trip test catches conversion errors
- Bootstrap and limited-history haircuts are referenced

**Critical Gaps:**
1. **No property tests for probabilistic invariants** (Phase 1). A trading system needs Hypothesis/property tests for:
   - Probability combinations always stay in [0, 1]
   - Arbitration weight normalization produces valid distributions
   - Conviction formula monotonicity properties

2. **Conviction formula unspecified**. Plan references Source.md §7.2 but Architecture.md §9.2 contains the actual formula. Plan should explicitly cite Architecture.md §9.2 as implementation source.

3. **FX ECB reference rate handling unclear**. `FxSnapshot` has `business_date (ECB CET)` and `fetched_at (UTC)` but no specification of:
   - How stale ECB rates are detected
   - What happens if ECB feed is down
   - Weekend/holiday handling

**Recommendation:** Add property test suite in Phase 1 (not later). Conviction math is core - test it early. Specify ECB staleness detection.

---

### Dimension 3: Security and Safety — **2/5**

**Five Non-Negotiables - Structural Compliance:**
- LLMs never sign trades: `pmacs-execution` (UDS) not built until Phase 4, correctly deferred
- LLMs never math: Arbitration/Sizing are Python engines, not LLMs
- Hash-chained state transitions: Task 4.2 (state_machine) includes audit write on every transition
- Local-only execution: No cloud API keys, Keychain for storage
- Operator owns kill switch: Deferred to Phase 4 (correct)

**Critical Issues:**

1. **Keychain error handling unspecified** (Task 3.4). "Raise `KeychainError` on missing key" - but what's the recovery path?
   - If Keychain corrupted at startup: System cannot boot. Need manual recovery procedure.
   - If Keychain fails at runtime: This should log CRITICAL and engage kill switch.
   - **Missing**: Error handling spec for Keychain failure modes.

2. **Logging secrets anti-pattern not fully testable** (Task 4.3). Pre-commit grep can catch `log(api_key)` but cannot catch:
   ```python
   log(f"API call failed: {error}")  # where error.__str__ contains key
   log.debug("Keychain access", extra={"key": key})  # structured logging
   ```
   **Fix needed**: Runtime check in Keychain wrapper that scrubs keys from all exceptions before logging.

3. **`cycle_id` enforcement is heuristic-only** (Task 4.3). "Heuristic grep" for `cycle_id` on audit-emitting functions cannot catch:
   - Functions that call audit-emitters without passing `cycle_id`
   - Audit events constructed without `cycle_id` and emitted later
   - **Missing**: Decorator-based enforcement like `@requires_cycle_id` for audit-emitting functions.

4. **Bootstrap mode kill switch** (Source.md §4.2). During Day 1 bootstrap (low historical_n), the system must use `PROCEED_BOOTSTRAP_LOW_CONFIDENCE` not abort everything. Plan mentions bootstrap haircut in Task 2.3 but doesn't specify kill switch exception logic.

**Fixable before Phase 1 completion:** Partially. Keychain recovery needs ops procedure. Grep-based checks have inherent limitations - add runtime sanity checks in Phase 4.

---

### Dimension 4: Test Coverage and Quality — **3/5**

**What's Tested:**
- Schema compilation and cross-field validators
- Audit chain integrity (tamper detection)
- State machine transitions (valid/invalid)
- FX round-trip accuracy
- Staleness budgets
- Data source integration (≥10/13 sources pass)
- Rate limiting

**Critical Gaps:**

1. **No property tests** (mentioned in Dimension 2). Trading systems need invariant testing beyond unit tests.

2. **Missing edge case tests**:
   - **Gatekeeper**: Tests for kill-switch-engaged rejection, halted/delisted rejection, max-positions rejection
   - **StalenessChecker**: Tests for CRITICAL/IMPORTANT/NICE_TO_HAVE degradation behavior
   - **State machine**: Tests for terminal-state guard (no transitions from RESOLVED_UP)
   - **Keychain**: Tests for missing key at startup vs runtime, corrupted Keychain

3. **Anti-pattern enforcement incomplete**. Task 4.3 lists grep checks but no tests that verify:
   - Direct `holding.state =` actually calls `transition()` under the hood (integration test)
   - `canonical_json()` is actually used for audit (grep can't verify runtime call)
   - `FreshnessResult` is never mutated (runtime invariant)

4. **Exit test gaps**:
   - No test that Ed25519 signing actually works (deferred to Phase 4 - OK)
   - No test that kill switch actually engages (deferred to Phase 4 - OK)
   - **But Phase 1 should test**: `state_machine.transition()` actually writes audit event

**Recommendation:** Add integration test file `tests/integration/test_audit_writing.py` that verifies state transitions emit audit events. Add edge case unit tests for Gatekeeper and StalenessChecker.

---

### Dimension 5: Code Quality — **4/5**

**Pydantic v2 Compliance:**
- `model_config = ConfigDict(...)` used (not `class Config:`)
- `@model_validator(mode="after")` used for cross-field validation
- `model_validate()` / `model_dump()` referenced (not `parse_obj()`/`dict()`)
- No explicit verification that `from pydantic.v1` is banned - relies on pre-commit hook

**File Organization:**
- Logical structure: schemas/, data/, storage/, logsys/, engines/
- **Ambiguity**: Task 2.3 lists `pmacs/schemas/arbitration.py`, `pmacs/schemas/conviction.py`, etc. Are these:
  - Pure Pydantic models (arbitration schemas)? OR
  - Engine implementations (ArbitrationEngine, ConvictionEngine classes)?
  Architecture.md §9 says engines live in `pmacs/engines/*.py`, but schemas for their inputs/outputs live in `pmacs/schemas/*.py`. Plan blurs this line. Clarify in Task 2.3: "Schema files for engine inputs/outputs; engines themselves implemented in later phases."

**Import Patterns:**
Not specified in plan. Should add: "No circular imports between schemas. All schemas import from `pydantic` only, not from other pmacs modules."

**Naming Conventions:**
- Follow Python standards (lowercase_with_underscores for modules/files)
- Test files follow `test_<module>.py` convention

---

### Dimension 6: Completeness Gaps — **3/5**

**Files from spec that are missing or ambiguous:**

1. **Missing from plan** (explicitly in Architecture.md §17):
   - `config/resources.toml`: Budget values unspecified
   - `config/risk.toml`: Threshold values unspecified
   - `config/crucible.toml`: 90s/2-cycles mentioned but not file content
   - `config/mutation.toml`: Activation threshold mentioned but not full content
   - **Fix**: Either add explicit default values to plan or reference a `config/defaults/` directory

2. **Engine vs schema boundary unclear** (Dimension 5 issue). If schemas are complete but engines not, what goes in `pmacs/schemas/arbitration.py`? Just the `Arbitrated` model? Or also `ArbitrationEngine` class? Architecture.md says engines in `pmacs/engines/`, but plan puts schemas in `pmacs/schemas/`. Clarify.

3. **Constants file scope** (Task 1.4) says "CI-tested values — do not edit casually" but doesn't list them. Should include:
   - Max position % (20%)
   - Max concurrent positions (5)
   - Catastrophe-net % (15%)
   - Mode names enum
   - State names enum (from HoldingState)
   - Anti-pattern thresholds (bootstrap haircut, limited-history haircut)
   - **Fix**: Either enumerate in plan or reference Architecture.md sections

4. **Process topology not built yet** (correct per spec). But plan should clarify:
   - No agents, no nervous, no cortex, no execution in Phase 1
   - These are Phase 3-4 (correct)
   - **But**: Kill switch is in Phase 1 exit test #5 (anti-patterns) but not built until Phase 4. This is OK (testing the framework for enforcing invariants), but should be explicit.

**Missing tasks:**
- **API key rotation**: How does operator update a key in Keychain? CLI command? Only via wizard?
- **Backup verification**: Architecture.md §24 mentions backup/restore. Phase 1 should verify audit log backup works (critical data).
- **Config reload**: If operator edits `config/risk.toml`, does system reload? Or require restart?

---

### Overall: **3.2/5**

### Critical Issues: **13**

**HIGH (fix before Phase 1 complete):**
1. **Keychain error recovery path unspecified** - what happens on corruption at startup?
2. **Conviction formula source unclear** - cite Architecture.md §9.2 explicitly
3. **No property tests for probabilistic invariants** - trading system core math untested
4. **FX ECB staleness detection unspecified** - how are weekend/holiday stale rates detected?
5. **Anti-pattern runtime enforcement incomplete** - grep cannot catch all violations
6. **Schema vs engine boundary ambiguous** - clarify what's in schemas/ vs engines/
7. **Config file contents not enumerated** - add default values or reference defaults/

**MEDIUM (fix in Phase 2-3):**
8. **Missing edge case unit tests** - Gatekeeper, StalenessChecker, State machine terminal states
9. **Bootstrap kill switch logic unspecified** - how does Day 1 avoid abort-all?
10. **API key rotation workflow missing** - how does operator update credentials?
11. **Audit writing integration test missing** - verify state_machine writes audit
12. **Structured logging secret exposure** - Keychain errors can leak keys in logged exceptions
13. **Backup verification not in Phase 1** - audit log backup is critical, test early

### Recommendations (Prioritized)

**DO BEFORE PHASE 1 COMPLETE:**
1. Add property test suite (`tests/property/test_probabilities.py`) for probabilistic invariants
2. Specify Keychain error recovery in ops documentation
3. Add explicit FX convention test: `pytest.raises(ValueError) on eur_per_usd field`
4. Clarify config file contents with example values
5. Clarify schema vs engine file boundary in plan
6. Add runtime sanity check decorator `@requires_cycle_id` for audit-emitters
7. Add Keychain exception scrubber before logging

**DO IN PHASE 2-3:**
8. Add edge case unit tests for Gatekeeper admittance logic
9. Add StalenessChecker degradation behavior tests
10. Specify bootstrap kill switch exception logic
11. Add API key rotation CLI command
12. Add audit writing integration test
13. Test audit log backup/restore in Phase 1 (move up from later phases)

**GENERAL FEEDBACK:**
The plan is well-structured and mostly spec-compliant. The main concerns are:
- **Testing rigor**: A trading system needs property-based testing for core math, not just unit tests
- **Operational readiness**: Keychain failure recovery and backup procedures are missing
- **Runtime invariant enforcement**: Pre-commit grep is not enough for critical safety invariants

**Risk Assessment:**
- Spec compliance: Good (4/5)
- Statistical correctness: Fair (3/5) - property tests missing
- Security: Poor (2/5) - Keychain failure path, runtime secret exposure
- Test coverage: Fair (3/5) - edge cases missing
- Code quality: Good (4/5) - Pydantic v2 compliant
- Completeness: Fair (3/5) - config contents, file boundaries ambiguous
