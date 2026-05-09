# Phase 3 Cross-AI Peer Reviews

**Phase:** 3 — Personas (PMACS Build Phases 5-6)
**Date:** 2026-05-09
**Reviewers:** Claude Opus 4.6 (in-session), Claude Sonnet 4.6 (external CLI)

---

## Reviewer: Claude Opus 4.6 (In-Session)

### Dimension 1: Spec Compliance — **4/5**

**Strengths:**
- Gatekeeper implements all 7 admittance checks from `Agents.md §4`: kill switch, halted/delisted, stale CRITICAL data, max concurrent positions, antipattern check, limited-history flagging, ADV flagging. Ordered correctly (fail-fast).
- All 7 personas have the required 4-file structure: runner, prompt (.md), grammar (.gbnf), sanity validator. Matches `Agents.md §5-11`.
- ArbitrationEngine implements Brier-inverse weighting per `Architecture.md §9.1`: `w_i = 1 / (rolling_brier + WEIGHT_EPSILON)`, MacroRegime 0.5x multiplier, extreme-probability dampening (p > 0.9 → 0.5x cap), normalization to sum 1.0.
- Bootstrap policy matches spec: immature agree → `PROCEED_BOOTSTRAP_LOW_CONFIDENCE`, immature disagree → `ABORT_NO_MATURE_SOURCES`.
- PersonaRunner base implements three-layer contract from `Agents.md §3`: Grammar → Pydantic model_validate → Sanity. Retry 2x with +0.05 temp bump. All constants match spec.
- Temperature 0.2 for all analysis personas. Matches spec.
- No lateral persona communication — each runner is independent.
- Queue engine implements 4-band priority (pinned, catalyst, normal, flagged).

**Critical Gaps:**
1. **Personas 4-7 missing `cycle_id` and `audit_writer` in `__init__`**: MacroRegime, CatalystSummarizer, MoatAnalyst accept `cycle_id` and `audit_writer` params and pass them to `super().__init__()`. GrowthHunter, InsiderActivity, ShortInterest, Forensics do NOT — they have no `cycle_id` or `audit_writer` params. This means the latter 4 personas cannot write audit events. Spec `Architecture.md §1.11` requires `cycle_id` on all audit-emitting functions, and `Architecture.md §5` requires audit logging for every LLM call.
2. **`_get_model_hash()` returns first hash found regardless of model**: The method iterates `config.model_hashes.items()` and returns the first value. If multiple models are configured, it returns the wrong hash. This should look up the specific model by name.

**Spec Alignment:** 85% — core logic matches spec, but audit gap on personas 4-7 is a compliance failure.

---

### Dimension 2: Statistical Correctness — **4/5**

**Strengths:**
- Brier-inverse weighting formula correct: `w_i = 1 / (brier + 0.05)`. WEIGHT_EPSILON prevents division by zero.
- MacroRegime 0.5x multiplier applied correctly: `raw_weight * MACRO_REGIME_WEIGHT_MULTIPLIER` after Brier-inverse computation, before normalization.
- Extreme-probability dampening: detects `p_up > 0.9` or `p_down > 0.9`, applies 0.5x factor to weight. Correct as anti-injection defense.
- Weight normalization: `norm_weights = [w / total for w in raw_weights]` ensures sum = 1.0.
- Probability sum preserved: weighted average of probability vectors maintains sum ≈ 1.0 (proven by linear combination).
- UNINFORMED_3STATE_BRIER = 0.667 matches theoretical value (Brier for uniform 3-class = 2/3).
- Bootstrap equal-weight averaging correct: `p_up = sum(s.p_up) / n` etc.

**Concerns:**
1. **Agreement score is binary (1.0 or 0.5)**: The `_mature_disagree()` function returns True/False, and agreement_score is set to 1.0 or 0.5. This doesn't capture graduated agreement (e.g., 3 sources up, 1 down should be 0.75, not 0.5). The spec doesn't specify a continuous formula, so this is technically compliant but crude.
2. **No sanity check on probability sum ≈ 1.0**: Sanity validators check for degenerate distributions (`p_up == p_flat == p_down`) but none verify `abs(p_up + p_flat + p_down - 1.0) < tolerance`. The GBNF grammar and Pydantic model should enforce this, but a defense-in-depth sanity check is missing.

**Statistical Rigor:** 80% — formulas correct, edge cases handled, minor gaps in agreement scoring.

---

### Dimension 3: Security & Safety — **4/5**

**Strengths:**
- LLMs never sign trades: PersonaRunner only produces `PersonaOutput` — no trade execution capability.
- LLMs never math: ArbitrationEngine is pure Python, deterministic. Personas only produce structured probability outputs.
- Three-layer validation prevents malformed/rogue LLM outputs from propagating: Grammar constrains output format, Pydantic validates schema, Sanity catches semantic errors.
- Extreme-probability dampening (>0.9 → 0.5x) is an anti-injection defense per `Agents.md §19.2`.
- Gatekeeper is deterministic Python — no LLM involvement. Correct per spec.
- Frozen Pydantic models (`GatekeeperResult`, `SanityResult`) prevent mutation after creation.
- Error codes on all WARN+ debug events: `LLM_CONNECTION_REFUSED`, `LLM_TIMEOUT`, `LLM_OUTPUT_EMPTY`, `GBNF_PARSE_FAIL`, `SANITY_VALIDATION_FAIL`, `ABORTED_LLM`. Matches `Architecture.md §5.5`.

**Concerns:**
1. **Personas 4-7 don't pass `cycle_id` to base runner** (see Dimension 1). Audit events from these personas would have empty `cycle_id`, violating `Architecture.md §1.11`.
2. **`_extract_json()` uses simple brace matching**: `text.find("{")` / `text.rfind("}")` — could extract incorrect JSON if the LLM output contains literal braces in strings. This is a robustness concern, not a security vulnerability per se.
3. **`_get_model_hash()` returns first hash blindly** — if model hashes are tampered with in config, this won't catch it for multi-model setups.

**Safety Score:** 75% — three-layer defense is strong, audit gap on personas 4-7 is the main concern.

---

### Dimension 4: Test Coverage & Quality — **5/5**

**Strengths:**
- 319 tests passed. Phase 3 deliverables include 142 tests across unit and integration:
  - Unit: test_personas (38), test_personas_extra (42), test_arbitration (13), test_gatekeeper (9) = 102
  - Integration: test_gatekeeper (12), test_3persona_cycle (15), test_7persona_cycle (13) = 40
- Integration tests cover all exit tests from Phases.md §2:
  - Gatekeeper: halted rejected, stale rejected, valid admitted, kill switch priority, portfolio limit with existing position, frozen model
  - 3-persona cycle: arbitrated output, Brier weight ordering, MacroRegime penalty, mature disagreement, no signals uniform, bootstrap with immature agreement, immature disagreement abort
  - 7-persona cycle: all 7 arbitrated, all mature proceed, all immature bootstrap, extreme-prob dampening, mixed mature/immature, weights sum to 1.0, disagreement, agreement scores
- Each sanity validator tested for pass, fail-retry-pass, fail-all-retries-abort (per exit test requirement)
- Edge cases tested: empty signals, all immature, mixed mature/immature, extreme probabilities
- Tests use synthetic fixtures — no llama-server dependency for deterministic tests

**Gaps:**
1. No test verifying that personas 4-7 produce audit events with correct cycle_id (because they don't accept it).
2. No test for `_extract_json()` edge cases (nested JSON, braces in strings).

**Test Coverage:** 95% — excellent coverage of all spec-defined exit tests and edge cases.

---

### Dimension 5: Code Quality — **4/5**

**Strengths:**
- Clean ABC-based inheritance: `PersonaRunner` defines clear abstract interface, all 7 subclasses implement it correctly.
- Consistent docstrings with spec references (`Agents.md §4`, `Architecture.md §9.1`).
- Pydantic v2 conventions correct: `model_validate()`, `model_dump()`, `ConfigDict`.
- Constants extracted to module level with descriptive names.
- Gatekeeper uses Protocol-based dependency injection for config.
- Queue engine is a clean pure function — no side effects.

**Issues:**
1. **Evidence formatting inconsistency**: Personas 1-3 (MacroRegime, CatalystSummarizer, MoatAnalyst) use direct attribute access (`packet.evidence`, `ev.id`, `ev.source.value`). Personas 4-7 use `getattr()` (`getattr(packet, "evidence", [])`, `getattr(ev, "id", "unknown")`). The getattr pattern is defensive but inconsistent. Pick one.
2. **Personas 4-7 use `str.replace()` template substitution** while 1-3 use f-string concatenation. Different prompt construction strategies for the same base class.
3. **Magic `0.15` threshold in MoatAnalystSanity** — moat_strength must be within 0.15 of component average. This should be a named constant.
4. **`_get_model_hash()` catches bare `Exception`** — should be more specific (ImportError, AttributeError, etc.).

**Code Quality:** 80% — clean architecture, minor inconsistencies between persona groups.

---

### Dimension 6: Completeness Gaps — **4/5**

**What's Delivered (Complete):**
- Gatekeeper: all 7 checks, frozen result model, all edge cases tested
- 7 persona runners: all extend PersonaRunner, all implement 4-file contract
- ArbitrationEngine: Brier-inverse weighting, MacroRegime penalty, extreme-prob dampening, bootstrap, disagreement abort
- Queue engine: 4-band priority
- Memory engine: stub (correctly returns None, documented as future work)
- All grammars load successfully
- All exit tests pass

**What's Missing (Non-blocking for Phase 3 → Phase 4):**
1. **Audit gap on personas 4-7**: These personas don't accept `cycle_id`/`audit_writer`, so LLM calls from them won't be audited. Fix: add `cycle_id` and `audit_writer` params to match personas 1-3.
2. **`_get_model_hash()` returns wrong hash for multi-model setups**: Should look up by model name, not return first entry.
3. **No probability sum sanity check**: Defense-in-depth for `p_up + p_flat + p_down ≈ 1.0`.
4. **GrowthHunterSanity missing degenerate distribution check**: All other persona sanity validators check for `p_up == p_flat == p_down` but GrowthHunterSanity doesn't.

**Completeness:** 85% — all exit tests pass, but audit gap on 4 personas needs fixing before Phase 4.

---

### Overall: **4.2/5**

### Critical Issues: **2**

1. **Personas 4-7 missing `cycle_id`/`audit_writer` params** — LLM calls from GrowthHunter, InsiderActivity, ShortInterest, Forensics produce no audit events. Violates `Architecture.md §1.11`.
2. **GrowthHunterSanity missing degenerate distribution check** — all other sanity validators catch `p_up == p_flat == p_down` but GrowthHunter doesn't.

### Recommendations (Prioritized)

**P0 (Fix before Phase 4):**
1. Add `cycle_id: str = ""` and `audit_writer: Any | None = None` params to GrowthHunter, InsiderActivity, ShortInterest, Forensics `__init__` methods. Pass to `super().__init__()`.
2. Add degenerate distribution check to GrowthHunterSanity (same pattern as all other sanity validators).

**P1 (Technical Debt):**
3. Fix `_get_model_hash()` to look up by model name, not return first hash.
4. Unify evidence formatting across all 7 personas (pick either direct access or getattr consistently).
5. Unify prompt construction (pick either replace() or f-string concatenation consistently).
6. Add probability sum sanity check to BaseSanityValidator: `abs(p_up + p_flat + p_down - 1.0) < 0.05`.
7. Extract `0.15` MoatAnalyst threshold to a named constant.

**P2 (Nice-to-Have):**
8. Improve agreement_score to be continuous (ratio of sources agreeing on dominant direction).
9. Add `_extract_json()` edge case tests (nested JSON, braces in strings).
10. Narrow `except Exception` in `_get_model_hash()` to specific exceptions.

---

## Reviewer Availability

| Reviewer | Available | Status |
|---|---|---|
| Claude Opus 4.6 (in-session) | Yes | Reviewed above |
| Claude Sonnet 4.6 (external CLI) | Yes | Reviewed below |
| Gemini | No | `gemini` CLI not found |
| Codex | No | `codex` CLI not found |
| OpenCode | No | `opencode` CLI not found |
| Qwen | No | `qwen` CLI not found |

---

## Reviewer: Claude Sonnet 4.6 (External CLI)

### Dimension 1: Spec Compliance — **3/5**

**Meets Requirements:**
- Gatekeeper: 7 deterministic checks implemented correctly
- Three-layer contract: Grammar → Pydantic → Sanity pipeline functional
- 7 personas with runner/prompt/grammar/sanity files
- Retry logic: 2 attempts with +0.05 temperature bump
- Arbitration Brier-inverse weighting with MacroRegime 0.5x multiplier
- Bootstrap policy: immature agree → PROCEED_BOOTSTRAP_LOW_CONFIDENCE

**Critical Deviations:**
- **Constructor inconsistency**: Personas 1-3 accept `cycle_id` + `audit_writer`, 4-7 don't
- **Evidence access inconsistency**: Direct attr access (1-3) vs `getattr()` (4-7)
- **No lateral communication**: Correctly enforced by architecture

### Dimension 2: Statistical Correctness — **3/5**

**Correct:**
- Brier-inverse weighting math: `w = 1.0 / (rolling_brier + epsilon)`
- MacroRegime 0.5x multiplier applied correctly
- Bootstrap equal-weight averaging for immature sources

**Issues:**
- **No probability sum validation**: Sanity validators don't check `p_up + p_flat + p_down ≈ 1.0`
- **Only checks degenerate case**: `p_up == p_flat == p_down` misses near-degenerate distributions
- **Binary agreement score**: Returns 1.0 or 0.5 instead of continuous measure

### Dimension 3: Security & Safety — **3/5**

**Strong:**
- LLM isolation: HTTP-only calls to `127.0.0.1:8080`
- Grammar constraints prevent most injection
- Audit logging on all LLM calls with SHA256 hashes
- Temperature 0.2 for analysis personas

**Vulnerabilities:**
- **`_extract_json()` fragile**: Simple brace matching breaks on nested JSON/braces in strings
- **No confidence calibration**: Extreme probabilities (>0.9) trigger dampening but no validation
- **Missing sanitization**: LLM output not stripped before JSON parsing

### Dimension 4: Test Coverage & Quality — **4/5**

**Strong:**
- 319 tests passed, 6 skipped (no llama-server)
- Unit + integration + E2E coverage
- Property tests for probability invariants

**Gaps:**
- No tests for `_extract_json()` edge cases
- Missing probability sum validation tests
- No tests for constructor consistency across personas

### Dimension 5: Code Quality — **3/5**

**Good:**
- Clear separation of concerns
- Base class abstraction reduces duplication
- Type hints with Pydantic v2 patterns

**Issues:**
- **`_get_model_hash()` bug**: Loads config fresh each call, returns first hash regardless of actual model
- **Inconsistent patterns**: Constructor signatures, evidence access methods vary
- **Magic numbers**: `0.9`, `0.5` thresholds scattered instead of centralized constants

### Dimension 6: Completeness Gaps — **2/5**

**Critical Missing:**
- No probability sum validation in sanity checks
- Agreement score not continuous as spec implies
- GrowthHunter/Forensics sanity validators missing field-specific checks
- MemoryEngine stub (always returns None)
- No confidence interval tracking for Brier scores

### Overall: **3.0/5**

### Spec Compliance Matrix

| Requirement | Status |
|---|---|
| 7-check Gatekeeper | PASS |
| 7 personas × 4 files | PASS |
| Three-layer contract | PASS |
| Retry +0.05 temp | PASS |
| Brier-inverse weights | PASS |
| MacroRegime 0.5x | PASS |
| Extreme-prob dampening | PASS |
| Bootstrap policy | PASS |
| Temp 0.2 analysis | PASS |
| No lateral comms | PASS |
| Prob sum validation | FAIL |

---

## Cross-Review Consensus

**Both reviewers agree on these critical issues:**

1. **Personas 4-7 missing `cycle_id`/`audit_writer`** — blocks audit logging for half the persona fleet
2. **No probability sum sanity check** — defense-in-depth gap for a math-critical system
3. **`_get_model_hash()` returns first hash blindly** — incorrect for multi-model configs
4. **Code inconsistencies between persona groups 1-3 and 4-7** — evidence access, prompt construction, constructor signatures

**Score convergence:**

| Dimension | Opus 4.6 | Sonnet 4.6 | Consensus |
|---|---|---|---|
| Spec Compliance | 4/5 | 3/5 | 3.5/5 |
| Statistical Correctness | 4/5 | 3/5 | 3.5/5 |
| Security & Safety | 4/5 | 3/5 | 3.5/5 |
| Test Coverage | 5/5 | 4/5 | 4.5/5 |
| Code Quality | 4/5 | 3/5 | 3.5/5 |
| Completeness | 4/5 | 2/5 | 3.0/5 |
| **Overall** | **4.2/5** | **3.0/5** | **3.6/5** |
