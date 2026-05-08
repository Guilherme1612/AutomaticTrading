"""Integration tests for 3-persona cycle (MacroRegime, CatalystSummarizer, MoatAnalyst).

Tests the deterministic pipeline: schemas validate, grammars load, sanity
validators catch degenerate outputs, and arbitration combines signals correctly.
No running llama-server required.
"""

from __future__ import annotations

import pytest

from pmacs.agents.grammars import load_grammar
from pmacs.agents.sanity.macro_regime import MacroRegimeSanity
from pmacs.engines.arbitration import ArbitrationSignal, arbitrate
from pmacs.schemas.agents import DirectionalProbability, PersonaName
from pmacs.schemas.arbitration import ArbitrationDecision
from pmacs.schemas.personas import (
    CatalystSummarizerOutput,
    MacroRegimeOutput,
    MoatAnalystOutput,
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


class Test3PersonaCycle:
    """Integration tests for 3-persona cycle + arbitration."""

    def test_3_personas_arbitrated(self) -> None:
        """3 personas produce DirectionalProbability; Arbitration combines them."""
        signals = [
            _make_signal("macro_regime", "AAPL", 0.5, 0.3, 0.2, rolling_brier=0.4),
            _make_signal("catalyst_summarizer", "AAPL", 0.6, 0.2, 0.2, rolling_brier=0.3),
            _make_signal("moat_analyst", "AAPL", 0.55, 0.25, 0.2, rolling_brier=0.35),
        ]
        result = arbitrate(signals, cycle_id="cycle-001")
        assert result.decision == ArbitrationDecision.PROCEED
        assert result.p_up > result.p_down
        assert result.matured_sources_used == 3
        assert result.ticker == "AAPL"
        # Probabilities must sum to ~1.0
        total = result.p_up + result.p_flat + result.p_down
        assert abs(total - 1.0) < 1e-6

    def test_3_personas_lower_brier_gets_higher_weight(self) -> None:
        """Persona with lower Brier score gets higher weight."""
        signals = [
            _make_signal("macro_regime", "AAPL", 0.5, 0.3, 0.2, rolling_brier=0.8),
            _make_signal("catalyst_summarizer", "AAPL", 0.6, 0.2, 0.2, rolling_brier=0.1),
            _make_signal("moat_analyst", "AAPL", 0.55, 0.25, 0.2, rolling_brier=0.5),
        ]
        result = arbitrate(signals, cycle_id="cycle-002")
        # catalyst_summarizer has lowest Brier -> highest weight
        weights = {w.persona.value: w.weight for w in result.persona_weights}
        assert weights["catalyst_summarizer"] > weights["macro_regime"]
        assert weights["catalyst_summarizer"] > weights["moat_analyst"]

    def test_macro_regime_weight_penalty(self) -> None:
        """MacroRegime gets 0.5x weight multiplier."""
        signals = [
            _make_signal("macro_regime", "AAPL", 0.5, 0.3, 0.2, rolling_brier=0.3),
            _make_signal("moat_analyst", "AAPL", 0.5, 0.3, 0.2, rolling_brier=0.3),
        ]
        result = arbitrate(signals, cycle_id="cycle-003")
        weights = {w.persona.value: w.weight for w in result.persona_weights}
        # Same Brier, but macro_regime has 0.5x penalty -> lower weight
        assert weights["moat_analyst"] > weights["macro_regime"]

    def test_mature_disagreement_aborts(self) -> None:
        """When mature sources disagree strongly, arbitration aborts."""
        signals = [
            _make_signal("macro_regime", "AAPL", 0.6, 0.3, 0.1, rolling_brier=0.3),
            _make_signal("moat_analyst", "AAPL", 0.1, 0.3, 0.6, rolling_brier=0.3),
        ]
        result = arbitrate(signals, cycle_id="cycle-004")
        assert result.decision == ArbitrationDecision.ABORT_DISAGREEMENT
        assert result.abort_reason == "MATURE_SOURCES_DISAGREE"

    def test_no_signals_returns_uniform(self) -> None:
        """Empty signals list returns uniform distribution."""
        result = arbitrate([], cycle_id="cycle-005")
        assert result.decision == ArbitrationDecision.ABORT_NO_MATURE_SOURCES
        assert result.abort_reason == "NO_SIGNALS"
        assert abs(result.p_up - 1.0 / 3) < 1e-6
        assert abs(result.p_flat - 1.0 / 3) < 1e-6
        assert abs(result.p_down - 1.0 / 3) < 1e-6

    def test_immature_agree_proceeds_bootstrap(self) -> None:
        """Immature sources that agree produce PROCEED_BOOTSTRAP_LOW_CONFIDENCE."""
        signals = [
            _make_signal(
                "macro_regime", "AAPL", 0.5, 0.3, 0.2,
                historical_n=10, rolling_brier=0.5,
            ),
            _make_signal(
                "catalyst_summarizer", "AAPL", 0.55, 0.25, 0.2,
                historical_n=15, rolling_brier=0.4,
            ),
        ]
        result = arbitrate(signals, cycle_id="cycle-006")
        assert result.decision == ArbitrationDecision.PROCEED_BOOTSTRAP_LOW_CONFIDENCE
        assert result.matured_sources_used == 0
        assert result.p_up > result.p_down

    def test_sanity_catches_degenerate(self) -> None:
        """Degenerate probability distribution fails MacroRegime sanity check."""
        validator = MacroRegimeSanity()
        output = {
            "regime": "EXPANSION",
            "regime_confidence": 0.8,
            "reasoning": "GDP accelerating",  # base class requires reasoning
            "p_up": 1.0,
            "p_flat": 0.0,
            "p_down": 0.0,
            "evidence_ids": ["ev1"],
            "yield_curve_signal": "NORMAL",
            "vix_regime": "LOW",
            "sector_rotation_summary": "test",
            "regime_reasoning": "test reasoning",
        }
        result = validator.validate(output, evidence=[])
        assert not result.passed

    def test_sanity_catches_low_confidence_non_uncertain(self) -> None:
        """Non-UNCERTAIN regime with confidence <= 0.5 fails sanity."""
        validator = MacroRegimeSanity()
        output = {
            "regime": "EXPANSION",
            "regime_confidence": 0.3,
            "reasoning": "Some reasoning here",
            "p_up": 0.6,
            "p_flat": 0.2,
            "p_down": 0.2,
            "evidence_ids": [],
            "yield_curve_signal": "NORMAL",
            "vix_regime": "LOW",
            "sector_rotation_summary": "test",
        }
        result = validator.validate(output, evidence=[])
        assert not result.passed
        assert "regime_confidence" in (result.reason or "")

    def test_sanity_uncertain_allows_low_confidence(self) -> None:
        """UNCERTAIN regime with low confidence passes sanity."""
        validator = MacroRegimeSanity()
        output = {
            "regime": "UNCERTAIN",
            "regime_confidence": 0.2,
            "reasoning": "Mixed signals",
            "p_up": 0.4,
            "p_flat": 0.35,
            "p_down": 0.25,
            "evidence_ids": [],
            "yield_curve_signal": "FLAT",
            "vix_regime": "MODERATE",
            "sector_rotation_summary": "mixed",
        }
        result = validator.validate(output, evidence=[])
        assert result.passed

    def test_gbnf_grammar_loads(self) -> None:
        """All 3 persona grammars load successfully."""
        for name in ["macro_regime", "catalyst_summarizer", "moat_analyst"]:
            grammar = load_grammar(name)
            assert len(grammar) > 0
            assert "root" in grammar

    def test_grammar_not_found_raises(self) -> None:
        """Loading a nonexistent grammar raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_grammar("nonexistent_persona")

    def test_output_models_validate(self) -> None:
        """All 3 persona output schemas validate correctly."""
        macro = MacroRegimeOutput(
            regime="EXPANSION",
            regime_confidence=0.8,
            regime_reasoning="GDP accelerating",
            yield_curve_signal="NORMAL",
            vix_regime="LOW",
            sector_rotation_summary="Tech leading",
            p_up=0.6,
            p_flat=0.2,
            p_down=0.2,
            evidence_ids=["ev1", "ev2"],
        )
        assert macro.regime == "EXPANSION"
        assert len(macro.evidence_ids) == 2

        catalyst = CatalystSummarizerOutput(
            ticker="AAPL",
            catalysts=[],
            net_catalyst_outlook="Neutral",
            p_up=0.4,
            p_flat=0.35,
            p_down=0.25,
            evidence_ids=["ev3"],
        )
        assert catalyst.ticker == "AAPL"

        moat = MoatAnalystOutput(
            ticker="AAPL",
            moat_components=[
                {
                    "moat_type": "NETWORK_EFFECTS",
                    "strength": 0.8,
                    "trajectory": "WIDENING",
                    "reasoning": "Strong network effects",
                    "evidence_ids": ["ev4"],
                }
            ],
            moat_strength=0.8,
            competitive_entry_risk="LOW",
            competitive_entry_reasoning="Strong moat",
            p_up=0.6,
            p_flat=0.2,
            p_down=0.2,
            evidence_ids=["ev4"],
        )
        assert moat.moat_strength == 0.8

    def test_prob_sum_validation_rejects_bad(self) -> None:
        """Schema rejects probabilities that don't sum to 1.0."""
        with pytest.raises(Exception):
            MacroRegimeOutput(
                regime="EXPANSION",
                regime_confidence=0.8,
                regime_reasoning="test",
                yield_curve_signal="NORMAL",
                vix_regime="LOW",
                sector_rotation_summary="test",
                p_up=0.5,
                p_flat=0.3,
                p_down=0.3,  # sum = 1.1
                evidence_ids=["ev1"],
            )

    def test_directional_probability_rejects_degenerate(self) -> None:
        """DirectionalProbability rejects all mass on one direction."""
        with pytest.raises(Exception):
            DirectionalProbability(
                persona=PersonaName.MACRO_REGIME,
                ticker="AAPL",
                p_up=1.0,
                p_flat=0.0,
                p_down=0.0,
            )

    def test_arbitrated_result_is_frozen(self) -> None:
        """Arbitrated schema is immutable."""
        result = arbitrate([], cycle_id="cycle-007")
        with pytest.raises(Exception):
            result.decision = ArbitrationDecision.PROCEED  # type: ignore[misc]
