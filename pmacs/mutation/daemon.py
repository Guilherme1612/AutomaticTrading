"""pmacs-mutation daemon process (Architecture.md §10).

Dormant for first 50 PAPER cycles, then activates to:
detect failure clusters, generate candidates, stage A/B tests,
collect outcomes, run stat tests, and recommend promotions.

All promotions require operator TOTP. No auto-promote.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pmacs.mutation.ab_runner import ABRunner
from pmacs.mutation.candidate_generator import generate_candidates
from pmacs.mutation.rollback import regression_detected
from pmacs.mutation.stat_test import welch_t_test

ACTIVATION_CYCLE_THRESHOLD = 50  # fallback; prefer config.mutation.min_paper_cycles


def mode_too_early(paper_cycle_count: int, config: Any | None = None) -> bool:
    """Check if mutation engine should remain dormant."""
    threshold = config.min_paper_cycles if config is not None else ACTIVATION_CYCLE_THRESHOLD
    return paper_cycle_count < threshold


class MutationDaemon:
    """Full mutation lifecycle orchestrator (Architecture.md §10.4).

    Each call to ``run_cycle`` executes one iteration:
    dormancy check → detect FDE clusters → generate candidates →
    stage proposals → activate A/B → collect outcomes → evaluate →
    stage for review → monitor rollbacks.
    """

    def __init__(
        self,
        config: Any,
        db_path: Path,
        audit_path: Path,
        registry_path: Path,
        sse_publisher: Any | None = None,
    ) -> None:
        self._config = config
        self._db_path = db_path
        self._audit_path = audit_path
        self._registry_path = registry_path
        self._sse = sse_publisher
        self._runner = ABRunner(config=config, db_path=db_path)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run_cycle(self, cycle_id: str, paper_cycle_count: int) -> None:
        """Execute one mutation daemon iteration.

        Args:
            cycle_id: Current cycle ID for audit logging.
            paper_cycle_count: Number of completed PAPER cycles.
        """
        # 1. Dormancy check
        if mode_too_early(paper_cycle_count, config=self._config):
            return

        # 2. Detect FDE failure clusters
        clusters = self._detect_failure_clusters()

        # 3. Generate candidates
        candidates = generate_candidates(
            clusters, paper_cycle_count, config=self._config
        )

        # 4. Stage proposals in SQLite
        for candidate in candidates:
            self._stage_proposal(candidate, cycle_id)

        # 5. Activate A/B tests for PROPOSED proposals
        self._activate_pending_ab_tests(cycle_id)

        # 6. Collect outcomes (from running A/B tests)
        self._collect_outcomes(cycle_id)

        # 7. Evaluate completed A/B tests with stat test
        self._evaluate_completed_tests(cycle_id)

        # 8. Stage significant results for operator review
        self._stage_for_review(cycle_id)

        # 9. Monitor rollbacks for promoted mutations
        self._monitor_rollbacks(cycle_id)

    # ------------------------------------------------------------------
    # Private methods
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _detect_failure_clusters(self) -> list[dict]:
        """Read recent FDE failure clusters from SQLite.

        Queries mutation-relevant failure taxonomies aggregated
        over the last 30 cycles.
        """
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT fde_cluster_trigger, COUNT(*) as cnt
                FROM mutation_proposals
                WHERE proposed_at > datetime('now', '-30 days')
                GROUP BY fde_cluster_trigger
            """).fetchall()
            return [
                {"taxonomy": row[0], "count": row[1]}
                for row in rows
                if row[0] is not None
            ]
        finally:
            conn.close()

    def _stage_proposal(self, candidate: Any, cycle_id: str) -> None:
        """Insert a candidate as PROPOSED into mutation_proposals."""
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO mutation_proposals "
                "(id, dimension, target, baseline_value, candidate_value, "
                "status, fde_cluster_trigger, proposed_at) "
                "VALUES (?, ?, ?, ?, ?, 'PROPOSED', ?, ?)",
                (
                    candidate.id,
                    candidate.dimension,
                    candidate.target,
                    candidate.baseline_config,
                    candidate.candidate_config,
                    candidate.trigger_taxonomy,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _activate_pending_ab_tests(self, cycle_id: str) -> None:
        """Start A/B for any PROPOSED proposals."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT id FROM mutation_proposals WHERE status = 'PROPOSED'"
            ).fetchall()
            for (proposal_id,) in rows:
                if self._runner.start(proposal_id):
                    conn.execute(
                        "UPDATE mutation_proposals SET status = 'RUNNING_AB', "
                        "started_at = ? WHERE id = ?",
                        (datetime.now(timezone.utc).isoformat(), proposal_id),
                    )
            conn.commit()
        finally:
            conn.close()

    def _collect_outcomes(self, cycle_id: str) -> None:
        """Record one outcome per running A/B test for this cycle.

        In production, this reads actual Brier/PnL metrics from DuckDB.
        For now, reads from mutation_outcomes table or is a no-op.
        """
        # Placeholder: in production wiring, this reads real metrics
        # and calls self._runner.record_outcome() for each active test.
        pass

    def _evaluate_completed_tests(self, cycle_id: str) -> None:
        """Run stat test on A/B tests with enough samples."""
        min_sample = self._config.min_sample_size
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT id FROM mutation_proposals WHERE status = 'RUNNING_AB'"
            ).fetchall()
            for (proposal_id,) in rows:
                state = self._runner.get_state(proposal_id)
                if state is None:
                    continue
                n = min(len(state.control_outcomes), len(state.candidate_outcomes))
                if n < min_sample:
                    continue

                result = welch_t_test(
                    state.control_outcomes,
                    state.candidate_outcomes,
                    alpha=self._config.p_value_threshold,
                    min_cohens_d=self._config.cohens_d_threshold,
                    min_sample=min_sample,
                )

                # Update proposal with results
                if result.is_significant:
                    new_status = "READY_FOR_REVIEW"
                else:
                    new_status = "REJECTED"

                conn.execute(
                    "UPDATE mutation_proposals SET status = ?, "
                    "effect_size = ?, p_value = ?, sample_size = ?, "
                    "completed_at = ? WHERE id = ?",
                    (
                        new_status,
                        result.cohens_d,
                        result.p_value,
                        result.sample_size,
                        datetime.now(timezone.utc).isoformat(),
                        proposal_id,
                    ),
                )
                self._runner.complete(proposal_id)
            conn.commit()
        finally:
            conn.close()

    def _stage_for_review(self, cycle_id: str) -> None:
        """Publish SSE events for proposals ready for operator review."""
        if self._sse is None:
            return
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT id, dimension, target, effect_size, p_value "
                "FROM mutation_proposals WHERE status = 'READY_FOR_REVIEW'"
            ).fetchall()
            for row in rows:
                self._sse.publish("mutation", "mutation.ready_for_review", {
                    "proposal_id": row[0],
                    "dimension": row[1],
                    "target": row[2],
                    "effect_size": row[3],
                    "p_value": row[4],
                })
        finally:
            conn.close()

    def _monitor_rollbacks(self, cycle_id: str) -> None:
        """Check promoted mutations for regression."""
        probation = self._config.probation_cycles
        window = self._config.auto_rollback_window
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT id, proposed_at FROM mutation_proposals "
                "WHERE status = 'OPERATOR_PROMOTED'"
            ).fetchall()
            for (proposal_id, proposed_at) in rows:
                # Count cycles since promotion
                cycle_rows = conn.execute(
                    "SELECT COUNT(*) FROM cycles "
                    "WHERE closed_at > ? AND state = 'CLOSED'",
                    (proposed_at,),
                ).fetchone()
                cycles_since = cycle_rows[0] if cycle_rows else 0

                # Regression detection uses synthetic metrics placeholder
                # In production, reads actual Brier/PnL from DuckDB
                # For now, skip auto-rollback (operator-triggered only)
                _ = cycles_since
                _ = regression_detected(
                    promoted_cycles_ago=cycles_since,
                    probation_cycles=probation,
                    post_metric=0.0,  # placeholder
                    baseline_metric=0.0,  # placeholder
                    rollback_window=window,
                )
        finally:
            conn.close()


def main_loop() -> None:
    """Legacy entry point — uses default config paths.

    In production, pmacs-mutation runs as a daemon process with this loop.
    """
    from pmacs.config import CONFIG_DIR, load_config

    cfg = load_config()
    daemon = MutationDaemon(
        config=cfg.mutation,
        db_path=Path("/var/db/pmacs/pmacs.db"),
        audit_path=Path("/var/log/pmacs/audit.log"),
        registry_path=CONFIG_DIR / "model_registry.json",
    )

    while True:
        # In production: await next cycle event from nervous
        time.sleep(60)
