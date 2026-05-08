"""Schema compilation tests — Phase 1 exit test #1.

Verifies ALL Pydantic models compile, instantiate, and cross-field validators work.
"""

import pytest
from datetime import date, datetime


class TestContracts:
    def test_holding_state_enum(self):
        from pmacs.schemas.contracts import HoldingState
        assert len(HoldingState) >= 22
        assert HoldingState.CANDIDATE
        assert HoldingState.ACTIVE
        assert HoldingState.STOPPED_OUT

    def test_holding_creates(self):
        from pmacs.schemas.contracts import Holding, HoldingState
        h = Holding(id="h1", ticker="AAPL")
        assert h.state == HoldingState.CANDIDATE
        assert h.ticker == "AAPL"

    def test_terminal_states(self):
        from pmacs.schemas.contracts import TERMINAL_STATES, HoldingState
        assert HoldingState.STOPPED_OUT in TERMINAL_STATES
        assert HoldingState.RESOLVED_UP in TERMINAL_STATES
        assert HoldingState.ACTIVE not in TERMINAL_STATES

    def test_valid_transitions(self):
        from pmacs.schemas.contracts import VALID_TRANSITIONS, HoldingState
        assert HoldingState.ACTIVE in VALID_TRANSITIONS
        assert HoldingState.STOPPED_OUT in VALID_TRANSITIONS[HoldingState.ACTIVE]
        assert HoldingState.CANDIDATE not in VALID_TRANSITIONS[HoldingState.ACTIVE]

    def test_thesis_creates(self):
        from pmacs.schemas.contracts import Thesis
        t = Thesis(id="t1", ticker="AAPL", text="Test thesis", hash="abc123")
        assert t.version == 1


class TestAgents:
    def test_directional_probability_valid(self):
        from pmacs.schemas.agents import DirectionalProbability, PersonaName
        dp = DirectionalProbability(
            persona=PersonaName.MACRO_REGIME,
            ticker="AAPL",
            p_up=0.5, p_flat=0.3, p_down=0.2,
        )
        assert dp.p_up == 0.5

    def test_directional_probability_rejects_bad_sum(self):
        from pmacs.schemas.agents import DirectionalProbability, PersonaName
        with pytest.raises(ValueError, match="sum to ~1.0"):
            DirectionalProbability(
                persona=PersonaName.MACRO_REGIME,
                ticker="AAPL",
                p_up=0.5, p_flat=0.5, p_down=0.5,
            )

    def test_directional_probability_rejects_degenerate(self):
        from pmacs.schemas.agents import DirectionalProbability, PersonaName
        with pytest.raises(ValueError, match="Degenerate"):
            DirectionalProbability(
                persona=PersonaName.MACRO_REGIME,
                ticker="AAPL",
                p_up=1.0, p_flat=0.0, p_down=0.0,
            )


class TestTrade:
    def test_trade_plan_creates(self):
        from pmacs.schemas.trade import TradePlan, TradeDirection, OrderType
        tp = TradePlan(
            id="tp1", ticker="AAPL", direction=TradeDirection.BUY,
            quantity=10, price_usd=150.0,
        )
        assert tp.order_type == OrderType.LIMIT

    def test_trade_result_creates(self):
        from pmacs.schemas.trade import TradeResult, TradeDirection
        tr = TradeResult(
            id="tr1", trade_plan_id="tp1", ticker="AAPL",
            direction=TradeDirection.BUY,
        )
        assert tr.status == "PENDING"


class TestSystem:
    def test_mode_enum(self):
        from pmacs.schemas.system import Mode
        assert len(Mode) == 7
        assert Mode.INSTALLING
        assert Mode.LIVE_EXPANDED

    def test_kill_switch_triggers(self):
        from pmacs.schemas.system import KillSwitchTrigger
        assert len(KillSwitchTrigger) == 10

    def test_mode_transitions(self):
        from pmacs.schemas.system import VALID_MODE_TRANSITIONS, Mode
        assert Mode.PAPER_VALIDATED in VALID_MODE_TRANSITIONS[Mode.PAPER]


class TestData:
    def test_evidence_creates(self):
        from pmacs.schemas.data import Evidence, DataSource, EvidenceType
        e = Evidence(
            id="e1", source=DataSource.POLYGON, type=EvidenceType.MARKET_DATA,
            ticker="AAPL", fetched_at=datetime.utcnow(), content_hash="abc",
        )
        assert e.ticker == "AAPL"

    def test_evidence_packet_creates(self):
        from pmacs.schemas.data import EvidencePacket
        ep = EvidencePacket(ticker="AAPL", cycle_id="c1")
        assert ep.source_count == 0


class TestFreshness:
    def test_freshness_result_creates(self):
        from pmacs.schemas.freshness import FreshnessResult, FreshnessStatus, CriticalityLevel
        fr = FreshnessResult(
            source="polygon", status=FreshnessStatus.STALE,
            criticality=CriticalityLevel.CRITICAL, age_seconds=600,
            max_age_seconds=300,
        )
        assert fr.should_abort is True

    def test_important_degrades(self):
        from pmacs.schemas.freshness import FreshnessResult, FreshnessStatus, CriticalityLevel
        fr = FreshnessResult(
            source="finra", status=FreshnessStatus.STALE,
            criticality=CriticalityLevel.IMPORTANT, age_seconds=200000,
            max_age_seconds=86400,
        )
        assert fr.should_degrade is True


class TestCurrency:
    def test_fx_rate_creates(self):
        from pmacs.schemas.currency import FxRate
        fx = FxRate(usd_per_eur=1.08, business_date=date(2024, 1, 15), fetched_at=datetime.utcnow())
        assert fx.usd_per_eur == 1.08
        assert abs(fx.eur_per_usd - 1 / 1.08) < 1e-6

    def test_round_trip(self):
        from pmacs.schemas.currency import FxRate, usd_to_eur, eur_to_usd
        fx = FxRate(usd_per_eur=1.08, business_date=date(2024, 1, 15), fetched_at=datetime.utcnow())
        original = 100.0
        converted = eur_to_usd(usd_to_eur(original, fx), fx)
        assert abs(converted - original) < 1e-6


class TestCatalysts:
    def test_catalyst_types(self):
        from pmacs.schemas.catalysts import CatalystType
        assert len(CatalystType) == 7

    def test_catalyst_creates(self):
        from pmacs.schemas.catalysts import Catalyst, CatalystType
        c = Catalyst(id="cat1", ticker="AAPL", type=CatalystType.EARNINGS_RELEASE)
        assert c.type == CatalystType.EARNINGS_RELEASE


class TestArbitration:
    def test_arbitrated_creates(self):
        from pmacs.schemas.arbitration import Arbitrated
        a = Arbitrated(ticker="AAPL", cycle_id="c1", p_up=0.6, p_flat=0.2, p_down=0.2)
        assert a.p_up == 0.6


class TestConviction:
    def test_verdict_tiers(self):
        from pmacs.schemas.conviction import VerdictTier, ConvictionResult
        assert ConvictionResult.score_to_verdict(0.7) == VerdictTier.STRONG_BUY
        assert ConvictionResult.score_to_verdict(0.4) == VerdictTier.BUY
        assert ConvictionResult.score_to_verdict(0.2) == VerdictTier.SKIP


class TestSizing:
    def test_sizing_result_creates(self):
        from pmacs.schemas.sizing import SizingResult
        sr = SizingResult(ticker="AAPL", position_size_usd=500, position_size_pct=0.1, share_count=3, kelly_fraction=0.2)
        assert sr.capped is False


class TestPortfolio:
    def test_portfolio_state(self):
        from pmacs.schemas.portfolio import PortfolioState
        ps = PortfolioState(cash_usd=5000, total_value_usd=5000)
        assert ps.position_count == 0


class TestQueue:
    def test_queue_item(self):
        from pmacs.schemas.queue import QueueItem, PriorityBand
        qi = QueueItem(cycle_id="c1", ticker="AAPL")
        assert qi.priority_band == PriorityBand.P3_NORMAL


class TestFailure:
    def test_failure_taxonomy_count(self):
        from pmacs.schemas.failure import FailureTaxonomy
        assert len(FailureTaxonomy) == 18

    def test_failed_assumption_creates(self):
        from pmacs.schemas.failure import FailedAssumption, FailureTaxonomy
        fa = FailedAssumption(
            id="fa1", taxonomy=FailureTaxonomy.MOAT_DRIFT_OVERESTIMATE,
            severity=0.7, holding_id="h1", cycle_id="c1",
        )
        assert fa.taxonomy == FailureTaxonomy.MOAT_DRIFT_OVERESTIMATE


class TestMutation:
    def test_mutation_candidate_creates(self):
        from pmacs.schemas.mutation import MutationCandidate, MutationDimension
        mc = MutationCandidate(
            id="mc1", dimension=MutationDimension.PERSONA_WEIGHT,
            target="macro_regime", baseline_value="1.0", candidate_value="1.2",
        )
        assert mc.reversible is True


class TestNoPydanticV1:
    def test_no_v1_imports(self):
        """Verify no pydantic.v1 imports exist in schemas."""
        import importlib
        import pkgutil
        import pmacs.schemas

        for importer, modname, ispkg in pkgutil.walk_packages(
            pmacs.schemas.__path__, pmacs.schemas.__name__ + "."
        ):
            mod = importlib.import_module(modname)
            source = inspect.getsource(mod)
            assert "pydantic.v1" not in source, f"Found pydantic.v1 in {modname}"
            assert "class Config:" not in source, f"Found class Config: in {modname}"


import inspect
