"""Task #8 — Unify the two cycle paths: engine params + kill-switch bypass.

Tests the three new ``run_cycle``/``initiate_cycle`` parameters:
- ``skip_kill_switch`` — operator-initiated runs may proceed even when the kill
  switch is ENGAGED. Automated callers never set it. Budget enforcement stays
  independent (still runs per LLM call).
- ``cycle_id`` — caller may pre-generate the id so the HTTP route can return it
  immediately; ``initiate_cycle`` uses it instead of uuid4.
- ``tickers`` — overrides universe composition for single-ticker SOLO runs.

spec_ref: Architecture.md §4.4, §12; Source.md §6 (mode ladder), Five
Non-Negotiables #5 (operator owns the kill switch — bypass is an explicit
per-run operator opt-in, never an auto-disengage).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from pmacs.cortex.kill_switch import engage, is_engaged
from pmacs.nervous.orchestrator import (
    CycleOrchestrator,
    KillSwitchEngagedError,
    initiate_cycle,
)
from pmacs.nervous.sse_publisher import SSEPublisher
from pmacs.storage.sqlite import init_db


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    init_db(db)
    return db


@pytest.fixture
def orch(tmp_db: Path, tmp_path: Path) -> CycleOrchestrator:
    return CycleOrchestrator(
        db_path=tmp_db,
        audit_path=tmp_path / "audit.log",
        sse_publisher=SSEPublisher(),
        config={"lock_path": str(tmp_path / "cycle.lock")},
    )


def _engage_kill_switch(db_path: Path) -> None:
    engage(reason="test breach", trigger="MANUAL", db_path=str(db_path))
    assert is_engaged(str(db_path)) is True


def _cycles_row(db_path: Path, cycle_id: str) -> sqlite3.Row | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT cycle_id, state, trigger FROM cycles WHERE cycle_id = ?",
            [cycle_id],
        ).fetchone()
    finally:
        conn.close()


# -- initiate_cycle (module function) --------------------------------------


class TestInitiateCycleBypass:
    def test_gated_raises_when_engaged(self, tmp_db: Path):
        _engage_kill_switch(tmp_db)
        with pytest.raises(KillSwitchEngagedError):
            initiate_cycle("OPERATOR", tmp_db)

    def test_bypass_proceeds_when_engaged(self, tmp_db: Path):
        _engage_kill_switch(tmp_db)
        cid = initiate_cycle("solo_research", tmp_db, skip_kill_switch=True)
        assert cid
        row = _cycles_row(tmp_db, cid)
        assert row is not None and row["state"] == "OPEN"
        assert row["trigger"] == "solo_research"

    def test_provided_cycle_id_is_used(self, tmp_db: Path):
        cid = initiate_cycle(
            "solo_research", tmp_db, cycle_id="SOLO-OUST-20260624T120000Z"
        )
        assert cid == "SOLO-OUST-20260624T120000Z"
        row = _cycles_row(tmp_db, cid)
        assert row is not None and row["cycle_id"] == "SOLO-OUST-20260624T120000Z"

    def test_bypass_still_creates_row_when_engaged(self, tmp_db: Path):
        _engage_kill_switch(tmp_db)
        cid = initiate_cycle(
            "manual", tmp_db, cycle_id="CYCLE-1", skip_kill_switch=True,
        )
        assert _cycles_row(tmp_db, cid) is not None


# -- run_cycle (method) plumbing -------------------------------------------


def _short_circuit_pipeline(orch: CycleOrchestrator) -> None:
    """Replace the heavy pipeline steps with no-ops so run_cycle completes
    after initiate_cycle, isolating the kill-switch bypass behavior."""
    patch.object(orch, "_step_clock_drift", lambda cid: None).start()
    patch.object(orch, "_step_checkpoint_resume", lambda cid, seq: None).start()
    patch.object(orch, "_run_pre_cycle", lambda cid, seq: seq + 10).start()
    patch.object(orch, "_run_all_symbols", lambda cid, seq: seq + 1).start()
    patch.object(orch, "_run_post_cycle", lambda cid, seq: seq + 10).start()
    patch.object(orch, "_finalize_cycle_metrics", lambda cid: None).start()


class TestRunCycleBypass:
    def test_gated_raises_when_engaged(self, orch: CycleOrchestrator, tmp_db: Path):
        _engage_kill_switch(tmp_db)
        with pytest.raises(KillSwitchEngagedError):
            orch.run_cycle("OPERATOR")

    def test_bypass_runs_to_completion_when_engaged(
        self, orch: CycleOrchestrator, tmp_db: Path
    ):
        _engage_kill_switch(tmp_db)
        _short_circuit_pipeline(orch)
        try:
            cid = orch.run_cycle(
                "solo_research",
                tickers=["OUST"],
                cycle_id="SOLO-OUST-1",
                skip_kill_switch=True,
            )
        finally:
            patch.stopall()
        assert cid == "SOLO-OUST-1"
        # The operator's explicit opt-in let the cycle open despite the engaged KS.
        row = _cycles_row(tmp_db, cid)
        assert row is not None and row["trigger"] == "solo_research"
        # The flag must NOT have auto-disengaged the kill switch.
        assert is_engaged(str(tmp_db)) is True

    def test_provided_cycle_id_persists(self, orch: CycleOrchestrator, tmp_db: Path):
        _short_circuit_pipeline(orch)
        try:
            cid = orch.run_cycle("manual", cycle_id="CYCLE-XYZ")
        finally:
            patch.stopall()
        assert cid == "CYCLE-XYZ"
        assert _cycles_row(tmp_db, cid)["cycle_id"] == "CYCLE-XYZ"


# -- single-ticker queue override ------------------------------------------


class TestRequestedTickers:
    def test_requested_tickers_override_universe(self, orch: CycleOrchestrator):
        orch._requested_tickers = ["OUST", "PLTR"]
        orch._step_universe_sync("test-cycle")
        assert orch._universe_tickers == ["OUST", "PLTR"]
        assert orch._universe_priority == {}

    def test_none_falls_through_to_universe(self, orch: CycleOrchestrator):
        # With no universe table populated, the DB read yields [] (no crash).
        orch._step_universe_sync("test-cycle")
        assert orch._universe_tickers == []


# -- guard: automated callers never set the flag ---------------------------


class TestNoAutomatedBypass:
    """Non-Negotiable #5: the kill-switch bypass is operator-initiated only.

    ``skip_kill_switch=True`` may appear ONLY in the operator web routes
    (pmacs/web/routes/) — the explicit operator entry points. Every other
    module (automated callers: boot_detector, mutation daemon, engines) must
    never set it, so automated/scheduled cycles stay kill-switch-gated.
    """

    def test_bypass_only_in_operator_routes(self):
        import os
        offenders = []
        for root, _, files in os.walk(os.path.join(os.getcwd(), "pmacs")):
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(root, f)
                rel = os.path.relpath(path, os.getcwd())
                if rel.startswith("pmacs/web/routes/"):
                    continue  # operator routes — allowed
                src = open(path).read()
                if "skip_kill_switch=True" in src:
                    offenders.append(rel)
        assert not offenders, (
            "skip_kill_switch=True must only be set by operator web routes; "
            f"found in automated/non-route modules: {offenders}"
        )
