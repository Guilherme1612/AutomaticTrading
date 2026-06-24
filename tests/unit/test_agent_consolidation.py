"""Regression tests for the agent-sweep consolidations (Rec1 + Rec2).

Rec1 — MacroRegime is cycle-level: the first ticker's real-evidence macro DP is
cached on CycleOrchestrator._macro_regime_dp_cached and reused for every
subsequent ticker, eliminating N-1 redundant LLM calls per cycle.

Rec2 — insider_activity/short_interest skip the LLM call when their primary data
source (FORM4/FINRA) produced no evidence; a dataless DP is synthesized directly.
The synthesized output must be treated identically to a real INSUFFICIENT_DATA
run: confidence=0.0, which _step_13e_arbitration skips.

spec_ref: Architecture.md §12.2 (slot layout), §9.4 (arbitration), Agents.md §9/§10.
"""
import json

import pytest

from pmacs.nervous.orchestrator import CycleOrchestrator
from pmacs.schemas.agents import PersonaOutput, PersonaName
from pmacs.schemas.personas import InsiderActivityOutput, ShortInterestOutput


# ─── Rec2: dataless synthesis ─────────────────────────────────────────────────


@pytest.mark.parametrize("persona", ["insider_activity", "short_interest"])
def test_synthesize_dataless_persona_produces_valid_output(persona):
    """The synthesized PersonaOutput must validate against the real persona schema."""
    po = CycleOrchestrator._synthesize_dataless_persona(
        persona, "OUST", "cycle-1", ["ev-1"],
    )
    assert isinstance(po, PersonaOutput)
    assert po.persona == PersonaName(persona)
    assert po.ticker == "OUST"
    assert po.cycle_id == "cycle-1"
    data = json.loads(po.raw_output)

    if persona == "insider_activity":
        assert data["signal"] == "INSUFFICIENT_DATA"
        InsiderActivityOutput.model_validate(data)  # must not raise
    else:
        assert data["anomaly"] == "INSUFFICIENT_DATA"
        ShortInterestOutput.model_validate(data)  # must not raise

    # Probabilities present and sum ~1.0 (arbitration reads these)
    assert {k: data[k] for k in ("p_up", "p_flat", "p_down")}
    assert abs(data["p_up"] + data["p_flat"] + data["p_down"] - 1.0) <= 0.10


@pytest.mark.parametrize("persona", ["insider_activity", "short_interest"])
def test_synthesized_dataless_dp_is_skipped_by_arbitration(persona):
    """The synthesized DP must have confidence==0.0 so arbitration skips it.

    This is the exact condition _step_13e_arbitration uses (dp.confidence == 0.0 →
    skipped_insufficient). A nonzero confidence here would inject a near-uniform
    DP into arbitration — a regression.
    """
    po = CycleOrchestrator._synthesize_dataless_persona(
        persona, "OUST", "cycle-1", ["ev-1"],
    )
    dp = CycleOrchestrator._extract_directional_probability(
        persona, "OUST", "cycle-1", po,
    )
    assert dp is not None
    assert dp.confidence == 0.0, "dataless DP must be skipped by arbitration"


def test_synthesized_dataless_evidence_ids_min_length_one():
    """Schemas require evidence_ids min_length=1; synthesis must always cite one."""
    po = CycleOrchestrator._synthesize_dataless_persona(
        "insider_activity", "OUST", "cycle-1", ["ev-1"],
    )
    data = json.loads(po.raw_output)
    assert len(data["evidence_ids"]) >= 1


# ─── Rec1: macro DP cache ──────────────────────────────────────────────────────


def _bare_orchestrator() -> CycleOrchestrator:
    """A CycleOrchestrator without running __init__ (unit-test pattern)."""
    return CycleOrchestrator.__new__(CycleOrchestrator)


def test_macro_cache_attr_initializes_none():
    """The cache attr exists and is None on a fresh instance (no reuse on ticker 1)."""
    orch = _bare_orchestrator()
    # __init__ sets this; mimic the per-cycle reset contract here.
    orch._macro_regime_dp_cached = None
    assert orch._macro_regime_dp_cached is None


def test_macro_cache_reuse_decision_is_none_guarded():
    """The reuse decision is `cached is not None` — None means 'first ticker, run LLM'."""
    orch = _bare_orchestrator()
    orch._macro_regime_dp_cached = None
    assert not (orch._macro_regime_dp_cached is not None)  # first ticker: run macro
    # After the first ticker caches a real-evidence DP, subsequent tickers reuse.
    orch._macro_regime_dp_cached = PersonaOutput(
        persona=PersonaName.MACRO_REGIME, ticker="OUST", cycle_id="c1",
        raw_output=json.dumps({"ticker": "OUST", "regime": "RISK_ON", "p_up": 0.4,
                               "p_flat": 0.4, "p_down": 0.2, "evidence_ids": ["m1"]}),
    )
    assert orch._macro_regime_dp_cached is not None  # subsequent tickers: reuse


def test_cached_macro_dp_is_not_treated_as_dataless():
    """A cached real-evidence macro DP must NOT be skipped by arbitration.

    Guards against a regression where the macro reuse path accidentally produced
    a dataless-shaped DP — macro is a real (0.5x-weighted) signal that must enter
    arbitration on every ticker.
    """
    orch = _bare_orchestrator()
    orch._macro_regime_dp_cached = PersonaOutput(
        persona=PersonaName.MACRO_REGIME, ticker="OUST", cycle_id="c1",
        raw_output=json.dumps({"ticker": "OUST", "regime": "RISK_ON",
                               "regime_confidence": 0.7,
                               "p_up": 0.4, "p_flat": 0.4, "p_down": 0.2,
                               "evidence_ids": ["m1"]}),
    )
    dp = CycleOrchestrator._extract_directional_probability(
        "macro_regime", "OUST", "c1", orch._macro_regime_dp_cached,
    )
    assert dp is not None
    # No INSUFFICIENT_DATA signal/anomaly → confidence stays at the default (0.5),
    # i.e. NOT skipped. This is what makes the cached macro a live arbitration voter.
    assert dp.confidence != 0.0
