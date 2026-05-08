"""Integration tests for stop-loss, trailing stop, thesis re-evaluation, and opportunity cost."""
from __future__ import annotations

from datetime import date

import pytest

from pmacs.engines.opportunity_cost import decide_hold_or_exit
from pmacs.engines.stop_loss_monitor import (
    StopCheckResult,
    check_stop_breach,
    determine_order_type,
)
from pmacs.engines.thesis_reeval import (
    check_thesis_aging,
    check_weekly_reeval,
    evaluate_thesis,
)
from pmacs.engines.trailing_stop import (
    compute_profit_r,
    maybe_arm_trailing,
    maybe_ratchet_trailing,
)
from pmacs.schemas.contracts import Holding, HoldingState, Thesis


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_holding(
    state: HoldingState = HoldingState.ACTIVE,
    stop_price_usd: float | None = 90.0,
    entry_price_usd: float = 100.0,
    conviction_score: float | None = 0.6,
) -> Holding:
    """Create a minimal Holding for testing."""
    thesis = Thesis(
        id="thesis-1",
        ticker="TEST",
        text="Test thesis",
        hash="abc123",
    )
    return Holding(
        id="hold-1",
        ticker="TEST",
        state=state,
        entry_price_usd=entry_price_usd,
        stop_price_usd=stop_price_usd,
        conviction_score=conviction_score,
        thesis=thesis,
        entry_date=date(2026, 1, 1),
    )


# ===========================================================================
# Stop breach detection
# ===========================================================================

class TestStopBreachDetection:
    """Integration tests for stop-loss breach detection."""

    def test_price_below_stop_triggers(self) -> None:
        """Price at or below stop_price_usd triggers breach."""
        holding = _make_holding(stop_price_usd=90.0)
        result = check_stop_breach(holding, current_price=85.0, market_state="RTH")
        assert result is not None
        assert result.triggered is True
        assert result.stop_price == 90.0
        assert result.current_price == 85.0
        assert result.is_gap_down is False
        assert result.order_type == "MARKET"

    def test_price_above_stop_no_trigger(self) -> None:
        """Price above stop_price_usd -> no breach."""
        holding = _make_holding(stop_price_usd=90.0)
        result = check_stop_breach(holding, current_price=95.0, market_state="RTH")
        assert result is None

    def test_price_exactly_at_stop_triggers(self) -> None:
        """Price exactly at stop -> triggers (<= check)."""
        holding = _make_holding(stop_price_usd=90.0)
        result = check_stop_breach(holding, current_price=90.0, market_state="RTH")
        assert result is not None
        assert result.triggered is True

    def test_no_stop_price_returns_none(self) -> None:
        """Holding with stop_price_usd=None -> no breach possible."""
        holding = _make_holding(stop_price_usd=None)
        result = check_stop_breach(holding, current_price=50.0, market_state="RTH")
        assert result is None


# ===========================================================================
# Gap-down order type selection
# ===========================================================================

class TestGapDownOrderType:
    """Tests for order type selection in gap-down vs RTH scenarios."""

    def test_rth_uses_market(self) -> None:
        """During RTH, breach uses MARKET order."""
        holding = _make_holding(stop_price_usd=90.0)
        result = check_stop_breach(holding, current_price=85.0, market_state="RTH")
        assert result is not None
        assert result.order_type == "MARKET"
        assert result.is_gap_down is False

    def test_non_rth_uses_market_on_open(self) -> None:
        """Outside RTH (gap-down), breach uses MARKET_ON_OPEN."""
        holding = _make_holding(stop_price_usd=90.0)
        result = check_stop_breach(holding, current_price=85.0, market_state="PRE_MARKET")
        assert result is not None
        assert result.order_type == "MARKET_ON_OPEN"
        assert result.is_gap_down is True

    def test_determine_order_type_rth(self) -> None:
        """determine_order_type returns MARKET for RTH."""
        assert determine_order_type("RTH") == "MARKET"

    def test_determine_order_type_non_rth(self) -> None:
        """determine_order_type returns MARKET_ON_OPEN for non-RTH."""
        assert determine_order_type("AH") == "MARKET_ON_OPEN"
        assert determine_order_type("PRE") == "MARKET_ON_OPEN"


# ===========================================================================
# Trailing stop arm at 1.5R
# ===========================================================================

class TestTrailingStopArm:
    """Integration tests for trailing stop arming."""

    def test_arm_at_2r(self) -> None:
        """Profit reaches 2.0R -> trailing stop arms."""
        state = maybe_arm_trailing(
            entry_price=100.0,
            current_price=110.0,
            stop_loss_price=95.0,
            atr_20=3.0,
            is_armed=False,
        )
        assert state.armed is True
        assert state.trailing_stop_price == pytest.approx(107.0)

    def test_no_arm_at_1r(self) -> None:
        """Profit at 1.0R -> trailing stop not armed."""
        state = maybe_arm_trailing(
            entry_price=100.0,
            current_price=105.0,
            stop_loss_price=95.0,
            atr_20=3.0,
            is_armed=False,
        )
        assert state.armed is False


# ===========================================================================
# Trailing ratchet up only
# ===========================================================================

class TestTrailingRatchet:
    """Tests for trailing stop ratchet behavior."""

    def test_ratchet_up_when_price_rises(self) -> None:
        """Price rises -> trailing ratchets up."""
        trailing = maybe_ratchet_trailing(
            current_price=120.0,
            atr_20=3.0,
            current_trailing=110.0,
        )
        assert trailing == pytest.approx(117.0)

    def test_no_ratchet_down_when_price_dips(self) -> None:
        """Price dips -> trailing stays at higher level."""
        trailing = maybe_ratchet_trailing(
            current_price=108.0,
            atr_20=3.0,
            current_trailing=110.0,
        )
        assert trailing == pytest.approx(110.0)


# ===========================================================================
# Thesis re-evaluation
# ===========================================================================

class TestThesisReEval:
    """Integration tests for thesis re-evaluation."""

    def test_low_conviction_exits(self) -> None:
        """Conviction < 0.1 -> EXIT_THESIS_INVALIDATED."""
        result = evaluate_thesis(
            current_conviction=0.05,
            crucible_severity=0.0,
            holding_pnl_pct=5.0,
        )
        assert result.action == "EXIT_THESIS_INVALIDATED"
        assert "Conviction collapsed" in result.reason

    def test_high_crucible_low_conviction_exits(self) -> None:
        """Crucible severity > 0.8 with conviction < 0.3 -> EXIT."""
        result = evaluate_thesis(
            current_conviction=0.2,
            crucible_severity=0.9,
            holding_pnl_pct=5.0,
        )
        assert result.action == "EXIT_THESIS_INVALIDATED"
        assert "Crucible severity" in result.reason

    def test_healthy_thesis_validated(self) -> None:
        """Good conviction, low crucible -> VALIDATED."""
        result = evaluate_thesis(
            current_conviction=0.7,
            crucible_severity=0.2,
            holding_pnl_pct=10.0,
        )
        assert result.action == "VALIDATED"

    def test_aging_review_underwater_exits(self) -> None:
        """Aging review + underwater > 10% -> EXIT."""
        result = evaluate_thesis(
            current_conviction=0.5,
            crucible_severity=0.1,
            holding_pnl_pct=-15.0,
            is_aging_review=True,
        )
        assert result.action == "EXIT_THESIS_INVALIDATED"
        assert "Aging review" in result.reason

    def test_aging_review_not_underwater_stays(self) -> None:
        """Aging review but position profitable -> VALIDATED."""
        result = evaluate_thesis(
            current_conviction=0.5,
            crucible_severity=0.1,
            holding_pnl_pct=5.0,
            is_aging_review=True,
        )
        assert result.action == "VALIDATED"


# ===========================================================================
# Thesis aging check
# ===========================================================================

class TestThesisAging:
    """Tests for thesis aging detection."""

    def test_aging_triggered_at_90_days(self) -> None:
        """90+ days since entry -> aging review triggered."""
        entry = date(2026, 1, 1)
        current = date(2026, 4, 1)  # 90 days
        assert check_thesis_aging(entry, current) is True

    def test_aging_not_triggered_before_90(self) -> None:
        """Less than 90 days -> no aging review."""
        entry = date(2026, 1, 1)
        current = date(2026, 3, 31)  # 89 days
        assert check_thesis_aging(entry, current) is False

    def test_aging_triggered_well_past_90(self) -> None:
        """Well past 90 days -> still triggered."""
        entry = date(2026, 1, 1)
        current = date(2026, 6, 1)  # 151 days
        assert check_thesis_aging(entry, current) is True


# ===========================================================================
# Weekly re-evaluation check
# ===========================================================================

class TestWeeklyReEval:
    """Tests for weekly re-evaluation check."""

    def test_weekly_due_at_7_days(self) -> None:
        """Exactly 7 days -> re-eval due."""
        entry = date(2026, 1, 1)
        current = date(2026, 1, 8)
        assert check_weekly_reeval(entry, current, 0.5) is True

    def test_weekly_not_due_at_5_days(self) -> None:
        """5 days -> not due."""
        entry = date(2026, 1, 1)
        current = date(2026, 1, 6)
        assert check_weekly_reeval(entry, current, 0.5) is False

    def test_weekly_due_at_14_days(self) -> None:
        """14 days (2 weeks) -> re-eval due."""
        entry = date(2026, 1, 1)
        current = date(2026, 1, 15)
        assert check_weekly_reeval(entry, current, 0.5) is True


# ===========================================================================
# Opportunity cost
# ===========================================================================

class TestOpportunityCost:
    """Integration tests for opportunity cost decisions."""

    def test_conviction_drop_exits(self) -> None:
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

    def test_underwater_with_better_alternatives_exits(self) -> None:
        """Underwater > 5% with alternatives 10%+ better -> EXIT."""
        result = decide_hold_or_exit(
            holding_pnl_pct=-8.0,
            days_held=45,
            alternative_expected_return_pct=12.0,
            conviction_at_entry=0.5,
            current_conviction=0.4,
        )
        assert result.action == "EXIT"
        assert "Underwater" in result.reason

    def test_hold_when_healthy(self) -> None:
        """Healthy position, good conviction -> HOLD."""
        result = decide_hold_or_exit(
            holding_pnl_pct=5.0,
            days_held=10,
            alternative_expected_return_pct=8.0,
            conviction_at_entry=0.7,
            current_conviction=0.6,
        )
        assert result.action == "HOLD"

    def test_hold_slight_conviction_drop(self) -> None:
        """Small conviction drop, conviction still above 0.2 -> HOLD."""
        result = decide_hold_or_exit(
            holding_pnl_pct=2.0,
            days_held=15,
            alternative_expected_return_pct=5.0,
            conviction_at_entry=0.6,
            current_conviction=0.4,
        )
        assert result.action == "HOLD"

    def test_hold_underwater_but_no_better_alternatives(self) -> None:
        """Underwater but alternatives not 10%+ better -> HOLD."""
        result = decide_hold_or_exit(
            holding_pnl_pct=-6.0,
            days_held=20,
            alternative_expected_return_pct=2.0,
            conviction_at_entry=0.5,
            current_conviction=0.4,
        )
        assert result.action == "HOLD"
