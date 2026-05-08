# Phase 7 Cross-AI Peer Reviews

**Phase:** 7 — Episodic + Mutation (FLYWHEEL-READY)
**Date:** 2026-05-08
**Reviewers:** Claude Sonnet 4.6

---

## Reviewer: Claude Sonnet 4.6

### Dimension 1: Spec Compliance — **4/5**

**Strengths:**
- Rule-based candidate generation matches `Agents.md §17.2` exactly. 6 rules implemented with correct taxonomy mappings and min_count thresholds.
- Crucible state machine matches `Agents.md §16` perfectly: severity routing (<0.3 DONE, 0.3-0.6 REWRITE, >=0.6 ABORT), 2-cycle max, 90s budget.
- Advisor-only model enforced: `operator_promote()` requires TOTP, raises `PermissionError` on invalid code.
- Shadow-only A/B execution: `ABRunner` correctly separates control/candidate arms, never touches production.
- Episodic context builder implements all 6 data sources from `Agents.md §18`: macro regime, recent failures, persona-ticker affinity, FDE history, lessons, overrides.
- Word limit enforced: truncation at exactly 200 words with "..." suffix.
- Activation threshold: dormant before 50 PAPER cycles implemented correctly.

**Critical Gaps:**
1. **Missing `apply_candidate_to_registry()` implementation**: Spec `Architecture.md §10.7` states this function lives in `pmacs-nervous` and is the ONLY function that writes to `model_registry.json`. The promotion flow stops at TOTP verification but never actually applies config changes.
2. **Daemon is skeleton-only**: `pmacs/mutation/daemon.py` contains only a `while True: sleep(60)` loop. No integration with FDE clusters, DuckDB, or SQLite. The lifecycle described in `Architecture.md §10.4` is not wired.
3. **Rollback Level 3 (atomic promotion) unverified**: Spec requires temp-file + rename atomic write. Without `apply_candidate_to_registry()`, this cannot be tested.

**Spec Alignment:** 85% — core logic matches spec, but production wiring is incomplete.

---

### Dimension 2: Statistical Correctness — **4/5**

**Strengths:**
- Welch's t-test correctly implemented:
  - Welch-Satterthwaite degrees of freedom: `df = (v1/n1 + v2/n2)^2 / ((v1/n1)^2/(n1-1) + (v2/n2)^2/(n2-1))`
  - Two-tailed p-value via t-distribution CDF
- Cohen's d uses pooled standard deviation correctly: `pooled_std = sqrt(((n1-1)v1 + (n2-1)v2) / (n1+n2-2))`
- Edge cases handled:
  - Zero variance in both groups -> returns deterministic difference or no effect
  - Small samples (n<2) -> returns non-significant result
  - Division by zero protected (pooled_std > 0 check)

**Concerns:**
1. **t-CDF approximation is simplified**: `_regularized_incomplete_beta` is a crude approximation. For accurate p-values, should use `scipy.stats.t.sf` or a more precise continued fraction implementation.
2. **No test for numerical stability**: No tests with extreme values (1e10, 1e-10) or highly skewed distributions.

**Statistical Rigor:** 80% — formulas correct, but CDF approximation could produce inaccurate p-values in tail regions (p < 0.001).

---

### Dimension 3: Security & Safety — **3/5**

**Strengths:**
- TOTP gating enforced: `operator_promote()` verifies code via `verify_totp()`, raises `PermissionError` on failure.
- No auto-promote: All mutations require explicit operator action. Advisor-only model preserved.
- Structural separation mostly enforced: `pmacs-mutation` writes to SQLite only, not config files directly.
- Audit logging: `episodic_context_injected` includes content_hash, `mutation_rolled_back` includes reason.

**Critical Security Gaps:**
1. **Missing `apply_candidate_to_registry()` is a security boundary violation**: Spec `Architecture.md §1.13` states "The mutation process cannot write to production config" — enforced by having promotion happen in `pmacs-nervous`. Without that function, the security boundary is incomplete.
2. **No filesystem permission test**: Exit test 3.1 requires verifying "mutation process cannot write to `model_registry.json` (filesystem permission denied)". No such test exists.
3. **TOTP secret handling**: `operator_promote()` takes `totp_secret` as argument. Should be from Keychain, not passed from caller. Current implementation risks secret leakage.
4. **Rollback Level 5 incomplete**: `flag_for_kill_switch_review()` exists but is not integrated with actual kill-switch logic.

**Safety Score:** 60% — TOTP enforcement is solid, but missing production config writer is a critical gap.

---

### Dimension 4: Test Coverage & Quality — **4/5**

**Strengths:**
- 80 tests across mutation and crucible components.
- Unit tests cover all core functions: stat test (15), crucible loop (17), mutation (22).
- Integration tests cover full lifecycle: mutation lifecycle (9), episodic (12).
- Boundary conditions tested: activation threshold, concurrent cap, rollback windows, word limit.

**Gaps:**
1. No test for `apply_candidate_to_registry()` — cannot test atomic promotion (Level 3).
2. No filesystem permission test — Level 1 rollback safety untested.
3. No kill-switch integration test — Level 5 rollback untested end-to-end.
4. No stress test for statistical edge cases (extreme values, skewed distributions).

**Test Coverage:** 80% — solid coverage of happy paths and boundaries, security boundaries under-tested.

---

### Dimension 5: Code Quality — **4/5**

**Strengths:**
- Clean, readable code with clear separation of concerns.
- Comprehensive docstrings with spec references.
- Type hints used throughout.
- Consistent error handling: `PermissionError` for TOTP failures, `None` for missing entities.
- Good use of Python standard library — no unnecessary dependencies.
- Crucible loop elegantly implemented with clear state machine.

**Issues:**
1. Skeleton code in production path: `daemon.py`'s `main_loop()` is placeholder.
2. `ABRunner` stores state in-memory — lost on restart. Should persist to SQLite.
3. `dimension` values use string literals ("prompts", "thresholds") instead of `MutationDimension` enum.
4. Magic numbers not all centralized: `AUTO_ROLLBACK_WINDOW = 50` in rollback.py, `probation_cycles = 30` in promotion.py.
5. `_t_cdf` approximation undocumented — no accuracy reference.

**Code Quality:** 80% — clean and maintainable, needs skeleton completion and state persistence.

---

### Dimension 6: Completeness Gaps — **3/5**

**What's Missing (Critical for Phase 7 -> Phase 8 transition):**

1. **`pmacs/nervous/mutation.py`**: Should contain `apply_candidate_to_registry()` with atomic write (temp + rename).
2. **`daemon.py` production wiring**: Main loop needs to query FDE clusters from KuzuDB, read Brier drift from DuckDB, call `generate_candidates()`, insert proposals to SQLite, start/monitor A/B tests, run stat tests, publish SSE events, check rollback conditions every cycle.
3. **Rollback integration**: `regression_detected()` needs actual DuckDB metrics, not synthetic parameters. `execute_rollback()` needs to call `apply_candidate_to_registry()` with rollback config. Kill-switch integration missing.
4. **UI integration**: Settings -> Mutation Engine panel not implemented. No templates/routes for listing A/B tests, showing progress bars, promote/reject buttons, rollback history.
5. **Configuration files**: `config/mutation.toml` exists but not read by any code. `activation_after_paper_cycles` should be loaded from config.
6. **Database tables**: `mutation_proposals`, `mutation_outcomes` schema exists but no migration scripts or indexes documented.

**Completeness:** 60% — core algorithms solid, production integration/UI/ops tooling incomplete.

---

### Overall: **3.7/5**

### Critical Issues: **3**

1. **Missing `apply_candidate_to_registry()` function** — Security boundary violation; blocks promotion flow.
2. **Daemon is skeleton-only** — System cannot run mutation lifecycle end-to-end.
3. **No filesystem permission test** — Level 1 rollback safety unverified.

### Recommendations (Prioritized)

**P0 (Blocker for Phase 8):**
1. Implement `pmacs/nervous/mutation.py` with `apply_candidate_to_registry()` using atomic write (temp + rename).
2. Wire `daemon.py` main loop to actual data sources (FDE, DuckDB, SQLite).
3. Add filesystem permission test: `pytest tests/integration/test_rollback.py::test_level1_structural_separation`.
4. Centralize magic numbers to `config/mutation.toml`.

**P1 (Technical Debt):**
5. Improve t-CDF accuracy or use `scipy.stats.t` if available.
6. Add state persistence to `ABRunner` (SQLite-backed).
7. Integrate rollback with DuckDB metrics queries.
8. Add kill-switch mutation review wiring.

**P2 (Nice-to-Have):**
9. Add stress tests for statistical functions (extreme values, skewed distributions).
10. Implement Settings -> Mutation Engine UI.
11. Add property-based tests for mutation invariants.
12. Document `_t_cdf` approximation accuracy.

**Summary:** Phase 7 delivers a solid foundation with correct algorithms and good test coverage. The core issue is incomplete production wiring — the system has all the right parts but they're not fully connected. With the P0 items addressed, this would be a 4.5/5 implementation. The advisor-only model and TOTP enforcement are exemplary security practices.

---

## Reviewer Availability

| Reviewer | Available | Status |
|---|---|---|
| Claude Sonnet 4.6 | Yes | Reviewed above |
| Gemini | No | `gemini` CLI not found |
| Codex | No | `codex` CLI not found |
| OpenCode | No | `opencode` CLI not found |
| Qwen | No | `qwen` CLI not found |
| Cursor | No | `cursor` CLI not found |
