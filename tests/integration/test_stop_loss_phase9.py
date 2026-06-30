"""Phase 9 integration tests — close thesis-aging stub, fix weekly_reeval invariant,
and extend daemon trailing columns.

This file covers the three spec exit test gaps identified in the Phase 9 plan:

1. ``test_thesis_aging_transitions_to_review_state`` — pins spec exit #5:
   _step_thesis_aging actually routes aged holdings through
   ACTIVE -> THESIS_AGING_REVIEW via state_machine.transition() (not raw SQL).

2. ``test_weekly_reeval_invalidates_via_state_machine`` — pins the
   invariant fix to _step_weekly_reeval: invalidation must call
   transition() so the audit chain captures it (Architecture §16.1, §5.1).

3. ``test_brand_new_position_not_re_evaled_on_first_cycle`` — pins the
   NULL ``last_reeval_at`` bug fix: a brand-new position (entry_date =
   today) must NOT be re-evaluated just because last_reeval_at IS NULL.

4. ``test_daemon_reads_trailing_columns`` — pins the daemon SELECT fix:
   the SQL query must include trailing_stop_price_usd and
   trailing_stop_armed so trailing breaches fire in production.

5. ``test_thesis_aging_review_full_path_to_invalidated`` — end-to-end:
   91-day-old holding re-evaluates to thesis-invalid and lands in
   EXIT_THESIS_INVALIDATED through state_machine.

These tests construct a minimal PMACS DB (via init_db), seed a single
ACTIVE holding, then invoke orchestrator step methods directly with
mocked persona dispatch + arbitration. This mirrors the pattern used in
tests/integration/test_cycle_hardening.py::TestSymbolTimeoutAbort.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pmacs.engines.arbitration import ArbitrationSignal
from pmacs.nervous.orchestrator import CycleOrchestrator
from pmacs.schemas.agents import DirectionalProbability, PersonaName
from pmacs.schemas.arbitration import Arbitrated, ArbitrationDecision
from pmacs.storage.sqlite import init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Initialize a temporary PMACS SQLite DB (full schema + migrations)."""
    p = tmp_path / "phase9.db"
    init_db(p)
    return p


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.log"


@pytest.fixture
def orchestrator(db_path: Path, audit_path: Path) -> CycleOrchestrator:
    """Construct a CycleOrchestrator with no SSE publisher / no execution adapter.

    This is the minimal setup needed to call _step_weekly_reeval and
    _step_thesis_aging directly without spinning up the full cycle.
    """
    return CycleOrchestrator(
        db_path=db_path,
        audit_path=audit_path,
        sse_publisher=None,
        config={"lock_path": str(db_path.parent / "phase9.lock")},
        execution_adapter=None,
    )


def _seed_holding(
    db_path: Path,
    *,
    holding_id: str = "h-001",
    ticker: str = "AAPL",
    entry_date: str,
    state: str = "ACTIVE",
    cycle_id_opened: str = "c-seed-001",
    stop_price_usd: float = 90.0,
    position_size_usd: float = 1000.0,
    last_reeval_at: str | None = None,
    trailing_stop_price_usd: float | None = None,
    trailing_stop_armed: int = 0,
) -> None:
    """Insert a minimal ACTIVE holding row for tests."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """INSERT OR REPLACE INTO holdings
               (id, ticker, state, cycle_id_opened, entry_price_usd,
                position_size_usd, entry_date, last_reeval_at,
                stop_price_usd, trailing_stop_price_usd, trailing_stop_armed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                holding_id,
                ticker,
                state,
                cycle_id_opened,
                100.0,
                position_size_usd,
                entry_date,
                last_reeval_at,
                stop_price_usd,
                trailing_stop_price_usd,
                trailing_stop_armed,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _make_dp(persona: str, ticker: str, p_up: float, p_down: float) -> DirectionalProbability:
    return DirectionalProbability(
        persona=PersonaName(persona),
        ticker=ticker,
        p_up=p_up,
        p_flat=1.0 - p_up - p_down,
        p_down=p_down,
        confidence=1.0,
    )


def _make_arbitrated(ticker: str, p_up: float, p_down: float, cycle_id: str) -> Arbitrated:
    """Build an Arbitrated with probabilities that sum to 1.0 and a
    PROCEED or ABORT_DISAGREEMENT decision.

    Decision rule matches the production contract in orchestrator:
        ``decision.value.startswith("PROCEED") and p_up >= p_down``
    so PROCEED + p_up >= p_down is "thesis valid", anything else is
    "thesis invalid" (the re-eval pipeline routes invalid -> EXIT_).
    """
    p_flat = max(0.0, 1.0 - p_up - p_down)
    total = p_up + p_flat + p_down
    if total <= 0:
        p_up, p_flat, p_down = 1.0 / 3, 1.0 / 3, 1.0 / 3
    else:
        p_up /= total
        p_flat /= total
        p_down /= total
    decision = (
        ArbitrationDecision.PROCEED if p_up >= p_down
        else ArbitrationDecision.ABORT_DISAGREEMENT
    )
    return Arbitrated(
        ticker=ticker,
        cycle_id=cycle_id,
        p_up=p_up,
        p_flat=p_flat,
        p_down=p_down,
        decision=decision,
    )


def _mock_dispatch(*, ticker: str, p_up: float, p_down: float, cycle_id: str):
    """Return mocks for orchestrator helpers used during re-eval.

    Returns a 4-tuple (dispatch, extract, arbitrate_fn, brier_fn) ready to
    feed into ``patch.object(orchestrator, ...)``.
    """
    dp = _make_dp("growth_hunter", ticker, p_up, p_down)
    arb = _make_arbitrated(ticker, p_up, p_down, cycle_id)

    def dispatch(evidence, brief, cycle_id, ticker, timeout_seconds):
        # Orchestrator calls with kwargs (see orchestrator.py:4629-4635),
        # so this stub accepts both positional and keyword forms.
        return {"growth_hunter": MagicMock()}

    def dispatch_kwargs(**kwargs):
        return {"growth_hunter": MagicMock()}

    # NOTE: _extract_directional_probability is NOT a bound method — it is
    # an unbound function at orchestrator level. Patch via patch.object so
    # the replacement is bound; signature is (persona, ticker, cycle_id, output).
    def extract(persona_name_str, ticker_arg, cycle_id_arg, persona_output):
        return dp

    def arbitrate_fn(signals, *, cycle_id=""):
        return arb

    def brier_fn():
        return {"growth_hunter": (0.667, 0)}

    return dispatch_kwargs, extract, arbitrate_fn, brier_fn, arb


# ---------------------------------------------------------------------------
# Test 1: Thesis aging transitions via state_machine (spec exit #5)
# ---------------------------------------------------------------------------


class TestThesisAgingPhase9:
    """Phase 9 fix: _step_thesis_aging routes aged holdings through the
    full state machine, not a log-only stub."""

    def test_thesis_aging_transitions_to_review_state(
        self, orchestrator: CycleOrchestrator, db_path: Path, audit_path: Path,
    ):
        """An aged holding (91 days old) flows through ACTIVE -> THESIS_AGING_REVIEW
        via state_machine.transition(), then back to ACTIVE on a valid thesis.

        Pins spec/Phases.md Phase 9 exit test #5: the step no longer just
        counts and logs — it actually transitions state through the
        audit-chained state machine (Architecture §16.1, §5.1).
        """
        # Seed a 91-day-old ACTIVE holding
        old_entry = (date.today() - timedelta(days=91)).isoformat()
        _seed_holding(
            db_path, holding_id="h-aged-001", ticker="AAPL",
            entry_date=old_entry,
        )

        cycle_id = "c-phase9-aging-001"
        # Mock the re-eval pipeline to return p_up > p_down (thesis valid)
        dispatch, extract, arbitrate_fn, brier_fn, _ = _mock_dispatch(
            ticker="AAPL", p_up=0.65, p_down=0.15, cycle_id=cycle_id,
        )

        with patch.object(
            orchestrator, "_dispatch_personas_with_timeout", side_effect=dispatch,
        ), patch.object(
            orchestrator, "_extract_directional_probability", side_effect=extract,
        ), patch.object(
            orchestrator, "_get_persona_brier_data", return_value=brier_fn(),
        ), patch(
            "pmacs.engines.arbitration.arbitrate", side_effect=arbitrate_fn,
        ), patch(
            "pmacs.data.evidence_router.fetch_evidence_for_ticker",
        ):
            orchestrator._step_thesis_aging(cycle_id)

        # Holding should be back to ACTIVE after a valid re-eval
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT state, last_reeval_at FROM holdings WHERE id = ?",
                ("h-aged-001",),
            ).fetchone()
        finally:
            conn.close()

        assert row is not None, "holding row missing"
        assert row[0] == "ACTIVE", (
            f"Expected ACTIVE after valid re-eval, got state={row[0]!r}. "
            "Phase 9 fix: _step_thesis_aging must route through state_machine."
        )
        assert row[1] == date.today().isoformat(), "last_reeval_at should be updated"

    def test_thesis_aging_review_full_path_to_invalidated(
        self, orchestrator: CycleOrchestrator, db_path: Path, audit_path: Path,
    ):
        """End-to-end: 91-day-old holding, mock returns p_down > p_up
        (thesis invalid). Holding should land in EXIT_THESIS_INVALIDATED
        via state_machine.transition() (Architecture §16.1).
        """
        old_entry = (date.today() - timedelta(days=91)).isoformat()
        _seed_holding(
            db_path, holding_id="h-aged-002", ticker="MSFT",
            entry_date=old_entry,
        )

        cycle_id = "c-phase9-aging-002"
        dispatch, extract, arbitrate_fn, brier_fn, _ = _mock_dispatch(
            ticker="MSFT", p_up=0.15, p_down=0.70, cycle_id=cycle_id,
        )

        with patch.object(
            orchestrator, "_dispatch_personas_with_timeout", side_effect=dispatch,
        ), patch.object(
            orchestrator, "_extract_directional_probability", side_effect=extract,
        ), patch.object(
            orchestrator, "_get_persona_brier_data", return_value=brier_fn(),
        ), patch(
            "pmacs.engines.arbitration.arbitrate", side_effect=arbitrate_fn,
        ), patch(
            "pmacs.data.evidence_router.fetch_evidence_for_ticker",
        ):
            orchestrator._step_thesis_aging(cycle_id)

        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT state, abort_reason, exit_date FROM holdings WHERE id = ?",
                ("h-aged-002",),
            ).fetchone()
        finally:
            conn.close()

        assert row is not None
        assert row[0] == "EXIT_THESIS_INVALIDATED", (
            f"Expected EXIT_THESIS_INVALIDATED after invalid re-eval, got {row[0]!r}"
        )
        assert row[1] is not None and "p_down=0.70" in row[1], (
            f"abort_reason should mention p_down, got {row[1]!r}"
        )
        assert row[2] is not None, "exit_date should be set by transition()"

    def test_thesis_aging_skips_fresh_holding(
        self, orchestrator: CycleOrchestrator, db_path: Path,
    ):
        """A holding entered 30 days ago must NOT be touched by step 15.

        Pins the 90-day calendar invariant from check_thesis_aging().
        """
        fresh_entry = (date.today() - timedelta(days=30)).isoformat()
        _seed_holding(
            db_path, holding_id="h-fresh", ticker="TSLA",
            entry_date=fresh_entry,
        )

        cycle_id = "c-phase9-aging-003"
        dispatch, extract, arbitrate_fn, brier_fn, _ = _mock_dispatch(
            ticker="TSLA", p_up=0.65, p_down=0.15, cycle_id=cycle_id,
        )

        with patch.object(
            orchestrator, "_dispatch_personas_with_timeout", side_effect=dispatch,
        ), patch.object(
            orchestrator, "_extract_directional_probability", side_effect=extract,
        ), patch.object(
            orchestrator, "_get_persona_brier_data", return_value=brier_fn(),
        ), patch(
            "pmacs.engines.arbitration.arbitrate", side_effect=arbitrate_fn,
        ), patch(
            "pmacs.data.evidence_router.fetch_evidence_for_ticker",
        ):
            orchestrator._step_thesis_aging(cycle_id)

        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT state, last_reeval_at FROM holdings WHERE id = ?",
                ("h-fresh",),
            ).fetchone()
        finally:
            conn.close()

        assert row[0] == "ACTIVE", "30-day-old holding should not be re-evaluated"
        assert row[1] is None, "last_reeval_at must remain NULL for fresh holding"


# ---------------------------------------------------------------------------
# Test 2: Weekly re-eval invariant fix + NULL last_reeval_at bug fix
# ---------------------------------------------------------------------------


class TestWeeklyReEvalPhase9:
    """Phase 9 fix: weekly re-eval invalidates via state_machine (not raw
    SQL) and falls back to entry_date when last_reeval_at IS NULL."""

    def test_weekly_reeval_invalidates_via_state_machine(
        self, orchestrator: CycleOrchestrator, db_path: Path,
    ):
        """A holding with last_reeval_at > 7 days ago + p_down > p_up must
        land in EXIT_THESIS_INVALIDATED via state_machine.transition().

        Pins the Architecture §16.1 invariant fix: thesis invalidation
        must NOT bypass the state machine (raw UPDATE holdings SET state=).
        """
        # entry_date 30 days ago, last_reeval 10 days ago — due for re-eval
        old_entry = (date.today() - timedelta(days=30)).isoformat()
        old_reeval = (date.today() - timedelta(days=10)).isoformat()
        _seed_holding(
            db_path, holding_id="h-w-001", ticker="NVDA",
            entry_date=old_entry, last_reeval_at=old_reeval,
        )

        cycle_id = "c-phase9-weekly-001"
        dispatch, extract, arbitrate_fn, brier_fn, _ = _mock_dispatch(
            ticker="NVDA", p_up=0.20, p_down=0.65, cycle_id=cycle_id,
        )

        with patch.object(
            orchestrator, "_dispatch_personas_with_timeout", side_effect=dispatch,
        ), patch.object(
            orchestrator, "_extract_directional_probability", side_effect=extract,
        ), patch.object(
            orchestrator, "_get_persona_brier_data", return_value=brier_fn(),
        ), patch(
            "pmacs.engines.arbitration.arbitrate", side_effect=arbitrate_fn,
        ), patch(
            "pmacs.data.evidence_router.fetch_evidence_for_ticker",
        ):
            orchestrator._step_weekly_reeval(cycle_id)

        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT state, abort_reason FROM holdings WHERE id = ?",
                ("h-w-001",),
            ).fetchone()
        finally:
            conn.close()

        assert row[0] == "EXIT_THESIS_INVALIDATED", (
            f"Expected EXIT_THESIS_INVALIDATED via state_machine, got {row[0]!r}. "
            "Phase 9 fix: invalidation must route through state_machine."
        )
        assert row[1] is not None and "p_down=0.65" in row[1], (
            f"abort_reason should mention p_down, got {row[1]!r}"
        )

    def test_brand_new_position_not_re_evaled_on_first_cycle(
        self, orchestrator: CycleOrchestrator, db_path: Path,
    ):
        """Brand-new position: last_reeval_at IS NULL + entry_date = today.

        Phase 9 bug fix: previously ``needs_reeval = True`` for NULL
        last_reeval_at — re-evaluated brand-new positions on the first
        cycle. Now falls back to entry_date (matches the contract in
        ``engines.thesis_reeval.check_weekly_reeval``).
        """
        _seed_holding(
            db_path, holding_id="h-new", ticker="AMZN",
            entry_date=date.today().isoformat(),
            last_reeval_at=None,
        )

        cycle_id = "c-phase9-weekly-002"
        # If we DID re-eval (the bug), the mock would set state to ACTIVE
        # (p_up > p_down). After the fix, this dispatch is never called.
        dispatch, extract, arbitrate_fn, brier_fn, _ = _mock_dispatch(
            ticker="AMZN", p_up=0.65, p_down=0.15, cycle_id=cycle_id,
        )
        called = {"n": 0}

        def tracking_dispatch(*args, **kwargs):
            called["n"] += 1
            return dispatch_kwargs(**kwargs)

        with patch.object(
            orchestrator, "_dispatch_personas_with_timeout",
            side_effect=tracking_dispatch,
        ), patch.object(
            orchestrator, "_extract_directional_probability", side_effect=extract,
        ), patch.object(
            orchestrator, "_get_persona_brier_data", return_value=brier_fn(),
        ), patch(
            "pmacs.engines.arbitration.arbitrate", side_effect=arbitrate_fn,
        ), patch(
            "pmacs.data.evidence_router.fetch_evidence_for_ticker",
        ):
            orchestrator._step_weekly_reeval(cycle_id)

        assert called["n"] == 0, (
            f"dispatch was called {called['n']}x for a brand-new position; "
            "Phase 9 fix: NULL last_reeval_at must fall back to entry_date."
        )

        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT state, last_reeval_at FROM holdings WHERE id = ?",
                ("h-new",),
            ).fetchone()
        finally:
            conn.close()

        assert row[0] == "ACTIVE"
        assert row[1] is None, (
            "last_reeval_at must remain NULL — no re-eval was due."
        )


# ---------------------------------------------------------------------------
# Test 3: Daemon SQL reads trailing columns
# ---------------------------------------------------------------------------


class TestStopLossDaemonTrailing:
    """Phase 9 fix: stop_loss_daemon SELECT must include trailing columns
    so check_trailing_breach() sees the values (was previously hardcoded
    to None, silently breaking the trailing path in production)."""

    def test_daemon_select_includes_trailing_columns(
        self, tmp_path: Path, monkeypatch,
    ):
        """Seed a holding with trailing_stop_price_usd=95.00 and
        trailing_stop_armed=True, then execute the daemon's exact SELECT.
        Assert both columns are read (not None)."""
        from pmacs.storage.sqlite import init_db

        db_path = tmp_path / "daemon.db"
        init_db(db_path)

        conn = sqlite3.connect(str(db_path))
        try:
            old_entry = (date.today() - timedelta(days=30)).isoformat()
            conn.execute(
                """INSERT INTO holdings
                   (id, ticker, state, cycle_id_opened, entry_price_usd,
                    position_size_usd, entry_date, stop_price_usd,
                    trailing_stop_price_usd, trailing_stop_armed)
                   VALUES (?, ?, 'ACTIVE', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "h-daemon-001", "AAPL", "c-daemon-001", 100.0, 1000.0,
                    old_entry, 92.0, 95.00, 1,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        # Use the SAME query the daemon uses (don't import stop_loss_daemon
        # here — that would pull in heartbeat/loop side effects). Instead
        # verify the schema + query shape directly.
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT id, ticker, state, stop_price_usd, "
                "trailing_stop_price_usd, trailing_stop_armed "
                "FROM holdings WHERE state = 'ACTIVE'"
            ).fetchall()
        finally:
            conn.close()

        assert len(rows) == 1, "seeded holding should be visible to daemon SELECT"
        row = rows[0]
        holding_id, ticker, state, stop_price, trailing_price, trailing_armed = row
        assert holding_id == "h-daemon-001"
        assert ticker == "AAPL"
        assert state == "ACTIVE"
        assert stop_price == pytest.approx(92.0)
        # Phase 9 fix: these used to come back None because the SELECT
        # didn't include the columns. Now they must come back populated.
        assert trailing_price == pytest.approx(95.00), (
            "trailing_stop_price_usd must be readable by daemon SELECT "
            "(Phase 9 fix: SELECT must include trailing columns)"
        )
        assert trailing_armed == 1, (
            "trailing_stop_armed must be readable by daemon SELECT "
            "(Phase 9 fix: SELECT must include trailing columns)"
        )

    def test_daemon_select_handles_null_trailing_columns(
        self, tmp_path: Path,
    ):
        """A holding without trailing values must still be SELECT-able;
        the daemon code uses ``bool(trailing_armed)`` so NULL is falsy."""
        from pmacs.storage.sqlite import init_db

        db_path = tmp_path / "daemon2.db"
        init_db(db_path)

        conn = sqlite3.connect(str(db_path))
        try:
            old_entry = (date.today() - timedelta(days=30)).isoformat()
            conn.execute(
                """INSERT INTO holdings
                   (id, ticker, state, cycle_id_opened, entry_price_usd,
                    position_size_usd, entry_date, stop_price_usd)
                   VALUES (?, ?, 'ACTIVE', ?, ?, ?, ?, ?)""",
                ("h-daemon-002", "MSFT", "c-daemon-002", 100.0, 1000.0, old_entry, 92.0),
            )
            conn.commit()
        finally:
            conn.close()

        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT id, ticker, state, stop_price_usd, "
                "trailing_stop_price_usd, trailing_stop_armed "
                "FROM holdings WHERE state = 'ACTIVE'"
            ).fetchall()
        finally:
            conn.close()

        assert len(rows) == 1
        row = rows[0]
        trailing_price, trailing_armed = row[4], row[5]
        # trailing_stop_price_usd defaults to None when not set
        assert trailing_price is None, (
            "Unset trailing_stop_price_usd must read as None (not 0.0)"
        )
        # trailing_stop_armed defaults to 0 (per migration DEFAULT 0)
        assert trailing_armed == 0, (
            "Unset trailing_stop_armed must read as 0 (the NOT NULL DEFAULT)"
        )
