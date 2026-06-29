"""Integration tests for the wave-2 debate pipeline (Agents.md §11b-§11d, §16.9).

Covers the two operator-visible guarantees added in Phase 7b Task 7:

1. The orchestrator now PERSISTS its MemoWriter output to the ``memos`` table
   (the live /memo/{ticker} page reads ``memos.memo_json``), with the
   deterministic reverse-DCF + scenario-price results injected so the template's
   valuation cards render. Previously the orchestrator computed the memo and
   discarded it (see memory: dual_memo_paths_gap).

2. Determinism / no-math-leak: advocates enter arbitration as IMMATURE voters
   (historical_n=0). When mature sources exist, ``arbitrate()`` weights only
   mature sources — so adding immature advocates cannot shift the arbitrated
   probabilities. This proves the wave-2 advocates don't leak into conviction
   (Five Non-Negotiable #2/#3). The auditor enters only as weight_multiplier
   caps, never as a probability emitter.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pmacs.engines.arbitration import ArbitrationSignal, arbitrate
from pmacs.nervous.orchestrator import CycleOrchestrator
from pmacs.schemas.agents import DirectionalProbability, PersonaName
from pmacs.schemas.reverse_dcf import ReverseDcfResult
from pmacs.schemas.scenario_price import ScenarioPriceResult
from pmacs.storage.sqlite import connect as sql_connect, init_db


# ---------------------------------------------------------------------------
# Test 1 — orchestrator persists a wave-2 memo to the memos table
# ---------------------------------------------------------------------------

def _mature_dp(persona, p_up, p_flat, p_down):
    return DirectionalProbability(
        persona=persona, ticker="X",
        p_up=p_up, p_flat=p_flat, p_down=p_down,
        confidence=0.5, evidence_ids=[], cycle_id="c1",
    )


def test_orchestrator_persists_memo_with_valuation_and_debate(tmp_path: Path) -> None:
    """_step_13mn_post_decision writes memos.memo_json with wave-2 fields injected."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    conn = sql_connect(db_path)
    conn.execute(
        "INSERT INTO cycles (cycle_id, opened_at, closed_at, state, trigger, mode) "
        "VALUES ('c1', '2026-06-19T00:00:00Z', NULL, 'OPEN', 'TEST', 'PAPER')"
    )
    conn.commit()
    conn.close()

    # Bypass __init__ — set only the attributes _step_13mn_post_decision touches.
    orch = CycleOrchestrator.__new__(CycleOrchestrator)
    orch._db_path = db_path
    orch._audit_path = None
    orch._current_price = 42.50
    orch._last_crucible_attacks = []
    orch._last_persona_results = {}
    orch._last_advocate_outputs = {}
    orch._last_auditor_flags = []
    orch._last_reverse_dcf = ReverseDcfResult(
        ticker="X", cycle_id="c1",
        implied_growth_pct=0.082, assumed_growth_pct=0.180,
        growth_gap_pct=0.098, fair_value_usd=55.0, current_price_usd=42.5,
        valuation_lean="BULLISH", sensitivity={"base": 55.0}, notes="",
    )
    orch._last_scenario_price = ScenarioPriceResult(
        ticker="X", cycle_id="c1",
        bull_price=70.0, base_price=55.0, bear_price=35.0,
        expected_price_usd=54.5, p_up=0.5, p_flat=0.3, p_down=0.2,
        current_price_usd=42.5, expected_return_pct=28.2, notes="",
    )
    # Stub the lazy storage adapters so the post-memo telemetry no-ops.
    orch._kuzu_adapter = None
    orch._qdrant_adapter = None
    orch._duckdb_adapter = None
    orch._get_qdrant_adapter = lambda: None
    orch._get_duckdb_adapter = lambda: None

    # Force MemoWriterRunner.run to return a PersonaOutput carrying an LLM-style
    # memo JSON with the wave-2 debate + falsification sections populated.
    memo_dict = {
        "verdict_line": "BUY — growth underpriced vs market-implied rate.",
        "thesis": "X trades at a market-implied 8.2% growth while estimating 18.0% — a 9.8pp gap.",
        "key_evidence": ["FCF yield 4.2%", "Revenue +18% YoY", "NRR 128%"],
        "key_risks": ["Deceleration below 20% for two quarters", "Gross margin compression"],
        "bull_bear_debate": {
            "bull_case": "Market is pricing 8% growth; company is growing 18%.",
            "bear_case": "Growth deceleration could compress the multiple rapidly.",
            "advocate_lean": "BULL",
            "reverse_dcf_gap": "market implies 8.2% vs estimated 18.0% (+9.8pp, BULLISH)",
        },
        "what_would_change_my_mind": [
            "Q3 revenue growth decelerates below 20% YoY for two consecutive quarters",
            "NRR drops below 110%",
        ],
        "conviction": 0.0,  # will be overwritten by the authoritative value
        "p_up": 0.0, "p_flat": 0.0, "p_down": 0.0,
    }
    fake_po = SimpleNamespace(raw_output=json.dumps(memo_dict))

    import pmacs.agents.memo_writer as mw_mod
    original_run = mw_mod.MemoWriterRunner.run
    mw_mod.MemoWriterRunner.run = lambda self, evidence, episodic_context=None: fake_po
    try:
        holding = SimpleNamespace(id="h1", thesis_summary="")
        arbitrated = SimpleNamespace(
            p_up=0.55, p_flat=0.30, p_down=0.15, persona_outputs=[],
        )
        orch._step_13mn_post_decision(
            holding=holding, ticker="X", cycle_id="c1", op=0,
            evidence_packets=[], brief="",
            verdict=SimpleNamespace(value="BUY"),
            conviction_score=0.62, arbitrated=arbitrated, crucible_severity=0.3,
        )
    finally:
        mw_mod.MemoWriterRunner.run = original_run

    # Read back the persisted memo.
    conn = sql_connect(db_path)
    try:
        row = conn.execute(
            "SELECT memo_json, verdict, conviction_score FROM memos "
            "WHERE ticker='X' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "orchestrator did not persist a memo row"
    persisted = json.loads(row[0])
    assert row[1] == "BUY"
    assert abs(row[2] - 0.62) < 1e-9

    # Authoritative arbitration numbers override the LLM-stated zeros.
    assert abs(persisted["conviction"] - 0.62) < 1e-9
    assert abs(persisted["p_up"] - 0.55) < 1e-9
    assert abs(persisted["p_flat"] - 0.30) < 1e-9
    assert abs(persisted["p_down"] - 0.15) < 1e-9

    # LLM-produced wave-2 sections are preserved.
    assert persisted["bull_bear_debate"]["advocate_lean"] == "BULL"
    assert persisted["what_would_change_my_mind"][0].startswith("Q3 revenue")

    # Deterministic valuation results are injected so the template can render them.
    assert persisted["reverse_dcf"]["valuation_lean"] == "BULLISH"
    assert abs(persisted["reverse_dcf"]["growth_gap_pct"] - 0.098) < 1e-9
    assert abs(persisted["scenario_price"]["expected_price_usd"] - 54.5) < 1e-9
    assert abs(persisted["scenario_price"]["expected_return_pct"] - 28.2) < 1e-9

    # Legacy mirror: decisions.thesis_summary carries the same JSON.
    conn = sql_connect(db_path)
    try:
        drow = conn.execute(
            "SELECT thesis_summary FROM decisions WHERE ticker='X' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert drow is not None
    assert json.loads(drow[0])["bull_bear_debate"]["advocate_lean"] == "BULL"


def test_orchestrator_persists_fallback_when_llm_returns_none(tmp_path: Path) -> None:
    """When the memo LLM returns None, a fallback memo is still persisted."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    conn = sql_connect(db_path)
    conn.execute(
        "INSERT INTO cycles (cycle_id, opened_at, closed_at, state, trigger, mode) "
        "VALUES ('c2', '2026-06-19T00:00:00Z', NULL, 'OPEN', 'TEST', 'PAPER')"
    )
    conn.commit()
    conn.close()

    orch = CycleOrchestrator.__new__(CycleOrchestrator)
    orch._db_path = db_path
    orch._audit_path = None
    orch._current_price = 0.0
    orch._last_crucible_attacks = []
    orch._last_persona_results = {}
    orch._last_advocate_outputs = {}
    orch._last_auditor_flags = []
    orch._last_reverse_dcf = None
    orch._last_scenario_price = None
    orch._kuzu_adapter = None
    orch._qdrant_adapter = None
    orch._duckdb_adapter = None
    orch._get_qdrant_adapter = lambda: None
    orch._get_duckdb_adapter = lambda: None

    import pmacs.agents.memo_writer as mw_mod
    original_run = mw_mod.MemoWriterRunner.run
    mw_mod.MemoWriterRunner.run = lambda self, evidence, episodic_context=None: None
    try:
        orch._step_13mn_post_decision(
            holding=SimpleNamespace(id="h2", thesis_summary=""),
            ticker="Y", cycle_id="c2", op=0,
            evidence_packets=[], brief="",
            verdict=SimpleNamespace(value="HOLD"),
            conviction_score=0.41,
            arbitrated=SimpleNamespace(p_up=0.4, p_flat=0.4, p_down=0.2, persona_outputs=[]),
            crucible_severity=0.2,
        )
    finally:
        mw_mod.MemoWriterRunner.run = original_run

    conn = sql_connect(db_path)
    try:
        row = conn.execute(
            "SELECT memo_json, verdict FROM memos WHERE ticker='Y'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    persisted = json.loads(row[0])
    assert persisted["thesis"]  # fallback thesis present
    assert persisted["verdict_line"].startswith("HOLD")
    assert "reverse_dcf" not in persisted  # none available → not injected
    assert "scenario_price" not in persisted


# ---------------------------------------------------------------------------
# Test 2 — determinism: advocates cannot leak into mature conviction
# ---------------------------------------------------------------------------

def _mature_signal(persona, p_up, p_flat, p_down):
    return ArbitrationSignal(
        _mature_dp(persona, p_up, p_flat, p_down),
        historical_n=50, rolling_brier=0.20,
    )


def _immature_signal(persona, p_up, p_flat, p_down):
    return ArbitrationSignal(
        _mature_dp(persona, p_up, p_flat, p_down),
        historical_n=0, rolling_brier=0.667,  # immature — advocates start here
    )


class TestDeterminismNoMathLeak:
    def test_advocates_do_not_shift_mature_arbitration(self):
        """Adding immature bull/bear advocates must not change mature-arbitration probs.

        Advocates enter with historical_n=0 (immature). arbitrate() weights only
        mature sources, so the advocates are spectators — conviction is unchanged.
        This is the no-math-leak guarantee (Five Non-Negotiable #2/#3).
        """
        mature = [
            _mature_signal(PersonaName.GROWTH_HUNTER, 0.55, 0.30, 0.15),
            _mature_signal(PersonaName.MOAT_ANALYST, 0.50, 0.32, 0.18),
            _mature_signal(PersonaName.FORENSICS, 0.48, 0.34, 0.18),
        ]
        baseline = arbitrate(mature, cycle_id="c1")

        # Same mature signals + two immature advocates pulling in opposite directions.
        with_advocates = arbitrate(
            mature + [
                _immature_signal(PersonaName.BULL_ADVOCATE, 0.60, 0.30, 0.10),
                _immature_signal(PersonaName.BEAR_ADVOCATE, 0.10, 0.30, 0.60),
            ],
            cycle_id="c1",
        )

        assert abs(baseline.p_up - with_advocates.p_up) < 1e-9
        assert abs(baseline.p_flat - with_advocates.p_flat) < 1e-9
        assert abs(baseline.p_down - with_advocates.p_down) < 1e-9
        assert baseline.decision == with_advocates.decision == "PROCEED"

    def test_auditor_simulator_emits_no_flags(self):
        """The auditor simulation must return empty flags — fabricating flags would
        cap real personas' weights on invented flaws (a math-leak)."""
        from pmacs.agents.simulation import make_simulation_output
        from pmacs.schemas.personas import AuditorOutput
        out = make_simulation_output(
            "cross_persona_auditor", AuditorOutput,
            [SimpleNamespace(ticker="X", evidence=[SimpleNamespace(id="ev-1", data={})])],
            cycle_id="c1",
        )
        assert out is not None
        assert out["flags"] == []
