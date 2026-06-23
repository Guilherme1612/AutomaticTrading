"""Unit tests for wave-2 orchestrator wiring (Agents.md §11b-§11d, §16.4, §9.4b).

Covers the deterministic, testable pieces of the wave-2 pipeline:
- _growth_to_fraction normalization
- ArbitrationSignal.weight_multiplier cap applied by arbitrate()
- _rebuild_evidence_brief auditor-flag injection (§16.4)
- _parse_auditor_flags defense-in-depth
- _compute_valuation graceful degradation (FCF-negative → NEUTRAL) + positive path

The full 9-persona + auditor integration is covered in tests/integration.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pmacs.data.evidence_router import DataSource
from pmacs.engines.arbitration import ArbitrationSignal, arbitrate
from pmacs.nervous.orchestrator import (
    CycleOrchestrator,
    _growth_to_fraction,
    _rebuild_evidence_brief,
)
from pmacs.schemas.agents import DirectionalProbability, PersonaName, PersonaOutput
from pmacs.schemas.personas import AuditorFlag, AuditorOutput


# ── _growth_to_fraction ───────────────────────────────────────────────────

class TestGrowthToFraction:
    def test_percent_is_divided(self):
        assert abs(_growth_to_fraction(18.0) - 0.18) < 1e-9

    def test_fraction_passes_through(self):
        assert abs(_growth_to_fraction(0.18) - 0.18) < 1e-9

    def test_clamps_high(self):
        assert _growth_to_fraction(150.0) == 0.5

    def test_clamps_low(self):
        assert _growth_to_fraction(-50.0) == -0.5

    def test_zero(self):
        assert _growth_to_fraction(0.0) == 0.0


# ── ArbitrationSignal.weight_multiplier ───────────────────────────────────

def _mature_signal(persona, p_up, p_flat, p_down, *, weight_multiplier=1.0):
    dp = DirectionalProbability(
        persona=persona, ticker="X",
        p_up=p_up, p_flat=p_flat, p_down=p_down,
        confidence=0.5, evidence_ids=[], cycle_id="c1",
    )
    return ArbitrationSignal(
        dp, historical_n=50, rolling_brier=0.20, weight_multiplier=weight_multiplier,
    )


class TestArbitrationWeightCap:
    def test_cap_shifts_result_toward_uncapped_signal(self):
        # Two mature signals agreeing on direction "up" but different magnitude, so
        # arbitrate proceeds to Brier-inverse weighting (no mature-disagreement abort).
        strong = _mature_signal(PersonaName.GROWTH_HUNTER, 0.60, 0.30, 0.10)
        weak = _mature_signal(PersonaName.MOAT_ANALYST, 0.40, 0.35, 0.25)

        uncapped = arbitrate([strong, weak], cycle_id="c1")
        # Equal Brier/maturity → equal weights → midpoint p_up = 0.50
        assert abs(uncapped.p_up - 0.50) < 0.02

        # Cap the weak-up signal heavily — the strong signal should dominate.
        strong2 = _mature_signal(PersonaName.GROWTH_HUNTER, 0.60, 0.30, 0.10)
        weak2 = _mature_signal(PersonaName.MOAT_ANALYST, 0.40, 0.35, 0.25, weight_multiplier=0.05)
        capped = arbitrate([strong2, weak2], cycle_id="c1")
        assert capped.p_up > uncapped.p_up + 0.05

    def test_default_multiplier_is_one(self):
        sig = _mature_signal(PersonaName.GROWTH_HUNTER, 0.5, 0.3, 0.2)
        assert sig.weight_multiplier == 1.0


# ── _rebuild_evidence_brief auditor-flag injection ────────────────────────

class TestRebuildEvidenceBrief:
    def _flag(self, target=PersonaName.GROWTH_HUNTER, severity=0.7):
        return AuditorFlag(
            flag_type="CONCLUSION_UNSUPPORTED",
            target_persona=target,
            severity=severity,
            description="growth_hunter conclusion not supported by cited evidence.",
            evidence_ids=["ev-1"],
            taxonomy_mapping="CONCLUSION_UNSUPPORTED",
        )

    def test_appends_auditor_flag_context(self):
        flags = [self._flag(), self._flag(PersonaName.MOAT_ANALYST, 0.4)]
        out = _rebuild_evidence_brief(
            ["pkt"], attacks=[{"type": "x"}],
            arbitrated=SimpleNamespace(decision="BUY", p_up=0.6, p_down=0.2),
            ticker="X", auditor_flags=flags,
        )
        # original + attack_summary + auditor_flag_context
        assert len(out) == 3
        afc = out[-1]
        assert afc["source"] == "auditor_flag_context"
        assert afc["flag_count"] == 2
        assert afc["flags"][0]["target_persona"] == "growth_hunter"
        assert afc["flags"][0]["taxonomy_mapping"] == "CONCLUSION_UNSUPPORTED"

    def test_no_auditor_context_when_flags_absent(self):
        out = _rebuild_evidence_brief(
            ["pkt"], attacks=[], arbitrated=SimpleNamespace(decision="BUY", p_up=0.6, p_down=0.2),
            ticker="X", auditor_flags=None,
        )
        assert len(out) == 2  # original + attack_summary only
        assert all(x.get("source") != "auditor_flag_context" for x in out
                   if isinstance(x, dict))


# ── _parse_auditor_flags ──────────────────────────────────────────────────

def _orchestrator():
    """Lightweight orchestrator bypassing __init__ for unit tests."""
    return CycleOrchestrator.__new__(CycleOrchestrator)


class TestParseAuditorFlags:
    def test_valid_output_yields_flags(self):
        valid = {
            "ticker": "X", "summary": "one flag",
            "flags": [{
                "flag_type": "CITATION_GAP",
                "target_persona": "growth_hunter",
                "severity": 0.6,
                "description": "conclusion not supported.",
                "evidence_ids": ["ev-1"],
                "taxonomy_mapping": "CITATION_GAP",
            }],
        }
        po = PersonaOutput(
            persona=PersonaName.CROSS_PERSONA_AUDITOR, ticker="X", cycle_id="c1",
            raw_output=__import__("json").dumps(valid),
            grammar_version="cross_persona_auditor", model_hash="h", temperature=0.2,
            retry_count=0,
        )
        flags = _orchestrator()._parse_auditor_flags(po, "c1", "X")
        assert len(flags) == 1
        assert flags[0].flag_type == "CITATION_GAP"

    def test_invalid_json_yields_empty(self):
        po = PersonaOutput(
            persona=PersonaName.CROSS_PERSONA_AUDITOR, ticker="X", cycle_id="c1",
            raw_output="not json",
            grammar_version="cross_persona_auditor", model_hash="h", temperature=0.2,
            retry_count=0,
        )
        assert _orchestrator()._parse_auditor_flags(po, "c1", "X") == []

    def test_empty_flags_valid(self):
        valid = {"ticker": "X", "summary": "clean", "flags": []}
        po = PersonaOutput(
            persona=PersonaName.CROSS_PERSONA_AUDITOR, ticker="X", cycle_id="c1",
            raw_output=__import__("json").dumps(valid),
            grammar_version="cross_persona_auditor", model_hash="h", temperature=0.2,
            retry_count=0,
        )
        assert _orchestrator()._parse_auditor_flags(po, "c1", "X") == []


# ── _compute_valuation ────────────────────────────────────────────────────

def _evidence_packet(ticker, ev_id, data):
    return SimpleNamespace(
        ticker=ticker,
        evidence=[SimpleNamespace(id=ev_id, source=DataSource.FUNDAMENTALS, data=data)],
    )


class TestComputeValuation:
    @pytest.fixture(autouse=True)
    def _stub_valuation_agent(self, monkeypatch):
        """Skip the post-arbitration ValuationAgent so these tests exercise the
        reverse-DCF path deterministically and fast. The forward-valuation flow
        (agent → engine → scenario_price source choice) is covered in
        tests/integration/test_forward_valuation_pipeline.py.
        """
        from pmacs.agents import valuation_agent as va_mod
        from pmacs.data import evidence_router as er_mod
        monkeypatch.setattr(
            va_mod.ValuationAgentRunner, "run",
            lambda self, evidence, episodic_context=None: None,
        )
        # The filter rebuilds real EvidencePackets from the lightweight
        # SimpleNamespace fixtures used here; short-circuit it since the runner
        # is stubbed and never inspects the filtered evidence.
        monkeypatch.setattr(er_mod, "filter_evidence_for_persona",
                            lambda evidence, persona_name: [])

    def test_fcf_negative_degrades_to_neutral(self):
        orch = _orchestrator()
        orch._current_price = 100.0
        packets = [
            _evidence_packet("X", f"fundamentals_X_profile", {"marketCapitalization": 2100.0}),
            _evidence_packet("X", f"fundamentals_X_metrics", {
                "annual_freeCashFlow": [{"period": "2025", "v": -50_000_000.0}],
                "revenueGrowthTTMYoy": 18.0,
            }),
        ]
        persona_results = {"growth_hunter": SimpleNamespace(
            raw_output='{"revenue_yoy_pct": 18.0}',
        )}
        arbitrated = SimpleNamespace(p_up=0.5, p_flat=0.3, p_down=0.2)
        rdcf, forward, scenario = orch._compute_valuation("X", "c1", packets, persona_results, arbitrated)
        assert forward is None  # agent stubbed → reverse-DCF fallback
        assert rdcf.valuation_lean == "NEUTRAL"
        assert not rdcf.is_available
        assert not scenario.is_available

    def test_positive_fcf_produces_lean_and_scenario(self):
        orch = _orchestrator()
        orch._current_price = 100.0
        packets = [
            _evidence_packet("X", f"fundamentals_X_profile", {"marketCapitalization": 2100.0}),
            _evidence_packet("X", f"fundamentals_X_metrics", {
                "annual_freeCashFlow": [{"period": "2025", "v": 100_000_000.0}],
                "revenueGrowthTTMYoy": 5.0,
            }),
        ]
        persona_results = {"growth_hunter": SimpleNamespace(
            raw_output='{"revenue_yoy_pct": 5.0}',
        )}
        arbitrated = SimpleNamespace(p_up=0.6, p_flat=0.3, p_down=0.1)
        rdcf, forward, scenario = orch._compute_valuation("X", "c1", packets, persona_results, arbitrated)
        assert forward is None  # agent stubbed → reverse-DCF grid feeds scenario_price
        assert rdcf.is_available
        assert rdcf.fair_value_usd is not None
        assert rdcf.valuation_lean in ("BULLISH", "BEARISH", "NEUTRAL")
        assert scenario.is_available
        assert scenario.expected_price_usd is not None

    def test_missing_market_cap_degrades(self):
        orch = _orchestrator()
        orch._current_price = 100.0
        packets = [_evidence_packet("X", f"fundamentals_X_metrics", {
            "annual_freeCashFlow": [{"period": "2025", "v": 100_000_000.0}],
        })]
        rdcf, forward, scenario = orch._compute_valuation("X", "c1", packets, {}, SimpleNamespace(p_up=0.5, p_flat=0.3, p_down=0.2))
        assert forward is None
        assert not rdcf.is_available
        assert "market cap" in rdcf.notes
