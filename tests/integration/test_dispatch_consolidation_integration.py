"""Integration test: drive the REAL ``CycleOrchestrator._dispatch_personas`` with
mocked runners to prove the agent-sweep consolidations fire at the wiring level.

Rec1 — MacroRegime is cycle-level: across two tickers the macro LLM runner is
invoked exactly once; the second ticker reuses the cached DP (the macro runner is
stripped from slot 0 and the cached output is injected into ``results``).

Rec2 — insider_activity / short_interest skip the LLM call when their primary data
source (FORM4 / FINRA) produced no evidence. The synthesized PersonaOutput must be
present in ``results`` AND extract to a confidence==0.0 directional probability so
arbitration skips it (the exact condition in ``_step_13e_arbitration``). When the
primary source IS present, the pre-check must NOT fire and the runner IS called.

No tokens are spent: every runner class's ``run`` is monkeypatched to a canned
``PersonaOutput``. No kill switch is involved (``_dispatch_personas`` is called
directly, not via ``run_cycle``).

spec_ref: Architecture.md §12.2 (slot layout), §9.4 (arbitration), Agents.md §9/§10.
"""
import json
from datetime import datetime, timezone

import pytest

import pmacs.nervous.orchestrator as orch_module
from pmacs.nervous.orchestrator import CycleOrchestrator
from pmacs.schemas.agents import PersonaOutput, PersonaName
from pmacs.schemas.data import (
    EvidencePacket,
    Evidence,
    EvidenceType,
    DataSource,
)

# Runner classes instantiated inside _dispatch_personas (local imports) — patching
# the ``run`` attribute on the class object affects every instance regardless of
# how the name is bound in the function's local scope.
from pmacs.agents.macro_regime import MacroRegimeRunner
from pmacs.agents.catalyst_summarizer import CatalystSummarizerRunner
from pmacs.agents.moat_analyst import MoatAnalystRunner
from pmacs.agents.growth_hunter import GrowthHunterRunner
from pmacs.agents.insider_activity import InsiderActivityRunner
from pmacs.agents.short_interest import ShortInterestRunner
from pmacs.agents.forensics import ForensicsRunner

# Class → persona_name string (persona_name is an instance attr, not class attr).
_RUNNER_PERSONAS: list[tuple[type, str]] = [
    (MacroRegimeRunner, "macro_regime"),
    (CatalystSummarizerRunner, "catalyst_summarizer"),
    (MoatAnalystRunner, "moat_analyst"),
    (GrowthHunterRunner, "growth_hunter"),
    (InsiderActivityRunner, "insider_activity"),
    (ShortInterestRunner, "short_interest"),
    (ForensicsRunner, "forensics"),
]
_TS = datetime(2026, 6, 24, tzinfo=timezone.utc)


def _ev(eid: str, source: DataSource, ticker: str = "OUST") -> Evidence:
    return Evidence(
        id=eid, source=source, type=EvidenceType.NEWS, ticker=ticker,
        fetched_at=_TS, content_hash="x" * 8, data={},
    )


def _packet(ticker: str, evs: list[Evidence]) -> EvidencePacket:
    return EvidencePacket(
        ticker=ticker, cycle_id="c1", evidence=evs, fetched_at=_TS,
        source_count=len(evs), has_stale_data=False,
    )


def _canned(persona: str, ticker: str) -> PersonaOutput:
    return PersonaOutput(
        persona=PersonaName(persona), ticker=ticker, cycle_id="c1",
        raw_output=json.dumps({
            "ticker": ticker, "p_up": 0.4, "p_flat": 0.4, "p_down": 0.2,
            "evidence_ids": ["e1"],
        }),
    )


def _bare_orchestrator() -> CycleOrchestrator:
    """A CycleOrchestrator without running __init__ — _dispatch_personas only
    touches _macro_regime_dp_cached, _publish_sse, _log_call_billing."""
    o = CycleOrchestrator.__new__(CycleOrchestrator)
    o._macro_regime_dp_cached = None
    o._publish_sse = lambda *a, **k: None
    o._log_call_billing = lambda *a, **k: None
    return o


@pytest.fixture
def patched_runners(monkeypatch):
    """Monkeypatch every runner's ``run`` to a canned no-op with a shared counter."""
    # log_debug writes to the debug stream; neutralize it for the bare orchestrator.
    monkeypatch.setattr(orch_module, "log_debug", lambda *a, **k: None)

    counts: dict[str, int] = {}

    def _make_run(persona: str):
        def _run(self, evidence, episodic_context=None):
            counts[persona] = counts.get(persona, 0) + 1
            return _canned(persona, getattr(self, "_ticker", "OUST"))
        return _run

    for cls, persona in _RUNNER_PERSONAS:
        monkeypatch.setattr(cls, "run", _make_run(persona))

    return counts


# ─── Rec2: dataless skip fires inside the real dispatch loop ────────────────────


def test_rec2_dataless_skip_when_no_primary_source(patched_runners):
    """No FORM4/FINRA evidence → insider/short LLM runners are NOT called; the
    synthesized dataless PersonaOutput is in results and extracts to confidence 0.0."""
    orch = _bare_orchestrator()
    # Evidence with NO form4 and NO finra sources.
    evidence = [
        _packet("OUST", [_ev("e-poly", DataSource.POLYGON),
                         _ev("e-fh", DataSource.FINNHUB)]),
    ]
    results = orch._dispatch_personas(evidence, brief="ctx", cycle_id="c1", ticker="OUST")

    # The two dataless personas were synthesized, not run.
    assert patched_runners.get("insider_activity", 0) == 0
    assert patched_runners.get("short_interest", 0) == 0
    assert "insider_activity" in results
    assert "short_interest" in results

    # The synthesized outputs must be arbitration-skippable (confidence == 0.0).
    for persona in ("insider_activity", "short_interest"):
        dp = CycleOrchestrator._extract_directional_probability(
            persona, "OUST", "c1", results[persona],
        )
        assert dp is not None
        assert dp.confidence == 0.0, f"{persona} synthesized DP must be skipped by arbitration"


def test_rec2_precheck_does_not_fire_when_primary_source_present(patched_runners):
    """FORM4/finra evidence present → the pre-check does NOT fire; the real runner
    IS called (here mocked) and returns a normal (non-dataless) output."""
    orch = _bare_orchestrator()
    evidence = [
        _packet("OUST", [_ev("e-f4", DataSource.FORM4),
                         _ev("e-finra", DataSource.FINRA),
                         _ev("e-poly", DataSource.POLYGON)]),
    ]
    results = orch._dispatch_personas(evidence, brief="ctx", cycle_id="c1", ticker="OUST")

    # Primary sources present → runners were invoked (not skipped).
    assert patched_runners.get("insider_activity", 0) == 1
    assert patched_runners.get("short_interest", 0) == 1
    assert "insider_activity" in results
    assert "short_interest" in results


# ─── Rec1: macro DP cached + reused across two tickers ─────────────────────────


def test_rec1_macro_runner_called_once_across_two_tickers(patched_runners):
    """Two _dispatch_personas calls (two tickers in one cycle): macro LLM runs
    exactly once; the second ticker reuses the cached DP from the first."""
    orch = _bare_orchestrator()
    evidence = [_packet("OUST", [_ev("e-fred", DataSource.FRED)])]

    # Ticker 1 — cache empty → macro runner runs, cache populated.
    r1 = orch._dispatch_personas(evidence, brief="ctx", cycle_id="c1", ticker="OUST")
    assert "macro_regime" in r1
    assert patched_runners.get("macro_regime", 0) == 1
    assert orch._macro_regime_dp_cached is not None  # cached after first ticker

    # Ticker 2 — cache populated → macro runner stripped from slot 0, NOT called;
    # cached DP injected into results.
    evidence2 = [_packet("PLTR", [_ev("e-fred2", DataSource.FRED)])]
    r2 = orch._dispatch_personas(evidence2, brief="ctx", cycle_id="c1", ticker="PLTR")
    assert patched_runners.get("macro_regime", 0) == 1  # still 1 — reused, not re-run
    assert "macro_regime" in r2
    # The injected DP is the cached object from ticker 1 (identity, not a fresh run).
    assert r2["macro_regime"] is orch._macro_regime_dp_cached
