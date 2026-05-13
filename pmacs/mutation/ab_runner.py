"""Shadow A/B test runner for Mutation Engine (Architecture.md §10).

Manages concurrent A/B tests between control (production) and
candidate (shadow) arms. Max 3 concurrent tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_CONCURRENT_AB = 3  # fallback; prefer config.mutation.max_ab_tests


@dataclass
class ABOutcome:
    proposal_id: str
    cycle_id: str
    arm: str  # "control" or "candidate"
    metric_name: str
    metric_value: float


@dataclass
class ABState:
    proposal_id: str
    status: str  # "RUNNING", "COMPLETE", "FAILED"
    control_outcomes: list[float] = field(default_factory=list)
    candidate_outcomes: list[float] = field(default_factory=list)
    started_at: datetime | None = None


class ABRunner:
    """Manages shadow A/B test execution.

    Caps concurrent tests at max_concurrent (default 3).
    Candidate arm always runs SHADOW-only (Architecture.md §16 anti-pattern).
    """

    def __init__(
        self,
        max_concurrent: int | None = None,
        config: Any | None = None,
        db_path: Path | None = None,
    ) -> None:
        if max_concurrent is not None:
            self.max_concurrent = max_concurrent
        elif config is not None:
            self.max_concurrent = config.max_ab_tests
        else:
            self.max_concurrent = MAX_CONCURRENT_AB
        self._db_path = db_path
        self._active: dict[str, ABState] = {}
        if db_path is not None:
            self._ensure_outcomes_table()
            self._restore_from_db()

    def start(self, proposal_id: str) -> bool:
        """Start A/B test for a proposal. Returns False if max concurrent reached."""
        if len(self._active) >= self.max_concurrent:
            return False
        self._active[proposal_id] = ABState(
            proposal_id=proposal_id,
            status="RUNNING",
            started_at=datetime.now(timezone.utc),
        )
        self._persist_start(proposal_id)
        return True

    def record_outcome(self, proposal_id: str, arm: str, value: float, *, cycle_id: str = "") -> None:
        """Record an outcome for an A/B test arm."""
        if proposal_id not in self._active:
            return
        state = self._active[proposal_id]
        if arm == "control":
            state.control_outcomes.append(value)
        else:
            state.candidate_outcomes.append(value)

        # Persist to SQLite for crash recovery
        self._persist_outcome(proposal_id, cycle_id, arm, value)

    def get_state(self, proposal_id: str) -> ABState | None:
        """Get the current state of an A/B test."""
        return self._active.get(proposal_id)

    def complete(self, proposal_id: str) -> ABState | None:
        """Complete an A/B test and return final state."""
        if proposal_id not in self._active:
            return None
        state = self._active.pop(proposal_id)
        state.status = "COMPLETE"
        self._persist_complete(proposal_id)
        return state

    @property
    def active_count(self) -> int:
        return len(self._active)

    def can_start(self) -> bool:
        return len(self._active) < self.max_concurrent

    def _restore_from_db(self) -> None:
        """Recover in-flight A/B tests from SQLite after restart."""
        if self._db_path is None:
            return
        import sqlite3

        conn = sqlite3.connect(str(self._db_path))
        try:
            rows = conn.execute(
                "SELECT id FROM mutation_proposals WHERE status = 'RUNNING_AB'"
            ).fetchall()
            for (pid,) in rows:
                if pid not in self._active:
                    state = ABState(
                        proposal_id=pid, status="RUNNING"
                    )
                    # Load accumulated outcomes from mutation_outcomes
                    try:
                        outcome_rows = conn.execute(
                            "SELECT arm, metric_value FROM mutation_outcomes "
                            "WHERE proposal_id = ?",
                            (pid,),
                        ).fetchall()
                        for arm, value in outcome_rows:
                            if arm == "control":
                                state.control_outcomes.append(value)
                            else:
                                state.candidate_outcomes.append(value)
                    except sqlite3.OperationalError:
                        pass  # Table may not exist yet
                    self._active[pid] = state
        finally:
            conn.close()

    def _ensure_outcomes_table(self) -> None:
        """Create mutation_outcomes table if it doesn't exist."""
        if self._db_path is None:
            return
        import sqlite3

        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS mutation_outcomes "
                "(proposal_id TEXT, cycle_id TEXT, arm TEXT, "
                "metric_name TEXT, metric_value REAL, recorded_at TEXT)"
            )
            conn.commit()
        finally:
            conn.close()

    def _persist_outcome(self, proposal_id: str, cycle_id: str, arm: str, value: float) -> None:
        """Write a single outcome to mutation_outcomes table."""
        if self._db_path is None:
            return
        import sqlite3

        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                conn.execute(
                    "INSERT INTO mutation_outcomes "
                    "(proposal_id, cycle_id, arm, metric_name, metric_value, recorded_at) "
                    "VALUES (?, ?, ?, 'brier', ?, ?)",
                    (proposal_id, cycle_id, arm, value, datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
            finally:
                conn.close()
        except sqlite3.OperationalError:
            pass  # Table may not exist yet

    def _persist_start(self, proposal_id: str) -> None:
        """Record A/B start in SQLite."""
        if self._db_path is None:
            return
        import sqlite3

        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "UPDATE mutation_proposals SET status = 'RUNNING_AB', "
                "started_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), proposal_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _persist_complete(self, proposal_id: str) -> None:
        """Record A/B completion in SQLite."""
        if self._db_path is None:
            return
        import sqlite3

        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "UPDATE mutation_proposals SET status = 'AB_COMPLETE', "
                "completed_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), proposal_id),
            )
            conn.commit()
        finally:
            conn.close()
