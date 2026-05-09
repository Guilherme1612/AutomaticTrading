---
phase: 04-code-review-tests
reviewed: 2026-05-09T12:00:00Z
depth: standard
files_reviewed: 7
files_reviewed_list:
  - tests/unit/test_conviction.py
  - tests/unit/test_sizing.py
  - tests/unit/test_portfolio_risk_gate.py
  - tests/unit/test_crucible_budget.py
  - tests/unit/test_crucible_memo.py
  - tests/integration/test_paper_trade.py
  - tests/e2e/test_smoke_cycle.py
findings:
  critical: 2
  warning: 5
  info: 6
  total: 13
status: issues_found
---

# Phase 4: Code Review Report — Test Coverage & Correctness

**Reviewed:** 2026-05-09T12:00:00Z
**Depth:** standard
**Files Reviewed:** 7
**Status:** issues_found

## Summary

Reviewed 7 test files against the Phase 7 and Phase 8 exit test criteria defined in `spec/Phases.md`. The unit tests for conviction, sizing, risk gate, and Crucible schemas are well-structured with meaningful assertions. However, there are two critical gaps: (1) the spec requires `tests/integration/test_full_pipeline.py` and `tests/integration/test_wizard.py` but neither file exists, and (2) the Crucible budget tests test schema validators rather than the actual timeout/cycle-max runtime behavior the exit test specifies. Several test quality issues were found around edge cases and non-determinism.

---

## Exit Test Coverage Matrix

### Phase 7 Exit Tests (spec/Phases.md lines 304-309)

| # | Exit Test Criterion | Covered? | Where | Gap |
|---|---|---|---|---|
| 1 | Full pipeline: Gatekeeper -> 7 personas -> Arbitration -> Crucible -> EV -> Sizing -> Conviction -> Risk Gate -> Verdict -> MemoWriter | **MISSING** | No `tests/integration/test_full_pipeline.py` exists | Entire end-to-end pipeline integration test absent. E2E smoke test only covers Arbitrated (synthetic) -> Conviction -> Sizing -> Risk Gate. No Gatekeeper, no persona calls, no actual Crucible attack loop, no EV engine, no MemoWriter. |
| 2 | Conviction: STRONG_BUY >= 0.6, BUY >= 0.3, SKIP < 0.3 | **PARTIAL** | `test_conviction.py` TestVerdictTier | Boundary values at 0.6 and 0.3 are tested. However, there is no test that a conviction score of exactly 0.59 maps to BUY (not SKIP). The 0.3 boundary is tested but not from the compute_conviction side. |
| 3 | Sizing: bootstrap haircuts, limited-history stacking, half-Kelly, max-position cap | **COVERED** | `test_sizing.py` | All four sub-criteria tested with known inputs and exact expected outputs. |
| 4 | Crucible: 90s timeout -> NO_TRADE, 2 cycle max -> NO_TRADE, severity > 0.6 -> SKIP | **NOT COVERED** | `test_crucible_budget.py` tests schema validators only | The exit test requires testing runtime behavior: 90-second timeout triggering NO_TRADE, and exceeding 2 rewrite cycles triggering NO_TRADE. The existing tests only validate Pydantic schema constraints (e.g., rewrite_cycle bounds, thesis_survives/severity consistency). No actual Crucible inner-loop execution with timeout simulation. |
| 5 | severity > 0.6 produces SKIP; low severity + high p_up produces STRONG_BUY/BUY | **PARTIAL** | `test_conviction.py` test_crucible_severity_full | Crucible severity > 0.6 reducing conviction to 0.0 is tested (which would produce SKIP). The positive path (low severity + high p_up -> STRONG_BUY) is tested in the E2E smoke. But no dedicated unit test covers this specific exit test criterion directly. |

### Phase 8 Exit Tests (spec/Phases.md lines 335-340)

| # | Exit Test Criterion | Covered? | Where | Gap |
|---|---|---|---|---|
| 1 | Wizard completes all 11 steps on fresh machine | **MISSING** | No `tests/integration/test_wizard.py` exists | `test_paper_trade.py` tests wizard step progression (10 non-COMPLETE steps) and individual step functions, but does not mock full 11-step wizard with API integrations. |
| 2 | STRONG_BUY -> TradePlan signed -> Alpaca paper -> fill -> ledger -> ACTIVE -> catastrophe-net -> audit | **MISSING** | No actual trade submission test | `test_paper_trade.py` tests ledger lifecycle and catastrophe-net independently, but never connects a signed TradePlan through to a paper fill. No Alpaca adapter integration test. |
| 3 | Full cycle on synthetic fixtures; audit chain verifies; all engines fire | **COVERED** | `test_smoke_cycle.py` | Smoke cycle tests arbitrate -> convict -> size -> risk gate -> ledger -> catastrophe-net -> close. Audit chain verification included. |
| 4 | SHADOW mode concurrently captures audit-only signals (no fake-trades in SHADOW) | **MISSING** | No test file | No test verifies SHADOW mode behavior: that signals are captured as audit-only entries without generating paper trades. |
| 5 | Paper ledger starts at $5,000 and reflects fill correctly | **COVERED** | `test_paper_trade.py` TestPaperLedger.test_initial_capital | Asserts `ledger.cash == 5000.0` and `total_value == 5000.0`. |

### Overall Coverage Score

- **Phase 7:** 2/5 exit tests fully covered, 2 partially, 1 not covered at all
- **Phase 8:** 2/5 exit tests fully covered, 0 partially, 3 not covered at all
- **Test Quality Score: 6/10** -- Unit tests are strong with meaningful assertions. Integration and E2E coverage has significant gaps relative to the spec exit criteria.

---

## Critical Issues

### CR-01: Missing `tests/integration/test_full_pipeline.py` — Phase 7 Exit Test 1

**File:** N/A (file does not exist)
**Issue:** The spec explicitly lists `tests/integration/test_full_pipeline.py` as required (Phases.md line 297). This is Exit Test 1 for Phase 7: "one ticker goes through the complete pipeline: Gatekeeper -> 7 personas -> Arbitration -> Crucible (with attack) -> EV -> Sizing -> Conviction -> Risk Gate -> Verdict -> MemoWriter. Audit trail shows every step." No such file exists. The E2E smoke test only covers a subset (synthetic Arbitrated -> Conviction -> Sizing -> Risk Gate -> Ledger).
**Fix:** Create `tests/integration/test_full_pipeline.py` that tests the full pipeline sequence with synthetic persona outputs, a real Crucible attack loop (or mock), EV computation, and MemoWriter output generation. Every step must produce an audit trail entry that is verified.

### CR-02: Crucible budget tests do not test actual timeout/cycle-max behavior

**File:** `tests/unit/test_crucible_budget.py`
**Issue:** The Phase 7 exit test 4 (Phases.md line 308) requires: "Crucible times out at 90s -> NO_TRADE; Crucible exceeds 2 cycles -> NO_TRADE; severity > 0.6 cycle 1 -> NO_TRADE without cycle 2." The current tests only validate Pydantic schema constraints on `CrucibleOutput` -- they test that `rewrite_cycle` must be 1 or 2, and that `thesis_survives` must be consistent with `severity > 0.6`. They do NOT test the Crucible inner loop (`Agents.md section 16`) which enforces the 90-second timeout and 2-cycle maximum. This is a runtime behavior test, not a schema validation test.
**Fix:** Add tests that call the Crucible inner loop (or the orchestrator that manages it) with a mock that simulates slow responses exceeding 90s, and verify that the result is NO_TRADE. Similarly test that attempting a 3rd rewrite cycle produces NO_TRADE. If the Crucible inner loop is not yet implemented, the tests should exist as stubs that will fail until implementation is complete (documenting the gap).

---

## Warnings

### WR-01: Missing `tests/integration/test_wizard.py` — Phase 8 Exit Test 1

**File:** N/A (file does not exist)
**Issue:** The spec explicitly lists `tests/integration/test_wizard.py` (Phases.md line 330) as a Phase 8 file. The wizard tests in `test_paper_trade.py` TestWizard cover step progression and individual step functions, but do not test the full 11-step wizard run with mocked APIs as the exit test requires ("Wizard completes all 11 steps on a fresh machine with mocked API keys in test mode").
**Fix:** Create `tests/integration/test_wizard.py` that runs all 11 wizard steps (WELCOME through COMPLETE) with mocked external dependencies (LLM, data API, broker API) and verifies the wizard reaches COMPLETE state with valid accumulated config.

### WR-02: No test for SHADOW mode audit-only behavior — Phase 8 Exit Test 4

**File:** N/A
**Issue:** Exit test 4 requires: "SHADOW mode concurrently captures audit-only signals (no fake-trades in SHADOW)." No test verifies that when the system is in SHADOW mode, pipeline signals produce audit entries but no paper ledger transactions.
**Fix:** Add a test (likely in `test_paper_trade.py` or `test_smoke_cycle.py`) that sets mode to SHADOW, runs a pipeline cycle with a STRONG_BUY verdict, and verifies: (a) audit entries exist for the cycle, (b) the paper ledger has no new positions.

### WR-03: No test for full trade submission lifecycle (TradePlan -> fill -> ACTIVE) — Phase 8 Exit Test 2

**File:** `tests/integration/test_paper_trade.py`
**Issue:** Exit test 2 requires: "a STRONG_BUY ticker -> TradePlan signed -> submitted to Alpaca paper -> fill received -> ledger updated -> holding transitions to ACTIVE -> catastrophe-net stop placed -> audit trail complete." The current tests test ledger operations, catastrophe-net, and wizard separately but never connect them into a single integrated flow with a signed TradePlan.
**Fix:** Add an integration test that constructs a TradePlan, signs it (Ed25519), submits it to a mocked Alpaca paper adapter, receives a fill, updates the ledger, verifies the position is ACTIVE, places the catastrophe-net stop, and writes audit entries for each step. Verify the audit chain at the end.

### WR-04: Non-deterministic `datetime.utcnow()` in PaperLedger

**File:** `tests/integration/test_paper_trade.py` (indirect via `pmacs/sim/ledger.py:112`)
**Issue:** `PaperLedger.open_position` calls `datetime.utcnow()` directly, making the `entry_date` field non-deterministic. While no current test asserts on `entry_date`, any future test that does will be flaky. More broadly, this makes the ledger harder to test for time-dependent logic (e.g., position aging, holding duration).
**Fix:** Consider injecting a clock/now function into PaperLedger, or using a fixture that patches `datetime.utcnow`. This is a testability concern, not a bug in existing tests.

### WR-05: `test_pipeline_skip_low_conviction` has conditional assertion that may not execute

**File:** `tests/e2e/test_smoke_cycle.py:147-159`
**Issue:** The test `test_pipeline_skip_low_conviction` uses `if conviction < 0.3:` before asserting SKIP. If the computed conviction is >= 0.3, the assertion inside the `if` block never executes, making the test pass trivially without verifying anything. The test should either assert that the conviction IS below 0.3 first, or use an unconditional assertion.
**Fix:**
```python
def test_pipeline_skip_low_conviction(self) -> None:
    arb = _make_arbitrated("XYZ", p_up=0.35, p_flat=0.40, p_down=0.25)
    conviction = compute_conviction(
        arb,
        crucible_severity=0.5,
        ev_multiple=0.5,
        is_bootstrap=True,
    )
    assert conviction < 0.3, f"Expected low conviction, got {conviction}"
    verdict = verdict_tier(conviction)
    assert verdict.value == "SKIP"
```

---

## Info

### IN-01: `test_crucible_budget.py` redefines `_make_output` helper (shadowed by later definition)

**File:** `tests/unit/test_crucible_budget.py:27-43` and `153-170`
**Issue:** The function `_make_output` is defined twice in the same module. The first definition (line 27) does not have `attack_count_override` parameter. The second definition (line 153) adds it. The first definition is used by `TestCrucibleSeveritySurvives` and `TestCrucibleAttackCount` classes. The second definition is used by `TestCrucibleSeverityMax` via the `attack_count_override` parameter in the wrong_count test. Since Python uses the last definition in the module, the first-definition users are actually calling the second definition, which happens to be compatible due to the default `None` for `attack_count_override`. This works by accident, not by design.
**Fix:** Remove the first `_make_output` definition (lines 27-43) and keep only the second (lines 153-170) which is the superset. Move it to the top of the file.

### IN-02: No test for conviction boundary at 0.59 (BUY) vs 0.6 (STRONG_BUY)

**File:** `tests/unit/test_conviction.py`
**Issue:** `TestVerdictTier` tests exactly 0.6 (STRONG_BUY) and 0.3 (BUY) as boundaries, but does not test just below the boundary (e.g., 0.59 should be BUY, not STRONG_BUY). This is a gap in boundary testing.
**Fix:** Add `assert verdict_tier(0.59) == VerdictTier.BUY` and `assert verdict_tier(0.29) == VerdictTier.SKIP`.

### IN-03: `test_crucible_memo.py` tests MemoWriter schema but not MemoWriter engine output

**File:** `tests/unit/test_crucible_memo.py`
**Issue:** The MemoWriter tests validate the Pydantic schema (`MemoWriterOutput`) but do not test the actual MemoWriter persona/engine that produces this output. The spec (Phase 7) expects the MemoWriter to produce correct verdicts based on conviction and evidence. This is a schema-only test, not an engine test.
**Fix:** Add unit tests for the MemoWriter engine/logic once implemented, verifying that conviction + evidence + dissenting personas produce correct verdict lines.

### IN-04: Wizard test counts 10 steps but spec says 11

**File:** `tests/integration/test_paper_trade.py:316-331` and `336-338`
**Issue:** The wizard defines 11 enum values (WELCOME through COMPLETE). The test `test_wizard_full_progression` iterates through 10 steps (excluding COMPLETE) and then asserts `is_complete()`. The `test_wizard_progress` asserts `total == 10`. This is correct (10 actionable steps + 1 terminal state = 11 enum values). However, the spec says "Wizard completes all 11 steps" which could be confusing. The WizardStep enum has 11 members including COMPLETE, but only 10 are completable. This matches the implementation but may cause confusion with the spec wording.
**Fix:** No code change needed. Document that the spec counts 11 enum values (including COMPLETE as terminal), while the test correctly counts 10 actionable steps.

### IN-05: `test_risk_gate.py` zero portfolio value test may mask a real issue

**File:** `tests/unit/test_portfolio_risk_gate.py:75-80`
**Issue:** The test `test_zero_portfolio_value` asserts that with `portfolio_value_usd=0.0` and `target_usd=0.0`, the gate passes. The implementation divides 0/0 and catches it as 0.0, which means any zero-cost position would pass concentration checks. This is technically correct for 0-cost positions but could be a footgun if `target_usd > 0` while `portfolio_value_usd == 0`.
**Fix:** Consider adding a test for the case `target_usd=500, portfolio_value_usd=0` to verify the gate correctly handles this edge case (should it fail or pass?).

### IN-06: No test for sizing with `matured_sources_used=3` (0.90 factor)

**File:** `tests/unit/test_sizing.py`
**Issue:** The bootstrap haircut table has entries for 0, 1, 2, 3, and 4+ matured sources. Tests cover 0, 1, 4, and 5, but not 2 (0.80) or 3 (0.90). These intermediate values should be tested to verify the lookup table works correctly.
**Fix:** Add tests for `matured_sources_used=2` (factor 0.80) and `matured_sources_used=3` (factor 0.90).

---

_Reviewed: 2026-05-09T12:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
