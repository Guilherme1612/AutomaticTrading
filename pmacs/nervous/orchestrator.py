"""Nervous orchestrator -- cycle lifecycle management (Architecture.md §4.4, §9).

Full CycleOrchestrator class with CycleLock, step dispatch, checkpoint resume,
kill switch guard, clock drift check, flywheel health, pre-cycle pipeline
(steps 2-3, 6-12), and audit chain.

Step numbers follow Architecture.md §9 cycle orchestration sequence:
  0   -- initiate_cycle
  0.5 -- clock drift check
  1   -- checkpoint resume
  2   -- FX snapshot (ECB rate)
  3   -- corporate actions (splits/dividends)
  4   -- kill switch check
  5   -- flywheel health snapshot
  6   -- macro regime classification
  7   -- catalyst resolution detection
  8   -- universe sync (halted/delisted check)
  9   -- gatekeeper admittance filter
  10  -- lessons flagger
  11  -- override learning
  12  -- queue composition
  13  -- symbol processing (stub for future waves)
  14  -- post-cycle processing (stub for future waves)
  29  -- close cycle
  30  -- release lock (implicit via CycleLock.__exit__)
"""
from __future__ import annotations

import fcntl
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from pmacs.cortex.clock_monitor import check_ntp_drift
from pmacs.cortex.kill_switch import is_engaged
from pmacs.engines.flywheel_health import snapshot_health
from pmacs.logsys import log_debug
from pmacs.nervous.checkpoint import is_completed, load_checkpoint, save_checkpoint
from pmacs.nervous.sse_publisher import SSEPublisher
from pmacs.schemas.system import Mode
from pmacs.storage.audit import AuditWriter


class CycleLockError(Exception):
    """Raised when the cycle lock cannot be acquired (another cycle is running)."""


class KillSwitchEngagedError(Exception):
    """Raised when attempting to initiate a cycle while kill switch is engaged."""


class ClockDriftError(Exception):
    """Raised when NTP drift exceeds the safe threshold."""


class CycleLock:
    """File-based exclusive lock using fcntl.flock (LOCK_EX | LOCK_NB).

    Non-blocking: raises CycleLockError immediately if another process holds it.
    Auto-releases on context manager exit (even on crash).

    Args:
        lock_path: Path to the lock file. Parent directories must exist.
    """

    def __init__(self, lock_path: str | Path = "/tmp/pmacs_cycle.lock") -> None:
        self._lock_path = Path(lock_path)
        self._fd = None

    def __enter__(self) -> "CycleLock":
        self._fd = open(self._lock_path, "w")
        try:
            fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._fd.close()
            self._fd = None
            raise CycleLockError(
                "Cannot acquire cycle lock — another cycle is already running"
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            self._fd.close()
            self._fd = None


class CycleOrchestrator:
    """Full cycle orchestrator with step dispatch, idempotency, and locking.

    Args:
        db_path: Path to the SQLite database.
        audit_path: Optional path to the audit log file.
        sse_publisher: Optional SSE publisher instance.
        config: Optional config dict for overrides.
    """

    def __init__(
        self,
        db_path: Path,
        audit_path: Path | None = None,
        sse_publisher: SSEPublisher | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._db_path = db_path
        self._audit_path = audit_path
        self._sse_publisher = sse_publisher
        self._config = config or {}
        self._lock_path: str = self._config.get(
            "lock_path", "/tmp/pmacs_cycle.lock"
        )
        self._clock_drift_threshold: float = float(
            self._config.get("clock_drift_threshold", 60.0)
        )

        # Step dispatch: maps step number to handler method.
        # Steps 0 and 29 use module-level initiate_cycle/close_cycle directly.
        # Steps 6-12 and 13-28 are handled by _run_pre_cycle/_run_symbol/_run_post_cycle.
        self._step_dispatch: dict[int, Callable] = {
            0: self._step_clock_drift,
            1: self._step_checkpoint_resume,
            4: self._step_kill_switch,
            5: self._step_flywheel_health,
        }

        # Pre-cycle pipeline state (populated by _run_pre_cycle)
        self._fx_rate: Any | None = None
        self._macro_regime_result: Any | None = None
        self._gatekeeper_results: dict[str, Any] = {}
        self._queue: list[Any] = []
        self._universe_tickers: list[str] = []

    # -- Public API --

    def run_cycle(self, trigger: str) -> str:
        """Main entry point. Acquires lock, runs steps 0-30, returns cycle_id.

        Steps:
            0   -- Create cycle row in SQLite, emit SSE + audit
            0.5 -- Check NTP clock drift
            1   -- Resume from checkpoint if mid-cycle crash occurred
            2-3 -- FX snapshot + corporate actions
            4   -- Kill switch gate (must be AFTER lock acquisition)
            5   -- Flywheel health snapshot
            6-12 -- Pre-cycle data pipeline + queue composition
            13  -- Symbol processing (stub)
            14  -- Post-cycle processing (stub)
            29  -- Close cycle in SQLite, emit SSE + audit
            30  -- Release lock (implicit via CycleLock.__exit__)

        Args:
            trigger: What triggered this cycle (e.g. 'TIMER', 'OPERATOR').

        Returns:
            The cycle_id of the completed cycle.

        Raises:
            CycleLockError: If another cycle is already running.
            KillSwitchEngagedError: If kill switch is engaged.
            ClockDriftError: If NTP drift exceeds threshold.
        """
        cycle_id: str = ""

        with CycleLock(self._lock_path):
            try:
                # Step 0 — Initiate cycle
                cycle_id = initiate_cycle(trigger, self._db_path, self._audit_path)
                self._publish_sse("cycle", "cycle.opened", {
                    "cycle_id": cycle_id,
                    "trigger": trigger,
                })
                op_seq = 0
                self._mark_op_complete(cycle_id, op_seq, "initiate_cycle")

                # Step 0.5 — Clock drift check
                op_seq = 1
                if not self._skip_if_complete(cycle_id, op_seq):
                    self._step_clock_drift(cycle_id)
                    self._mark_op_complete(cycle_id, op_seq, "clock_drift_check")

                # Step 1 — Checkpoint resume (determine starting point)
                op_seq = 2
                if not self._skip_if_complete(cycle_id, op_seq):
                    self._step_checkpoint_resume(cycle_id, op_seq)
                    self._mark_op_complete(cycle_id, op_seq, "checkpoint_resume")

                # Step 4 -- Kill switch check (MUST be after lock acquisition)
                op_seq = 4
                if not self._skip_if_complete(cycle_id, op_seq):
                    self._step_kill_switch(cycle_id)
                    self._mark_op_complete(cycle_id, op_seq, "kill_switch_check")

                # Step 5 — Flywheel health snapshot
                op_seq = 5
                if not self._skip_if_complete(cycle_id, op_seq):
                    self._step_flywheel_health(cycle_id)
                    self._mark_op_complete(cycle_id, op_seq, "flywheel_health")

                # Steps 2-3 (FX + corp actions), 6-12 (pipeline + queue):
                # pre-cycle data pipeline + queue composition
                op_seq = self._run_pre_cycle(cycle_id, 3)

                # Steps 13a-13p: symbol processing (stub)
                op_seq = self._run_symbol(cycle_id, None, op_seq)

                # Steps 14-28: post-cycle steps (stub)
                op_seq = self._run_post_cycle(cycle_id, op_seq)

                # Step 29 — Close cycle
                op_seq = 29
                if not self._skip_if_complete(cycle_id, op_seq):
                    close_cycle(cycle_id, self._db_path, self._audit_path)
                    self._publish_sse("cycle", "cycle.closed", {
                        "cycle_id": cycle_id,
                    })
                    self._mark_op_complete(cycle_id, op_seq, "close_cycle")

                log_debug(
                    "CYCLE_COMPLETED",
                    payload={"cycle_id": cycle_id, "trigger": trigger},
                    level="INFO",
                    cycle_id=cycle_id,
                    msg=f"Cycle completed: {cycle_id[:8]}",
                )

                return cycle_id

            except (KillSwitchEngagedError, ClockDriftError) as exc:
                # Abort cycle — emit aborted event and re-raise
                if cycle_id:
                    self._publish_sse("cycle", "cycle.aborted", {
                        "cycle_id": cycle_id,
                        "reason": str(exc),
                    })
                    if self._audit_path is not None:
                        writer = AuditWriter(self._audit_path)
                        writer.append(
                            "cycle_aborted",
                            {"cycle_id": cycle_id, "reason": str(exc)},
                            cycle_id=cycle_id,
                        )
                        writer.close()
                raise

    # -- Step implementations --

    def _step_clock_drift(self, cycle_id: str) -> None:
        """Step 0.5: Check NTP drift. Abort if over threshold."""
        triggered, drift_s = check_ntp_drift(threshold=self._clock_drift_threshold)

        log_debug(
            "CYCLE_CLOCK_DRIFT_CHECK",
            payload={
                "drift_s": drift_s,
                "threshold_s": self._clock_drift_threshold,
                "triggered": triggered,
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Clock drift check: {drift_s}s drift (threshold {self._clock_drift_threshold}s)"
            if drift_s is not None
            else "Clock drift check: NTP unavailable, skipping",
        )

        if triggered:
            log_debug(
                "CYCLE_ABORTED_CLOCK_DRIFT",
                payload={"drift_s": drift_s, "threshold_s": self._clock_drift_threshold},
                level="WARN",
                error_code="CLOCK_DRIFT_DETECTED",
                cycle_id=cycle_id,
                msg=f"Cycle aborted: clock drift {drift_s}s exceeds {self._clock_drift_threshold}s",
            )
            raise ClockDriftError(
                f"Clock drift {drift_s}s exceeds safe threshold {self._clock_drift_threshold}s"
            )

    def _step_checkpoint_resume(self, cycle_id: str, op_seq: int) -> None:
        """Step 1: Load checkpoint state for resume awareness."""
        state = load_checkpoint(cycle_id, self._db_path)
        if state is not None:
            log_debug(
                "CYCLE_RESUME_FROM_CHECKPOINT",
                payload={
                    "cycle_id": cycle_id,
                    "resume_op_seq": state.op_seq,
                    "resume_op_type": state.op_type,
                },
                level="INFO",
                cycle_id=cycle_id,
                msg=f"Resuming from checkpoint: op_seq={state.op_seq} op_type={state.op_type}",
            )
        else:
            log_debug(
                "CYCLE_FRESH_START",
                payload={"cycle_id": cycle_id},
                level="INFO",
                cycle_id=cycle_id,
                msg="Fresh cycle start, no checkpoint found",
            )

    def _step_kill_switch(self, cycle_id: str) -> None:
        """Step 4: Check kill switch. Abort if engaged."""
        if is_engaged(self._db_path):
            log_debug(
                "CYCLE_ABORTED_KILL_SWITCH",
                payload={"cycle_id": cycle_id},
                level="WARN",
                error_code="KILL_SWITCH_ENGAGED",
                cycle_id=cycle_id,
                msg="Cycle aborted: kill switch is engaged",
            )
            raise KillSwitchEngagedError(
                "Cannot continue cycle: kill switch is engaged"
            )

    def _step_flywheel_health(self, cycle_id: str) -> None:
        """Step 5: Snapshot flywheel health for cycle context."""
        snap = snapshot_health(
            rolling_brier_avg=0.0,
            rolling_sharpe=0.0,
            calibration_gap=0.0,
        )
        log_debug(
            "CYCLE_FLYWHEEL_HEALTH",
            payload={
                "cycle_id": cycle_id,
                "rolling_brier_avg": snap.rolling_brier_avg,
                "rolling_sharpe": snap.rolling_sharpe,
                "calibration_gap": snap.calibration_gap,
                "active_mutations": snap.active_mutations,
                "pending_reviews": snap.pending_reviews,
                "lessons_count": snap.lessons_count,
            },
            level="INFO",
            cycle_id=cycle_id,
            msg="Flywheel health snapshot recorded",
        )

    def _run_pre_cycle(self, cycle_id: str, start_op_seq: int) -> int:
        """Steps 3, 6-12: Pre-cycle data pipeline and queue composition.

        op_seq 3 = FX snapshot + corporate actions (Architecture.md steps 2-3)
        op_seq 4-5 handled by run_cycle (kill switch, flywheel health)
        op_seq 6-12 = macro regime, catalysts, universe, gatekeeper,
                      lessons, overrides, queue composition

        Returns next op_seq (13) after pre-cycle block completes.
        Each step checks idempotency via _skip_if_complete before executing.
        """
        # op_seq 3: FX snapshot (Architecture.md step 2)
        op_seq = 3
        if not self._skip_if_complete(cycle_id, op_seq):
            self._step_fx_snapshot(cycle_id)
            self._step_corporate_actions(cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "fx_snapshot")

        # Step 6: Macro regime
        op_seq = 6
        if not self._skip_if_complete(cycle_id, op_seq):
            self._step_macro_regime(cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "macro_regime")

        # Step 7: Catalyst resolution
        op_seq = 7
        if not self._skip_if_complete(cycle_id, op_seq):
            self._step_catalyst_resolution(cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "catalyst_resolution")

        # Step 8: Universe sync
        op_seq = 8
        if not self._skip_if_complete(cycle_id, op_seq):
            self._step_universe_sync(cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "universe_sync")

        # Step 9: Gatekeeper
        op_seq = 9
        if not self._skip_if_complete(cycle_id, op_seq):
            self._step_gatekeeper(cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "gatekeeper")

        # Step 10: Lessons flagger
        op_seq = 10
        if not self._skip_if_complete(cycle_id, op_seq):
            self._step_lessons_flagger(cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "lessons_flagger")

        # Step 11: Override learning
        op_seq = 11
        if not self._skip_if_complete(cycle_id, op_seq):
            self._step_override_learning(cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "override_learning")

        # Step 12: Queue composition
        op_seq = 12
        if not self._skip_if_complete(cycle_id, op_seq):
            self._step_queue_composition(cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "queue_composition")

        return 13  # Next op_seq after pre-cycle block

    # -- Pre-cycle step implementations --

    def _step_fx_snapshot(self, cycle_id: str) -> None:
        """Step 2: Fetch ECB EUR/USD rate and store in fx_snapshots."""
        from pmacs.data.fx import fetch_ecb_rate
        from pmacs.schemas.currency import FxRate

        try:
            rate: FxRate = fetch_ecb_rate()
        except Exception as exc:
            log_debug(
                "CYCLE_ABORTED_FX_UNAVAILABLE",
                payload={"cycle_id": cycle_id, "error": str(exc)},
                level="WARN",
                error_code="FX_RATE_UNAVAILABLE",
                cycle_id=cycle_id,
                msg=f"Cycle aborted: FX rate unavailable: {exc}",
            )
            raise RuntimeError(f"FX_RATE_UNAVAILABLE: {exc}") from exc

        self._fx_rate = rate
        now = datetime.now(timezone.utc).isoformat()

        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "INSERT OR REPLACE INTO fx_snapshots "
                "(cycle_id, fetched_at, business_date, usd_per_eur) "
                "VALUES (?, ?, ?, ?)",
                (cycle_id, now, str(rate.business_date), rate.usd_per_eur),
            )
            conn.commit()
        finally:
            conn.close()

        log_debug(
            "CYCLE_FX_SNAPSHOT",
            payload={
                "cycle_id": cycle_id,
                "usd_per_eur": rate.usd_per_eur,
                "business_date": str(rate.business_date),
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"FX snapshot: usd_per_eur={rate.usd_per_eur}",
        )

    def _step_corporate_actions(self, cycle_id: str) -> None:
        """Step 3: Check for splits/dividends on active holdings."""
        from pmacs.data.corp_actions import adjust_cost_basis_for_dividend, adjust_price_for_split

        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                rows = conn.execute(
                    "SELECT id, ticker, entry_price_usd, position_size_usd "
                    "FROM holdings WHERE state = 'OPEN'"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.OperationalError:
            rows = []

        # Stub: no real split/dividend data source yet.
        # Log how many active holdings were checked.
        log_debug(
            "CYCLE_CORP_ACTIONS",
            payload={
                "cycle_id": cycle_id,
                "active_holdings_count": len(rows),
                "actions_applied": 0,
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Corporate actions checked for {len(rows)} active holdings (none applied)",
        )

    def _step_macro_regime(self, cycle_id: str) -> None:
        """Step 6: Run MacroRegime persona and store result."""
        from pmacs.agents.macro_regime import MacroRegimeRunner

        runner = MacroRegimeRunner(cycle_id=cycle_id)
        result = runner.run(evidence=[], episodic_context=None)

        if result is not None:
            self._macro_regime_result = result
            log_debug(
                "CYCLE_MACRO_REGIME",
                payload={
                    "cycle_id": cycle_id,
                    "persona": "macro_regime",
                    "status": "completed",
                },
                level="INFO",
                cycle_id=cycle_id,
                msg="MacroRegime persona completed",
            )
        else:
            log_debug(
                "CYCLE_MACRO_REGIME_ABORTED",
                payload={"cycle_id": cycle_id, "persona": "macro_regime"},
                level="WARN",
                error_code="ABORTED_LLM",
                cycle_id=cycle_id,
                msg="MacroRegime persona aborted (LLM failure), continuing",
            )

    def _step_catalyst_resolution(self, cycle_id: str) -> None:
        """Step 7: Detect resolved catalysts."""
        from pmacs.data.resolution.detector import CatalystResolutionDetector

        detector = CatalystResolutionDetector()
        resolved = detector.run_all(self._db_path)

        log_debug(
            "CYCLE_CATALYST_RESOLUTION",
            payload={
                "cycle_id": cycle_id,
                "resolved_count": len(resolved),
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Catalyst resolution: {len(resolved)} catalysts resolved",
        )

    def _step_universe_sync(self, cycle_id: str) -> None:
        """Step 8: Get current universe and check for halted/delisted."""
        from pmacs.data.universe import get_universe

        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                entries = get_universe(conn, include_halted=False)
            finally:
                conn.close()
        except sqlite3.OperationalError:
            # Universe table may not exist yet (pre-bootstrap)
            entries = []

        self._universe_tickers = [e.ticker for e in entries]
        halted = [e.ticker for e in entries if e.halted]

        log_debug(
            "CYCLE_UNIVERSE_SYNC",
            payload={
                "cycle_id": cycle_id,
                "universe_size": len(self._universe_tickers),
                "halted_count": len(halted),
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Universe sync: {len(self._universe_tickers)} tickers active, "
                f"{len(halted)} halted",
        )

    def _step_gatekeeper(self, cycle_id: str) -> None:
        """Step 9: Run gatekeeper admittance filter on universe."""
        from pmacs.agents.gatekeeper import gate
        from pmacs.config import PMACSConfig, RiskConfig

        # Build a config-like object for gate()
        config = self._build_gate_config()
        results: dict[str, Any] = {}

        for ticker in self._universe_tickers:
            gk_result = gate(
                ticker=ticker,
                cycle_id=cycle_id,
                db_path=self._db_path,
                config=config,
            )
            results[ticker] = gk_result

        admitted = sum(1 for r in results.values() if r.admitted)
        rejected = sum(1 for r in results.values() if not r.admitted)

        self._gatekeeper_results = results

        log_debug(
            "CYCLE_GATEKEEPER",
            payload={
                "cycle_id": cycle_id,
                "admitted": admitted,
                "rejected": rejected,
                "total": len(results),
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Gatekeeper: {admitted} admitted, {rejected} rejected "
                f"out of {len(results)}",
        )

    def _step_lessons_flagger(self, cycle_id: str) -> None:
        """Step 10: Daily flagging of resolution patterns for lessons."""
        # Reads from resolution history -- stub until resolution data flows.
        log_debug(
            "CYCLE_LESSONS_FLAGGER",
            payload={"cycle_id": cycle_id, "lessons_flagged": 0},
            level="INFO",
            cycle_id=cycle_id,
            msg="Lessons flagger: checked resolution patterns (stub)",
        )

    def _step_override_learning(self, cycle_id: str) -> None:
        """Step 11: Cluster recent operator overrides."""
        from pmacs.engines.override_learning import cluster_overrides

        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                rows = conn.execute(
                    "SELECT original_verdict, override_verdict, ticker "
                    "FROM operator_overrides "
                    "ORDER BY id DESC LIMIT 50"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.OperationalError:
            rows = []

        overrides = [
            {"from_verdict": r[0], "to_verdict": r[1], "ticker": r[2]}
            for r in rows
        ]
        clusters = cluster_overrides(overrides)

        log_debug(
            "CYCLE_OVERRIDE_LEARNING",
            payload={
                "cycle_id": cycle_id,
                "override_count": len(overrides),
                "cluster_count": len(clusters),
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Override learning: {len(clusters)} clusters from "
                f"{len(overrides)} overrides",
        )

    def _step_queue_composition(self, cycle_id: str) -> None:
        """Step 12: Compose queue from gatekeeper results + pins."""
        from pmacs.engines.queue import compose_queue
        from pmacs.schemas.queue import QueueItem

        # Load persistent pins
        pinned_tickers: list[str] = []
        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                pin_rows = conn.execute(
                    "SELECT ticker FROM persistent_pins"
                ).fetchall()
                pinned_tickers = [r[0] for r in pin_rows]
            finally:
                conn.close()
        except sqlite3.OperationalError:
            pass

        queue = compose_queue(
            universe_tickers=self._universe_tickers,
            pinned_tickers=pinned_tickers,
            cycle_id=cycle_id,
            gatekeeper_results=self._gatekeeper_results,
        )

        # Write queue to SQLite
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(self._db_path))
        try:
            for item in queue:
                conn.execute(
                    "INSERT OR REPLACE INTO queue "
                    "(cycle_id, ticker, priority_band, pinned, enqueued_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        item.cycle_id,
                        item.ticker,
                        int(item.priority_band),
                        int(item.pinned),
                        item.enqueued_at or now,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        self._queue = queue

        self._publish_sse("cycle", "queue.composed", {
            "cycle_id": cycle_id,
            "queue_size": len(queue),
            "pinned_count": sum(1 for q in queue if q.pinned),
        })

        log_debug(
            "CYCLE_QUEUE_COMPOSED",
            payload={
                "cycle_id": cycle_id,
                "queue_size": len(queue),
                "pinned_count": sum(1 for q in queue if q.pinned),
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Queue composed: {len(queue)} items",
        )

    def _build_gate_config(self) -> Any:
        """Build a config-like object compatible with gate() ConfigLike protocol.

        Tries to load full PMACSConfig; falls back to a minimal mock.
        """
        try:
            from pmacs.config import load_config
            return load_config()
        except Exception:
            # Minimal config for testing / missing config files
            from dataclasses import dataclass, field

            @dataclass
            class _MockRisk:
                max_concurrent_positions: int = 5

            @dataclass
            class _MockConfig:
                risk: _MockRisk = field(default_factory=_MockRisk)  # type: ignore[assignment]

            return _MockConfig()

    def _run_symbol(
        self, cycle_id: str, queue_item: Any | None, op_seq: int
    ) -> int:
        """Steps 13a-13p: Symbol processing. Stub for future waves."""
        log_debug(
            "CYCLE_SYMBOL_PROCESSING_STUB",
            payload={"cycle_id": cycle_id, "op_seq": op_seq},
            level="INFO",
            cycle_id=cycle_id,
            msg="Symbol processing stub — no symbols processed in this wave",
        )
        return 14  # Next op_seq after symbol block

    def _run_post_cycle(self, cycle_id: str, op_seq: int) -> int:
        """Steps 14-28: Post-cycle processing. Stub for future waves."""
        log_debug(
            "CYCLE_POST_PROCESSING_STUB",
            payload={"cycle_id": cycle_id, "op_seq": op_seq},
            level="INFO",
            cycle_id=cycle_id,
            msg="Post-cycle processing stub — no post-cycle ops in this wave",
        )
        return 29  # Next op_seq after post-cycle block

    # -- Idempotency helpers --

    def _mark_op_complete(
        self, cycle_id: str, op_seq: int, op_type: str
    ) -> None:
        """Write completed operation to op_idempotency table."""
        save_checkpoint(cycle_id, op_seq, op_type, self._db_path)

    def _skip_if_complete(self, cycle_id: str, op_seq: int) -> bool:
        """Check idempotency — skip if op already completed."""
        return is_completed(cycle_id, op_seq, self._db_path)

    # -- SSE helper --

    def _publish_sse(
        self, stream: str, event_type: str, data: dict[str, Any]
    ) -> None:
        """Publish SSE event if publisher is available."""
        if self._sse_publisher is not None:
            self._sse_publisher.publish(stream, event_type, data)

    # -- Mode helper --

    @staticmethod
    def _current_mode(db_path: Path) -> str:
        """Read current mode from mode_history or default to INSTALLING."""
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT to_mode FROM mode_history ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                return row[0]
        finally:
            conn.close()
        return Mode.INSTALLING.value


# -- Module-level functions (backward compat) --


def initiate_cycle(trigger: str, db_path: Path, audit_path: Path | None = None) -> str:
    """Open a new cycle.

    Checks kill switch first -- raises KillSwitchEngagedError if engaged.
    Creates cycle_id (UUID4), inserts into SQLite, emits SSE and audit events.

    Args:
        trigger: What triggered this cycle (e.g. 'TIMER', 'OPERATOR').
        db_path: Path to the SQLite database.
        audit_path: Optional path to the audit log file.

    Returns:
        The newly created cycle_id.

    Raises:
        KillSwitchEngagedError: If the kill switch is currently engaged.
    """
    # Kill switch gate -- must check BEFORE any state mutation
    if is_engaged(db_path):
        log_debug(
            "CYCLE_OPEN_BLOCKED_KILL_SWITCH",
            payload={"trigger": trigger},
            level="WARN",
            error_code="KILL_SWITCH_ENGAGED",
            msg="Cycle initiation blocked: kill switch is engaged",
        )
        raise KillSwitchEngagedError("Cannot initiate cycle: kill switch is engaged")

    cycle_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    mode = _current_mode(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO cycles (cycle_id, opened_at, closed_at, state, trigger, mode) "
            "VALUES (?, ?, NULL, 'OPEN', ?, ?)",
            (cycle_id, now, trigger, mode),
        )
        conn.commit()
    finally:
        conn.close()

    # Emit SSE event via the publisher (import here to avoid circular imports)
    from pmacs.nervous.sse_publisher import SSEPublisher as _SSEPublisher

    _publisher: _SSEPublisher | None = getattr(initiate_cycle, "_publisher", None)  # type: ignore[attr-defined]
    if _publisher is not None:
        _publisher.publish("cycle", "cycle.open", {
            "cycle_id": cycle_id,
            "trigger": trigger,
            "mode": mode,
            "opened_at": now,
        })

    # Write audit event
    if audit_path is not None:
        writer = AuditWriter(audit_path)
        writer.append("cycle_opened", {
            "cycle_id": cycle_id,
            "trigger": trigger,
            "mode": mode,
            "opened_at": now,
        })
        writer.close()

    log_debug(
        "CYCLE_OPENED",
        payload={"cycle_id": cycle_id, "trigger": trigger, "mode": mode},
        level="INFO",
        cycle_id=cycle_id,
        msg=f"Cycle opened: {cycle_id[:8]} trigger={trigger}",
    )

    return cycle_id


def close_cycle(cycle_id: str, db_path: Path, audit_path: Path | None = None) -> None:
    """Close an open cycle.

    Updates SQLite state to CLOSED, emits SSE and audit events.

    Args:
        cycle_id: The cycle to close.
        db_path: Path to the SQLite database.
        audit_path: Optional path to the audit log file.
    """
    now = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE cycles SET state = 'CLOSED', closed_at = ? WHERE cycle_id = ?",
            (now, cycle_id),
        )
        conn.commit()
    finally:
        conn.close()

    # Emit SSE event
    from pmacs.nervous.sse_publisher import SSEPublisher as _SSEPublisher

    _publisher: _SSEPublisher | None = getattr(close_cycle, "_publisher", None)  # type: ignore[attr-defined]
    if _publisher is not None:
        _publisher.publish("cycle", "cycle.close", {
            "cycle_id": cycle_id,
            "closed_at": now,
        })

    # Write audit event
    if audit_path is not None:
        writer = AuditWriter(audit_path)
        writer.append("cycle_closed", {
            "cycle_id": cycle_id,
            "closed_at": now,
        }, cycle_id=cycle_id)
        writer.close()

    log_debug(
        "CYCLE_CLOSED",
        payload={"cycle_id": cycle_id, "closed_at": now},
        level="INFO",
        cycle_id=cycle_id,
        msg=f"Cycle closed: {cycle_id[:8]}",
    )


def _current_mode(db_path: Path) -> str:
    """Read current mode from mode_history or default to INSTALLING."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT to_mode FROM mode_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is not None:
            return row[0]
    finally:
        conn.close()
    return Mode.INSTALLING.value
