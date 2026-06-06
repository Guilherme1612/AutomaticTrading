---
phase: re-audit-agents-math
reviewed: 2026-05-30T12:04:00Z
depth: deep
files_reviewed: 24
files_reviewed_list:
  - pmacs/agents/gatekeeper.py
  - pmacs/agents/forensics.py
  - pmacs/agents/growth_hunter.py
  - pmacs/agents/insider_activity.py
  - pmacs/agents/short_interest.py
  - pmacs/agents/crucible.py
  - pmacs/agents/memo_writer.py
  - pmacs/agents/catalyst_summarizer.py
  - pmacs/agents/macro_regime.py
  - pmacs/agents/moat_analyst.py
  - pmacs/agents/base.py
  - pmacs/agents/episodic_context.py
  - pmacs/nervous/orchestrator.py
  - pmacs/engines/pricing.py
  - pmacs/engines/sizing.py
  - pmacs/engines/arbitration.py
  - pmacs/engines/conviction.py
  - pmacs/engines/state_machine.py
  - pmacs/engines/calibration.py
  - pmacs/engines/flywheel_health.py
  - pmacs/engines/failure_diagnostic.py
  - pmacs/engines/portfolio_risk_gate.py
  - pmacs/engines/crucible_loop.py
  - pmacs/engines/stop_loss_monitor.py
  - pmacs/engines/mode_manager.py
  - pmacs/engines/trailing_stop.py
  - pmacs/engines/reconciliation.py
  - pmacs/engines/opportunity_cost.py
  - pmacs/engines/thesis_reeval.py
  - pmacs/engines/lessons.py
  - pmacs/engines/causal_attribution.py
  - pmacs/engines/override_learning.py
  - pmacs/engines/fundamental_routing.py
  - pmacs/engines/cash_ledger.py
  - pmacs/engines/memory.py
  - pmacs/engines/mutation.py
  - pmacs/engines/scaling.py
  - pmacs/engines/crucible_calibration.py
  - pmacs/schemas/data.py
findings:
  critical: 1
  warning: 3
  info: 3
  total: 7
status: issues_found
---

# Re-Audit: Agent & Engine Math Verification

**Reviewed:** 2026-05-30T12:04:00Z
**Depth:** deep
**Files Reviewed:** 38
**Status:** issues_found

## Previous Fix Verification

### Fix 1: Gatekeeper enum (state = 'ACTIVE') -- VERIFIED

**File:** `pmacs/agents/gatekeeper.py`
**Status:** CORRECT

Line 91: `_count_active_positions()` queries `WHERE state = 'ACTIVE'`
Line 109: `_has_active_position()` queries `WHERE ticker = ? AND state = 'ACTIVE'`

Both queries now correctly use `'ACTIVE'` instead of the old `'OPEN'` value. This matches the `HoldingState.ACTIVE` enum used everywhere else in the system. No new issues introduced.

### Fix 2: Evidence field access (6 agents) -- VERIFIED

**Files:** `pmacs/agents/forensics.py`, `growth_hunter.py`, `insider_activity.py`, `short_interest.py`, `crucible.py`, `memo_writer.py`
**Status:** CORRECT

All 6 agents now use `ev.title or str(ev.data)[:200]` (lines confirmed per file):
- forensics.py:64
- growth_hunter.py:71
- insider_activity.py:63
- short_interest.py:64
- crucible.py:54
- memo_writer.py:54

This correctly accesses the `Evidence.title: str | None` and `Evidence.data: dict[str, Any]` fields defined in `pmacs/schemas/data.py:42-55`. When `title` is `None`, the fallback stringifies the `data` dict and truncates to 200 chars. No new issues introduced.

### Fix 3: Brier wiring in arbitration -- VERIFIED

**File:** `pmacs/nervous/orchestrator.py`
**Status:** CORRECT

The orchestrator now has:
1. `_get_persona_brier_data()` method (line 1036-1055) that queries DuckDB `persona_performance` table for per-persona `(avg_brier, sample_count)`.
2. `_step_13e_arbitration()` (line 1388-1393) calls `_get_persona_brier_data()` and passes `(historical_n, rolling_brier)` to each `ArbitrationSignal`.
3. The weekly re-eval path at line 2749 also calls `_get_persona_brier_data()`.

The `ArbitrationSignal` class (arbitration.py:46-94) correctly accepts and uses `historical_n` and `rolling_brier` for maturity classification and Brier-inverse weighting. No new issues introduced.

### Fix 4: $1000 hard cap in sizing -- VERIFIED

**File:** `pmacs/engines/sizing.py`
**Status:** CORRECT

Line 12: `MAX_POSITION_USD: float = 1000.0` with comment referencing Architecture.md.
Line 77: `target_usd = min(target_usd, MAX_POSITION_USD)` caps the final USD amount.

The cap is applied AFTER the Kelly/haircut calculation and AFTER the `max_position_pct` (20%) cap, ensuring the $1000 hard cap is the final enforcement. With $5000 portfolio, 20% = $1000, so both caps align. No new issues introduced.

### Fix 5: Holdings persistence via _upsert_holding -- PARTIALLY VERIFIED (see CR-01)

**File:** `pmacs/nervous/orchestrator.py`
**Status:** PARTIALLY CORRECT

The `_upsert_holding()` method is correctly implemented (lines 190-242) with proper UPSERT SQL. It is called at these abort/exit paths:
- Line 1169: symbol processing complete (normal path)
- Line 1190: antipattern detected
- Line 1365: all personas failed / timeout
- Line 1397: no valid directional probabilities
- Line 1533: Crucible abort
- Line 1607: sizing abort
- Line 1644: verdict SKIP
- Line 1675: risk gate blocked

**HOWEVER:** The kill-switch mid-cycle abort path at lines 2250-2299 does NOT call `_upsert_holding()`. See CR-01 below.

---

## Critical Issues

### CR-01: Kill-switch mid-cycle abort does not persist holdings

**File:** `pmacs/nervous/orchestrator.py:2262-2286`
**Issue:** The `_handle_mid_cycle_kill_switch()` method iterates all in-flight holdings, transitions their state via `transition()`, but then calls `self._symbol_holdings.clear()` at line 2286 WITHOUT calling `_upsert_holding()` for any of them. The state transitions are applied to the in-memory Python objects but never written to SQLite.

This means that if the kill switch engages mid-cycle, all in-flight holdings will have their state changed in memory only. On the next cycle, these holdings will be re-read from SQLite still in their pre-transition states (e.g., `PHASE1_RESEARCH`, `ACTIVE`). The orphaned holdings will never reach terminal states in the database, creating phantom open positions.

**Fix:**
```python
# At line 2283, after each successful transition, add:
            if is_valid_transition(holding.state, target):
                holding = transition(
                    holding,
                    target,
                    "mid_cycle_abort",
                    cycle_id,
                    op,
                )
                self._upsert_holding(holding, cycle_id)  # ADD THIS LINE
                interrupted.append(ticker)
                op += 1

# OR, after the loop at line 2285, batch-persist:
        for ticker in interrupted:
            h = self._symbol_holdings.get(ticker)
            if h is not None:
                self._upsert_holding(h, cycle_id)

        self._symbol_holdings.clear()
```

---

## Warnings

### WR-01: Weekly re-eval thesis invalidation constructs a new Holding instead of using the in-flight one

**File:** `pmacs/nervous/orchestrator.py:2777-2793`
**Issue:** The weekly re-eval path at line 2777 constructs a fresh `Holding(id=holding_id, ticker=ticker, state=HoldingState.ACTIVE, cycle_id_opened=cycle_id)` with only 4 fields populated. It then calls `transition()` on this partial object and writes the state to SQLite via raw `conn.execute("UPDATE holdings SET state = ?...")` instead of using `_upsert_holding()`.

Problems:
1. The partial Holding object is missing `entry_date`, `entry_price_usd`, `conviction_score`, etc. -- the transition would succeed but the `_upsert_holding()` would overwrite those fields with `None`.
2. The raw SQL update at line 2793-2801 bypasses `_upsert_holding()` and writes directly, which is fragile -- it could miss fields that `_upsert_holding()` would normally update.
3. The `cycle_id_opened` is set to the CURRENT `cycle_id`, not the original opening cycle. This would corrupt the holding's lineage.

This is not a critical bug because the code currently uses raw SQL to update only `state` and `last_reeval_at`, so it doesn't overwrite other fields. But it bypasses the canonical `_upsert_holding()` path and creates a misleading partial Holding object.

**Fix:** Load the full holding from the `row` data (all fields available from the SELECT at line 2698-2701), construct a complete Holding, transition it, then use `_upsert_holding()` instead of raw SQL.

### WR-02: Opportunity cost scan exits are not persisted or actioned

**File:** `pmacs/nervous/orchestrator.py:2947-3015`
**Issue:** The `_step_opportunity_cost()` method runs `run_opportunity_cost_scan()` which returns `OpportunityCostResult` objects with `action="EXIT"` and `exit_state=HoldingState.EXIT_OPPORTUNITY_COST`. However, the method only logs the count of EXIT recommendations -- it never transitions the holdings or calls `_upsert_holding()`. The EXIT recommendations are computed but discarded.

This means the opportunity cost engine computes exits that are never acted upon. Holdings that should be exited for opportunity cost remain `ACTIVE` indefinitely.

**Fix:** After computing results, iterate EXIT recommendations and call `transition()` + `_upsert_holding()` for each:
```python
for r in results:
    if r.action == "EXIT" and r.exit_state is not None:
        # Find the holding and transition it
        holding = next((h for h in active_holdings if h.id == r.holding_id), None)
        if holding and is_valid_transition(holding.state, r.exit_state):
            transition(holding, r.exit_state, r.reason, cycle_id, 0)
            self._upsert_holding(holding, cycle_id)
```

### WR-03: Base persona runner uses `getattr(ev, 'id', 'unknown')` instead of direct field access

**File:** `pmacs/agents/forensics.py:64`, `growth_hunter.py:71`, `insider_activity.py:63`, `short_interest.py:64`, `crucible.py:54`, `memo_writer.py:54`
**Issue:** All 6 agents that were fixed for evidence field access still use `getattr(ev, 'id', 'unknown')` when iterating evidence, even though `Evidence.id` is a required field (not optional) on the Pydantic model (`pmacs/schemas/data.py:46`). The `getattr` with a fallback suggests uncertainty about the schema, but `id` is always present on a valid `Evidence` object. This is a minor code smell -- the `getattr` fallback masks potential schema violations that should fail loudly.

The 3 other agents (`catalyst_summarizer.py`, `macro_regime.py`, `moat_analyst.py`) correctly use direct field access: `ev.id`, `ev.source.value`, `ev.type.value`.

**Fix:** Replace `getattr(ev, 'id', 'unknown')` with `ev.id` for consistency and to match the other 3 agents' pattern.

---

## Info

### IN-01: `catalyst_summarizer.py:58` accesses `evidence[0].ticker` without empty-list guard

**File:** `pmacs/agents/catalyst_summarizer.py:58`
**Issue:** `ticker = evidence[0].ticker if evidence else "UNKNOWN"` -- the `if evidence` guard is present, so this is safe. Same pattern exists in `moat_analyst.py:58`. No bug, but noting for consistency.

### IN-02: `opportunity_cost.py:121` hardcodes `pnl_pct = 0.0`

**File:** `pmacs/engines/opportunity_cost.py:121`
**Issue:** In `evaluate_holding()`, the PnL computation is stubbed: `pnl_pct = 0.0  # Will be overridden by caller with real data`. The comment says the caller should provide real data, and the `run_opportunity_cost_scan()` function does accept a `pnl_pcts` parameter that overrides this. But if `pnl_pcts` is `None` (the default), all holdings evaluate with 0% PnL, making the "underwater > 5%" exit trigger impossible. This is likely intentional for the current phase but worth tracking.

### IN-03: Dead code in `growth_hunter.py:55-59`

**File:** `pmacs/agents/growth_hunter.py:55-59`
**Issue:** Lines 55-59 compute `prompt_path` via string manipulation but then line 63 overwrites it with `Path(__file__).parent / "prompts" / "growth_hunter.md"`. The first computation (lines 55-59) is dead code.

---

## Fresh Scan Summary

### Engines (27 files reviewed)

All deterministic engines are clean and well-structured:

- **arbitration.py**: Correct Brier-inverse weighting, bootstrap logic, maturity separation. Proper use of `ArbitrationSignal` wrapper.
- **conviction.py**: Correct direction/maturity/crucible/EV factor multiplication. Proper clamping to [-1.0, 1.0].
- **state_machine.py**: Enforces valid transitions via `VALID_TRANSITIONS` map. Terminal state immutability. Hash-chained audit.
- **sizing.py**: Correct half-Kelly with bootstrap/limited-history haircuts. $1000 hard cap enforced.
- **pricing.py**: Correct EV computation. `MAX_STOP_LOSS_PCT = 0.15` enforced (non-negotiable).
- **calibration.py**: Correct Brier score computation for 3-outcome. Weight refitting via `1 / (brier + epsilon)`.
- **flywheel_health.py**: Correct promotion/demotion gate checks. Proper metric queries from DuckDB.
- **failure_diagnostic.py**: Complete 18-taxonomy coverage with proper severity scoring.
- **portfolio_risk_gate.py**: Correct position limit, concentration, and sector exposure checks.
- **crucible_loop.py**: Correct 2-cycle budget management with severity-based routing.
- **stop_loss_monitor.py**: Correct fixed and trailing stop breach detection with RTH/non-RTH order type selection.
- **mode_manager.py**: Correct mode ladder with TOTP gating for LIVE modes.
- **trailing_stop.py**: Correct arm-at-1.5R and ratchet-up-only logic.
- **cash_ledger.py**: Correct append-only ledger with WAL mode and negative-balance flooring.
- **All other engines**: Stubs or re-exports, no issues.

### Agents (14 files reviewed)

- **base.py**: Robust three-layer validation pipeline with retry, temperature bump, and simulation fallback. Multi-backend support (llama-server, OpenAI, Anthropic). Proper evidence sanitization (non-mutating, per anti-pattern).
- **gatekeeper.py**: Correct deterministic admittance filter. `state = 'ACTIVE'` query fixed.
- **All 7 persona runners + crucible + memo_writer**: Correct `build_prompt()` implementations with proper evidence field access. Temperature values match spec (0.2 analysis, 0.1 Crucible, 0.3 MemoWriter).

---

_Reviewed: 2026-05-30T12:04:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
