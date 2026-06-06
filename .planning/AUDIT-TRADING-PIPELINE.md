# PMACS Trading Pipeline Audit Report

**Date:** 2026-05-30
**Scope:** State machine, execution pipeline, paper trading, stop-loss, kill switch, sizing, mode management
**Depth:** Deep (cross-file analysis)

## Summary

Audited 22 source files across the PMACS trading pipeline. Found **3 critical issues**, **7 warnings**, and **4 informational items**. The three critical findings are: (1) gatekeeper queries a non-existent holding state `'OPEN'` instead of `'ACTIVE'`, making concurrent position limits completely ineffective; (2) `cancel_catastrophe_net` engages the kill switch with a trigger ID not in the validated TRIGGER_IDS list, which could cause auditing gaps; (3) the cash ledger `apply_flow` uses a read-then-write pattern without row-level locking, enabling TOCTOU race conditions under concurrent writes.

The execution pipeline correctly enforces the "LLMs never sign trades" non-negotiable -- the signing key is Ed25519, held in a filesystem-protected key file, and the orchestrator creates the TradePlan in Python code (not from LLM output directly). The kill switch architecture is sound: system can engage without TOTP, only operator can disengage with TOTP. Mode transitions are properly TOTP-gated for promotions.

---

## Critical Issues

### CR-01: Gatekeeper queries `state = 'OPEN'` but HoldingState uses `ACTIVE` -- position limit bypass

**Files:**
- `pmacs/agents/gatekeeper.py:91`
- `pmacs/agents/gatekeeper.py:109`

**Issue:** The `_count_active_positions()` function queries `WHERE state = 'OPEN'` and `_has_active_position()` also queries `WHERE state = 'OPEN'`. However, the `HoldingState` enum in `pmacs/schemas/contracts.py` has no `OPEN` state -- the active position state is `ACTIVE`. This means the gatekeeper always sees 0 active positions and never enforces the max concurrent positions limit (5 positions). A sixth, seventh, or more concurrent positions could be opened, violating the spec constraint.

Meanwhile, the orchestrator at `pmacs/nervous/orchestrator.py:1573` correctly uses `WHERE state = 'ACTIVE'` for its own position count check. But the gatekeeper (step 9 in the pipeline, which runs before the orchestrator's risk gate at step 13l) will always admit tickers regardless of position count.

**Impact:** The max 5 concurrent positions constraint from `pmacs/constants.py:10` (`MAX_CONCURRENT_POSITIONS = 5`) is unenforceable at the gatekeeper layer. Position concentration limits are partially enforced at the later risk gate (step 13l), but the gatekeeper pre-filter is completely bypassed.

**Fix:**
```python
# In pmacs/agents/gatekeeper.py, line 91:
# Change:
"SELECT COUNT(*) FROM holdings WHERE state = 'OPEN'"
# To:
"SELECT COUNT(*) FROM holdings WHERE state = 'ACTIVE'"

# And line 109:
# Change:
"SELECT 1 FROM holdings WHERE ticker = ? AND state = 'OPEN'"
# To:
"SELECT 1 FROM holdings WHERE ticker = ? AND state = 'ACTIVE'"
```

### CR-02: Cash ledger TOCTOU race condition on `apply_flow`

**File:** `pmacs/engines/cash_ledger.py:128-191`

**Issue:** The `apply_flow` method reads the latest snapshot row (line 137-146), computes a new balance in Python (line 148), then inserts a new row (lines 168-172). Between the SELECT and INSERT, another process or thread could also read the same balance and insert its own row, resulting in both flows being applied against the same base balance -- effectively double-counting one flow. The SQLite WAL mode (line 58) provides concurrent read access but does not serialize these read-then-write sequences.

This matters because `pmacs-stoploss` (stop_loss_daemon.py) and `pmacs-nervous` (orchestrator.py) can both write to the ledger concurrently. If a stop-out triggers an exit flow at the same moment the orchestrator is processing fills, the ledger could lose track of one of the flows.

**Impact:** Cash balance can drift from actual position values. The `validate_total` method (line 193) detects drift after the fact but does not prevent it. Repeated drift triggers WARN-level logs but the underlying data is already incorrect.

**Fix:**
```python
# Wrap the read-compute-write in a SQLite IMMEDIATE transaction:
def apply_flow(self, flow: CashFlow, cycle_id: str = "") -> float:
    conn = self._connect()
    try:
        conn.execute("BEGIN IMMEDIATE")  # Acquires write lock immediately
        row = conn.execute(
            "SELECT cash_usd, positions_value_usd, total_value_usd "
            "FROM paper_account ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            cash = STARTING_CAPITAL
            pos_val = 0.0
        else:
            cash = float(row[0])
            pos_val = float(row[1])

        new_cash = round(cash + flow.amount_usd, 2)
        if new_cash < 0:
            new_cash = 0.0

        new_total = round(new_cash + pos_val, 2)
        now = self._now()
        conn.execute(
            "INSERT INTO paper_account (snapshot_at, cash_usd, positions_value_usd, total_value_usd) "
            "VALUES (?, ?, ?, ?)",
            (now, new_cash, pos_val, new_total),
        )
        conn.commit()
        # ... logging ...
        return new_cash
    except:
        conn.rollback()
        raise
    finally:
        conn.close()
```

### CR-03: Kill switch engaged with unregistered trigger ID `CATASTROPHE_CANCEL_FAILED`

**Files:**
- `pmacs/execution/catastrophe_net.py:132`
- `pmacs/cortex/kill_switch.py:60-73`

**Issue:** When `cancel_catastrophe_net` fails, it calls `engage_kill_switch(trigger="CATASTROPHE_CANCEL_FAILED")`. However, the `TRIGGER_IDS` tuple in `kill_switch.py` (lines 60-73) lists 12 valid trigger IDs and `CATASTROPHE_CANCEL_FAILED` is not among them. While the `engage()` function does not validate the trigger parameter against `TRIGGER_IDS`, this creates an auditing inconsistency: the trigger name stored in the database will not match any known trigger, making it harder to diagnose and potentially breaking any downstream monitoring that filters by trigger ID.

**Impact:** Audit trail has an unregistered trigger. Downstream monitoring or kill switch disengage re-checks (lines 237-259) that correlate trigger names may fail to match.

**Fix:**
```python
# In pmacs/cortex/kill_switch.py, add to TRIGGER_IDS:
TRIGGER_IDS: tuple[str, ...] = (
    "AUDIT_CHAIN_INTEGRITY",
    "ROLLING_5D_LOSS",
    "SINGLE_DAY_MTM_LOSS",
    "RECONCILIATION_MISMATCH",
    "BROKER_AUTH_FAILURE",
    "DISK_SPACE_LOW",
    "NTP_DRIFT",
    "META_MONITOR_UNRESPONSIVE",
    "CRASH_LOOP",
    "MODEL_INTEGRITY",
    "CYCLE_BLOCKED_BUDGET_DAILY",
    "CYCLE_BLOCKED_BUDGET_MONTHLY",
    "CATASTROPHE_CANCEL_FAILED",  # Add this
)
```

---

## Warnings

### WR-01: Catastrophe-net stop failure does not prevent position from remaining open

**File:** `pmacs/execution/service.py:231-250`

**Issue:** When `_place_catastrophe_stop` fails, the method logs a CRITICAL error and returns `None`, but the position remains open with no broker-side protection. The `stop_order_id` in the response is `None`. The trade fill is still accepted and the position is fully active. Per Architecture.md, the broker receives ONLY the catastrophe-net stop. If this stop fails, there is no safety net.

The orchestrator's execution path (`orchestrator.py:1813-1821`) has the same pattern: it logs CRITICAL but continues.

**Fix:** Consider engaging the kill switch on catastrophe-net placement failure (not just cancellation failure), since an unprotected position is a spec violation. At minimum, the holding should be transitioned to HALTED and an immediate exit order attempted:
```python
# In service.py _place_catastrophe_stop, after the except block:
if stop_order_id is None:
    # Engage kill switch for unprotected position
    from pmacs.cortex.kill_switch import engage as engage_kill_switch
    engage_kill_switch(
        reason=f"Catastrophe-net placement failed for {plan.ticker}",
        trigger="CATASTROPHE_CANCEL_FAILED",
        cycle_id=plan.cycle_id,
    )
```

### WR-02: Orchestrator creates new event loop per trade -- potential resource leak

**File:** `pmacs/nervous/orchestrator.py:1787-1823`

**Issue:** The execution step creates a new `asyncio.new_event_loop()` for each trade submission and closes it in a `finally` block. If multiple symbols are processed in a single cycle, this creates and destroys event loops repeatedly. While the `finally` block should prevent leaks, any exception in `loop.run_until_complete` that bypasses the finally (e.g., a SIGTERM during the await) could leave an unclosed loop. More importantly, this pattern cannot run inside an existing event loop, which could cause issues if the orchestrator is ever run from an async context.

**Fix:** Use `asyncio.run()` (Python 3.7+) which handles loop creation/cleanup more robustly, or maintain a single event loop for the cycle duration.

### WR-03: Sizing engine does not enforce absolute dollar cap ($1,000 max position)

**File:** `pmacs/engines/sizing.py:73`

**Issue:** The sizing engine caps position size at `max_position_pct` (20% of portfolio) on line 73, but does not enforce the absolute dollar cap. With a $5,000 portfolio, 20% = $1,000 which happens to match the spec. However, if `portfolio_value_usd` is passed incorrectly or grows (e.g., to $10,000 through price appreciation), the 20% cap would allow a $2,000 position, exceeding the $1,000 spec maximum. The spec says "Max single position: 20% ($1,000)" -- the $1,000 is a hard floor, not just an example.

**Fix:**
```python
# In pmacs/engines/sizing.py size_position():
MAX_POSITION_USD = 1000.0  # Hard cap from spec

target_usd = target_pct * x.portfolio_value_usd
target_usd = min(target_usd, MAX_POSITION_USD)  # Add absolute cap
target_shares = target_usd / x.current_price if x.current_price > 0 else 0.0
```

### WR-04: Stop-loss daemon `_HoldingProxy` does not set `stop_type` attribute

**File:** `pmacs/cortex/stop_loss_daemon.py:245-253`

**Issue:** The `_HoldingProxy` class created in the stop-loss daemon sets `stop_price_usd` and `trailing_stop_price_usd` but does not set a `stop_type` attribute. The `check_holding` function passes this proxy to `check_stop_breach` and `check_trailing_breach`. While the current stop-loss monitor functions do not access `stop_type`, any future enhancement that does will get an `AttributeError` rather than a clear error.

**Fix:** Either add `stop_type` to the proxy or use the actual `Holding` model from the database.

### WR-05: Kill switch `KillSwitchState` enum differs between `kill_switch.py` and `system.py`

**Files:**
- `pmacs/cortex/kill_switch.py:30-33` -- defines `KillSwitchState` with `ARMED` / `ENGAGED`
- `pmacs/schemas/system.py:21-24` -- defines `KillSwitchState` with `DISENGAGED` / `ENGAGED`

**Issue:** Two different `KillSwitchState` enums exist. `kill_switch.py` uses `ARMED` as the disengaged state, while `system.py` uses `DISENGAGED`. The `kill_switch.py` module is the authoritative implementation and uses `ARMED`. The `system.py` enum appears unused but could cause confusion if imported. The disengage function at line 266 sets state to `KillSwitchState.ARMED.value` which writes `"ARMED"` to the database. Any code importing from `system.py` and checking for `DISENGAGED` would never match.

**Fix:** Remove or reconcile the duplicate enum in `pmacs/schemas/system.py`. Rename it to match the implementation or add a deprecation comment.

### WR-06: Trailing stop `maybe_arm_trailing` ignores ratchet when first arming

**File:** `pmacs/engines/trailing_stop.py:43-76`

**Issue:** When `maybe_arm_trailing` first arms the trailing stop (line 69-75), it sets `trailing_price = current_price - 1.0 * atr_20`. However, `maybe_ratchet_trailing` is a separate function that must be called explicitly. If the price spikes above 1.5R and then drops before the next cycle, the trailing stop is set at the arm-time price minus ATR, which could be well below the best price achieved. The spec says "ratchet up only" but the arming point itself is not ratcheted against historical highs. If this is intentional, it should be documented.

**Fix:** Consider storing the highest price since arming and computing the trailing from that, or document that the trailing stop is computed from the current price at arm time only.

### WR-07: Mode transition validation does not check promotion gate thresholds

**File:** `pmacs/engines/mode_manager.py:41-82`

**Issue:** The `transition_mode` function validates the transition is in `VALID_MODE_TRANSITIONS` and requires TOTP for promotions, but does NOT check the numerical promotion gate thresholds defined in `pmacs/constants.py:49-85` (`PROMOTION_THRESHOLDS`). For example, PAPER to PAPER_VALIDATED requires >= 90 cycles, >= 200 trades, Brier <= 0.30, Sharpe >= 0.0, drawdown <= 15%. The mode_manager accepts any valid transition with TOTP, regardless of whether the flywheel metrics meet these thresholds.

The thresholds exist in constants but no code path enforces them during mode transitions. This is an architectural gap -- the gate checks need to be implemented or called from `transition_mode`.

**Fix:**
```python
# In mode_manager.py, add threshold enforcement before TOTP check:
from pmacs.constants import PROMOTION_THRESHOLDS

def _check_promotion_gates(from_mode: Mode, to_mode: Mode, db_path: Path) -> str | None:
    """Check if flywheel metrics meet promotion thresholds. Returns failure reason or None."""
    # Build threshold key from mode transition
    key = f"{from_mode.value}_to_{to_mode.value}"
    thresholds = PROMOTION_THRESHOLDS.get(key)
    if thresholds is None:
        return None  # No thresholds defined for this transition

    # Query DuckDB for current metrics
    # ... implementation to compare current metrics against thresholds ...
```

---

## Info

### IN-01: MockAdapter returns zero fill quantity and UNKNOWN ticker

**File:** `pmacs/execution/adapter.py:92-102`

**Issue:** `MockAdapter.poll_fill` returns `filled_quantity=0` and `ticker="UNKNOWN"`. The `ExecutionService._handle_client` (service.py:153-165) compensates by overwriting these from the TradePlan. This works but means any test that calls `poll_fill` directly (without the service layer) gets misleading zero-fill results.

**Fix:** Consider having MockAdapter accept the original plan and return realistic fill data.

### IN-02: Hardcoded portfolio value fallback in orchestrator sizing step

**File:** `pmacs/nervous/orchestrator.py:1502`

**Issue:** `portfolio_value = 5000.0` is hardcoded as a fallback. While the ledger lookup follows, if the ledger is `None` (test mode), sizing uses the stale $5,000 base. This matches the spec for initial capital but will be wrong after any trades.

### IN-03: `place_stop_order` ABC signature uses `int` for `qty` but TradePlan uses `int` for quantity

**Files:**
- `pmacs/execution/adapter.py:52` -- `qty: int`
- `pmacs/schemas/trade.py:33` -- `quantity: int = Field(ge=1)`

**Issue:** Consistent, but `sizing.py` computes `target_shares` as a `float` and the orchestrator converts with `max(1, int(shares))` (orchestrator.py:1739). The `int()` truncation (not rounding) means 1.9 shares becomes 1 share, slightly under-sizing.

### IN-04: AuditWriter opened and closed repeatedly in state_machine.transition

**File:** `pmacs/engines/state_machine.py:96-105`

**Issue:** Each state transition creates a new `AuditWriter` instance, opens the file, appends one entry, and closes it. In a cycle processing multiple symbols, this creates many open/close cycles. While functionally correct, a shared writer or batched writes would be more efficient.

---

## Non-Negotiable Compliance Check

| Non-Negotiable | Status | Evidence |
|---|---|---|
| 1. LLMs never sign trades | PASS | TradePlan created in orchestrator.py:1737-1743 by Python code. Signing key is Ed25519 from `signing.py`, not from LLM output. LLM produces structured analysis only; Python creates the TradePlan object. |
| 2. LLMs never math | PASS | `compute_ev`, `size_position`, `compute_conviction`, `verdict_tier` all in Python engines. LLM outputs are structured probabilities fed into deterministic engines. |
| 3. Hash-chained state transitions | PASS | `state_machine.transition` writes to AuditWriter on every transition (line 96-105). AuditWriter appends with prev_sha256. |
| 4. Local-only execution | PASS | Inference on :8080, pf-blocked from internet. No cloud LLM calls in reviewed code. |
| 5. Operator owns kill switch | PASS | `engage()` (line 98) requires no TOTP. `disengage()` (line 192) requires valid TOTP via `verify_totp`. System can engage, only operator can lift. |

## Anti-Pattern Compliance Check

| Anti-Pattern | Status | Evidence |
|---|---|---|
| `holding.state =` outside state_machine | PASS (orchestrator) | Orchestrator uses `transition()` at lines 1497, 1519-1520, 1555-1556, 1585-1586, 1703. |
| `json.dumps(payload)` for audit | PASS | Uses `AuditWriter.append()` throughout. |
| Custom rate-limit logic | NOT REVIEWED | Out of scope for this audit. |
| `cycle_id=None` on audit-emitting functions | WARN | `catastrophe_net.execute_exit` correctly requires cycle_id (line 168). But `cash_ledger.apply_flow` defaults `cycle_id=""` (line 128), which is an empty string, not `None`. |
| Tight broker-side stops | PASS | Broker gets only catastrophe-net at 15% (catastrophe_net.py). Tight stops are PMACS-internal only. |
| `eur_per_usd` field | PASS | `FX_CONVENTION = "usd_per_eur"` in constants.py. |
| Mutation auto-applying | NOT REVIEWED | Out of scope for this audit. |

---

**Files Reviewed (22):**
- `pmacs/engines/state_machine.py`
- `pmacs/engines/mode_manager.py`
- `pmacs/engines/sizing.py`
- `pmacs/engines/pricing.py`
- `pmacs/engines/conviction.py`
- `pmacs/engines/portfolio_risk_gate.py`
- `pmacs/engines/stop_loss_monitor.py`
- `pmacs/engines/trailing_stop.py`
- `pmacs/engines/cash_ledger.py`
- `pmacs/execution/service.py`
- `pmacs/execution/adapter.py`
- `pmacs/execution/signing.py`
- `pmacs/execution/catastrophe_net.py`
- `pmacs/execution/alpaca_paper.py`
- `pmacs/sim/alpaca_paper_adapter.py`
- `pmacs/cortex/kill_switch.py`
- `pmacs/cortex/totp.py`
- `pmacs/cortex/stop_loss_daemon.py`
- `pmacs/nervous/orchestrator.py`
- `pmacs/schemas/contracts.py`
- `pmacs/schemas/trade.py`
- `pmacs/schemas/system.py`
- `pmacs/agents/gatekeeper.py`
- `pmacs/constants.py`

---

_Audited: 2026-05-30_
_Auditor: Claude (gsd-code-reviewer)_
_Depth: deep_
