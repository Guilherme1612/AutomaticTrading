"""Integration tests for full 7-persona cycle arbitration.

Tests all 7 persona schemas, grammars, and arbitration with mixed
mature/immature signals. No running llama-server required.
"""

from __future__ import annotations

import pytest

from pmacs.agents.grammars import load_grammar
from pmacs.engines.arbitration import ArbitrationSignal, arbitrate
from pmacs.schemas.agents import DirectionalProbability, PersonaName
from pmacs.schemas.arbitration import ArbitrationDecision
from pmacs.schemas.personas import (
    CatalystSummarizerOutput,
    ForensicsOutput,
    GrowthHunterOutput,
    InsiderActivityOutput,
    MacroRegimeOutput,
    MoatAnalystOutput,
    ShortInterestOutput,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signal(
    persona_name: str,
    ticker: str,
    p_up: float,
    p_flat: float,
    p_down: float,
    *,
    historical_n: int = 30,
    rolling_brier: float = 0.4,
) -> ArbitrationSignal:
    """Build an ArbitrationSignal from persona name and probabilities."""
    dp = DirectionalProbability(
        persona=PersonaName(persona_name),
        ticker=ticker,
        p_up=p_up,
        p_flat=p_flat,
        p_down=p_down,
        reasoning=f"{persona_name} analysis",
    )
    return ArbitrationSignal(dp, historical_n=historical_n, rolling_brier=rolling_brier)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class Test7PersonaCycle:
    """Integration tests for full 7-persona cycle + arbitration."""

    def test_7_personas_arbitrated(self) -> None:
        """All 7 personas produce signals; Arbitration combines them."""
        signals = [
            _make_signal("macro_regime", "AAPL", 0.5, 0.3, 0.2, rolling_brier=0.4),
            _make_signal("catalyst_summarizer", "AAPL", 0.6, 0.2, 0.2, rolling_brier=0.3),
            _make_signal("moat_analyst", "AAPL", 0.55, 0.25, 0.2, rolling_brier=0.35),
            _make_signal("growth_hunter", "AAPL", 0.7, 0.15, 0.15, rolling_brier=0.25),
            _make_signal("insider_activity", "AAPL", 0.4, 0.4, 0.2, historical_n=10, rolling_brier=0.5),
            _make_signal("short_interest", "AAPL", 0.45, 0.35, 0.2, rolling_brier=0.45),
            _make_signal("forensics", "AAPL", 0.3, 0.4, 0.3, rolling_brier=0.55),
        ]
        result = arbitrate(signals, cycle_id="cycle-7p-001")
        assert result.decision == ArbitrationDecision.PROCEED
        # insider_activity is immature (n=10), so 6 mature sources used
        assert result.matured_sources_used == 6
        assert result.p_up > result.p_down
        # Probabilities sum to ~1.0
        total = result.p_up + result.p_flat + result.p_down
        assert abs(total - 1.0) < 1e-6

    def test_all_mature_proceed(self) -> None:
        """All 7 mature sources produce PROCEED."""
        signals = [
            _make_signal(name, "AAPL", 0.5, 0.3, 0.2, rolling_brier=0.4)
            for name in [
                "macro_regime", "catalyst_summarizer", "moat_analyst",
                "growth_hunter", "insider_activity", "short_interest", "forensics",
            ]
        ]
        result = arbitrate(signals, cycle_id="cycle-7p-002")
        assert result.decision == ArbitrationDecision.PROCEED
        assert result.matured_sources_used == 7

    def test_all_immature_agree_bootstrap(self) -> None:
        """All immature sources that agree produce PROCEED_BOOTSTRAP_LOW_CONFIDENCE."""
        signals = [
            _make_signal(
                name, "AAPL", 0.5, 0.3, 0.2,
                historical_n=5, rolling_brier=0.5,
            )
            for name in [
                "macro_regime", "catalyst_summarizer", "moat_analyst",
                "growth_hunter", "insider_activity", "short_interest", "forensics",
            ]
        ]
        result = arbitrate(signals, cycle_id="cycle-7p-003")
        assert result.decision == ArbitrationDecision.PROCEED_BOOTSTRAP_LOW_CONFIDENCE
        assert result.matured_sources_used == 0

    def test_extreme_prob_dampening(self) -> None:
        """Extreme probability (>0.9) gets dampened."""
        signals = [
            _make_signal("moat_analyst", "AAPL", 0.95, 0.03, 0.02, rolling_brier=0.2),
            _make_signal("growth_hunter", "AAPL", 0.5, 0.3, 0.2, rolling_brier=0.4),
        ]
        result = arbitrate(signals, cycle_id="cycle-7p-004")
        # moat_analyst extreme prob dampened by 0.5x, so not dominated
        assert result.p_up < 0.95

    def test_extreme_down_dampening(self) -> None:
        """Extreme p_down also gets dampened."""
        signals = [
            _make_signal("forensics", "AAPL", 0.02, 0.03, 0.95, rolling_brier=0.3),
            _make_signal("growth_hunter", "AAPL", 0.5, 0.3, 0.2, rolling_brier=0.4),
        ]
        result = arbitrate(signals, cycle_id="cycle-7p-005")
        assert result.p_down < 0.95

    def test_all_grammars_load(self) -> None:
        """All 7 persona grammars load successfully."""
        for name in [
            "macro_regime", "catalyst_summarizer", "moat_analyst",
            "growth_hunter", "insider_activity", "short_interest", "forensics",
        ]:
            grammar = load_grammar(name)
            assert len(grammar) > 0
            assert "root" in grammar

    def test_all_output_models_instantiate(self) -> None:
        """All 7 persona output schemas create valid instances."""
        macro = MacroRegimeOutput(
            regime="EXPANSION", regime_confidence=0.8,
            regime_reasoning="GDP accelerating",
            yield_curve_signal="NORMAL", vix_regime="LOW",
            sector_rotation_summary="Tech leading",
            p_up=0.6, p_flat=0.2, p_down=0.2,
            evidence_ids=["ev1"],
        )
        assert macro.regime == "EXPANSION"

        catalyst = CatalystSummarizerOutput(
            ticker="AAPL", catalysts=[], net_catalyst_outlook="Neutral",
            p_up=0.4, p_flat=0.35, p_down=0.25,
            evidence_ids=["ev2"],
        )
        assert catalyst.ticker == "AAPL"

        moat = MoatAnalystOutput(
            ticker="AAPL",
            moat_components=[{
                "moat_type": "NETWORK_EFFECTS", "strength": 0.8,
                "trajectory": "WIDENING", "reasoning": "Strong",
                "evidence_ids": ["ev3"],
            }],
            moat_strength=0.8, competitive_entry_risk="LOW",
            competitive_entry_reasoning="Strong moat",
            p_up=0.6, p_flat=0.2, p_down=0.2,
            evidence_ids=["ev3"],
        )
        assert moat.moat_strength == 0.8

        growth = GrowthHunterOutput(
            ticker="AAPL",
            revenue_acceleration="ACCELERATING",
            gross_margin_trend="EXPANDING",
            growth_durability="HIGH",
            growth_durability_reasoning="Strong recurring revenue",
            key_risk_to_growth="Competition",
            p_up=0.65, p_flat=0.2, p_down=0.15,
            evidence_ids=["ev4"],
        )
        assert growth.growth_durability == "HIGH"

        insider = InsiderActivityOutput(
            ticker="AAPL",
            transactions=[{
                "insider_name": "Jane Doe",
                "insider_role": "CEO",
                "transaction_type": "OPEN_MARKET_BUY",
                "amount_usd": 500000.0,
                "shares": 2500,
                "date": "2026-05-01",
                "evidence_id": "ev5",
            }],
            signal="CEO_BUY",
            signal_reasoning="CEO made large open market buy",
            p_up=0.6, p_flat=0.3, p_down=0.1,
            evidence_ids=["ev5"],
        )
        assert insider.signal == "CEO_BUY"

        short = ShortInterestOutput(
            ticker="AAPL",
            anomaly="NORMAL",
            anomaly_reasoning="Short interest within normal range",
            p_up=0.45, p_flat=0.35, p_down=0.2,
            evidence_ids=["ev6"],
        )
        assert short.anomaly == "NORMAL"

        forensics = ForensicsOutput(
            ticker="AAPL",
            red_flags=[],
            red_flag_count=0,
            overall_accounting_quality="CLEAN",
            p_up=0.5, p_flat=0.3, p_down=0.2,
            evidence_ids=["ev7"],
        )
        assert forensics.overall_accounting_quality == "CLEAN"

    def test_forensics_flag_count_mismatch_rejected(self) -> None:
        """ForensicsOutput rejects mismatch between red_flag_count and len(red_flags)."""
        with pytest.raises(Exception):
            ForensicsOutput(
                ticker="AAPL",
                red_flags=[{
                    "category": "REVENUE_QUALITY",
                    "severity": 0.8,
                    "description": "Unusual revenue recognition",
                    "evidence_ids": ["ev1"],
                }],
                red_flag_count=0,  # mismatch: 1 flag but count says 0
                overall_accounting_quality="MATERIAL_CONCERNS",
                p_up=0.3, p_flat=0.3, p_down=0.4,
                evidence_ids=["ev1"],
            )

    def test_mixed_mature_immature_uses_only_mature(self) -> None:
        """When mature sources exist, immature sources are excluded from weighting."""
        signals = [
            _make_signal("moat_analyst", "AAPL", 0.6, 0.2, 0.2, rolling_brier=0.3),
            _make_signal("growth_hunter", "AAPL", 0.7, 0.15, 0.15, rolling_brier=0.25),
            _make_signal("insider_activity", "AAPL", 0.4, 0.4, 0.2, historical_n=5, rolling_brier=0.5),
        ]
        result = arbitrate(signals, cycle_id="cycle-7p-006")
        assert result.decision == ArbitrationDecision.PROCEED
        assert result.matured_sources_used == 2
        # Only moat_analyst and growth_hunter in weights
        assert len(result.persona_weights) == 2

    def test_persona_weights_sum_to_one(self) -> None:
        """Persona weights sum to approximately 1.0 for mature sources."""
        signals = [
            _make_signal(name, "AAPL", 0.5, 0.3, 0.2, rolling_brier=0.4)
            for name in ["moat_analyst", "growth_hunter", "short_interest"]
        ]
        result = arbitrate(signals, cycle_id="cycle-7p-007")
        assert result.decision == ArbitrationDecision.PROCEED
        total_weight = sum(w.weight for w in result.persona_weights)
        assert abs(total_weight - 1.0) < 1e-6

    def test_disagreement_with_one_up_one_down(self) -> None:
        """One source bullish, another bearish -> ABORT_DISAGREEMENT."""
        signals = [
            _make_signal("growth_hunter", "AAPL", 0.7, 0.2, 0.1, rolling_brier=0.3),
            _make_signal("forensics", "AAPL", 0.1, 0.2, 0.7, rolling_brier=0.3),
        ]
        result = arbitrate(signals, cycle_id="cycle-7p-008")
        assert result.decision == ArbitrationDecision.ABORT_DISAGREEMENT

    def test_agreement_score_high_when_aligned(self) -> None:
        """Agreement score is 1.0 when all mature sources agree on direction."""
        signals = [
            _make_signal("moat_analyst", "AAPL", 0.6, 0.25, 0.15, rolling_brier=0.3),
            _make_signal("growth_hunter", "AAPL", 0.55, 0.3, 0.15, rolling_brier=0.35),
            _make_signal("short_interest", "AAPL", 0.5, 0.35, 0.15, rolling_brier=0.4),
        ]
        result = arbitrate(signals, cycle_id="cycle-7p-009")
        assert result.decision == ArbitrationDecision.PROCEED
        assert result.agreement_score == 1.0

    def test_agreement_score_partial_when_mixed_directions(self) -> None:
        """Agreement score is 0.5 when mature sources disagree on dominant direction."""
        # moat_analyst: p_flat (0.5) dominant, growth_hunter: p_up (0.5) dominant
        # Not a strong disagreement (no p > 0.5 for opposing up/down), but different directions
        signals = [
            _make_signal("moat_analyst", "AAPL", 0.2, 0.5, 0.3, rolling_brier=0.3),
            _make_signal("growth_hunter", "AAPL", 0.5, 0.3, 0.2, rolling_brier=0.35),
        ]
        result = arbitrate(signals, cycle_id="cycle-7p-010")
        assert result.decision == ArbitrationDecision.PROCEED
        assert result.agreement_score == 0.5
