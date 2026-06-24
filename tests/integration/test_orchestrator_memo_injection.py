"""Task #8 Part C — orchestrator memo is self-sufficient (no live demo globals).

The orchestrator deterministically injects ``agent_signals`` + the four
``crucible_*`` narrative fields into ``memos.memo_json`` so the dashboard's
memo/agents pages render from the persisted JSON alone — without the demo
path's live globals (``_last_cycle_agent_results`` etc.), which Part E deletes.

This drives one ticker through ``run_cycle`` with mocked LLM runners (no real
inference) and asserts the persisted ``memo_json`` carries the injected keys.

spec_ref: Architecture.md §16.9 (memo persistence), Source.md §16 (memo page);
the injection lives in ``_step_13mn_post_decision`` (orchestrator.py ~2866).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pmacs.agents.gatekeeper import GatekeeperResult
from pmacs.data.universe import UniverseEntry, add_ticker, init_universe_table
from pmacs.nervous.orchestrator import CycleOrchestrator
from pmacs.nervous.sse_publisher import SSEPublisher
from pmacs.schemas.agents import PersonaName, PersonaOutput
from pmacs.schemas.conviction import VerdictTier
from pmacs.sim.ledger import PaperLedger
from pmacs.storage.sqlite import init_db

_ALL_PERSONAS = [
    "macro_regime", "catalyst_summarizer",
    "moat_analyst", "growth_hunter",
    "insider_activity", "short_interest", "forensics",
]


def _make_fx_rate():
    from pmacs.schemas.currency import FxRate
    return FxRate(
        usd_per_eur=1.085,
        business_date=date(2026, 5, 12),
        fetched_at=datetime.now(timezone.utc),
    )


def _make_persona_output(persona_name: str, ticker: str) -> PersonaOutput:
    return PersonaOutput(
        persona=PersonaName(persona_name),
        ticker=ticker,
        cycle_id="test",
        raw_output=json.dumps({
            "p_up": 0.65, "p_flat": 0.20, "p_down": 0.15, "ticker": ticker,
        }),
        grammar_version="v1",
        model_hash="abc123",
        temperature=0.2,
        retry_count=0,
    )


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        init_universe_table(conn)
        add_ticker(conn, UniverseEntry(ticker="AAPL", sector="Tech"))
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture
def orchestrator(tmp_db: Path, tmp_path: Path) -> CycleOrchestrator:
    orch = CycleOrchestrator(
        db_path=tmp_db,
        audit_path=tmp_path / "audit.log",
        sse_publisher=SSEPublisher(),
        config={"lock_path": str(tmp_path / "cycle.lock")},
    )
    orch._ledger = PaperLedger()
    return orch


def _latest_memo(db_path: Path, ticker: str) -> dict:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT memo_json FROM memos WHERE ticker = ? "
            "ORDER BY decided_at DESC LIMIT 1",
            (ticker,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"no memos row persisted for {ticker}"
    return json.loads(row[0])


class TestMemoInjection:
    def test_persisted_memo_carries_agent_signals_and_crucible_fields(
        self, orchestrator: CycleOrchestrator, tmp_db: Path,
    ) -> None:
        """A mocked-LLM orchestrator cycle must persist a memo_json containing
        the deterministic agent_signals list + the four crucible_* fields, so
        the dashboard renders from persisted JSON without live demo globals."""
        gate_results = {"AAPL": GatekeeperResult(ticker="AAPL", admitted=True)}
        aapl_outputs = {name: _make_persona_output(name, "AAPL") for name in _ALL_PERSONAS}

        with patch("pmacs.data.fx.fetch_ecb_rate", return_value=_make_fx_rate()), \
             patch("pmacs.agents.macro_regime.MacroRegimeRunner"), \
             patch(
                 "pmacs.agents.gatekeeper.gate",
                 side_effect=lambda ticker, cycle_id, **kw: gate_results.get(
                     ticker, GatekeeperResult(ticker=ticker, admitted=False)),
             ), \
             patch.object(orchestrator, "_dispatch_personas", return_value=aapl_outputs), \
             patch("pmacs.agents.crucible.CrucibleRunner") as mock_crucible_cls, \
             patch("pmacs.agents.memo_writer.MemoWriterRunner") as mock_memo_cls, \
             patch("pmacs.engines.conviction.compute_conviction", return_value=0.65), \
             patch(
                 "pmacs.engines.conviction.verdict_tier",
                 return_value=VerdictTier.STRONG_BUY,
             ):
            mock_crucible_inst = MagicMock()
            mock_crucible_inst.run.return_value = MagicMock(
                raw_output=json.dumps({"severity_score": 0.1})
            )
            mock_crucible_cls.return_value = mock_crucible_inst

            mock_memo_inst = MagicMock()
            # Non-JSON raw_output → orchestrator falls back to a minimal dict,
            # so the deterministic injection (Part C) is the sole source of the
            # agent_signals / crucible_* keys — a direct test of that block.
            mock_memo_inst.run.return_value = MagicMock(raw_output="memo")
            mock_memo_cls.return_value = mock_memo_inst

            cycle_id = orchestrator.run_cycle("TIMER")

        assert cycle_id

        memo = _latest_memo(tmp_db, "AAPL")

        # Authoritative arbitration numbers (existing injection).
        for k in ("p_up", "p_flat", "p_down", "conviction"):
            assert k in memo, f"missing deterministic {k}"

        # Part C: agent_signals built from arbitrated.persona_outputs. The 7
        # wave-1 personas are present, PLUS the wave-2 bull/bear debate
        # advocates (which also land in arbitrated.persona_outputs) — so the
        # persisted memo carries every signal the dashboard's Agent Signals
        # card needs, without the demo path's live globals.
        assert "agent_signals" in memo, "agent_signals not injected into memo_json"
        sigs = memo["agent_signals"]
        assert isinstance(sigs, list) and len(sigs) >= len(_ALL_PERSONAS), (
            f"expected >= {len(_ALL_PERSONAS)} agent signals, got {len(sigs)}"
        )
        sig_personas = {s["persona"] for s in sigs}
        for name in _ALL_PERSONAS:
            assert name in sig_personas, f"wave-1 persona {name} missing from agent_signals"
        # Wave-2 debate advocates are appended by _step_13d5_debate.
        assert "bull_advocate" in sig_personas and "bear_advocate" in sig_personas, (
            f"wave-2 advocates missing; got {sorted(sig_personas)}"
        )
        first = sigs[0]
        for k in ("persona", "signal", "direction", "p_up", "p_flat", "p_down",
                  "confidence", "analysis", "evidence_cited"):
            assert k in first, f"agent_signal missing field {k}: {first}"

        # Part C: crucible narrative fields.
        assert "crucible_severity" in memo and isinstance(
            memo["crucible_severity"], (int, float)
        )
        assert "crucible_attacks" in memo and isinstance(
            memo["crucible_attacks"], list
        )
        assert "crucible_summary" in memo and isinstance(
            memo["crucible_summary"], str
        )
        assert "crucible_thesis_survives" in memo and isinstance(
            memo["crucible_thesis_survives"], bool
        )
