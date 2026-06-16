"""pmacs-mutation daemon process (Architecture.md §10).

Dormant for first 50 PAPER cycles, then activates to:
detect failure clusters, generate candidates, stage A/B tests,
collect outcomes, run stat tests, and recommend promotions.

All promotions require an explicit operator action. No auto-promote.
"""
from __future__ import annotations

import logging
import sqlite3  # noqa: F811 — kept for type refs

from pmacs.storage.sqlite import connect as _sql_connect
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pmacs.constants import MUTATION_ACTIVATION_CYCLES
from pmacs.logsys import log_debug
from pmacs.mutation.ab_runner import ABRunner
from pmacs.mutation.candidate_generator import generate_candidates
from pmacs.mutation.rollback import execute_rollback, regression_detected
from pmacs.mutation.stat_test import welch_t_test

logger = logging.getLogger(__name__)


def mode_too_early(paper_cycle_count: int, config: Any | None = None) -> bool:
    """Check if mutation engine should remain dormant."""
    threshold = config.min_paper_cycles if config is not None else MUTATION_ACTIVATION_CYCLES
    return paper_cycle_count < threshold


def _read_duckdb_metric(duckdb_path: Path, proposal_id: str, arm: str) -> float | None:
    """Read the latest Brier or PnL metric for an A/B arm from DuckDB.

    Returns None if DuckDB is unavailable or no data exists.
    DuckDB is the analytics store for resolution history and rolling metrics
    (Architecture.md §9 storage layer).
    """
    try:
        import duckdb

        conn = duckdb.connect(str(duckdb_path), read_only=True)
        try:
            # Query rolling Brier score for the arm's persona/dimension
            row = conn.execute(
                "SELECT AVG(brier_score) FROM resolution_metrics "
                "WHERE proposal_id = ? AND arm = ? AND evaluated = true",
                [proposal_id, arm],
            ).fetchone()
            if row is not None and row[0] is not None:
                return float(row[0])
        finally:
            conn.close()
    except Exception:
        pass
    return None


class MutationDaemon:
    """Full mutation lifecycle orchestrator (Architecture.md §10.4).

    Each call to ``run_cycle`` executes one iteration:
    dormancy check -> detect FDE clusters -> generate candidates ->
    stage proposals -> activate A/B -> collect outcomes -> evaluate ->
    stage for review -> monitor rollbacks.
    """

    def __init__(
        self,
        config: Any,
        db_path: Path,
        audit_path: Path,
        registry_path: Path,
        sse_publisher: Any | None = None,
        duckdb_path: Path | None = None,
    ) -> None:
        self._config = config
        self._db_path = db_path
        self._audit_path = audit_path
        self._registry_path = registry_path
        self._sse = sse_publisher
        self._duckdb_path = duckdb_path
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

        try:
            # 2. Detect FDE failure clusters from real FDE data
            clusters = self._detect_failure_clusters()

            # 3. Generate candidates from clusters
            candidates = generate_candidates(
                clusters, paper_cycle_count, config=self._config
            )

            # 4. Stage proposals in SQLite
            for candidate in candidates:
                self._stage_proposal(candidate, cycle_id)
                if self._sse is not None:
                    self._sse.publish("mutation", "mutation.proposed", {
                        "mutation_id": candidate.id,
                        "candidate_name": candidate.target,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "dimension": candidate.dimension,
                        "trigger_taxonomy": candidate.trigger_taxonomy,
                        "trigger_count": candidate.trigger_count,
                    })

            # 5. Activate A/B tests for PROPOSED proposals
            self._activate_pending_ab_tests(cycle_id)

            # 6. Collect outcomes from running A/B tests
            self._collect_outcomes(cycle_id)

            # 7. Evaluate completed A/B tests with stat test
            self._evaluate_completed_tests(cycle_id)

            # 8. Stage significant results for operator review
            self._stage_for_review(cycle_id)

            # 9. Monitor rollbacks for promoted mutations
            self._monitor_rollbacks(cycle_id)

        except sqlite3.Error as exc:
            log_debug(
                "MUTATION_CYCLE_SQLITE_ERROR",
                payload={"error": str(exc), "cycle_id": cycle_id},
                level="WARN",
                error_code="SQLITE_WRITE_FAILED",
                cycle_id=cycle_id,
                msg=f"Mutation daemon SQLite error: {exc}",
            )
        except Exception as exc:
            log_debug(
                "MUTATION_CYCLE_ERROR",
                payload={"error": str(exc), "cycle_id": cycle_id},
                level="WARN",
                error_code="MUTATION_SANITY_CHECK_FAILED",
                cycle_id=cycle_id,
                msg=f"Mutation daemon cycle error: {exc}",
            )

    # ------------------------------------------------------------------
    # Private methods
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        conn = _sql_connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _detect_failure_clusters(self) -> list[dict]:
        """Read recent FDE failure clusters from SQLite.

        Queries failure_classifications table for FDE taxonomy counts
        aggregated over the last 30 cycles. Falls back to scanning
        mutation_proposals if failure_classifications table absent.
        """
        conn = self._get_conn()
        try:
            # Primary: read from FDE classifications (failure_diagnostic engine output)
            try:
                rows = conn.execute("""
                    SELECT taxonomy, COUNT(*) as cnt
                    FROM failure_classifications
                    WHERE classified_at > datetime('now', '-30 days')
                    GROUP BY taxonomy
                """).fetchall()
                clusters = [
                    {"taxonomy": row[0], "count": row[1]}
                    for row in rows
                    if row[0] is not None
                ]
                if clusters:
                    return clusters
            except sqlite3.OperationalError:
                pass  # Table may not exist yet

            # Fallback: scan mutation_proposals for previously detected clusters
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
                    if self._sse is not None:
                        self._sse.publish("mutation", "mutation.ab_started", {
                            "mutation_id": proposal_id,
                            "candidate_name": proposal_id,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
            conn.commit()
        finally:
            conn.close()

    def _collect_outcomes(self, cycle_id: str) -> None:
        """Record one outcome per running A/B test for this cycle.

        Reads Brier/PnL metrics from DuckDB analytics store for each
        active A/B test arm. Falls back to reading from SQLite
        mutation_outcomes table if DuckDB is unavailable.
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT id FROM mutation_proposals WHERE status = 'RUNNING_AB'"
            ).fetchall()
        finally:
            conn.close()

        for (proposal_id,) in rows:
            # Try DuckDB first (production path)
            if self._duckdb_path is not None:
                control_val = _read_duckdb_metric(
                    self._duckdb_path, proposal_id, "control"
                )
                candidate_val = _read_duckdb_metric(
                    self._duckdb_path, proposal_id, "candidate"
                )
                if control_val is not None:
                    self._runner.record_outcome(proposal_id, "control", control_val)
                if candidate_val is not None:
                    self._runner.record_outcome(proposal_id, "candidate", candidate_val)
                if self._sse is not None:
                    state = self._runner.get_state(proposal_id)
                    if state is not None:
                        self._sse.publish("mutation", "mutation.ab_progress", {
                            "mutation_id": proposal_id,
                            "candidate_name": proposal_id,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "control_n": len(state.control_outcomes),
                            "candidate_n": len(state.candidate_outcomes),
                        })
                continue

            # Fallback: read from SQLite mutation_outcomes table
            conn2 = self._get_conn()
            try:
                outcome_rows = conn2.execute(
                    "SELECT arm, metric_value FROM mutation_outcomes "
                    "WHERE proposal_id = ? AND cycle_id = ?",
                    (proposal_id, cycle_id),
                ).fetchall()
                for arm, value in outcome_rows:
                    self._runner.record_outcome(proposal_id, arm, value)
            except sqlite3.OperationalError:
                pass  # Table may not exist yet
            finally:
                conn2.close()

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

                # SSE events for A/B completion
                if self._sse is not None:
                    ts = datetime.now(timezone.utc).isoformat()
                    self._sse.publish("mutation", "mutation.ab_complete", {
                        "mutation_id": proposal_id,
                        "candidate_name": proposal_id,
                        "timestamp": ts,
                        "effect_size": result.cohens_d,
                        "p_value": result.p_value,
                        "sample_size": result.sample_size,
                        "significant": result.is_significant,
                    })
                    if new_status == "REJECTED":
                        self._sse.publish("mutation", "mutation.rejected", {
                            "mutation_id": proposal_id,
                            "candidate_name": proposal_id,
                            "timestamp": ts,
                            "reason": "stat_test_not_significant",
                            "p_value": result.p_value,
                            "effect_size": result.cohens_d,
                        })
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
        """Check promoted mutations for regression.

        Reads post-promotion metrics from DuckDB and compares to baseline.
        Triggers auto-rollback if regression detected after probation period
        and within the rollback window.
        """
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
                try:
                    cycle_rows = conn.execute(
                        "SELECT COUNT(*) FROM cycles "
                        "WHERE closed_at > ? AND state = 'CLOSED'",
                        (proposed_at,),
                    ).fetchone()
                    cycles_since = cycle_rows[0] if cycle_rows else 0
                except sqlite3.OperationalError:
                    cycles_since = 0

                if cycles_since == 0:
                    continue

                # Read post-promotion metric from DuckDB (lower Brier is better)
                post_metric = None
                baseline_metric = None
                if self._duckdb_path is not None:
                    post_metric = _read_duckdb_metric(
                        self._duckdb_path, proposal_id, "candidate"
                    )
                    baseline_metric = _read_duckdb_metric(
                        self._duckdb_path, proposal_id, "control"
                    )

                # If no DuckDB metrics available, skip auto-rollback
                if post_metric is None or baseline_metric is None:
                    continue

                if regression_detected(
                    promoted_cycles_ago=cycles_since,
                    probation_cycles=probation,
                    post_metric=post_metric,
                    baseline_metric=baseline_metric,
                    lower_is_better=True,
                    rollback_window=window,
                ):
                    execute_rollback(
                        proposal_id,
                        reason="auto_rollback: regression after probation",
                        db_path=self._db_path,
                        audit_path=self._audit_path,
                        sse_publisher=self._sse,
                        cycle_id=cycle_id,
                        registry_path=self._registry_path,
                    )
                    log_debug(
                        "MUTATION_AUTO_ROLLBACK",
                        payload={
                            "proposal_id": proposal_id,
                            "cycles_since": cycles_since,
                            "post_metric": post_metric,
                            "baseline_metric": baseline_metric,
                        },
                        level="WARN",
                        error_code="MUTATION_ROLLBACK_FAILED",
                        cycle_id=cycle_id,
                        msg=f"Auto-rollback triggered for {proposal_id}",
                    )
        finally:
            conn.close()


def main_loop() -> None:
    """Legacy entry point -- uses default config paths.

    In production, pmacs-mutation runs as a daemon process with this loop.
    Activation gate: skips everything until cycle count >= 50 (config/mutation.toml).
    """
    from pmacs.config import CONFIG_DIR, data_dir, load_config

    cfg = load_config()
    mutation_cfg = cfg.mutation
    d = data_dir()

    db_path = d / "pmacs.db"
    audit_path = d / "audit.log"
    registry_path = CONFIG_DIR / "model_registry.json"
    duckdb_path = d / "pmacs_analytics.duckdb"

    daemon = MutationDaemon(
        config=mutation_cfg,
        db_path=db_path,
        audit_path=audit_path,
        registry_path=registry_path,
        duckdb_path=duckdb_path,
    )

    log_debug(
        "PROCESS_START",
        payload={"process": "pmacs-mutation", "activation_threshold": mutation_cfg.min_paper_cycles},
        level="INFO",
        msg="pmacs-mutation daemon starting",
    )

    # Write initial heartbeat so pmacs status shows RUNNING immediately
    try:
        from pmacs.cortex.health import write_heartbeat as _wh
        _wh("pmacs-mutation", heartbeat_dir=db_path.parent / "heartbeats")
    except Exception:
        pass

    while True:
        cycle_id = f"mutation-{int(time.time())}"

        # Read paper cycle count from SQLite
        paper_cycle_count = 0
        try:
            conn = _sql_connect(db_path)
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM cycles WHERE state = 'CLOSED'"
                ).fetchone()
                if row is not None:
                    paper_cycle_count = row[0]
            finally:
                conn.close()
        except Exception:
            pass  # DB unavailable -- treat as 0 cycles

        log_debug(
            "MUTATION_DAEMON_ITERATION",
            payload={"cycle_id": cycle_id, "paper_cycle_count": paper_cycle_count},
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Mutation daemon iteration {cycle_id}",
        )

        try:
            daemon.run_cycle(cycle_id, paper_cycle_count)
        except Exception as exc:
            log_debug(
                "MUTATION_DAEMON_LOOP_ERROR",
                payload={"error": str(exc), "cycle_id": cycle_id},
                level="WARN",
                error_code="MUTATION_DAEMON_LOOP_ERROR",
                cycle_id=cycle_id,
                msg=f"Mutation daemon loop error: {exc}",
            )

        # Write heartbeat so pmacs status can detect this process is alive
        try:
            from pmacs.cortex.health import write_heartbeat as _wh
            from pmacs.config import data_dir as _data_dir
            _wh("pmacs-mutation", heartbeat_dir=_data_dir() / "heartbeats")
        except Exception:
            pass

        time.sleep(60)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    main_loop()
