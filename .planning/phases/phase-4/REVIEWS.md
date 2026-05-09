# Phase 4 Cross-AI Peer Reviews

**Phase:** 4 — Pipeline + Paper (PMACS Build Phases 7-8)
**Date:** 2026-05-09
**Reviewers:** Claude Opus 4.6 (in-session), Claude Sonnet 4.6 (external CLI)

---

## Reviewer: Claude Opus 4.6 (In-Session)

### Dimension 1: Spec Compliance — **3/5**

**Strengths:**
- Conviction thresholds match spec exactly: STRONG_BUY >= 0.6, BUY >= 0.3, SKIP < 0.3 (Architecture.md §9.2). `verdict_tier()` at `conviction.py:57-61` implements this correctly.
- Sizing uses half-Kelly (`sizing.py:57`), bootstrap haircut table, limited-history haircut (0.50), correlation factor, max-position cap (20%). Matches Architecture.md §9.3.
- EV formula correct: `ev = p_up * gain - p_down * loss`, `ev_multiple = EV / stop_loss` (`pricing.py:28-29`). Matches Architecture.md §9.4.
- Portfolio risk gate: 5 positions, 20% concentration, 40% sector limits (`portfolio_risk_gate.py:14-20`). All constants match spec.
- Catastrophe-net: 15% below entry (`catastrophe_net.py:22`), uses `CATASTROPHE_NET_PCT` from constants. Matches Architecture.md §9.
- Paper ledger starts at $5,000 (`ledger.py:53`), uses `PAPER_CAPITAL_USD` constant.
- Mode transitions match spec: INSTALLING → SHADOW + PAPER, TOTP required for LIVE modes (`system.py:52-60`, `mode_manager.py:53-56`).
- Wizard has 11 steps (WELCOME=1 through COMPLETE=11) — matches Source.md §12 step count.
- Crucible temperature 0.1 ✓, MemoWriter temperature 0.3 ✓ — matches spec.

**Critical Deviations:**
1. **Crucible `build_prompt()` never includes evidence** (`crucible.py:46-62`): `evidence_text` is built at lines 51-54 but never substituted into the template. `template.replace("{episodic_context}", context_block)` only replaces episodic context. The Crucible — which is supposed to "get the full evidence set" (Agents.md §12) — receives zero evidence. This renders the adversarial loop ineffective.
2. **MemoWriter `build_prompt()` same bug** (`memo_writer.py:46-62`): Identical pattern. `evidence_text` built but never used. MemoWriter can't synthesize outputs it never sees.
3. **Conviction engine missing non-bootstrap maturity floor** (`conviction.py:33-34`): Spec says `maturity_factor is floored at 0.25` for non-bootstrap. Implementation has no floor — if `matured_sources_used = 0`, maturity_factor = 0.0, making conviction = 0.0 even with strong directional signal.
4. **Wrong spec section references**: Crucible docstring says `Agents.md §14` but spec §12 is Crucible. MemoWriter says `Agents.md §15` but spec §13 is MemoWriter. §14 and §15 don't exist in Agents.md.

**Spec Alignment:** 60% — core formulas correct, but Crucible and MemoWriter are non-functional without evidence injection.

---

### Dimension 2: Statistical Correctness — **4/5**

**Strengths:**
- Conviction formula is a multiplicative decomposition: `direction × maturity × crucible × ev`. Each factor in [0, 1]. Product is in [-1, 1]. Correct.
- direction = `p_up - p_down` — captures directional bias correctly.
- crucible_factor = `1 - severity` — linear penalty. At severity=0.6, factor=0.4. At severity=1.0, factor=0.0. Correct.
- ev_factor = `ev_multiple / 1.5` capped at 1.0 — diminishing returns on EV. Sound.
- Half-Kelly formula: `f = (p_up * gain - p_down * loss) / loss` — valid formulation of Kelly criterion. `safety_kelly = kelly_fraction * 0.5` — standard half-Kelly. Correct.
- Bootstrap haircut table {0: 0.50, 1: 0.65, 2: 0.80, 3: 0.90} — monotonically increasing, reasonable progression.
- Correlation factor: `max(0.3, 1.0 - max(correlations))` — ensures minimum 30% position even with perfect correlation. Sound.

**Concerns:**
1. **Maturity factor denominator is hardcoded to 4.0** (`conviction.py:32-34`): `matured_sources_used / 4.0`. But there are 7 possible analysis personas. If 6 sources are mature, maturity = 6/4 = 1.5, capped to 1.0. If only 1 is mature, maturity = 0.25. The denominator should arguably be the number of analysis personas or a configurable constant.
2. **No floor on non-bootstrap maturity factor**: As noted above. With 0 mature sources, conviction = 0 regardless of signal strength.
3. **Kelly fraction can be very large**: If stop_loss_pct is tiny and p_up is high, Kelly fraction can be >> 1.0. The half-Kelly mitigates this, and `max_position_pct` caps it at 20%, but there's no explicit upper bound on the raw Kelly fraction.
4. **EV formula assumes binary outcome** (up or down): `ev = p_up * gain - p_down * loss`. The flat probability `p_flat` is ignored in EV computation. This means a position with p_up=0.5, p_flat=0.4, p_down=0.1 has the same EV as p_up=0.5, p_flat=0.0, p_down=0.1. The flat component is dead money — the formula is correct for EV of a *directional* trade where flat = no gain/loss, but this should be documented.

**Statistical Rigor:** 80% — formulas are mathematically sound, minor gaps in edge cases.

---

### Dimension 3: Security & Safety — **5/5**

**Strengths:**
- LLMs never sign trades: Crucible and MemoWriter produce structured outputs only — no trade execution capability. ✓
- LLMs never math: Conviction, sizing, EV, risk gate are all pure deterministic Python. ✓
- Catastrophe-net always placed: `ledger.py:104-105` defaults stop_price to catastrophe-net if not provided. `catastrophe_net.py` computes 15% below entry. ✓
- TOTP gating on LIVE modes: `mode_manager.py:53-56` enforces TOTP verification for LIVE_EARLY, LIVE_STANDARD, LIVE_EXPANDED. ✓
- Paper ledger enforces position limits: max 5 concurrent (`ledger.py:94`), max 20% single position (`ledger.py:86-91`), cash sufficiency check (`ledger.py:80-83`). ✓
- Catastrophe-net order validation: `validate_stop_order()` checks all required fields, side=SELL, type=STOP_MARKET, reason=catastrophe_net. ✓
- Mode transitions are explicit: `VALID_MODE_TRANSITIONS` whitelist prevents invalid jumps. ✓
- Kill switch triggers are comprehensive: 10 trigger types including DAILY_LOSS, ROLLING_5D_LOSS, AUDIT_CHAIN_BREAK, CRASH_LOOP. ✓
- No tight broker-side stops: `catastrophe_net.py` docstring explicitly references anti-pattern from Architecture.md §16.7. ✓

**No Concerns Found.**

**Safety Score:** 100% — all Five Non-Negotiables are upheld.

---

### Dimension 4: Test Coverage & Quality — **4/5**

**Strengths:**
- 471 tests passed. Phase 4 deliverables include 112 tests:
  - Unit: test_conviction (17), test_sizing (16), test_portfolio_risk_gate (9), test_crucible_budget (10), test_crucible_memo (15) = 67
  - Integration: test_paper_trade (38) = 38
  - E2E: test_smoke_cycle (7) = 7
- Conviction tests verify thresholds (STRONG_BUY >= 0.6, BUY >= 0.3, SKIP < 0.3)
- Sizing tests verify bootstrap haircuts, limited-history haircuts, half-Kelly, max-position cap
- Crucible budget tests verify 90s timeout → NO_TRADE, 2 cycle max → NO_TRADE, severity > 0.6 → SKIP
- Paper trade tests cover full lifecycle: open → update → close, position limits, catastrophe-net
- E2E smoke test covers full pipeline sequence with audit chain

**Gaps:**
1. **No test verifying Crucible/MemoWriter `build_prompt()` includes evidence**: Since the code has a bug (evidence not injected), tests likely pass because they mock the LLM call and never exercise the prompt-building path.
2. **No test for conviction with 0 mature sources (non-bootstrap)**: The maturity floor gap (0.0 floor for non-bootstrap) isn't caught because no test checks the edge case.
3. **No test for mode transition SHADOW → PAPER_VALIDATED (skipping PAPER)**: The transition table shows SHADOW → PAPER, but no test validates the transition graph completeness.
4. **3 pre-existing test failures** (API key related) — not Phase 4's fault but should be noted.

**Test Coverage:** 80% — good coverage of happy paths and most edge cases, but evidence injection bug is untested.

---

### Dimension 5: Code Quality — **4/5**

**Strengths:**
- Clean engine design: each engine is a pure function taking a frozen input dataclass/dataclass and returning a frozen output. No side effects.
- Consistent use of frozen dataclasses for inputs/outputs (conviction, sizing, pricing, risk gate).
- Constants properly extracted to `pmacs/constants.py`: CATASTROPHE_NET_PCT, MAX_CONCURRENT_POSITIONS, MAX_SINGLE_POSITION_PCT, PAPER_CAPITAL_USD.
- Good separation of concerns: computation (engines) vs. orchestration (nervous) vs. execution (execution service).
- `catastrophe_net.py` correctly does NOT submit orders — just builds the order dict.
- Mode manager is a clean state machine with explicit transition table.
- Wizard has clean sequential step progression with config collection.

**Issues:**
1. **Dead code in Crucible and MemoWriter `build_prompt()`**: `evidence_text` variable built but never used. Linter should catch this but didn't.
2. **Inconsistent schema approach**: Conviction uses Pydantic (`ConvictionResult`), but sizing, pricing, and risk gate use frozen dataclasses. Should be consistent — either all Pydantic or all dataclasses. CLAUDE.md says "ALL Pydantic models live in `pmacs/schemas/`" — but sizing/pricing/risk_gate don't use Pydantic.
3. **`datetime.utcnow()` deprecated** (`ledger.py:112`): Python 3.12+ deprecates this in favor of `datetime.now(timezone.utc)`.
4. **MemoWriter temperature comment wrong** (`memo_writer.py:30`): "Higher than analysis, lower than Crucible" — but 0.3 > 0.1 (Crucible), so it's higher than both. Should say "Highest temperature" or just remove the comparison.
5. **`getattr()` pattern in Crucible/MemoWriter evidence access** (`crucible.py:53-54`, `memo_writer.py:53-54`): Uses `getattr(packet, "evidence", [])` and `getattr(ev, "id", "unknown")` while personas 1-3 use direct attribute access. Inconsistent with Phase 3 personas.

**Code Quality:** 75% — clean architecture, but evidence injection bug and inconsistency issues.

---

### Dimension 6: Completeness Gaps — **3/5**

**What's Delivered (Complete):**
- Conviction engine with verdict tiers ✓
- Sizing engine with half-Kelly + haircuts ✓
- EV/pricing engine ✓
- Portfolio risk gate ✓
- Crucible persona (4-file structure) ✓
- MemoWriter persona (4-file structure) ✓
- Paper ledger ($5K) ✓
- Catastrophe-net stop ✓
- Mode manager with TOTP gating ✓
- 11-step wizard ✓
- All sanity validators ✓
- 112 tests ✓

**What's Missing (Blocking for Phase 5):**
1. **Evidence injection in Crucible and MemoWriter** — both personas receive no evidence in their prompts. The Crucible's adversarial attack is blind. This makes the entire Crucible step a no-op in practice.
2. **Non-bootstrap maturity floor** — without it, conviction = 0 whenever no sources are matured, which means SKIP even with strong directional signal from bootstrap personas.
3. **Crucible inner loop implementation** — the phase definition calls for "2-cycle max, 90s budget, NO_TRADE default" (Agents.md §16). The tests verify budget constraints, but the actual loop orchestration (running multiple attack cycles) doesn't appear to be implemented in `crucible.py`. The runner only makes a single LLM call.
4. **Alpaca paper adapter** — listed in the phase definition (`pmacs/sim/alpaca_paper_adapter.py`, `pmacs/execution/alpaca_adapter.py`) but not in the SUMMARY.md deliverables. May be deferred to Phase 5.
5. **Schemas in `pmacs/schemas/` for sizing/pricing/risk_gate** — these engines use inline dataclasses instead of Pydantic models in the schemas directory. Violates CLAUDE.md convention.

**Completeness:** 55% — engines and schemas work, but the Crucible loop is incomplete and evidence injection is broken.

---

### Overall: **3.8/5**

### Critical Issues: **3**

1. **Crucible and MemoWriter `build_prompt()` don't inject evidence** — the adversarial thesis attacker and the memo writer receive no evidence. `evidence_text` is built but never substituted into the template.
2. **Conviction non-bootstrap maturity floor missing** — spec says floor at 0.25, implementation has no floor (can reach 0.0).
3. **Crucible inner loop not implemented** — spec requires 2-cycle adversarial attack with 90s budget. The runner makes a single LLM call with no loop.

### Recommendations (Prioritized)

**P0 (Fix before Phase 5):**
1. Fix `build_prompt()` in both `crucible.py` and `memo_writer.py`: add `template.replace("{evidence}", evidence_text)` alongside the existing `{episodic_context}` replacement. Verify the prompt templates have `{evidence}` placeholder.
2. Add `maturity_factor = max(0.25, ...)` for the non-bootstrap path in `conviction.py:34`.
3. Implement Crucible inner loop: 2-cycle max, 90s timeout, NO_TRADE on budget exhaust, severity > 0.6 cycle 1 → SKIP without cycle 2.
4. Fix spec section references: Crucible → §12, MemoWriter → §13.

**P1 (Technical Debt):**
5. Convert sizing/pricing/risk_gate schemas to Pydantic v2 BaseModel in `pmacs/schemas/` — consistent with CLAUDE.md convention.
6. Fix `datetime.utcnow()` → `datetime.now(timezone.utc)` in `ledger.py`.
7. Fix MemoWriter temperature comment.
8. Unify evidence access pattern: use direct attribute access consistently (not `getattr()`).

**P2 (Nice-to-Have):**
9. Add test for `build_prompt()` evidence injection in Crucible and MemoWriter.
10. Add test for conviction with 0 mature sources (non-bootstrap edge case).
11. Document why EV ignores p_flat (binary outcome assumption for directional trades).
12. Make maturity factor denominator (4.0) a named constant or derive from persona count.

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
| Cursor | No | `cursor` CLI not found |

---

## Reviewer: Claude Sonnet 4.6 (External CLI)

### Dimension 1: Spec Compliance — **4/5**

**Strengths:**
- Conviction formula matches Architecture.md §9.2: `direction * maturity_factor * crucible_factor * ev_factor` with correct constants
- Verdict tiers match Source.md §7.2: STRONG_BUY >= 0.6, BUY >= 0.3, SKIP < 0.3
- Sizing engine matches Architecture.md §9.3: half-Kelly (0.5), bootstrap haircut schedule, limited-history 0.50, correlation floor 0.3, max 20%
- Risk gate matches Architecture.md §9.5: 5 positions, 20% concentration, 40% sector
- Crucible matches Agents.md §14: temperature=0.1, max_tokens=768, severity as max, thesis_survives vs 0.6
- MemoWriter matches Agents.md §15: temperature=0.3, correct output schema
- Catastrophe-net matches Architecture.md §16.7: 15% below entry, broker-side only
- Mode ladder matches Source.md §1: INSTALLING → SHADOW → PAPER with TOTP on LIVE modes
- Paper capital matches: $5,000 start, 20% max single, 5 max concurrent

**Major Issues:**
1. **HOLD verdict mapped to BUY** — `verdict_tier()` line 56 returns `VerdictTier.BUY` for active holdings with valid thesis. Source.md §7.2 lists HOLD as a distinct tier with blue-500 color. This conflation may cause incorrect UI rendering.
2. **MemoWriter sanity missing probability sum check** — No validation that `p_up + p_flat + p_down ≈ 1.0`.
3. **MemoWriter sanity missing conviction range check** — No validation that conviction is within [0.0, 1.0].

**Minor Issues:**
4. Bootstrap haircut lookup condition `n_mature < 4` is misleading since n_mature is already clamped to [0,4].
5. Crucible prompt mentions "2 rewrite cycles" but runner doesn't enforce the budget — relies on external CrucibleLoop.

---

### Dimension 2: Statistical Correctness — **4/5**

**Strengths:**
- Kelly formula correct: `f = (p_up * gain - p_down * loss) / loss` — standard binary Kelly
- Half-Kelly correct: `kelly_fraction * 0.5`
- EV formula correct: `EV = p_up * gain - p_down * loss`
- Bootstrap haircut schedule monotonic: 50% → 65% → 80% → 90% → 100%
- Conviction multiplicative model bounded correctly

**Major Issues:**
1. **Kelly formula assumes binary outcome but probabilities are ternary** — `p_flat` dropped from Kelly computation. Should use conditional probability `p_up / (1 - p_flat)` or document the simplification.
2. **Negative stop_loss_pct not guarded** — `compute_kelly()` checks `== 0` but not `< 0`. Negative stop_loss flips Kelly sign.

**Minor Issues:**
3. Bootstrap maturity floor can amplify negative direction asymmetrically.
4. Correlation factor floor at 0.3 means perfectly correlated positions still get 30% size.

---

### Dimension 3: Security & Safety — **3/5**

**Strengths:**
- LLMs never sign trades ✓
- LLMs never math ✓
- Hash-chained audit log ✓
- TOTP gating on LIVE modes ✓
- Catastrophe-net always placed ✓

**Critical Issues:**
1. **Phase 4 engines emit NO audit entries** — conviction.py, sizing.py, pricing.py, portfolio_risk_gate.py compute critical decisions but leave zero audit trail. Architecture.md §1.8 requires "ALWAYS log to both audit AND debug streams." Violates Five Non-Negotiable #3.
2. **Kill switch not checked by any Phase 4 engine** — If kill switch is engaged, engines continue computing and ledger continues accepting positions. Engines are defenseless.
3. **Mode transition has no persistence** — `transition_mode()` returns ModeTransition but doesn't persist it. On restart, mode state is lost.

**Major Issues:**
4. **PaperLedger uses PAPER_CAPITAL_USD for position limit, not total_value** — 20% limit always calculated against initial $5K, not current portfolio value. More conservative but doesn't match spec's dynamic sizing intent.
5. **Wizard smoke_test is a stub** — always returns `all_ok: True` without verifying LLM/data/broker connectivity.

---

### Dimension 4: Test Coverage & Quality — **4/5**

**Strengths:**
- 112 tests across 7 files
- Pure deterministic tests with `pytest.approx()`
- Boundary tests at 0.6, 0.3 verdict thresholds
- Edge cases: negative Kelly, zero stop_loss, full severity, duplicate tickers, insufficient cash
- E2E pipeline test covers arbitrate → conviction → sizing → risk gate → ledger → catastrophe stop

**Gaps:**
1. No test for audit emission from Phase 4 engines
2. Missing exact boundary tests: 20.0% concentration, 40.0% sector, severity exactly 0.6 in conviction
3. Wizard tests are structural (check dict keys) not functional (verify checks work)
4. No test for mode transition persistence

---

### Dimension 5: Code Quality — **4/5**

**Strengths:**
- Clean pure-function architecture: input dataclass → function → output dataclass
- Spec references in every file
- Constants centralized in pmacs/constants.py
- Full type annotations
- No global state

**Major Issues:**
1. **Magic numbers** — `0.50`, `4.0`, `1.5`, `0.3` hardcoded in formulas rather than named constants
2. **No logging** — None of the engines emit debug or info logs. Architecture.md §1.8 requires dual-stream logging.
3. **`build_prompt` in CrucibleRunner** — template substitution only replaces `{episodic_context}`, not `{evidence}`. Evidence text built but never inserted.

---

### Dimension 6: Completeness Gaps — **3/5**

**Missing Components:**
1. **CrucibleLoop engine** — spec requires 2-cycle/90s-budget/NO_TRADE state machine. Only single-shot LLM call exists.
2. **Audit integration** — no audit emission from any Phase 4 engine
3. **Kill switch integration** — engines don't check kill switch state
4. **Wizard steps incomplete** — Only 7 of 11 step files. Smoke test is stub. VERIFY_LLM and VERIFY_DATA not implemented.
5. **SHADOW mode behavior** — no SHADOW-specific audit-only behavior implemented
6. **Mode transition persistence** — no SQLite/file persistence
7. **Auto-demotion logic** — schema supports `triggered_by="AUTO_DEMOTION"` but no logic implements it

**Overall: 3.7/5**

---

## Cross-Review Consensus

**Both reviewers agree on these critical issues:**

1. **Crucible and MemoWriter `build_prompt()` don't inject evidence** — evidence_text built but never substituted. Templates also lack `{evidence}` placeholder. Both are bugs.
2. **Crucible inner loop not implemented** — spec requires 2-cycle/90s-budget/NO_TRADE. Only single-shot LLM call exists.
3. **No audit emission from Phase 4 engines** — conviction, sizing, pricing, risk gate produce no audit trail.
4. **No kill switch integration in Phase 4 engines** — engines continue operating if kill switch engaged.

**Score convergence:**

| Dimension | Opus 4.6 | Sonnet 4.6 | Consensus |
|---|---|---|---|
| Spec Compliance | 3/5 | 4/5 | 3.5/5 |
| Statistical Correctness | 4/5 | 4/5 | 4.0/5 |
| Security & Safety | 5/5 | 3/5 | 4.0/5 |
| Test Coverage | 4/5 | 4/5 | 4.0/5 |
| Code Quality | 4/5 | 4/5 | 4.0/5 |
| Completeness | 3/5 | 3/5 | 3.0/5 |
| **Overall** | **3.8/5** | **3.7/5** | **3.75/5** |

### Unified P0 Recommendations (Fix before Phase 5)

1. **Fix evidence injection** — Add `{evidence}` placeholder to Crucible/MemoWriter prompt templates AND substitute `evidence_text` in `build_prompt()`
2. **Add non-bootstrap maturity floor** — `conviction.py:34` needs `max(0.25, ...)` for non-bootstrap path
3. **Implement CrucibleLoop** — 2-cycle max, 90s timeout, NO_TRADE default, severity > 0.6 → SKIP without cycle 2
4. **Add audit emission** — All Phase 4 engines must emit hash-chained audit entries with `cycle_id` (Architecture.md §1.8, Five Non-Negotiable #3)
5. **Fix spec section references** — Crucible → §12, MemoWriter → §13

### Unified P1 Recommendations

6. Convert sizing/pricing/risk_gate to Pydantic v2 BaseModel in `pmacs/schemas/`
7. Add kill switch check in pipeline orchestration
8. Extract magic numbers to named constants
9. Add debug logging to all engines
10. Fix `datetime.utcnow()` → `datetime.now(timezone.utc)`
11. Guard against negative stop_loss_pct
12. Implement wizard smoke_test (actual LLM/data/broker verification)
13. Add HOLD as distinct VerdictTier

---

## Supplementary Agent Findings

Four parallel code-review agents examined the implementation in depth. Their findings surfaced additional critical issues not caught by the primary reviewers.

### Additional Critical Issues from Agents

**AC-01: Base sanity validator always rejects Crucible and MemoWriter outputs**

File: `pmacs/agents/sanity/base.py` lines 41-43

The base sanity validator unconditionally checks for a `reasoning` field:
```python
reasoning = output.get("reasoning", "")
if not reasoning or not reasoning.strip():
    return SanityResult(passed=False, reason="reasoning field is empty")
```

Neither `CrucibleOutput` nor `MemoWriterOutput` has a `reasoning` field. `output.get("reasoning", "")` returns `""`, which is falsy, so the validator always returns `passed=False`. **Both Crucible and MemoWriter personas can never produce valid output.** The three-layer pipeline fails at Layer 3 on every attempt, exhausts retries, and returns `None` (abort).

**Fix:** Change base sanity to skip reasoning check when field is absent:
```python
reasoning = output.get("reasoning", None)
if reasoning is not None and not reasoning.strip():
    return SanityResult(passed=False, reason="reasoning field is empty")
```

**AC-02: PAPER_VALIDATED transition not TOTP-gated**

File: `pmacs/engines/mode_manager.py:12-16`

`LIVE_MODES` frozenset only contains `LIVE_EARLY`, `LIVE_STANDARD`, `LIVE_EXPANDED`. But spec (Source.md line 147, Phases.md §3.4) explicitly states promotion to `PAPER_VALIDATED` also requires TOTP: "Moving from PAPER to PAPER_VALIDATED, or from any PAPER mode to any LIVE mode, requires TOTP."

**Fix:** Add `Mode.PAPER_VALIDATED` to the TOTP-required set.

**AC-03: Wizard steps don't match spec Source.md §12**

File: `pmacs/installer/wizard.py:12-24`

The `WizardStep` enum defines a completely different 11-step sequence than what Source.md §12 prescribes. The spec calls for: Welcome, Backend detection, Model download, Keychain setup, Embedding model, Data connectivity, Universe seed, Cycle preferences, TOTP enrollment, Smoke test, Promote to SHADOW+PAPER. The code has: Welcome, Check system, Create dirs, Generate keys, Configure LLM, Verify LLM, Configure data, Verify data, Configure broker, Smoke test, Complete.

**AC-04: HOLD verdict missing from VerdictTier enum**

File: `pmacs/schemas/conviction.py:10-13`

`VerdictTier` only has STRONG_BUY, BUY, SKIP. Source.md §7.2 defines four tiers including HOLD (blue-500 color, "position maintained"). Architecture.md line 1645 shows the DB `verdict` column should contain HOLD. `verdict_tier()` maps active holdings to BUY instead of HOLD.

**AC-05: `test_full_pipeline.py` does not exist**

Phase 7 exit test 1 requires a full pipeline integration test (Gatekeeper → 7 personas → Arbitration → Crucible → EV → Sizing → Conviction → Risk Gate → Verdict → MemoWriter). This file is listed in the phase definition but was never created. The E2E smoke test covers only a subset.

### Additional Major Issues from Agents

- **Risk gate doesn't check available cash** — can approve positions exceeding cash on hand
- **Crucible prompt diverges from spec §12.3** — missing severity 0.8 guidance, "Do not offer balanced perspectives", "Do not invent flaws to justify your existence"
- **MemoWriter prompt diverges from spec §13.3** — missing "What did they see?" in DISSENT, missing "do not over-explain standard financial concepts"
- **MemoWriter sanity missing conviction consistency check** — spec §13.5 requires conviction match engine output ±0.01
- **Ledger `open_position` doesn't validate shares > 0 or price > 0** — zero/negative values create bogus positions
- **Ledger `close_position` doesn't validate close price > 0**
- **Key generation step doesn't set directory permissions (0o700)** — only files get chmod 0o600
- **Wizard step ordering not enforced** — `complete_step` docstring says "raises ValueError if out of order" but no check exists
- **configure_broker.py discards API keys** — stores boolean flags, not actual credentials

### Updated Critical Issue Count: **8**

The original 3 + 5 additional from agents = 8 critical issues. The two most severe are AC-01 (base sanity rejects all Crucible/MemoWriter output) and the evidence injection bug — together they make both Phase 4 personas completely non-functional.
