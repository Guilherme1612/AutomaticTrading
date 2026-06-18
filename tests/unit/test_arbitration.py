"""Unit tests for the arbitration engine (Architecture.md §9.1)."""

from __future__ import annotations

import pytest

from pmacs.engines.arbitration import (
    MACRO_REGIME_WEIGHT_MULTIPLIER,
    EXTREME_PROB_DAMPENING_FACTOR,
    ArbitrationSignal,
    arbitrate,
)
from pmacs.schemas.agents import DirectionalProbability, PersonaName
from pmacs.schemas.arbitration import ArbitrationDecision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dp(
    persona: PersonaName = PersonaName.MOAT_ANALYST,
    ticker: str = "AAPL",
    p_up: float = 0.5,
    p_flat: float = 0.3,
    p_down: float = 0.2,
    historical_n: int = 30,
    rolling_brier: float = 0.3,
) -> ArbitrationSignal:
    """Build an ArbitrationSignal with sensible defaults."""
    dp = DirectionalProbability(
        persona=persona,
        ticker=ticker,
        p_up=p_up,
        p_flat=p_flat,
        p_down=p_down,
    )
    return ArbitrationSignal(dp, historical_n=historical_n, rolling_brier=rolling_brier)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSingleMatureSource:
    """Single mature source -> PROCEED with that source's probabilities."""

    def test_proceed(self):
        sig = _dp(p_up=0.6, p_flat=0.2, p_down=0.2, historical_n=50, rolling_brier=0.2)
        result = arbitrate([sig], cycle_id="c001")

        assert result.decision == ArbitrationDecision.PROCEED
        assert result.ticker == "AAPL"
        assert result.matured_sources_used == 1
        assert abs(result.p_up - 0.6) < 1e-6
        assert abs(result.p_flat - 0.2) < 1e-6
        assert abs(result.p_down - 0.2) < 1e-6
        assert result.abort_reason is None

    def test_probability_sums_to_one(self):
        sig = _dp(p_up=0.4, p_flat=0.35, p_down=0.25, historical_n=100)
        result = arbitrate([sig], cycle_id="c002")

        total = result.p_up + result.p_flat + result.p_down
        assert abs(total - 1.0) < 1e-6


class TestTwoMatureSourcesAgree:
    """Two mature sources that agree -> PROCEED with combined probs."""

    def test_combined(self):
        s1 = _dp(
            persona=PersonaName.MOAT_ANALYST,
            p_up=0.6, p_flat=0.2, p_down=0.2,
            historical_n=50, rolling_brier=0.2,
        )
        s2 = _dp(
            persona=PersonaName.GROWTH_HUNTER,
            p_up=0.7, p_flat=0.2, p_down=0.1,
            historical_n=40, rolling_brier=0.3,
        )
        result = arbitrate([s1, s2], cycle_id="c003")

        assert result.decision == ArbitrationDecision.PROCEED
        assert result.matured_sources_used == 2
        # Weight for s1: 1/(0.2+0.05) = 4.0, s2: 1/(0.3+0.05) = 2.857
        # Total: 6.857, w1=0.583, w2=0.417
        # p_up = 0.6*0.583 + 0.7*0.417 = 0.350 + 0.292 = 0.642
        assert abs(result.p_up - 0.6417) < 0.001
        assert abs(result.p_flat - 0.2) < 0.001
        total = result.p_up + result.p_flat + result.p_down
        assert abs(total - 1.0) < 1e-4


class TestTwoMatureSourcesDisagree:
    """Two mature sources that disagree -> ABORT_DISAGREEMENT."""

    def test_abort(self):
        s1 = _dp(
            persona=PersonaName.MOAT_ANALYST,
            p_up=0.6, p_flat=0.2, p_down=0.2,
            historical_n=50, rolling_brier=0.2,
        )
        s2 = _dp(
            persona=PersonaName.FORENSICS,
            p_up=0.1, p_flat=0.2, p_down=0.7,
            historical_n=40, rolling_brier=0.3,
        )
        result = arbitrate([s1, s2], cycle_id="c004")

        assert result.decision == ArbitrationDecision.ABORT_DISAGREEMENT
        assert result.abort_reason == "MATURE_SOURCES_DISAGREE"
        assert result.matured_sources_used == 2


class TestAllImmatureAgree:
    """All immature sources that agree -> PROCEED_BOOTSTRAP_LOW_CONFIDENCE."""

    def test_bootstrap(self):
        s1 = _dp(
            persona=PersonaName.MOAT_ANALYST,
            p_up=0.6, p_flat=0.3, p_down=0.1,
            historical_n=5,
        )
        s2 = _dp(
            persona=PersonaName.GROWTH_HUNTER,
            p_up=0.65, p_flat=0.25, p_down=0.1,
            historical_n=10,
        )
        result = arbitrate([s1, s2], cycle_id="c005")

        assert result.decision == ArbitrationDecision.PROCEED_BOOTSTRAP_LOW_CONFIDENCE
        assert result.matured_sources_used == 0
        # Equal weight: (0.6+0.65)/2 = 0.625
        assert abs(result.p_up - 0.625) < 1e-6
        assert result.abort_reason is None


class TestAllImmatureDisagree:
    """All immature sources that disagree -> ABORT_NO_MATURE_SOURCES."""

    def test_abort(self):
        s1 = _dp(
            persona=PersonaName.MOAT_ANALYST,
            p_up=0.6, p_flat=0.3, p_down=0.1,
            historical_n=5,
        )
        s2 = _dp(
            persona=PersonaName.FORENSICS,
            p_up=0.1, p_flat=0.2, p_down=0.7,
            historical_n=10,
        )
        result = arbitrate([s1, s2], cycle_id="c006")

        assert result.decision == ArbitrationDecision.ABORT_NO_MATURE_SOURCES
        assert result.abort_reason == "NO_MAJORITY_DIRECTION"


class TestMacroRegimeMultiplier:
    """MacroRegime persona gets 0.5x weight multiplier."""

    def test_macro_weight_reduced(self):
        macro = _dp(
            persona=PersonaName.MACRO_REGIME,
            p_up=0.5, p_flat=0.3, p_down=0.2,
            historical_n=50, rolling_brier=0.3,
        )
        analyst = _dp(
            persona=PersonaName.MOAT_ANALYST,
            p_up=0.6, p_flat=0.2, p_down=0.2,
            historical_n=50, rolling_brier=0.3,
        )
        result = arbitrate([macro, analyst], cycle_id="c007")

        assert result.decision == ArbitrationDecision.PROCEED
        # Same Brier, but macro gets 0.5x: raw macro weight = 1/(0.3+0.05)*0.5 = 1.429
        # analyst: 1/(0.3+0.05) = 2.857
        # total = 4.286, macro_w = 0.333, analyst_w = 0.667
        macro_w = [pw for pw in result.persona_weights if pw.persona == PersonaName.MACRO_REGIME][0]
        analyst_w = [pw for pw in result.persona_weights if pw.persona == PersonaName.MOAT_ANALYST][0]

        assert abs(macro_w.weight - 1.0 / 3) < 0.01
        assert abs(analyst_w.weight - 2.0 / 3) < 0.01
        # Analyst should have higher weight
        assert analyst_w.weight > macro_w.weight


class TestExtremeProbDampening:
    """Persona with p > 0.9 gets weight capped at 0.5x."""

    def test_extreme_up_dampened(self):
        extreme = _dp(
            persona=PersonaName.MOAT_ANALYST,
            p_up=0.95, p_flat=0.03, p_down=0.02,
            historical_n=50, rolling_brier=0.2,
        )
        normal = _dp(
            persona=PersonaName.GROWTH_HUNTER,
            p_up=0.55, p_flat=0.25, p_down=0.2,
            historical_n=50, rolling_brier=0.2,
        )
        result = arbitrate([extreme, normal], cycle_id="c008")

        assert result.decision == ArbitrationDecision.PROCEED
        # Same Brier, but extreme gets 0.5x dampening
        # extreme raw: 1/(0.2+0.05)*0.5 = 2.0
        # normal raw: 1/(0.2+0.05) = 4.0
        # total = 6.0, extreme_w = 1/3, normal_w = 2/3
        extreme_w = [pw for pw in result.persona_weights if pw.persona == PersonaName.MOAT_ANALYST][0]
        normal_w = [pw for pw in result.persona_weights if pw.persona == PersonaName.GROWTH_HUNTER][0]

        assert abs(extreme_w.weight - 1.0 / 3) < 0.01
        assert abs(normal_w.weight - 2.0 / 3) < 0.01

    def test_extreme_down_dampened(self):
        extreme = _dp(
            persona=PersonaName.FORENSICS,
            p_up=0.02, p_flat=0.03, p_down=0.95,
            historical_n=50, rolling_brier=0.2,
        )
        normal = _dp(
            persona=PersonaName.MOAT_ANALYST,
            p_up=0.6, p_flat=0.2, p_down=0.2,
            historical_n=50, rolling_brier=0.2,
        )
        result = arbitrate([extreme, normal], cycle_id="c009")

        # These disagree (p_down=0.95 vs p_up=0.6), so ABORT_DISAGREEMENT
        assert result.decision == ArbitrationDecision.ABORT_DISAGREEMENT


class TestWeightNormalization:
    """Weights must sum to 1.0."""

    def test_weights_sum_to_one(self):
        signals = [
            _dp(
                persona=PersonaName.MOAT_ANALYST,
                p_up=0.5, p_flat=0.3, p_down=0.2,
                historical_n=50, rolling_brier=0.2,
            ),
            _dp(
                persona=PersonaName.GROWTH_HUNTER,
                p_up=0.4, p_flat=0.4, p_down=0.2,
                historical_n=50, rolling_brier=0.4,
            ),
            _dp(
                persona=PersonaName.CATALYST_SUMMARIZER,
                p_up=0.55, p_flat=0.25, p_down=0.2,
                historical_n=50, rolling_brier=0.35,
            ),
        ]
        result = arbitrate(signals, cycle_id="c010")

        assert result.decision == ArbitrationDecision.PROCEED
        total_weight = sum(pw.weight for pw in result.persona_weights)
        assert abs(total_weight - 1.0) < 1e-6


class TestProbabilitySums:
    """Combined probabilities must sum to ~1.0."""

    def test_sums_remain_one(self):
        signals = [
            _dp(
                persona=PersonaName.MOAT_ANALYST,
                p_up=0.45, p_flat=0.35, p_down=0.2,
                historical_n=50, rolling_brier=0.15,
            ),
            _dp(
                persona=PersonaName.GROWTH_HUNTER,
                p_up=0.6, p_flat=0.2, p_down=0.2,
                historical_n=50, rolling_brier=0.5,
            ),
        ]
        result = arbitrate(signals, cycle_id="c011")

        total = result.p_up + result.p_flat + result.p_down
        assert abs(total - 1.0) < 1e-6


class TestEmptySignals:
    """No signals -> ABORT_NO_MATURE_SOURCES."""

    def test_empty(self):
        result = arbitrate([], cycle_id="c012")

        assert result.decision == ArbitrationDecision.ABORT_NO_MATURE_SOURCES
        assert result.abort_reason == "NO_SIGNALS"


class TestBootstrapNotAllAgree:
    """Immature sources where some say 'flat' and some say 'up' still agree
    if they don't have opposing directions (no up vs down conflict)."""

    def test_flat_and_up_agree(self):
        s1 = _dp(
            persona=PersonaName.MOAT_ANALYST,
            p_up=0.4, p_flat=0.5, p_down=0.1,
            historical_n=5,
        )
        s2 = _dp(
            persona=PersonaName.GROWTH_HUNTER,
            p_up=0.5, p_flat=0.4, p_down=0.1,
            historical_n=10,
        )
        result = arbitrate([s1, s2], cycle_id="c013")

        # Both dominant directions differ (flat vs up) but neither is down
        # _all_agree_direction checks if ALL directions are the same
        # flat != up -> disagree -> ABORT_NO_MATURE_SOURCES
        assert result.decision == ArbitrationDecision.ABORT_NO_MATURE_SOURCES


class TestMajorityThreshold60Percent:
    """Majority direction requires >= 60% of signals (true ceiling)."""

    def _make(self, n_up: int, n_flat: int, n_down: int) -> list[ArbitrationSignal]:
        signals = []
        personas = [
            PersonaName.MOAT_ANALYST,
            PersonaName.GROWTH_HUNTER,
            PersonaName.CATALYST_SUMMARIZER,
            PersonaName.INSIDER_ACTIVITY,
            PersonaName.SHORT_INTEREST,
            PersonaName.FORENSICS,
            PersonaName.MACRO_REGIME,
        ]
        for i in range(n_up):
            signals.append(_dp(persona=personas[i], p_up=0.6, p_flat=0.3, p_down=0.1, historical_n=5))
        for i in range(n_flat):
            signals.append(_dp(persona=personas[n_up + i], p_up=0.2, p_flat=0.7, p_down=0.1, historical_n=5))
        for i in range(n_down):
            signals.append(_dp(persona=personas[n_up + n_flat + i], p_up=0.1, p_flat=0.3, p_down=0.6, historical_n=5))
        return signals

    def test_four_of_seven_is_not_majority(self):
        """4/7 = 57%, below true 60% threshold."""
        result = arbitrate(self._make(4, 3, 0), cycle_id="c014")
        assert result.decision == ArbitrationDecision.ABORT_NO_MATURE_SOURCES
        assert result.abort_reason == "NO_MAJORITY_DIRECTION"

    def test_five_of_seven_is_majority(self):
        """5/7 = 71%, meets true 60% threshold."""
        result = arbitrate(self._make(5, 2, 0), cycle_id="c015")
        assert result.decision == ArbitrationDecision.PROCEED_BOOTSTRAP_LOW_CONFIDENCE
        assert result.abort_reason is None

    def test_three_of_five_is_majority(self):
        """3/5 = 60%, exactly meets threshold."""
        result = arbitrate(self._make(3, 2, 0), cycle_id="c016")
        assert result.decision == ArbitrationDecision.PROCEED_BOOTSTRAP_LOW_CONFIDENCE

    def test_two_of_five_is_not_majority(self):
        """2/5 = 40% up, with 1 flat and 2 down — no direction holds 60%."""
        result = arbitrate(self._make(2, 1, 2), cycle_id="c017")
        assert result.decision == ArbitrationDecision.ABORT_NO_MATURE_SOURCES
