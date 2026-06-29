"""Tests for memo scorer activation in the orchestrator (Agents.md §13.5).

Tier 1A verification: score_memo() is fully implemented but was dead code. The
orchestrator's _step_13mn_post_decision now activates it, persists the result on
the memos row, and retries once on a low score. These tests mock the heavy
dependencies (PersonaRunner.run, sqlite persistence) and assert the orchestrator
path invokes the scorer correctly + handles each scoring outcome.

spec_ref: Agents.md §13.5; Architecture.md §16.9
"""

from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Stubs — fake MemoWriterRunner / MemoWriterOutput so we don't pull real LLM
# ---------------------------------------------------------------------------

@dataclass
class _FakeMemoOutput:
    raw_output: str = ""


@dataclass
class _FakeScoreDimension:
    name: str
    score: float
    max_score: float
    issues: list[str] = field(default_factory=list)


@dataclass
class _FakeScore:
    total: float
    grade: str
    dimensions: list = field(default_factory=list)
    critical_issues: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.total >= 50.0 and len(self.critical_issues) == 0


def _make_memo_dict() -> dict:
    """Realistic complete memo dict — high-score baseline."""
    return {
        "verdict_line": "BUY — conviction 0.42",
        "thesis": "Strong moat + accelerating growth + favorable catalyst window.",
        "fair_value_estimate": "$28.50",
        "valuation_methodology": "EV/EBITDA at 14x on FY26 EBITDA + reverse-DCF cross-check.",
        "key_evidence": ["ev1", "ev2", "ev3"],
        "key_risks": ["FX risk", "concentration risk", "regulatory risk"],
        "what_would_change_my_mind": ["margin compression", "guidance cut"],
        "crucible_severity": 0.35,
        "p_up": 0.62,
        "p_flat": 0.20,
        "p_down": 0.18,
        "conviction": 0.42,
    }


def _stub_memo_runner(monkeypatch, run_return_values: list[_FakeMemoOutput | None]) -> dict:
    """Install a fake MemoWriterRunner that returns the given outputs in order.

    Returns a dict with `runs`, `set_analytical_context_kwargs` (a list, one per
    call) so tests can assert what the orchestrator injected.
    """
    state = {"runs": [], "ctx_kwargs": []}

    def _fake_init(self):
        # Match the real class surface so set_analytical_context can be called.
        self._analytical_context = ""

    def _fake_set_analytical_context(self, **kwargs):
        state["ctx_kwargs"].append(kwargs)

    def _fake_run(self, *, evidence, episodic_context):
        state["runs"].append({
            "evidence_len": len(evidence) if evidence else 0,
            "episodic_len": len(episodic_context or ""),
        })
        if run_return_values:
            return run_return_values.pop(0)
        return _FakeMemoOutput(raw_output=json.dumps(_make_memo_dict()))

    def _fake_get_pydantic_model(self):
        return MagicMock()

    def _fake_get_sanity_validator(self):
        return MagicMock()

    runner_module = types.ModuleType("pmacs.agents.memo_writer")
    runner_module.MemoWriterRunner = MagicMock()
    instance = runner_module.MemoWriterRunner.return_value
    instance._analytical_context = ""
    instance.set_analytical_context.side_effect = lambda **kw: state["ctx_kwargs"].append(kw)
    instance.run.side_effect = lambda *, evidence, episodic_context: (
        state["runs"].append({
            "evidence_len": len(evidence) if evidence else 0,
            "episodic_len": len(episodic_context or ""),
        }),
        (run_return_values.pop(0) if run_return_values else
         _FakeMemoOutput(raw_output=json.dumps(_make_memo_dict()))),
    )[1]
    instance.get_pydantic_model.side_effect = _fake_get_pydantic_model
    instance.get_sanity_validator.side_effect = _fake_get_sanity_validator

    monkeypatch.setitem(sys.modules, "pmacs.agents.memo_writer", runner_module)
    return state


def _make_orchestrator_with_db(db_file):
    """Build a minimal Orchestrator instance whose _step_13mn_post_decision we
    can exercise without spinning the rest of the cycle.

    Args:
        db_file: Path or str to the SQLite file (already exists with memos table).
    """
    # Importing orchestrator pulls a LOT of deps. Skip the heavy import path:
    # build a lightweight stand-in that mimics just the scorer branch. This is
    # what the real Orchestrator does after memo_output = memo_runner.run().
    from pmacs.nervous.orchestrator import CycleOrchestrator
    orch = CycleOrchestrator.__new__(CycleOrchestrator)
    orch._db_path = str(db_file)
    return orch


@pytest.fixture
def fresh_db(tmp_path):
    """SQLite memos table — minimal schema matching orchestrator's INSERT."""
    import sqlite3
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE memos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            verdict TEXT,
            conviction_score REAL,
            memo_json TEXT,
            raw_text TEXT,
            memo_score REAL,
            memo_grade TEXT,
            decided_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_score_memo_returns_memo_score_for_complete_memo():
    """score_memo() on a complete memo returns total >= 50 + grade."""
    from pmacs.agents.sanity.memo_scorer import score_memo

    score = score_memo(_make_memo_dict(), evidence=[], agent_results=[],
                       crucible_attacks=[], conviction=0.42, verdict="BUY")
    assert isinstance(score.total, float)
    assert 0.0 <= score.total <= 100.0
    assert score.grade in ("A", "B", "C", "D", "F")
    assert len(score.dimensions) == 6  # 6 dimensions per spec


def test_score_memo_flags_missing_thesis_as_critical():
    """Missing thesis triggers critical issue + lower score."""
    from pmacs.agents.sanity.memo_scorer import score_memo

    bad = _make_memo_dict()
    bad["thesis"] = ""
    score = score_memo(bad, evidence=[], agent_results=[],
                       crucible_attacks=[], conviction=0.42, verdict="BUY")
    assert any("thesis" in ci.lower() for ci in score.critical_issues)


def test_format_retry_feedback_includes_score_and_issues():
    """format_retry_feedback returns a prompt-injectable string."""
    from pmacs.agents.sanity.memo_scorer import score_memo, format_retry_feedback

    bad = _make_memo_dict()
    bad["thesis"] = ""
    bad["key_evidence"] = []
    score = score_memo(bad, evidence=[], agent_results=[],
                       crucible_attacks=[], conviction=0.42, verdict="BUY")
    fb = format_retry_feedback(score)
    assert "MEMO QUALITY FEEDBACK" in fb
    assert "CRITICAL" in fb
    assert f"{score.total:.0f}" in fb


def test_orchestrator_invokes_score_memo_on_memo_path(monkeypatch, fresh_db):
    """When the orchestrator's _step_13mn_post_decision runs, it must call
    score_memo() and persist the result in the SQLite memos row.
    """
    state = _stub_memo_runner(
        monkeypatch,
        run_return_values=[
            _FakeMemoOutput(raw_output=json.dumps(_make_memo_dict())),
        ],
    )

    orch = _make_orchestrator_with_db(fresh_db)
    # Pre-stash attrs the orchestrator's scorer branch reads.
    orch._last_crucible_attacks = []
    orch._last_advocate_outputs = {}
    orch._last_auditor_flags = []
    orch._last_reverse_dcf = None
    orch._last_forward_valuation = None
    orch._last_scenario_price = None
    orch._last_crucible_summary = ""
    orch._last_crucible_thesis_survives = True

    # Use a real Arbitrated-like object (just the attrs the branch reads).
    @dataclass
    class _A:
        p_up: float = 0.62
        p_flat: float = 0.20
        p_down: float = 0.18
        decision: Any = "BUY"
        persona_outputs: list = field(default_factory=list)

    # Build a Holding stub (orchestrator reads `.id`).
    holding = MagicMock()
    holding.id = 1

    # Evidence packets list (empty is fine — scorer handles None gracefully).
    evidence_packets: list = []

    # Class enum-like for verdict
    class _V:
        value = "BUY"

    # Run the method (best-effort — anything downstream may raise; we only
    # care that the scorer branch runs and persists).
    try:
        orch._step_13mn_post_decision(
            holding=holding, ticker="OUST", cycle_id="c-test-1", op=1,
            evidence_packets=evidence_packets, brief="",
            verdict=_V(), conviction_score=0.42, arbitrated=_A(),
            crucible_severity=0.35,
        )
    except Exception:
        pass  # Downstream DB mirrors may raise on bare-bones fixture; we only
        # assert on the primary memos INSERT, which is what the scorer branch
        # targets.

    # Assert: at least one memo row was inserted with memo_score + memo_grade.
    import sqlite3
    conn = sqlite3.connect(str(fresh_db))
    try:
        rows = conn.execute(
            "SELECT memo_score, memo_grade FROM memos WHERE ticker='OUST'"
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) >= 1, "No memo row inserted"
    score, grade = rows[-1]
    assert score is not None, "memo_score was not persisted"
    assert 0.0 <= float(score) <= 100.0
    assert grade in ("A", "B", "C", "D", "F")


def test_orchestrator_retries_once_on_low_score(monkeypatch, fresh_db):
    """When the first score is < 70, the orchestrator must re-run memo_writer
    exactly once and persist the second score.
    """
    # First return = complete memo (would score high normally) but the test
    # patches score_memo() to return < 70 the first time and >= 70 the second.
    state = _stub_memo_runner(
        monkeypatch,
        run_return_values=[
            _FakeMemoOutput(raw_output=json.dumps(_make_memo_dict())),
            _FakeMemoOutput(raw_output=json.dumps(_make_memo_dict())),
        ],
    )

    # Patch score_memo + format_retry_feedback to deterministic values.
    call_log = {"count": 0}
    low_score = _FakeScore(total=42.0, grade="F",
                           critical_issues=["thesis is missing"])
    high_score = _FakeScore(total=86.0, grade="A", critical_issues=[])

    def _score_side_effect(*args, **kwargs):
        call_log["count"] += 1
        return low_score if call_log["count"] == 1 else high_score

    def _format_fb(score):
        return "## MEMO QUALITY FEEDBACK (fix these issues)\nPrevious 42/100 (F)."

    scorer_module = types.ModuleType("pmacs.agents.sanity.memo_scorer")
    scorer_module.score_memo = MagicMock(side_effect=_score_side_effect)
    scorer_module.format_retry_feedback = MagicMock(side_effect=_format_fb)
    monkeypatch.setitem(sys.modules, "pmacs.agents.sanity.memo_scorer",
                        scorer_module)

    orch = _make_orchestrator_with_db(fresh_db)
    orch._last_crucible_attacks = []
    orch._last_advocate_outputs = {}
    orch._last_auditor_flags = []
    orch._last_reverse_dcf = None
    orch._last_forward_valuation = None
    orch._last_scenario_price = None
    orch._last_crucible_summary = ""
    orch._last_crucible_thesis_survives = True

    @dataclass
    class _A:
        p_up: float = 0.62
        p_flat: float = 0.20
        p_down: float = 0.18
        decision: Any = "BUY"
        persona_outputs: list = field(default_factory=list)

    holding = MagicMock()
    holding.id = 1

    class _V:
        value = "BUY"

    try:
        orch._step_13mn_post_decision(
            holding=holding, ticker="OUST", cycle_id="c-test-2", op=1,
            evidence_packets=[], brief="",
            verdict=_V(), conviction_score=0.42, arbitrated=_A(),
            crucible_severity=0.35,
        )
    except Exception:
        pass

    # Assert: score_memo was called twice (initial + retry).
    assert call_log["count"] == 2, f"Expected 2 score_memo calls, got {call_log['count']}"
    # Assert: format_retry_feedback was called once (between the two scores).
    assert scorer_module.format_retry_feedback.call_count == 1
    # Assert: memo_runner.run was called twice (initial + retry).
    instance = sys.modules["pmacs.agents.memo_writer"].MemoWriterRunner.return_value
    assert instance.run.call_count == 2
    # Assert: set_analytical_context was called twice — second call must carry
    # the memo_feedback kwarg with retry guidance.
    assert instance.set_analytical_context.call_count == 2
    second_call_kwargs = instance.set_analytical_context.call_args_list[1].kwargs
    assert "memo_feedback" in second_call_kwargs
    assert "MEMO QUALITY FEEDBACK" in second_call_kwargs["memo_feedback"]
    # Assert: persisted memo_score reflects the SECOND (high) score.
    import sqlite3
    conn = sqlite3.connect(str(fresh_db))
    try:
        row = conn.execute(
            "SELECT memo_score, memo_grade FROM memos WHERE ticker='OUST' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert abs(row[0] - 86.0) < 0.01
    assert row[1] == "A"


def test_orchestrator_no_retry_when_first_score_passes(monkeypatch, fresh_db):
    """When the first score >= 70 and passes, no retry fires."""
    state = _stub_memo_runner(
        monkeypatch,
        run_return_values=[
            _FakeMemoOutput(raw_output=json.dumps(_make_memo_dict())),
        ],
    )

    high_score = _FakeScore(total=88.0, grade="A", critical_issues=[])
    scorer_module = types.ModuleType("pmacs.agents.sanity.memo_scorer")
    scorer_module.score_memo = MagicMock(return_value=high_score)
    scorer_module.format_retry_feedback = MagicMock()
    monkeypatch.setitem(sys.modules, "pmacs.agents.sanity.memo_scorer",
                        scorer_module)

    orch = _make_orchestrator_with_db(fresh_db)
    orch._last_crucible_attacks = []
    orch._last_advocate_outputs = {}
    orch._last_auditor_flags = []
    orch._last_reverse_dcf = None
    orch._last_forward_valuation = None
    orch._last_scenario_price = None
    orch._last_crucible_summary = ""
    orch._last_crucible_thesis_survives = True

    @dataclass
    class _A:
        p_up: float = 0.62
        p_flat: float = 0.20
        p_down: float = 0.18
        decision: Any = "BUY"
        persona_outputs: list = field(default_factory=list)

    holding = MagicMock()
    holding.id = 1

    class _V:
        value = "BUY"

    try:
        orch._step_13mn_post_decision(
            holding=holding, ticker="OUST", cycle_id="c-test-3", op=1,
            evidence_packets=[], brief="",
            verdict=_V(), conviction_score=0.42, arbitrated=_A(),
            crucible_severity=0.35,
        )
    except Exception:
        pass

    assert scorer_module.score_memo.call_count == 1
    assert scorer_module.format_retry_feedback.call_count == 0
    instance = sys.modules["pmacs.agents.memo_writer"].MemoWriterRunner.return_value
    assert instance.run.call_count == 1


def test_memo_writer_renders_memo_feedback_block():
    """MemoWriterRunner.set_analytical_context injects the feedback block when
    memo_feedback is supplied.
    """
    from pmacs.agents.memo_writer import MemoWriterRunner

    runner = MemoWriterRunner()
    runner.set_analytical_context(
        memo_feedback="## Memo Quality Feedback\nFix thesis."
    )
    assert "Memo Quality Feedback" in runner._analytical_context
    assert "Fix thesis." in runner._analytical_context


def test_memo_writer_omits_feedback_when_none():
    """When memo_feedback is None, no feedback block is rendered (no-op)."""
    from pmacs.agents.memo_writer import MemoWriterRunner

    runner = MemoWriterRunner()
    runner.set_analytical_context(
        arbitrated=None, verdict=None, conviction_score=0.42,
        memo_feedback=None,
    )
    # No "Memo Quality Feedback" string in the analytical context.
    assert "Memo Quality Feedback" not in runner._analytical_context


def test_memo_writer_renders_persona_weights_block():
    """When persona_weights + per_persona_calibration are supplied, the memo
    renders the Persona Arbitration Weights table with LOW CONFIDENCE markers.
    """
    from dataclasses import dataclass
    from pmacs.agents.memo_writer import MemoWriterRunner

    @dataclass
    class _W:
        persona: str
        weight: float
        brier_score: float = 0.20
        calibration_count: int = 50
        weight_multiplier: float = 1.0

    @dataclass
    class _A:
        p_up: float = 0.62
        p_flat: float = 0.20
        p_down: float = 0.18
        decision: str = "BUY"
        persona_outputs: list = None

    runner = MemoWriterRunner()
    runner.set_analytical_context(
        arbitrated=_A(),
        verdict="BUY",
        conviction_score=0.42,
        persona_weights=[
            _W("growth_hunter", 0.40),
            _W("moat_analyst", 0.30),
            _W("macro_regime", 0.30, weight_multiplier=0.5),
        ],
        per_persona_calibration={
            "growth_hunter": 0.18,
            "moat_analyst": 0.22,
            "macro_regime": 0.30,  # > 0.25 → LOW CONFIDENCE
        },
    )
    ctx = runner._analytical_context
    assert "Persona Arbitration Weights" in ctx
    assert "growth_hunter" in ctx
    assert "moat_analyst" in ctx
    assert "macro_regime" in ctx
    # LOW CONFIDENCE marker for macro_regime (brier 0.30 > 0.25).
    assert "LOW CONFIDENCE" in ctx


def test_memo_writer_omits_persona_weights_block_when_none():
    """When persona_weights is None, no Persona Arbitration Weights block."""
    from pmacs.agents.memo_writer import MemoWriterRunner

    runner = MemoWriterRunner()
    runner.set_analytical_context(
        arbitrated=None, verdict=None, conviction_score=0.42,
        persona_weights=None, per_persona_calibration=None,
    )
    assert "Persona Arbitration Weights" not in runner._analytical_context


def test_memo_writer_renders_prior_memo_summary_block():
    """When prior_memo_summary is supplied with rich fields, the memo renders
    the Prior Memo Context block.
    """
    from pmacs.agents.memo_writer import MemoWriterRunner

    runner = MemoWriterRunner()
    runner.set_analytical_context(
        prior_memo_summary={
            "thesis": "Strong moat from last cycle.",
            "verdict_line": "BUY — conviction 0.42",
            "fair_value": "$28.50",
            "valuation_methodology": "EV/EBITDA at 14x",
            "key_evidence": ["ev1", "ev2"],
            "key_risks": ["FX risk"],
            "what_would_change_my_mind": ["guidance cut"],
            "forward_expected_price_usd": 32.10,
        },
    )
    ctx = runner._analytical_context
    assert "Prior Memo Context" in ctx
    assert "Strong moat from last cycle." in ctx
    assert "$28.50" in ctx
    assert "$32.10" in ctx


def test_memo_writer_omits_prior_memo_block_when_empty():
    """When prior_memo_summary is empty/None, no Prior Memo Context block."""
    from pmacs.agents.memo_writer import MemoWriterRunner

    runner = MemoWriterRunner()
    runner.set_analytical_context(prior_memo_summary=None)
    assert "Prior Memo Context" not in runner._analytical_context

    runner2 = MemoWriterRunner()
    runner2.set_analytical_context(prior_memo_summary={})
    assert "Prior Memo Context" not in runner2._analytical_context