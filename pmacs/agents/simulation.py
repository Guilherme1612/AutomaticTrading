"""Deterministic simulation outputs for when llama-server is unavailable.

Produces conservative, low-conviction persona outputs that pass Pydantic
validation (Layer 2) and sanity checks (Layer 3). Every simulation output
is marked with a distinctive prefix so the audit trail can distinguish
simulated from real LLM outputs.

Architecture.md §4.1: LLMs never sign trades. Simulation mode does not
change this — outputs are still advisory only.
"""
from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel

from pmacs.schemas.data import EvidencePacket


def make_simulation_output(
    persona_name: str,
    model_cls: type[BaseModel],
    evidence: list[EvidencePacket],
    cycle_id: str = "",
) -> dict[str, Any] | None:
    """Generate a deterministic, conservative simulation output for a persona.

    Returns a dict that can be validated by model_cls.model_validate().
    Returns None if the persona is not supported for simulation.
    """
    ticker = evidence[0].ticker if evidence else "UNKNOWN"
    ev_ids = _make_evidence_ids(evidence)

    generators = {
        "macro_regime": _simulate_macro_regime,
        "catalyst_summarizer": _simulate_catalyst_summarizer,
        "moat_analyst": _simulate_moat_analyst,
        "growth_hunter": _simulate_growth_hunter,
        "insider_activity": _simulate_insider_activity,
        "short_interest": _simulate_short_interest,
        "forensics": _simulate_forensics,
        # Wave-2 debate + audit personas (Agents.md §11b-§11d)
        "bull_advocate": _simulate_bull_advocate,
        "bear_advocate": _simulate_bear_advocate,
        "cross_persona_auditor": _simulate_cross_persona_auditor,
        # Post-arbitration forward-valuation persona (Agents.md §13b)
        "valuation_agent": _simulate_valuation_agent,
    }

    gen = generators.get(persona_name)
    if gen is None:
        return None

    output = gen(ticker, ev_ids)

    # Verify the output passes Pydantic validation before returning
    try:
        model_cls.model_validate(output)
    except Exception:
        return None

    return output


def _make_evidence_ids(evidence: list[EvidencePacket]) -> list[str]:
    """Extract evidence IDs from packets, ensuring at least one."""
    ids: list[str] = []
    for packet in evidence:
        for ev in packet.evidence:
            if ev.id:
                ids.append(ev.id)
    if not ids:
        ids = ["sim-evidence-001"]
    return ids


def _simulate_macro_regime(ticker: str, ev_ids: list[str]) -> dict:
    return {
        "regime": "UNCERTAIN",
        "regime_confidence": 0.3,
        "regime_reasoning": "SIMULATION — llama-server unavailable, assuming uncertain regime",
        "yield_curve_signal": "NORMAL",
        "vix_regime": "LOW",
        "sector_rotation_summary": "SIMULATION — no sector rotation data available",
        "p_up": 0.3,
        "p_flat": 0.4,
        "p_down": 0.3,
        "evidence_ids": ev_ids[:1],
        "catalyst_type": "EARNINGS",
        "description": "SIMULATION — placeholder catalyst",
        "expected_date": None,
        "status": "PENDING",
        "thesis_impact": "NEUTRAL",
    }


def _simulate_catalyst_summarizer(ticker: str, ev_ids: list[str]) -> dict:
    return {
        "ticker": ticker,
        "catalysts": [],
        "net_catalyst_outlook": "SIMULATION — no catalyst data, assuming neutral outlook",
        "p_up": 0.3,
        "p_flat": 0.4,
        "p_down": 0.3,
        "evidence_ids": ev_ids[:1],
        "moat_type": "INTANGIBLE_ASSETS",
        "strength": 0.3,
        "trajectory": "STABLE",
        "reasoning": "SIMULATION — no moat data available",
    }


def _simulate_moat_analyst(ticker: str, ev_ids: list[str]) -> dict:
    return {
        "ticker": ticker,
        "moat_components": [
            {
                "moat_type": "INTANGIBLE_ASSETS",
                "strength": 0.3,
                "trajectory": "STABLE",
                "reasoning": "SIMULATION — no moat data available",
                "evidence_ids": ev_ids[:1],
            },
        ],
        "moat_strength": 0.3,
        "competitive_entry_risk": "MODERATE",
        "competitive_entry_reasoning": "SIMULATION — default moderate risk",
        "p_up": 0.3,
        "p_flat": 0.4,
        "p_down": 0.3,
        "evidence_ids": ev_ids[:1],
    }


def _simulate_growth_hunter(ticker: str, ev_ids: list[str]) -> dict:
    return {
        "ticker": ticker,
        "revenue_yoy_pct": None,
        "revenue_acceleration": "UNKNOWN",
        "gross_margin_pct": None,
        "gross_margin_trend": "UNKNOWN",
        "tam_penetration_pct": None,
        "growth_durability": "UNKNOWN",
        "growth_durability_reasoning": "SIMULATION — no growth data available",
        "key_risk_to_growth": "SIMULATION — data unavailable",
        "p_up": 0.3,
        "p_flat": 0.4,
        "p_down": 0.3,
        "evidence_ids": ev_ids[:1],
        "insider_name": "SIMULATION",
        "insider_role": "N/A",
        "transaction_type": "BUY",
        "amount_usd": 0.0,
        "shares": 0,
        "date": "2026-01-01",
        "evidence_id": ev_ids[0] if ev_ids else "sim-001",
    }


def _simulate_insider_activity(ticker: str, ev_ids: list[str]) -> dict:
    return {
        "ticker": ticker,
        "transactions": [],
        "signal": "NO_SIGNAL",
        "signal_reasoning": "SIMULATION — no insider transaction data",
        "p_up": 0.3,
        "p_flat": 0.4,
        "p_down": 0.3,
        "evidence_ids": ev_ids[:1],
    }


def _simulate_short_interest(ticker: str, ev_ids: list[str]) -> dict:
    return {
        "ticker": ticker,
        "short_pct_float": None,
        "days_to_cover": None,
        "short_change_pct": None,
        "anomaly": "INSUFFICIENT_DATA",
        "anomaly_reasoning": "SIMULATION — no short interest data",
        "p_up": 0.3,
        "p_flat": 0.4,
        "p_down": 0.3,
        "evidence_ids": ev_ids[:1],
        "category": "VALUATION",
        "severity": 0.1,
        "description": "SIMULATION — placeholder forensics flag",
    }


def _simulate_forensics(ticker: str, ev_ids: list[str]) -> dict:
    return {
        "ticker": ticker,
        "red_flags": [],
        "red_flag_count": 0,
        "overall_accounting_quality": "INSUFFICIENT_DATA",
        "p_up": 0.3,
        "p_flat": 0.4,
        "p_down": 0.3,
        "evidence_ids": ev_ids[:1],
    }


def _simulate_bull_advocate(ticker: str, ev_ids: list[str]) -> dict:
    """Conservative simulation: near-uniform, no fabricated bull conviction.

    Targets growth_hunter by default (a wave-1 persona always exists). Advocacy
    is not fabrication — simulation emits a near-flat distribution and concedes
    the bear case so sanity accepts the degenerate-leaning distribution.
    """
    return {
        "ticker": ticker,
        "target_persona": "growth_hunter",
        "p_up": 0.34,
        "p_flat": 0.36,
        "p_down": 0.30,
        "reasoning": (
            "SIMULATION — llama-server unavailable; no bull conviction can be "
            "supported without the wave-1 reads. Conceding the bear case is "
            "not warranted either; emitting a near-uniform distribution against "
            "growth_hunter."
        ),
        "strongest_bear_counterpoint": "SIMULATION — no evidence available to evaluate",
        "evidence_ids": ev_ids[:1],
    }


def _simulate_bear_advocate(ticker: str, ev_ids: list[str]) -> dict:
    return {
        "ticker": ticker,
        "target_persona": "growth_hunter",
        "p_up": 0.30,
        "p_flat": 0.36,
        "p_down": 0.34,
        "reasoning": (
            "SIMULATION — llama-server unavailable; no bear conviction can be "
            "supported without the wave-1 reads. Emitting a near-uniform, "
            "slightly bear-leaning distribution against growth_hunter."
        ),
        "strongest_bull_counterpoint": "SIMULATION — no evidence available to evaluate",
        "evidence_ids": ev_ids[:1],
    }


def _simulate_cross_persona_auditor(ticker: str, ev_ids: list[str]) -> dict:
    """Conservative simulation: empty flags (clean output).

    An auditor that fabricates flags would cap real personas' arbitration
    weights on invented flaws — a math-leak. Simulation returns an empty flag
    list so it contributes no weight caps and no FDE writes.
    """
    return {
        "ticker": ticker,
        "flags": [],
        "summary": "SIMULATION — llama-server unavailable; no audit performed (clean by default)",
    }


def _simulate_valuation_agent(ticker: str, ev_ids: list[str]) -> dict:
    """Conservative simulation: near-uniform bull/base/bear, no fabrication.

    The LLM never emits a price (§1.6) — only assumptions. Simulation emits a
    near-uniform scenario distribution with no acquisition impact and explicit
    data_gaps, so the ForwardValuationEngine can still compute a price from
    plausible base-case assumptions when llama-server is down. Growth path is
    ordered bull > base > bear; margins are STABLE with ~0 delta; exit multiple
    is a conservative 15x. Every rationale cites an evidence_id and carries a
    self-critique note so the sanity validator accepts it.
    """
    eid = ev_ids[:1] or ["sim-evidence-001"]
    return {
        "ticker": ticker,
        "horizon_months": 12,
        "bull": {
            "revenue_growth_path_pct": 0.20,
            "margin_trajectory": "STABLE",
            "margin_delta_pct": 0.01,
            "ebitda_margin_at_horizon_pct": 0.25,
            "acquisition_revenue_contribution_pct": 0.0,
            "acquisition_confidence": "NONE",
            "exit_multiple": 18.0,
            "rationale": (
                "SIMULATION — llama-server unavailable; bull assumes high-end "
                f"consensus growth and a stable margin. Self-critique: growth 0.20 "
                f"> base > bear; e1 cited. evidence={eid[0]}"
            ),
            "probability_of_occurrence": 0.30,
            "evidence_ids": eid,
        },
        "base": {
            "revenue_growth_path_pct": 0.12,
            "margin_trajectory": "STABLE",
            "margin_delta_pct": 0.0,
            "ebitda_margin_at_horizon_pct": 0.22,
            "acquisition_revenue_contribution_pct": 0.0,
            "acquisition_confidence": "NONE",
            "exit_multiple": 15.0,
            "rationale": (
                "SIMULATION — base case at consensus growth, stable margin, peer-"
                f"median exit multiple. Self-critique: highest probability. evidence={eid[0]}"
            ),
            "probability_of_occurrence": 0.40,
            "evidence_ids": eid,
        },
        "bear": {
            "revenue_growth_path_pct": 0.04,
            "margin_trajectory": "COMPRESSING",
            "margin_delta_pct": -0.02,
            "ebitda_margin_at_horizon_pct": 0.18,
            "acquisition_revenue_contribution_pct": 0.0,
            "acquisition_confidence": "NONE",
            "exit_multiple": 10.0,
            "rationale": (
                "SIMULATION — bear assumes low-end growth and margin compression "
                f"with a below-median exit multiple. Self-critique: ordered lowest. evidence={eid[0]}"
            ),
            "probability_of_occurrence": 0.30,
            "evidence_ids": eid,
        },
        "data_gaps": [
            "SIMULATION — llama-server unavailable; assumptions are conservative defaults",
            "management guidance: N/A, using analyst consensus proxy",
            "acquisitions: N/A, not inferred this cycle",
        ],
        "evidence_ids": eid,
    }
