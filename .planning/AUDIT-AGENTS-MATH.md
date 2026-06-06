# PMACS Agent & Math Engine Audit Report

**Date:** 2026-05-30
**Scope:** Agent personas (pmacs/agents/), math engines (pmacs/engines/), orchestrator dispatch (pmacs/nervous/orchestrator.py)
**Auditor:** Claude Code (deep audit)
**Spec references:** Architecture.md SS9, SS16; Agents.md SS3-13, SS15, SS18; Source.md constants

---

## Executive Summary

Deep audit of the 7 analysis personas, 7 mathematical engines, and orchestrator dispatch pipeline. **16 findings**: 0 Critical, 4 High, 8 Medium, 4 Low. The most significant issues are:

1. **All ArbitrationSignals created as immature** -- orchestrator never passes Brier scores or historical_n, so the Brier-inverse weighting system (the core of arbitration) is effectively disabled. Every signal uses the default uniform Brier (0.667), making all weights equal.

2. **Six agents reference `ev.content` which does not exist** on the Evidence model (it has `data: dict` and `title: str`). These agents produce garbage evidence text in prompts, sending `str(ev)` (the full Pydantic repr) to the LLM instead of structured content.

3. **State machine opens a new AuditWriter per transition** -- potential file descriptor leak under high throughput.

The mathematical formulas in arbitration.py, conviction.py, sizing.py, and pricing.py are individually correct. The architecture is sound. The bugs are integration-level: data that the engines need is not being passed correctly.

---

## Three-Layer Contract Verification

### Per-Persona File Inventory

| Persona | Runner | Prompt | Grammar | Sanity |
|---------|--------|--------|---------|--------|
| MacroRegime | base.py / macro_regime.py | prompts/macro_regime.md | grammars/macro_regime.gbnf | sanity/macro_regime.py |
| CatalystSummarizer | catalyst_summarizer.py | prompts/catalyst_summarizer.md | grammars/catalyst_summarizer.gbnf | sanity/catalyst_summarizer.py |
| MoatAnalyst | moat_analyst.py | prompts/moat_analyst.md | grammars/moat_analyst.gbnf | sanity/moat_analyst.py |
| GrowthHunter | growth_hunter.py | prompts/growth_hunter.md | grammars/growth_hunter.gbnf | sanity/growth_hunter.py |
| InsiderActivity | insider_activity.py | prompts/insider_activity.md | grammars/insider_activity.gbnf | sanity/insider_activity.py |
| ShortInterest | short_interest.py | prompts/short_interest.md | grammars/short_interest.gbnf | sanity/short_interest.py |
| Forensics | forensics.py | prompts/forensics.md | grammars/forensics.gbnf | sanity/forensics.py |
| Crucible | crucible.py | prompts/crucible.md | grammars/crucible.gbnf | sanity/crucible.py |
| MemoWriter | memo_writer.py | prompts/memo_writer.md | grammars/memo_writer.gbnf | sanity/memo_writer.py |

**Status: PASS** -- All 9 personas have complete 4-file sets (runner, prompt, grammar, sanity).

---

## Findings

### HIGH-01: ArbitrationSignals Always Created as Immature (Brier-Weighting Disabled)

**Severity:** HIGH
**Category:** Logic error -- core arbitration feature non-functional
**Files:** `pmacs/nervous/orchestrator.py:1313`, `pmacs/engines/arbitration.py:54-63`

**Issue:**
The orchestrator constructs `ArbitrationSignal` objects at line 1313:
```python
signals.append(ArbitrationSignal(dp))
```

The `ArbitrationSignal.__init__` defaults `historical_n=0` and `rolling_brier=0.667` (UNINFORMED_3STATE_BRIER). Since no Brier scores or historical counts are ever passed, **every signal is classified as immature** (`is_mature` returns `False` because `historical_n < 30`).

This means:
- The mature-source path (lines 270-339 in arbitration.py) is **never reached** in practice
- Brier-inverse weighting (`1 / (brier + epsilon)`) is **never computed** for real data
- All arbitration falls through to the bootstrap path (equal-weight average)
- The MacroRegime 0.5x weight multiplier and extreme-probability dampening are **never applied**

The calibration engine (calibration.py) correctly computes Brier scores and refits weights, but its output is never wired back into the orchestrator's signal construction.

**Fix:**
In `_step_13e_arbitration`, look up per-persona Brier scores and historical counts from DuckDB before constructing signals:
```python
# After building dp from _extract_directional_probability:
duckdb_adapter = self._get_duckdb_adapter()
rolling_brier = UNINFORMED_3STATE_BRIER
historical_n = 0
if duckdb_adapter is not None:
    row = duckdb_adapter.execute(
        "SELECT avg_brier, cycle_count FROM persona_performance "
        "WHERE persona = ? ORDER BY computed_at DESC LIMIT 1",
        [persona_name_str],
    )
    if row:
        rolling_brier = row[0][0] or UNINFORMED_3STATE_BRIER
        historical_n = int(row[0][1] or 0)
signals.append(ArbitrationSignal(dp, historical_n=historical_n, rolling_brier=rolling_brier))
```

---

### HIGH-02: Six Agents Reference Non-Existent `ev.content` Field

**Severity:** HIGH
**Category:** Logic error -- evidence not reaching LLM prompts
**Files:**
- `pmacs/agents/forensics.py:64`
- `pmacs/agents/growth_hunter.py:71`
- `pmacs/agents/insider_activity.py:63`
- `pmacs/agents/short_interest.py:64`
- `pmacs/agents/crucible.py:54`
- `pmacs/agents/memo_writer.py:54`

**Issue:**
The `Evidence` schema (`pmacs/schemas/data.py:42-55`) has these fields:
```
id, source, type, ticker, fetched_at, content_hash, data (dict), url, title, published_at
```

There is **no `content` field**. The actual content is in `data: dict[str, Any]`.

These six agents all do:
```python
evidence_text += f"[{getattr(ev, 'id', 'unknown')}] {getattr(ev, 'content', str(ev))}\n"
```

Since `ev.content` does not exist, `getattr(ev, 'content', str(ev))` always falls through to `str(ev)`, which produces the full Pydantic model repr -- something like:
```
Evidence(id='abc123', source=<DataSource.POLYGON: 'polygon'>, type=<EvidenceType.MARKET_DATA: 'market_data'>, ...)
```

This is sent to the LLM as "evidence", wasting tokens and providing no useful information. The LLM makes decisions based on schema metadata, not actual data content.

**Note:** MacroRegime, CatalystSummarizer, and MoatAnalyst use a different, correct pattern: `ev.title or 'untitled'` via `_format_evidence`.

**Fix:**
Replace `getattr(ev, 'content', str(ev))` with proper data extraction. The simplest fix:
```python
# Instead of getattr(ev, 'content', str(ev)):
content = ev.data.get("summary") or ev.data.get("text") or ev.title or str(ev.data)[:200]
evidence_text += f"[{ev.id}] {content}\n"
```

---

### HIGH-03: State Machine Opens New AuditWriter Per Transition (FD Leak Risk)

**Severity:** HIGH
**Category:** Resource leak -- file descriptor exhaustion under load
**File:** `pmacs/engines/state_machine.py:95-105`

**Issue:**
Every state transition creates a fresh `AuditWriter(audit_path)` instance, writes one event, then closes it:
```python
if audit_path is not None:
    from pmacs.storage.audit import AuditWriter
    writer = AuditWriter(audit_path)
    try:
        writer.append("state_transition", transition_event, cycle_id=cycle_id)
    finally:
        writer.close()
```

In a cycle processing 5 symbols with ~5 transitions each, this opens/closes the audit file 25+ times. The `AuditWriter.__init__` opens the file, and while `close()` is in a `finally` block, if the process crashes between open and close (or if the OS file descriptor table is full), events are lost.

The base.py `_audit_llm_call` method (line 602) uses a **shared** `self._audit` writer passed at construction time, which is the correct pattern.

**Fix:**
Accept an optional `AuditWriter` parameter (or use a module-level cached writer) instead of creating a new one per call:
```python
def transition(
    holding: Holding,
    new_state: HoldingState,
    reason: str,
    cycle_id: str,
    op_seq: int,
    audit_path: Path | None = None,
    audit_writer: AuditWriter | None = None,  # prefer this
) -> Holding:
```

---

### HIGH-04: Sizing Engine Missing $1000 Hard Cap

**Severity:** HIGH
**Category:** Spec violation -- position exceeds $1000 max
**File:** `pmacs/engines/sizing.py:73-75`
**Spec ref:** CLAUDE.md: "Max single position: 20% ($1,000)"

**Issue:**
The sizing engine caps at `max_position_pct` (20% = $1000 on $5000 paper capital), but this is a **percentage-only** cap. If `portfolio_value_usd` grows above $5000 (e.g., gains), 20% could exceed $1000.

```python
target_pct = min(target_pct, x.max_position_pct)  # caps at 20%
target_usd = target_pct * x.portfolio_value_usd     # 20% of $6000 = $1200
```

The spec says "Max single position: 20% ($1,000)" -- both constraints apply simultaneously. If portfolio grows to $6000, 20% = $1200 violates the $1000 cap.

**Fix:**
Add a hard USD cap after the percentage cap:
```python
target_usd = target_pct * x.portfolio_value_usd
target_usd = min(target_usd, 1000.0)  # Hard $1000 cap (Source.md)
```

---

### MED-01: Arbitration Bootstrap Equality Check Vacuous

**Severity:** MEDIUM
**Category:** Dead code / logic error
**File:** `pmacs/engines/arbitration.py:221`

**Issue:**
```python
agreement_score=1.0 if _all_agree_direction(immature) else 0.0,
```

This line is inside the `if immature and _all_agree_direction(immature):` block (line 196). The `_all_agree_direction(immature)` check was already True to enter this branch, so the ternary always evaluates to `1.0`. The `else 0.0` is dead code.

**Fix:**
Replace with simply `agreement_score=1.0` or remove the field (the condition guarantees agreement).

---

### MED-02: Conviction `maturity_factor` Floor Too Low for Non-Bootstrap

**Severity:** MEDIUM
**Category:** Edge case -- weak signal amplification
**File:** `pmacs/engines/conviction.py:34`

**Issue:**
```python
maturity_factor = max(0.25, min(arb.matured_sources_used / 4.0, 1.0))
```

When combined with HIGH-01 (all signals are immature), `matured_sources_used` is always 0. The floor of 0.25 means conviction is computed as `direction * 0.25 * crucible * ev`, even when no mature sources exist. This may be intentional for the non-bootstrap case, but it conflicts with the bootstrap floor of 0.50 (higher confidence with fewer sources).

For the non-bootstrap case, 0 matured sources should arguably produce a lower floor (e.g., 0.10) or an abort, since the system has no evidence of calibration quality.

**Fix:**
Consider:
```python
if not is_bootstrap and arb.matured_sources_used == 0:
    maturity_factor = 0.10  # Very low confidence with no mature sources
```

---

### MED-03: Pricing Engine `compute_ev` Ignores ATR When Explicit Values Are Defaults

**Severity:** MEDIUM
**Category:** Logic error -- ATR bypass on default config
**File:** `pmacs/engines/pricing.py:86-92`

**Issue:**
The `using_explicit` check compares against the module-level defaults:
```python
using_explicit = (
    x.target_gain_pct != DEFAULT_TARGET_GAIN_PCT
    or x.stop_loss_pct != DEFAULT_STOP_LOSS_PCT
)
```

When the orchestrator calls `compute_ev(EvInputs(p_up=..., p_down=..., atr_pct=None, current_price=...))` without setting `target_gain_pct` or `stop_loss_pct`, they default to `DEFAULT_TARGET_GAIN_PCT` and `DEFAULT_STOP_LOSS_PCT`. Since `atr_pct=None`, `compute_target_and_stop` returns the same defaults. So the check passes and `using_explicit` is False.

However, if the config file values differ from the code defaults (0.10 and 0.15), and the caller passes the config-derived values explicitly, the `!=` comparison will trigger and the ATR-based computation is skipped even when `atr_pct=None` (no ATR available). This is a corner case that could produce wrong target/stop values.

**Fix:**
Add `atr_pct` to the explicit check:
```python
using_explicit = (
    atr_pct is None  # ATR not available, use explicit values
    and (x.target_gain_pct != DEFAULT_TARGET_GAIN_PCT
         or x.stop_loss_pct != DEFAULT_STOP_LOSS_PCT)
)
```

---

### MED-04: Kelly Formula Assumes Symmetric Payoff

**Severity:** MEDIUM
**Category:** Mathematical limitation
**File:** `pmacs/engines/sizing.py:39-42`

**Issue:**
```python
def compute_kelly(p_up, p_down, target_gain_pct, stop_loss_pct):
    if stop_loss_pct == 0:
        return 0.0
    return (p_up * target_gain_pct - p_down * stop_loss_pct) / stop_loss_pct
```

The standard Kelly formula for asymmetric payoff is:
```
f = (p/b - q) where b = gain/loss ratio
```

The implemented formula is a simplified variant that divides by `stop_loss_pct` rather than using the gain/loss ratio. This produces a mathematically different result when `target_gain_pct != stop_loss_pct`.

Example: `p_up=0.6, p_down=0.3, target=0.10, stop=0.15`
- Implementation: `(0.6*0.10 - 0.3*0.15) / 0.15 = (0.06 - 0.045) / 0.15 = 0.10`
- Standard Kelly: `(0.6 * (0.10/0.15) - 0.3) = 0.4 - 0.3 = 0.10`

The results match in this case, but the division by `stop_loss_pct` can produce unreasonably large Kelly fractions when `stop_loss_pct` is very small (but not zero). The zero guard catches `stop_loss_pct == 0` but not very small values like 0.001.

**Fix:**
Add a minimum stop_loss guard:
```python
if stop_loss_pct < 0.01:
    return 0.0
```

---

### MED-05: Orchestrator `_current_price` Stored as Instance Attribute

**Severity:** MEDIUM
**Category:** Thread safety / state leak
**File:** `pmacs/nervous/orchestrator.py:1267`

**Issue:**
```python
self._current_price = current_price
```

This is set in `_step_13d_personas` and read in `_step_13h_l_decision` (line 1479). Between these calls, other symbol processing could overwrite `self._current_price` if the orchestrator ever processes symbols concurrently. Currently it does not (symbols are sequential), but this is fragile -- the price should be threaded through the pipeline explicitly rather than stored on `self`.

**Fix:**
Pass `current_price` through the return tuples of each step, or store it in a per-symbol dict.

---

### MED-06: `compute_brier` Uses Population Variance Instead of Brier Score

**Severity:** MEDIUM
**Category:** Specification conformance
**File:** `pmacs/engines/calibration.py:46-49`

**Issue:**
The implementation computes:
```python
result = sum((f - a) ** 2 for f, a in zip(forecast, actual_vec))
```

For a 3-outcome forecast, the standard Brier score divides by `2R` where `R` is the number of possible outcomes. The formula is:
```
Brier = (1/2R) * sum((f_i - o_i)^2) = (1/6) * sum(...)
```

The implementation omits the `1/(2*3) = 1/6` normalization. This means Brier scores are 6x larger than the standard definition, making 0.667 (UNINFORMED_3STATE_BRIER in arbitration.py) represent something different than intended.

However, this is **internally consistent** -- the same unnormalized formula is used in both calibration and arbitration, so the Brier-inverse weighting is still correct relative to other personas. The constant `UNINFORMED_3STATE_BRIER = 0.667` should be `0.667 * 6 = 4.0` under standard Brier, but since both sides use the same scale, the ratio works out.

**Fix:**
If spec conformance matters, add normalization. If internal consistency is sufficient, document that Brier scores are unnormalized.

---

### MED-07: Lessons Engine `write_lesson_to_qdrant` Missing `cycle_id` in Error Log

**Severity:** MEDIUM
**Category:** Anti-pattern violation (missing cycle_id)
**File:** `pmacs/engines/lessons.py:113-119`

**Issue:**
```python
log_debug(
    "LESSON_WRITE_FAILED",
    payload={...},
    level="WARN",
    error_code="LESSON_WRITE_FAILED",
    msg=f"Lesson Qdrant write failed...",
)
```

The `log_debug` call is missing `cycle_id=lesson.cycle_id`. Per Architecture.md SS16.5, all WARN+ debug events require cycle_id. The lesson has `cycle_id` available on the object.

**Fix:**
Add `cycle_id=lesson.cycle_id` to the `log_debug` call.

---

### MED-08: Arbitration `disagreement_severity` Only Checks `p_up` Variance

**Severity:** MEDIUM
**Category:** Incomplete metric
**File:** `pmacs/engines/arbitration.py:132-148`

**Issue:**
```python
p_ups = [s.p_up for s in signals]
mean_up = sum(p_ups) / len(p_ups)
variance = sum((p - mean_up) ** 2 for p in p_ups) / len(p_ups)
```

This computes variance only on `p_up`, ignoring `p_down` and `p_flat`. Two signals could have the same `p_up` but vastly different `p_down` (one bullish, one bearish with different flat probabilities), and the disagreement severity would report 0.0.

**Fix:**
Compute variance across all three probability dimensions:
```python
variance = sum(
    (s.p_up - mean_up)**2 + (s.p_down - mean_down)**2 + (s.p_flat - mean_flat)**2
    for s in signals
) / len(signals)
```

---

### LOW-01: GrowthHunter Has Dead Path Construction Code

**Severity:** LOW
**Category:** Dead code
**File:** `pmacs/agents/growth_hunter.py:55-58`

**Issue:**
```python
prompt_path = (
    __file__.replace(".py", "")
    .rsplit("/", 1)[0]
    .replace("/agents", "/agents/prompts/growth_hunter.md")
)
```

This constructs a prompt_path but is immediately overwritten by the correct Path-based construction on lines 61-64. The variable is never used.

**Fix:**
Remove lines 55-58.

---

### LOW-02: CatalystSummarizer and MoatAnalyst Access `evidence[0]` Without Guard

**Severity:** LOW
**Category:** Potential IndexError
**Files:**
- `pmacs/agents/catalyst_summarizer.py:58`
- `pmacs/agents/moat_analyst.py:58`

**Issue:**
```python
ticker = evidence[0].ticker if evidence else "UNKNOWN"
```

While the `if evidence` guard prevents IndexError, the methods also call `self._format_evidence(evidence)` which iterates over empty lists fine. However, if evidence is empty, the prompt will have no evidence text, and the LLM will generate output with no data basis. The sanity validators do not check for empty evidence.

**Fix:**
Consider returning None or logging a warning when evidence is empty.

---

### LOW-03: Bootstrap Haircut Dictionary Has Redundant Final Entry

**Severity:** LOW
**Category:** Code clarity
**File:** `pmacs/engines/sizing.py:10,67`

**Issue:**
```python
BOOTSTRAP_HAIRCUT: dict[int, float] = {0: 0.50, 1: 0.65, 2: 0.80, 3: 0.90}
```

Line 67:
```python
bootstrap_factor = BOOTSTRAP_HAIRCUT.get(n_mature, 1.0) if n_mature < 4 else 1.0
```

When `n_mature >= 4`, the `if n_mature < 4` check returns `1.0` directly, bypassing the dict. When `n_mature == 3`, `BOOTSTRAP_HAIRCUT.get(3, 1.0)` returns `0.90`. The `1.0` default in `.get()` is only reached if `n_mature` is somehow not 0-3 but still `< 4`, which is impossible since `n_mature = min(x.matured_sources_used, 4)` and it's an int.

Not a bug, just confusing logic.

---

### LOW-04: `FlywheelHealth` Returns 0.0 on Missing Data (Masks Errors)

**Severity:** LOW
**Category:** Silent failure
**File:** `pmacs/engines/flywheel_health.py:105-126`

**Issue:**
`get_rolling_brier`, `get_rolling_sharpe`, and `get_max_drawdown` all return `0.0` when DuckDB is unavailable or the file does not exist. For promotion gates, a Brier of `0.0` passes all checks (`0.0 <= 0.30`), and a Sharpe of `0.0` passes `>= 0.0`. This means promotion gates can pass without any real data.

**Fix:**
Return `None` (or `float('inf')` for Brier/drawdown, `float('-inf')` for Sharpe) when data is unavailable, and have `check_promotion_gates` fail gates where data is missing.

---

## Summary Table

| ID | Finding | Severity | File(s) |
|----|---------|----------|---------|
| HIGH-01 | ArbitrationSignals always immature (Brier-weighting disabled) | HIGH | orchestrator.py:1313, arbitration.py:54 |
| HIGH-02 | 6 agents reference non-existent `ev.content` | HIGH | forensics.py:64, growth_hunter.py:71, insider_activity.py:63, short_interest.py:64, crucible.py:54, memo_writer.py:54 |
| HIGH-03 | State machine opens AuditWriter per transition (FD leak) | HIGH | state_machine.py:95-105 |
| HIGH-04 | Sizing missing $1000 hard cap | HIGH | sizing.py:73-75 |
| MED-01 | Bootstrap agreement check is vacuous | MEDIUM | arbitration.py:221 |
| MED-02 | Conviction maturity floor 0.25 for 0 mature sources | MEDIUM | conviction.py:34 |
| MED-03 | Pricing `using_explicit` check can bypass ATR | MEDIUM | pricing.py:86-92 |
| MED-04 | Kelly can produce oversized fractions on tiny stops | MEDIUM | sizing.py:39-42 |
| MED-05 | `_current_price` stored on `self` (fragile) | MEDIUM | orchestrator.py:1267 |
| MED-06 | Brier score unnormalized (internally consistent) | MEDIUM | calibration.py:46-49 |
| MED-07 | Lessons engine missing cycle_id in error log | MEDIUM | lessons.py:113-119 |
| MED-08 | Disagreement severity only checks p_up | MEDIUM | arbitration.py:132-148 |
| LOW-01 | Dead path construction code in GrowthHunter | LOW | growth_hunter.py:55-58 |
| LOW-02 | Empty evidence not guarded in CatalystSummarizer/MoatAnalyst | LOW | catalyst_summarizer.py:58, moat_analyst.py:58 |
| LOW-03 | Bootstrap haircut dict logic redundant | LOW | sizing.py:10,67 |
| LOW-04 | Flywheel returns 0.0 on missing data (passes gates) | LOW | flywheel_health.py:105-126 |

---

## Mathematical Formula Verification

### Arbitration (arbitration.py)
- Brier-inverse weighting: `w_i = 1 / (brier + 0.05)` -- **Correct**
- MacroRegime 0.5x multiplier -- **Correct**
- Extreme-prob dampening: cap weight at 0.5x when p > 0.9 -- **Correct**
- Normalization: `w_i / sum(w)` -- **Correct**
- Agreement check: any p_up > 0.5 and p_down > 0.5 -> disagree -- **Correct**

### Conviction (conviction.py)
- Formula: `direction * maturity * crucible * ev` -- **Correct**
- Direction: `p_up - p_down` -- **Correct**
- Crucible factor: `1.0 - severity` -- **Correct**
- EV factor: `ev_multiple / 1.5` capped at 1.0 -- **Correct**
- Verdict tiers: STRONG_BUY >= 0.6, BUY >= 0.3, SKIP < 0.3 -- **Correct per spec**

### Sizing (sizing.py)
- Half-Kelly: `kelly * 0.5` -- **Correct**
- Bootstrap haircut table -- **Correct**
- Correlation factor: `max(0.3, 1.0 - max(correlations))` -- **Correct**
- Max position cap at 20% -- **Correct** (but missing $1000 hard cap, see HIGH-04)

### Pricing (pricing.py)
- EV: `p_up * gain - p_down * loss` -- **Correct**
- Target: `max(0.05, 1.5 * ATR)` -- **Correct**
- Stop: `min(0.15, max(0.10, 2.0 * ATR))` -- **Correct**
- Catastrophe-net hard cap at 15% -- **Correct per spec**

### Calibration (calibration.py)
- Brier: `sum((f - a)^2)` for 3-outcome -- **Correct** (unnormalized, see MED-06)
- Weight refit: `1 / (brier + 0.05)`, normalized -- **Correct**

### State Machine (state_machine.py)
- Terminal state immutability -- **Correct**
- Valid transition check -- **Correct**
- Hash-chained audit -- **Correct**
- Auto-fill exit_date and cycle_id_closed -- **Correct**

---

## Spec Compliance Check

| Requirement | Status | Notes |
|-------------|--------|-------|
| Max position 20% | PASS | sizing.py:73 |
| Max position $1000 | **FAIL** | No USD hard cap (HIGH-04) |
| Catastrophe-net 15% | PASS | pricing.py:33, MAX_STOP_LOSS_PCT |
| Brier-inverse weighting | **DEGRADED** | Formula correct but never receives real data (HIGH-01) |
| Half-Kelly | PASS | sizing.py:57 |
| Conviction formula | PASS | conviction.py:39 |
| Three-layer contract | PASS | All 9 personas have grammar+pydantic+sanity |
| Temperature 0.2 for analysis | PASS | All 7 analysis personas use 0.2 |
| State transitions via state_machine | PASS | Single transition point enforced |
| Anti-pattern: cycle_id required | **FAIL** | lessons.py missing cycle_id (MED-07) |

---

_Audit complete. 16 findings: 4 HIGH, 8 MEDIUM, 4 LOW. No Critical security findings._
_Auditor: Claude Code (deep audit)_
_Date: 2026-05-30_
