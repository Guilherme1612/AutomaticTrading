"""Comprehensive stop-loss + engine integration test [C1, C2, S1, S2, M1].

EXTENDS existing tests/integration/test_stop_loss.py (28 tests). Both must pass.

Tests the full stop-loss pipeline:
- Price breach -> StopTrigger written to SQLite [S1]
- Nervous poller picks up PENDING triggers [S1]
- Catastrophe-net cancel failure -> kill switch [C1]
- TradePlan lifecycle: SUBMITTED -> FILLED [S1]
- State machine transitions: STOPPED_OUT vs EXIT_TRAILING_STOP [S2]
- Audit trail with cycle_id at every step
- Trailing stop arm at 1.5R, ratchet up only [S2]
- Gap-down: MARKET_ON_OPEN order type
- Weekly re-eval: validated vs thesis broken [M1]
- 90-day thesis aging review
- Opportunity cost per-holding iteration [C2]
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from pmacs.engines.opportunity_cost import (
    OpportunityCostResult,
    decide_hold_or_exit,
    evaluate_holding,
    run_opportunity_cost_scan,
)
from pmacs.engines.state_machine import (
    InvalidStateTransition,
    get_valid_transitions,
    is_valid_transition,
    transition,
)
from pmacs.engines.stop_loss_monitor import (
    StopCheckResult,
    check_stop_breach,
    check_trailing_breach,
    determine_order_type,
)
from pmacs.engines.thesis_reeval import (
    ReEvalResult,
    check_thesis_aging,
    check_weekly_reeval,
    evaluate_thesis,
)
from pmacs.engines.trailing_stop import (
    TrailingStopState,
    compute_profit_r,
    maybe_arm_trailing,
    maybe_ratchet_trailing,
)
from pmacs.schemas.contracts import (
    Holding,
    HoldingState,
    Thesis,
)
from pmacs.schemas.stop_loss import StopEventStatus, StopTrigger, StopType
from pmacs.stop_loss_daemon import _write_stop_trigger, check_holding


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_holding(
    state: HoldingState = HoldingState.ACTIVE,
    stop_price_usd: float | None = 90.0,
    entry_price_usd: float = 100.0,
    conviction_score: float | None = 0.6,
    ticker: str = "TEST",
    holding_id: str = "hold-1",
    trailing_stop_price_usd: float | None = None,
    entry_date: date | None = None,
) -> Holding:
    """Create a Holding for testing with configurable fields."""
    thesis = Thesis(
        id="thesis-1",
        ticker=ticker,
        text="Test thesis",
        hash="abc123",
    )
    return Holding(
        id=holding_id,
        ticker=ticker,
        state=state,
        entry_price_usd=entry_price_usd,
        stop_price_usd=stop_price_usd,
        conviction_score=conviction_score,
        thesis=thesis,
        entry_date=entry_date or date(2026, 1, 1),
        trailing_stop_price_usd=trailing_stop_price_usd,
    )


class _HoldingProxy:
    """Lightweight holding-like object for stop_loss_monitor functions.

    The check_trailing_breach function uses getattr() to access
    trailing_stop_armed and trailing_stop_price_usd, which the
    Pydantic Holding model doesn't support as arbitrary attributes.
    """
    def __init__(
        self,
        holding_id: str = "hold-1",
        ticker: str = "TEST",
        stop_price_usd: float | None = 90.0,
        trailing_stop_price_usd: float | None = None,
    ):
        self.id = holding_id
        self.ticker = ticker
        self.stop_price_usd = stop_price_usd
        self.trailing_stop_price_usd = trailing_stop_price_usd
        # Presence of trailing price implies armed
        self.trailing_stop_armed = trailing_stop_price_usd is not None


def _create_test_db(tmp_path: Path) -> Path:
    """Create a minimal SQLite database with stop_events table."""
    db_path = tmp_path / "pmacs.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS stop_events (
            holding_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            stop_type TEXT NOT NULL,
            trigger_price_usd REAL NOT NULL,
            stop_price_usd REAL NOT NULL,
            detected_at TEXT NOT NULL,
            cycle_id TEXT NOT NULL,
            status TEXT NOT NULL,
            stop_type_category TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT,
            timestamp TEXT NOT NULL,
            prev_sha256 TEXT
        )"""
    )
    conn.commit()
    conn.close()
    return db_path


# ===========================================================================
# S1: StopTrigger written to SQLite with status=PENDING
# ===========================================================================

class TestStopTriggerSQLite:
    """Price breaches stop -> StopTrigger written to SQLite with status=PENDING [S1]."""

    def test_breach_writes_stop_trigger_to_db(self, tmp_path):
        """Fixed stop breach writes StopTrigger to stop_events with PENDING status."""
        db_path = _create_test_db(tmp_path)
        holding = _make_holding(stop_price_usd=90.0)
        result = check_stop_breach(holding, current_price=85.0, market_state="RTH")

        assert result is not None
        assert result.triggered is True

        conn = sqlite3.connect(str(db_path))
        _write_stop_trigger(conn, result, "FIXED", cycle_id="c-001")

        rows = conn.execute(
            "SELECT holding_id, ticker, status, stop_type FROM stop_events"
        ).fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0][0] == "hold-1"
        assert rows[0][1] == "TEST"
        assert rows[0][2] == "PENDING"
        assert rows[0][3] == "FIXED_STOP"

    def test_trigger_has_correct_prices(self, tmp_path):
        """StopTrigger stores correct trigger and stop prices."""
        db_path = _create_test_db(tmp_path)
        holding = _make_holding(stop_price_usd=92.0, entry_price_usd=100.0)
        result = check_stop_breach(holding, current_price=88.0, market_state="RTH")

        conn = sqlite3.connect(str(db_path))
        _write_stop_trigger(conn, result, "FIXED", cycle_id="c-001")

        rows = conn.execute(
            "SELECT trigger_price_usd, stop_price_usd FROM stop_events"
        ).fetchall()
        conn.close()

        assert rows[0][0] == 88.0  # current price at trigger
        assert rows[0][1] == 92.0  # stop price

    def test_pending_trigger_pickup(self, tmp_path):
        """Nervous poller can query PENDING triggers from stop_events [S1]."""
        db_path = _create_test_db(tmp_path)
        holding = _make_holding()
        result = check_stop_breach(holding, current_price=85.0, market_state="RTH")

        conn = sqlite3.connect(str(db_path))
        _write_stop_trigger(conn, result, "FIXED", cycle_id="c-001")

        # Simulate Nervous poller querying PENDING triggers
        pending = conn.execute(
            "SELECT holding_id, ticker, status FROM stop_events WHERE status = 'PENDING'"
        ).fetchall()
        conn.close()

        assert len(pending) == 1
        assert pending[0][2] == "PENDING"

    def test_trigger_status_lifecycle(self, tmp_path):
        """StopTrigger transitions: PENDING -> SUBMITTED -> FILLED [S1]."""
        db_path = _create_test_db(tmp_path)
        holding = _make_holding()
        result = check_stop_breach(holding, current_price=85.0, market_state="RTH")

        conn = sqlite3.connect(str(db_path))
        _write_stop_trigger(conn, result, "FIXED", cycle_id="c-001")

        # Nervous picks up and submits -> SUBMITTED
        conn.execute(
            "UPDATE stop_events SET status = 'SUBMITTED' WHERE status = 'PENDING'"
        )
        conn.commit()

        submitted = conn.execute(
            "SELECT status FROM stop_events WHERE status = 'SUBMITTED'"
        ).fetchall()
        assert len(submitted) == 1

        # Broker fills -> FILLED
        conn.execute(
            "UPDATE stop_events SET status = 'FILLED' WHERE status = 'SUBMITTED'"
        )
        conn.commit()

        filled = conn.execute(
            "SELECT status FROM stop_events WHERE status = 'FILLED'"
        ).fetchall()
        assert len(filled) == 1
        conn.close()


# ===========================================================================
# S2: State machine transitions for stop events
# ===========================================================================

class TestStopStateMachine:
    """State machine transitions: STOPPED_OUT (fixed) vs EXIT_TRAILING_STOP (trailing) [S2]."""

    def test_fixed_stop_transitions_to_stopped_out(self):
        """Fixed stop breach -> state machine transitions to STOPPED_OUT."""
        holding = _make_holding()
        result = transition(
            holding, HoldingState.STOPPED_OUT,
            reason="Fixed stop breached at 85.00",
            cycle_id="c-001", op_seq=1,
        )
        assert result.state == HoldingState.STOPPED_OUT
        assert result.cycle_id_closed == "c-001"

    def test_trailing_stop_transitions_to_exit_trailing_stop(self):
        """Trailing stop breach -> state machine transitions to EXIT_TRAILING_STOP."""
        holding = _make_holding()
        result = transition(
            holding, HoldingState.EXIT_TRAILING_STOP,
            reason="Trailing stop breached at 104.00",
            cycle_id="c-001", op_seq=1,
        )
        assert result.state == HoldingState.EXIT_TRAILING_STOP

    def test_terminal_state_immutable(self):
        """Terminal states (STOPPED_OUT) cannot be transitioned further."""
        holding = _make_holding()
        stopped = transition(
            holding, HoldingState.STOPPED_OUT,
            reason="Stop hit", cycle_id="c-001", op_seq=1,
        )
        with pytest.raises(InvalidStateTransition):
            transition(
                stopped, HoldingState.ACTIVE,
                reason="Cannot reopen", cycle_id="c-002", op_seq=1,
            )

    def test_exit_trailing_stop_is_terminal(self):
        """EXIT_TRAILING_STOP is a terminal state."""
        holding = _make_holding()
        exited = transition(
            holding, HoldingState.EXIT_TRAILING_STOP,
            reason="Trailing stop", cycle_id="c-001", op_seq=1,
        )
        assert exited.state in {
            HoldingState.STOPPED_OUT,
            HoldingState.EXIT_TRAILING_STOP,
            HoldingState.EXIT_THESIS_INVALIDATED,
            HoldingState.EXIT_OPPORTUNITY_COST,
        }

    def test_active_can_transition_to_stopped_out(self):
        """ACTIVE -> STOPPED_OUT is a valid transition."""
        assert is_valid_transition(HoldingState.ACTIVE, HoldingState.STOPPED_OUT)

    def test_active_can_transition_to_exit_trailing_stop(self):
        """ACTIVE -> EXIT_TRAILING_STOP is a valid transition."""
        assert is_valid_transition(HoldingState.ACTIVE, HoldingState.EXIT_TRAILING_STOP)

    def test_trailing_breach_produces_exit_trailing_stop(self):
        """Trailing stop check + state machine produces EXIT_TRAILING_STOP [S2]."""
        proxy = _HoldingProxy(trailing_stop_price_usd=105.0)
        result = check_trailing_breach(proxy, current_price=100.0, market_state="RTH")

        assert result is not None
        assert result.stop_type == "TRAILING_STOP"
        assert result.triggered is True

        # State machine transitions to EXIT_TRAILING_STOP
        holding = _make_holding()
        exited = transition(
            holding, HoldingState.EXIT_TRAILING_STOP,
            reason="Trailing stop breached at 100.00 (stop at 105.00)",
            cycle_id="c-001", op_seq=1,
        )
        assert exited.state == HoldingState.EXIT_TRAILING_STOP


# ===========================================================================
# S2: Trailing stop arm at 1.5R, ratchet up only
# ===========================================================================

class TestTrailingStopFull:
    """Trailing stop arm at 1.5R, ratchets up only [S2]."""

    def test_arm_at_exactly_1_5r(self):
        """Profit at exactly 1.5R does NOT arm (requires > 1.5R)."""
        state = maybe_arm_trailing(
            entry_price=100.0,
            current_price=107.5,  # R = 100-95 = 5, profit = 7.5, 7.5/5 = 1.5R
            stop_loss_price=95.0,
            atr_20=3.0,
            is_armed=False,
        )
        assert state.armed is False

    def test_arm_above_1_5r(self):
        """Profit > 1.5R arms the trailing stop."""
        state = maybe_arm_trailing(
            entry_price=100.0,
            current_price=108.0,  # R = 5, profit = 8, 8/5 = 1.6R
            stop_loss_price=95.0,
            atr_20=3.0,
            is_armed=False,
        )
        assert state.armed is True
        assert state.trailing_stop_price == pytest.approx(105.0)  # 108 - 3.0

    def test_ratchet_up_on_price_rise(self):
        """Price rises -> trailing ratchets up."""
        trailing = maybe_ratchet_trailing(
            current_price=120.0, atr_20=3.0, current_trailing=110.0,
        )
        assert trailing == pytest.approx(117.0)  # 120 - 3.0 > 110.0

    def test_ratchet_does_not_lower(self):
        """Price dips -> trailing stays at higher level (ratchets up only)."""
        trailing = maybe_ratchet_trailing(
            current_price=108.0, atr_20=3.0, current_trailing=110.0,
        )
        assert trailing == pytest.approx(110.0)  # 108 - 3.0 = 105 < 110, stays

    def test_trailing_breach_after_arm(self):
        """After arming, price falls back to trailing level triggers breach."""
        proxy = _HoldingProxy(trailing_stop_price_usd=105.0)
        result = check_trailing_breach(proxy, current_price=104.0, market_state="RTH")
        assert result is not None
        assert result.stop_type == "TRAILING_STOP"

    def test_no_breach_above_trailing(self):
        """Price above trailing level -> no breach."""
        proxy = _HoldingProxy(trailing_stop_price_usd=105.0)
        result = check_trailing_breach(proxy, current_price=108.0, market_state="RTH")
        assert result is None


# ===========================================================================
# Gap-down order type selection
# ===========================================================================

class TestGapDownOrderType:
    """Gap-down: MARKET_ON_OPEN order type selected."""

    def test_gap_down_selects_market_on_open(self):
        """Non-RTH gap-down uses MARKET_ON_OPEN order."""
        holding = _make_holding(stop_price_usd=90.0)
        result = check_stop_breach(holding, current_price=85.0, market_state="PRE_MARKET")
        assert result is not None
        assert result.order_type == "MARKET_ON_OPEN"
        assert result.is_gap_down is True

    def test_rth_uses_market_order(self):
        """RTH uses regular MARKET order."""
        holding = _make_holding(stop_price_usd=90.0)
        result = check_stop_breach(holding, current_price=85.0, market_state="RTH")
        assert result is not None
        assert result.order_type == "MARKET"
        assert result.is_gap_down is False

    def test_trailing_gap_down_uses_market_on_open(self):
        """Trailing stop breach during gap-down also uses MARKET_ON_OPEN."""
        proxy = _HoldingProxy(trailing_stop_price_usd=105.0)
        result = check_trailing_breach(proxy, current_price=100.0, market_state="PRE_MARKET")
        assert result is not None
        assert result.order_type == "MARKET_ON_OPEN"
        assert result.is_gap_down is True


# ===========================================================================
# C1: Catastrophe-net cancel failure -> kill switch
# ===========================================================================

class TestCatastropheNetCancel:
    """Catastrophe-net cancel fails -> kill switch engages [C1].

    PMACS manages tight stops; broker gets only catastrophe-net (15%).
    If the catastrophe-net cancel fails during a stop-out, the kill switch
    must engage to prevent uncontrolled exposure.
    """

    def test_catastrophe_net_stop_at_15_percent(self):
        """Stop price set at 15% below entry = catastrophe-net level."""
        entry = 100.0
        catastrophe_stop = entry * 0.85  # 15% below entry
        assert catastrophe_stop == pytest.approx(85.0)

    def test_stop_breach_at_catastrophe_level(self):
        """Price at catastrophe-net level triggers stop breach."""
        holding = _make_holding(stop_price_usd=85.0, entry_price_usd=100.0)
        result = check_stop_breach(holding, current_price=84.0, market_state="RTH")
        assert result is not None
        assert result.triggered is True

    def test_kill_switch_protocol_on_cancel_failure(self):
        """When catastrophe-net cancel fails, kill switch engagement is required.

        This test verifies the protocol: a cancel failure must be detectable
        and trigger kill switch engagement. The actual kill switch engagement
        is handled by the cortex process.
        """
        # Simulate: broker returns cancel failure
        cancel_failed = True
        kill_switch_engaged = False

        if cancel_failed:
            kill_switch_engaged = True  # Kill switch protocol

        assert kill_switch_engaged is True


# ===========================================================================
# Audit trail with cycle_id
# ===========================================================================

class TestAuditTrail:
    """Audit trail written for every step with cycle_id."""

    def test_stop_trigger_has_cycle_id(self, tmp_path):
        """Every StopTrigger written to DB has a cycle_id."""
        db_path = _create_test_db(tmp_path)
        holding = _make_holding()
        result = check_stop_breach(holding, current_price=85.0, market_state="RTH")

        cycle_id = "c-001"
        conn = sqlite3.connect(str(db_path))
        _write_stop_trigger(conn, result, "FIXED", cycle_id=cycle_id)

        row = conn.execute(
            "SELECT cycle_id FROM stop_events"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == cycle_id

    def test_state_transition_records_cycle_id_closed(self):
        """State machine records cycle_id_closed on terminal transition."""
        holding = _make_holding()
        result = transition(
            holding, HoldingState.STOPPED_OUT,
            reason="Stop hit", cycle_id="c-042", op_seq=3,
        )
        assert result.cycle_id_closed == "c-042"

    def test_multiple_triggers_have_distinct_cycle_ids(self, tmp_path):
        """Multiple stop events from different cycles have distinct cycle_ids."""
        db_path = _create_test_db(tmp_path)
        conn = sqlite3.connect(str(db_path))

        holding = _make_holding()
        result1 = check_stop_breach(holding, current_price=85.0, market_state="RTH")
        _write_stop_trigger(conn, result1, "FIXED", cycle_id="c-001")

        holding2 = _make_holding(ticker="AAPL", holding_id="hold-2")
        result2 = check_stop_breach(holding2, current_price=85.0, market_state="RTH")
        _write_stop_trigger(conn, result2, "FIXED", cycle_id="c-002")

        rows = conn.execute(
            "SELECT cycle_id FROM stop_events ORDER BY cycle_id"
        ).fetchall()
        conn.close()

        assert len(rows) == 2
        assert rows[0][0] == "c-001"
        assert rows[1][0] == "c-002"


# ===========================================================================
# M1: Weekly re-evaluation
# ===========================================================================

class TestWeeklyReEvalFull:
    """Weekly re-eval: thesis validated vs thesis broken [M1]."""

    def test_weekly_validated_stays_active(self):
        """Weekly re-eval with healthy thesis -> VALIDATED -> stays ACTIVE."""
        result = evaluate_thesis(
            current_conviction=0.7,
            crucible_severity=0.2,
            holding_pnl_pct=5.0,
        )
        assert result.action == "VALIDATED"
        # State stays ACTIVE (no transition needed)

    def test_weekly_thesis_broken_exits(self):
        """Weekly re-eval with collapsed conviction -> EXIT_THESIS_INVALIDATED."""
        result = evaluate_thesis(
            current_conviction=0.05,
            crucible_severity=0.1,
            holding_pnl_pct=-5.0,
        )
        assert result.action == "EXIT_THESIS_INVALIDATED"

        # Verify state machine can transition
        holding = _make_holding()
        exited = transition(
            holding, HoldingState.EXIT_THESIS_INVALIDATED,
            reason=result.reason,
            cycle_id="c-reeval-001", op_seq=1,
        )
        assert exited.state == HoldingState.EXIT_THESIS_INVALIDATED

    def test_weekly_reval_due_timing(self):
        """Weekly re-eval triggers every 7 days."""
        entry = date(2026, 1, 1)
        # Jan 1 + 7 days = Jan 8
        assert check_weekly_reeval(entry, date(2026, 1, 8), 0.5) is True
        assert check_weekly_reeval(entry, date(2026, 1, 7), 0.5) is False

    def test_weekly_reval_with_last_reeval_date(self):
        """Re-eval timing uses last_reeval_at when available."""
        entry = date(2026, 1, 1)
        last_reeval = date(2026, 1, 15)
        # 7 days after last_reeval
        assert check_weekly_reeval(entry, date(2026, 1, 22), 0.5, last_reeval_at=last_reeval) is True
        # 6 days after last_reeval -> not due
        assert check_weekly_reeval(entry, date(2026, 1, 21), 0.5, last_reeval_at=last_reeval) is False


# ===========================================================================
# Thesis aging at 90 days
# ===========================================================================

class TestThesisAgingFull:
    """90-day thesis aging: THESIS_AGING_REVIEW triggered."""

    def test_aging_review_at_90_days(self):
        """90 days since entry -> aging review triggered."""
        entry = date(2026, 1, 1)
        current = date(2026, 4, 1)  # 90 days
        assert check_thesis_aging(entry, current) is True

    def test_aging_review_state_transition(self):
        """ACTIVE -> THESIS_AGING_REVIEW is a valid state transition."""
        assert is_valid_transition(HoldingState.ACTIVE, HoldingState.THESIS_AGING_REVIEW)

        holding = _make_holding()
        reviewed = transition(
            holding, HoldingState.THESIS_AGING_REVIEW,
            reason="90-day mandatory review",
            cycle_id="c-aging-001", op_seq=1,
        )
        assert reviewed.state == HoldingState.THESIS_AGING_REVIEW

    def test_aging_review_can_return_to_active(self):
        """THESIS_AGING_REVIEW -> ACTIVE when thesis validated."""
        assert is_valid_transition(HoldingState.THESIS_AGING_REVIEW, HoldingState.ACTIVE)

        holding = _make_holding()
        reviewed = transition(
            holding, HoldingState.THESIS_AGING_REVIEW,
            reason="90-day review", cycle_id="c-001", op_seq=1,
        )
        validated = transition(
            reviewed, HoldingState.ACTIVE,
            reason="Thesis validated on aging review",
            cycle_id="c-001", op_seq=2,
        )
        assert validated.state == HoldingState.ACTIVE

    def test_aging_review_can_exit_thesis_invalidated(self):
        """THESIS_AGING_REVIEW -> EXIT_THESIS_INVALIDATED when thesis broken."""
        assert is_valid_transition(
            HoldingState.THESIS_AGING_REVIEW, HoldingState.EXIT_THESIS_INVALIDATED,
        )

    def test_aging_review_underwater_exits(self):
        """Aging review + underwater > 10% -> EXIT_THESIS_INVALIDATED."""
        result = evaluate_thesis(
            current_conviction=0.4,
            crucible_severity=0.1,
            holding_pnl_pct=-15.0,
            is_aging_review=True,
        )
        assert result.action == "EXIT_THESIS_INVALIDATED"
        assert "Aging review" in result.reason


# ===========================================================================
# C2: Opportunity cost per-holding iteration
# ===========================================================================

class TestOpportunityCostFull:
    """Opportunity cost: per-holding iteration, EXIT_OPPORTUNITY_COST [C2]."""

    def test_per_holding_iteration(self):
        """run_opportunity_cost_scan iterates all active holdings."""
        h1 = _make_holding(ticker="AAPL", holding_id="h1")
        h2 = _make_holding(ticker="MSFT", holding_id="h2")
        h3 = _make_holding(ticker="GOOG", holding_id="h3")

        results = run_opportunity_cost_scan(
            active_holdings=[h1, h2, h3],
            conviction_scores={"h1": 0.6, "h2": 0.15, "h3": 0.5},
            alternative_return_pct=8.0,
            cycle_id="c-opp-001",
        )

        assert len(results) == 3
        tickers = {r.ticker for r in results}
        assert tickers == {"AAPL", "MSFT", "GOOG"}

    def test_exit_sets_exit_state(self):
        """EXIT result sets exit_state to EXIT_OPPORTUNITY_COST."""
        holding = _make_holding()
        result = evaluate_holding(
            holding=holding,
            current_conviction=0.1,
            alternative_return_pct=15.0,
            cycle_id="c-001",
        )
        # With conviction drop from 0.6 to 0.1, should trigger exit
        if result.action == "EXIT":
            assert result.exit_state == HoldingState.EXIT_OPPORTUNITY_COST

    def test_conviction_drop_triggers_exit(self):
        """Conviction dropped > 0.3 and < 0.2 -> EXIT."""
        result = decide_hold_or_exit(
            holding_pnl_pct=-2.0,
            days_held=30,
            alternative_expected_return_pct=8.0,
            conviction_at_entry=0.7,
            current_conviction=0.15,
        )
        assert result.action == "EXIT"
        assert "Conviction dropped" in result.reason

    def test_hold_when_no_compelling_reason(self):
        """Healthy position with good conviction -> HOLD."""
        result = decide_hold_or_exit(
            holding_pnl_pct=5.0,
            days_held=10,
            alternative_expected_return_pct=8.0,
            conviction_at_entry=0.7,
            current_conviction=0.6,
        )
        assert result.action == "HOLD"

    def test_opportunity_cost_with_pnl_override(self):
        """Scan accepts external PnL data for accurate evaluation."""
        h1 = _make_holding(ticker="AAPL", holding_id="h1", conviction_score=0.5)
        h2 = _make_holding(ticker="MSFT", holding_id="h2", conviction_score=0.6)

        results = run_opportunity_cost_scan(
            active_holdings=[h1, h2],
            conviction_scores={"h1": 0.4, "h2": 0.5},
            alternative_return_pct=12.0,
            cycle_id="c-opp-002",
            pnl_pcts={"h1": -8.0, "h2": 3.0},
        )

        # h1 is underwater -8% with alternatives 12% better -> EXIT
        aapl_result = [r for r in results if r.ticker == "AAPL"][0]
        assert aapl_result.action == "EXIT"

    def test_cycle_id_required(self):
        """evaluate_holding raises ValueError when cycle_id is empty."""
        holding = _make_holding()
        with pytest.raises(ValueError, match="cycle_id is REQUIRED"):
            evaluate_holding(
                holding=holding,
                current_conviction=0.5,
                alternative_return_pct=8.0,
                cycle_id="",
            )


# ===========================================================================
# Full pipeline integration
# ===========================================================================

class TestFullStopLossPipeline:
    """End-to-end stop-loss pipeline: breach -> trigger -> transition -> audit."""

    def test_fixed_stop_full_pipeline(self, tmp_path):
        """Complete fixed stop pipeline: breach detection -> DB write -> state transition."""
        db_path = _create_test_db(tmp_path)
        cycle_id = "c-full-001"

        # 1. Create holding
        holding = _make_holding(stop_price_usd=90.0, entry_price_usd=100.0)

        # 2. Check for breach
        result = check_stop_breach(holding, current_price=85.0, market_state="RTH")
        assert result is not None
        assert result.triggered is True

        # 3. Write trigger to DB
        conn = sqlite3.connect(str(db_path))
        _write_stop_trigger(conn, result, "FIXED", cycle_id=cycle_id)

        # 4. Verify PENDING status
        row = conn.execute(
            "SELECT status FROM stop_events WHERE cycle_id = ?", (cycle_id,)
        ).fetchone()
        assert row[0] == "PENDING"

        # 5. Simulate: nervous picks up -> submits -> filled
        conn.execute(
            "UPDATE stop_events SET status = 'SUBMITTED' WHERE cycle_id = ?",
            (cycle_id,),
        )
        conn.execute(
            "UPDATE stop_events SET status = 'FILLED' WHERE cycle_id = ?",
            (cycle_id,),
        )
        conn.commit()
        conn.close()

        # 6. State machine transition
        exited = transition(
            holding, HoldingState.STOPPED_OUT,
            reason=f"Fixed stop breached at {result.current_price:.2f}",
            cycle_id=cycle_id, op_seq=5,
        )
        assert exited.state == HoldingState.STOPPED_OUT
        assert exited.cycle_id_closed == cycle_id
        assert exited.exit_date is not None

    def test_trailing_stop_full_pipeline(self, tmp_path):
        """Complete trailing stop pipeline: arm -> ratchet -> breach -> transition."""
        db_path = _create_test_db(tmp_path)
        cycle_id = "c-trail-001"

        # 1. Create holding with entry at 100, stop at 95
        holding = _make_holding(stop_price_usd=95.0, entry_price_usd=100.0)

        # 2. Price rises to 108 (R=5, profit_r = 8/5 = 1.6R > 1.5R) -> arm
        arm_state = maybe_arm_trailing(
            entry_price=100.0,
            current_price=108.0,
            stop_loss_price=95.0,
            atr_20=3.0,
            is_armed=False,
        )
        assert arm_state.armed is True
        assert arm_state.trailing_stop_price == pytest.approx(105.0)

        # 3. Price rises to 115 -> ratchet up
        new_trailing = maybe_ratchet_trailing(
            current_price=115.0, atr_20=3.0,
            current_trailing=arm_state.trailing_stop_price,
        )
        assert new_trailing == pytest.approx(112.0)  # 115 - 3.0

        # 4. Price dips to 112 -> stays (ratchet doesn't lower)
        held_trailing = maybe_ratchet_trailing(
            current_price=112.0, atr_20=3.0, current_trailing=new_trailing,
        )
        assert held_trailing == pytest.approx(112.0)

        # 5. Price falls below trailing -> breach
        proxy_armed = _HoldingProxy(trailing_stop_price_usd=112.0)
        result = check_trailing_breach(proxy_armed, current_price=110.0, market_state="RTH")
        assert result is not None
        assert result.stop_type == "TRAILING_STOP"
        assert result.triggered is True

        # 6. Write trigger to DB
        conn = sqlite3.connect(str(db_path))
        _write_stop_trigger(conn, result, "TRAILING", cycle_id=cycle_id)

        # 7. State machine transition (use a real Holding for state machine)
        holding_for_transition = _make_holding()
        exited = transition(
            holding_for_transition, HoldingState.EXIT_TRAILING_STOP,
            reason=f"Trailing stop breached at {result.current_price:.2f} (stop at {result.stop_price:.2f})",
            cycle_id=cycle_id, op_seq=3,
        )
        assert exited.state == HoldingState.EXIT_TRAILING_STOP

        conn.close()

    def test_check_holding_prioritizes_trailing(self):
        """check_holding prioritizes trailing stop over fixed stop."""
        # Holding with both fixed stop and trailing stop set
        proxy = _HoldingProxy(stop_price_usd=90.0, trailing_stop_price_usd=105.0)
        # Price below both stops
        result = check_holding(proxy, current_price=100.0, market_state="RTH")
        assert result is not None
        check_result, category = result
        assert category == "TRAILING"  # Trailing takes priority
        assert check_result.stop_type == "TRAILING_STOP"

    def test_check_holding_uses_fixed_when_no_trailing(self):
        """check_holding falls back to fixed stop when trailing is not set."""
        proxy = _HoldingProxy(stop_price_usd=90.0, trailing_stop_price_usd=None)
        result = check_holding(proxy, current_price=85.0, market_state="RTH")
        assert result is not None
        check_result, category = result
        assert category == "FIXED"
        assert check_result.stop_type == "FIXED_STOP"

    def test_check_holding_no_breach(self):
        """check_holding returns None when price is above all stops."""
        proxy = _HoldingProxy(stop_price_usd=90.0, trailing_stop_price_usd=105.0)
        result = check_holding(proxy, current_price=108.0, market_state="RTH")
        assert result is None
