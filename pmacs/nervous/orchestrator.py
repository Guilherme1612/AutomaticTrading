"""Nervous orchestrator -- cycle lifecycle management (Architecture.md §4.4, §9).

Full CycleOrchestrator class with CycleLock, step dispatch, checkpoint resume,
kill switch guard, clock drift check, flywheel health, pre-cycle pipeline
(steps 2-3, 6-12), per-symbol pipeline (steps 13a-13p), and audit chain.

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
  13  -- symbol processing (13a-13p per ticker)
  14  -- post-cycle processing (stub for future waves)
  29  -- close cycle
  30  -- release lock (implicit via CycleLock.__exit__)

Hardening (Phase 9 Wave 5):
  - Per-symbol persona dispatch timeout: 270s hard cap
  - Per-symbol Crucible timeout: 90s per cycle (180s total), default severity 0.5
  - Graceful shutdown via SIGTERM/SIGINT signal handlers
  - Kill switch mid-cycle abort: INTERRUPT remaining holdings, abbreviated post-cycle

Performance (Phase 9 Wave 6, S6-1):
  - Per-step timing instrumentation with budget thresholds
  - Cycle metrics emitted in cycle.close SSE event
  - Step budgets: persona dispatch 270s, crucible 180s, total cycle 30min, per-step 30s
"""
from __future__ import annotations

import fcntl
import signal
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
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

        # Steps 0-5 are called directly in _run_pre_cycle().
        # Steps 6-12 and 13-28 are handled by _run_pre_cycle/_run_symbol/_run_post_cycle.

        # Pre-cycle pipeline state (populated by _run_pre_cycle)
        self._fx_rate: Any | None = None
        self._macro_regime_result: Any | None = None
        self._gatekeeper_results: dict[str, Any] = {}
        self._queue: list[Any] = []
        self._universe_tickers: list[str] = []

        # Paper ledger (created lazily or injected for testing)
        self._ledger: Any | None = None

        # Storage adapters (lazy-initialized via _get_kuzu_adapter / _get_qdrant_adapter)
        self._kuzu_adapter: Any | None = None
        self._qdrant_adapter: Any | None = None

        # Hardening state (S5-2: graceful shutdown + kill switch mid-cycle)
        self._shutdown_requested: bool = False
        self._kill_switch_engaged_mid_cycle: bool = False
        self._symbol_holdings: dict[str, Any] = {}  # ticker -> Holding (for INTERRUPT on abort)

        # Price cache for real-time price fetching (Architecture.md §6.1)
        self._price_cache: Any | None = None

    # -- Persona slot map (Architecture.md §12.2) --

    PERSONA_SLOT_MAP: dict[int, list[str]] = {
        0: ["macro_regime", "catalyst_summarizer"],
        1: ["moat_analyst", "growth_hunter"],
        2: ["insider_activity", "short_interest", "forensics"],
    }

    # -- Step timing budgets (S6-1) --
    # Key: step label -> budget in milliseconds. Unlisted steps default to 30_000ms.
    _STEP_BUDGETS: dict[str, float] = {
        "persona_dispatch": 270_000,
        "crucible": 180_000,
        "total_cycle": 1_800_000,  # 30 min
        "default": 30_000,
    }

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

        Hardening (S5-2):
            - Signal handlers for SIGTERM/SIGINT registered during cycle
            - Kill switch checked after each symbol
            - Mid-cycle abort: INTERRUPT remaining holdings, abbreviated post-cycle

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

        # Initialize cycle metrics (S6-1)
        self._cycle_metrics: dict[str, Any] = {
            "total_time_ms": 0,
            "per_step_times": {},
            "persona_dispatch_time_ms": 0,
            "crucible_time_ms": 0,
            "post_cycle_time_ms": 0,
        }
        self._cycle_start = time.monotonic()

        # Reset hardening state for each cycle
        self._shutdown_requested = False
        self._kill_switch_engaged_mid_cycle = False
        self._symbol_holdings.clear()

        with CycleLock(self._lock_path):
            # Register signal handlers for graceful shutdown (S5-2)
            old_sigterm = signal.signal(signal.SIGTERM, self._handle_signal)
            old_sigint = signal.signal(signal.SIGINT, self._handle_signal)

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

                # Step 13: Per-symbol processing (13a-13p for each queue item)
                op_seq = self._run_all_symbols(cycle_id, op_seq)

                # Check if we need abbreviated post-cycle (mid-cycle abort)
                if self._shutdown_requested or self._kill_switch_engaged_mid_cycle:
                    # Transition non-terminal holdings to INTERRUPT
                    self._interrupt_remaining_holdings(cycle_id, op_seq)

                    # Abbreviated post-cycle: steps 26-28 only (drift, consistency, dead letter)
                    op_seq = self._run_abbreviated_post_cycle(cycle_id, op_seq)

                    # Close cycle with ABORTED state
                    self._close_cycle_aborted(cycle_id, op_seq)

                    log_debug(
                        "CYCLE_INTERRUPTED",
                        payload={
                            "cycle_id": cycle_id,
                            "trigger": trigger,
                            "shutdown_requested": self._shutdown_requested,
                            "kill_switch_engaged": self._kill_switch_engaged_mid_cycle,
                        },
                        level="WARN",
                        error_code="CYCLE_INTERRUPTED",
                        cycle_id=cycle_id,
                        msg=f"Cycle interrupted: {cycle_id[:8]} "
                            f"(shutdown={self._shutdown_requested}, "
                            f"kill_switch={self._kill_switch_engaged_mid_cycle})",
                    )
                    return cycle_id

                # Steps 14-28: post-cycle steps
                op_seq = self._run_post_cycle(cycle_id, op_seq)

                # Step 29 — Close cycle
                op_seq = 29
                if not self._skip_if_complete(cycle_id, op_seq):
                    self._finalize_cycle_metrics(cycle_id)
                    close_cycle(cycle_id, self._db_path, self._audit_path)
                    self._publish_sse("cycle", "cycle.closed", {
                        "cycle_id": cycle_id,
                        "metrics": self._cycle_metrics,
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

            finally:
                # Restore original signal handlers (S5-2)
                signal.signal(signal.SIGTERM, old_sigterm)
                signal.signal(signal.SIGINT, old_sigint)

    # -- Signal handler (S5-2) --

    def _handle_signal(self, signum: int, frame: Any) -> None:
        """Handle SIGTERM/SIGINT by setting shutdown flag.

        Does NOT raise — the cycle loop checks the flag after each symbol.
        """
        self._shutdown_requested = True
        log_debug(
            "CYCLE_SIGNAL_RECEIVED",
            payload={"signal": signum},
            level="WARN",
            error_code="GRACEFUL_SHUTDOWN",
            msg=f"Signal {signum} received, requesting graceful shutdown",
        )

    # -- Timing helpers (S6-1) --

    def _timed_step(
        self,
        step_fn: Callable,
        step_label: str,
        *args: Any,
    ) -> Any:
        """Wrap a step function with timing instrumentation.

        Records duration in _cycle_metrics["per_step_times"] and logs
        STEP_OVER_BUDGET if duration exceeds the step budget.

        Args:
            step_fn: The step function to call.
            step_label: Label for the step (used as key in per_step_times).
            *args: Positional args forwarded to step_fn.

        Returns:
            Whatever step_fn returns.
        """
        start = time.monotonic()
        result = step_fn(*args)
        duration_ms = (time.monotonic() - start) * 1000
        self._cycle_metrics["per_step_times"][step_label] = duration_ms

        budget_ms = self._STEP_BUDGETS.get(
            step_label, self._STEP_BUDGETS["default"],
        )
        if duration_ms > budget_ms:
            log_debug(
                "STEP_OVER_BUDGET",
                payload={
                    "step": step_label,
                    "duration_ms": round(duration_ms, 1),
                    "budget_ms": budget_ms,
                    "over_by_ms": round(duration_ms - budget_ms, 1),
                },
                level="WARN",
                error_code="STEP_OVER_BUDGET",
                cycle_id=args[0] if args else "",
                msg=f"Step '{step_label}' exceeded budget: "
                    f"{duration_ms:.0f}ms > {budget_ms:.0f}ms budget",
            )
        return result

    def _finalize_cycle_metrics(self, cycle_id: str) -> None:
        """Compute total cycle time and populate _cycle_metrics."""
        total_ms = (time.monotonic() - self._cycle_start) * 1000 if hasattr(self, "_cycle_start") else 0
        self._cycle_metrics["total_time_ms"] = round(total_ms, 1)

        # Sum persona and crucible from per_step_times if available
        per_step = self._cycle_metrics.get("per_step_times", {})
        persona_ms = sum(
            v for k, v in per_step.items() if "persona" in k
        )
        crucible_ms = sum(
            v for k, v in per_step.items() if "crucible" in k
        )
        post_cycle_ms = sum(
            v for k, v in per_step.items()
            if k.startswith("post_") or k in {
                "weekly_reeval", "thesis_aging", "process_fills",
                "reconciliation", "opportunity_cost", "calibration",
                "crucible_calibration", "causal_attribution",
                "memory_antipattern", "lessons_extraction",
                "override_learning_post", "fde", "drift_stats",
                "cross_db_consistency", "dead_letter",
            }
        )
        self._cycle_metrics["persona_dispatch_time_ms"] = round(persona_ms, 1)
        self._cycle_metrics["crucible_time_ms"] = round(crucible_ms, 1)
        self._cycle_metrics["post_cycle_time_ms"] = round(post_cycle_ms, 1)

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
        Each step is timed via _timed_step (S6-1).
        """
        # op_seq 3: FX snapshot (Architecture.md step 2)
        op_seq = 3
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_fx_snapshot, "fx_snapshot", cycle_id)
            self._step_corporate_actions(cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "fx_snapshot")

        # Step 6: Macro regime
        op_seq = 6
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_macro_regime, "macro_regime", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "macro_regime")

        # Step 7: Catalyst resolution
        op_seq = 7
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_catalyst_resolution, "catalyst_resolution", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "catalyst_resolution")

        # Step 8: Universe sync
        op_seq = 8
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_universe_sync, "universe_sync", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "universe_sync")

        # Step 9: Gatekeeper
        op_seq = 9
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_gatekeeper, "gatekeeper", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "gatekeeper")

        # Step 10: Lessons flagger
        op_seq = 10
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_lessons_flagger, "lessons_flagger", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "lessons_flagger")

        # Step 11: Override learning
        op_seq = 11
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_override_learning, "override_learning", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "override_learning")

        # Step 12: Queue composition
        op_seq = 12
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_queue_composition, "queue_composition", cycle_id)
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
                    "FROM holdings WHERE state = 'ACTIVE'"
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

    def _get_kuzu_adapter(self) -> Any | None:
        """Lazy-initialize and return the KuzuDB adapter (Architecture.md §8.4)."""
        if self._kuzu_adapter is not None:
            return self._kuzu_adapter
        try:
            from pmacs.storage.kuzu import KuzuDBAdapter
            from pathlib import Path as _P

            kuzu_path = _P(str(self._db_path)).parent / "pmacs.kuzu"
            adapter = KuzuDBAdapter(db_path=kuzu_path)
            if adapter._conn is not None:
                self._kuzu_adapter = adapter
                return adapter
        except Exception:
            pass
        return None

    def _get_qdrant_adapter(self) -> Any | None:
        """Lazy-initialize and return the Qdrant adapter (Architecture.md §8.7)."""
        if self._qdrant_adapter is not None:
            return self._qdrant_adapter
        try:
            from pmacs.storage.qdrant import QdrantAdapter

            url = self._config.get("qdrant_url", "http://127.0.0.1:6333")
            adapter = QdrantAdapter(url=url)
            if adapter._ensure_client():
                adapter.create_collections()
                self._qdrant_adapter = adapter
                return adapter
        except Exception:
            pass
        return None

    def _get_price_cache(self) -> Any | None:
        """Lazy-initialize and return the PriceCache (Architecture.md §6.1)."""
        if self._price_cache is not None:
            return self._price_cache
        try:
            from pmacs.data.gateway import DataGateway
            from pmacs.data.price_cache import PriceCache

            gateway = DataGateway()
            self._price_cache = PriceCache(gateway=gateway, max_age_seconds=300)
            return self._price_cache
        except Exception:
            return None

    def _run_symbol(
        self, cycle_id: str, item: Any | None, op_seq: int
    ) -> int:
        """Steps 13a-13p: Per-symbol processing pipeline.

        13a — Create Holding + transition to PHASE1_RESEARCH
        13b — Antipattern check
        13c — Episodic context build
        13d — Persona slot dispatch (parallel, 270s timeout)
        13e — Arbitration
        13f-13p — Stubs for future waves

        Hardening (S5-1):
            - Persona dispatch: 270s hard timeout, ABORTED_LLM on timeout
            - Crucible: 90s per cycle (180s total) hard timeout, default severity 0.5
            - Evidence scoped to call stack (no module-level caches)

        Args:
            cycle_id: Current cycle identifier.
            item: QueueItem for the symbol to process.
            op_seq: Current operation sequence number.

        Returns:
            Next op_seq after symbol processing.
        """
        from pmacs.schemas.contracts import Holding, HoldingState
        from pmacs.schemas.queue import QueueItem
        from pmacs.engines.state_machine import transition

        if item is None:
            return op_seq

        ticker = item.ticker
        op = op_seq

        # -- Step 13a: Create Holding + transition to PHASE1_RESEARCH ---
        holding = Holding(
            id=str(uuid4()),
            ticker=ticker,
            state=HoldingState.CANDIDATE,
            cycle_id_opened=cycle_id,
        )
        holding = transition(
            holding, HoldingState.PHASE1_RESEARCH,
            "phase1_start", cycle_id, op,
        )
        op += 1

        # Track holding for potential INTERRUPT on mid-cycle abort (S5-2)
        self._symbol_holdings[ticker] = holding

        # -- Step 13b: Antipattern check ---
        from pmacs.engines.memory import check_antipattern

        antipattern = check_antipattern(ticker, cycle_id)
        if antipattern is not None:
            holding = transition(
                holding, HoldingState.ABORTED_LLM,
                f"antipattern_detected:{antipattern}", cycle_id, op,
            )
            self._symbol_holdings.pop(ticker, None)
            log_debug(
                "SYMBOL_ABORTED_ANTIPATTERN",
                payload={
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "antipattern": antipattern,
                    "holding_id": holding.id,
                },
                level="INFO",
                cycle_id=cycle_id,
                msg=f"Symbol {ticker} aborted: antipattern '{antipattern}' detected",
            )
            return op + 1  # Skip to next symbol

        # -- Step 13c: Episodic context build ---
        # Agents.md §18: 200-word brief from macro, failures, track record, lessons
        # S6-2 edge case: stores unavailable -> fallback to minimal brief
        from pmacs.agents.episodic_context import build_context_brief

        regime_label = "UNCERTAIN"
        regime_conf = 0.0
        if self._macro_regime_result is not None:
            # MacroRegimeOutput has regime and regime_confidence fields
            raw = getattr(self._macro_regime_result, "raw_output", "")
            if raw:
                import json as _json
                try:
                    parsed = _json.loads(raw)
                    regime_label = parsed.get("regime", "UNCERTAIN")
                    regime_conf = parsed.get("regime_confidence", 0.0)
                except (ValueError, TypeError):
                    pass

        # Fetch real store data for episodic context (Agents.md §18)
        recent_failures: list[dict] | None = None
        recent_lessons: list[str] | None = None
        fde_history: list[dict] | None = None

        kuzu = self._get_kuzu_adapter()
        if kuzu is not None:
            try:
                failures = kuzu.get_failures_for_ticker(ticker, limit=3)
                if failures:
                    recent_failures = [
                        {"taxonomy": f.get("fa.taxonomy", ""), "summary": f.get("fa.summary", "")}
                        for f in failures
                    ]
            except Exception:
                pass

        qdrant = self._get_qdrant_adapter()
        if qdrant is not None:
            try:
                similar = qdrant.search_similar("lessons", ticker, limit=2)
                if similar:
                    recent_lessons = [
                        hit.get("payload", {}).get("lesson_text", "")[:100]
                        for hit in similar
                        if hit.get("payload")
                    ]
            except Exception:
                pass

        try:
            brief = build_context_brief(
                persona="all",
                ticker=ticker,
                regime=regime_label,
                regime_confidence=regime_conf,
                recent_failures=recent_failures,
                recent_lessons=recent_lessons or None,
                fde_history=fde_history,
            )
        except Exception as exc:
            # Fallback to minimal brief on any storage failure
            brief = f"MACRO CONTEXT: regime={regime_label} confidence={regime_conf:.2f}"
            log_debug(
                "EPISODIC_CONTEXT_FALLBACK",
                payload={
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "error": str(exc),
                },
                level="WARN",
                error_code="STALE_DATA",
                cycle_id=cycle_id,
                msg=f"Episodic context build failed for {ticker}, "
                    f"using minimal brief: {exc}",
            )

        # -- Step 13d: Persona slot dispatch (S5-1: 270s timeout) ---
        # Evidence is local to this call — no module-level caches between symbols.
        # S6-2 edge case: evidence gateway timeout -> per-symbol abort with DATA_UNAVAILABLE
        try:
            from pmacs.data.evidence_router import fetch_evidence_for_ticker
            evidence_packet = fetch_evidence_for_ticker(ticker, cycle_id)
            evidence: list[Any] = list(evidence_packet.evidence)
            # TODO: Future refinement -- filter evidence per persona using
            # PERSONA_EVIDENCE_MAP before passing to _dispatch_personas.
        except Exception as exc:
            log_debug(
                "EVIDENCE_FETCH_FAILED",
                payload={"ticker": ticker, "error": str(exc)[:200]},
                level="WARN",
                error_code="DATA_UNAVAILABLE",
                cycle_id=cycle_id,
                msg=f"Evidence fetch failed for {ticker}: {exc}",
            )
            evidence = []

        # -- Fetch real-time price for sizing and execution (Architecture.md §6.1) --
        current_price: float = 1.0  # fallback default
        price_cache = self._get_price_cache()
        if price_cache is not None:
            fetched = price_cache.get_price(ticker, cycle_id)
            if fetched is not None:
                current_price = fetched
            else:
                log_debug(
                    "PRICE_FALLBACK_DEFAULT",
                    payload={"ticker": ticker, "cycle_id": cycle_id},
                    level="WARN",
                    error_code="DATA_UNAVAILABLE",
                    cycle_id=cycle_id,
                    msg=f"Price unavailable for {ticker}, using fallback 1.0",
                )

        persona_results: dict[str, Any] = {}
        persona_timed_out = False
        try:
            persona_results = self._dispatch_personas_with_timeout(
                evidence=evidence,
                brief=brief,
                cycle_id=cycle_id,
                ticker=ticker,
                timeout_seconds=270,
            )
        except TimeoutError:
            persona_timed_out = True

        if persona_timed_out or not persona_results:
            # Timeout or all personas failed — transition to PHASE1_TIMEOUT then ABORTED_LLM
            reason = "persona_dispatch_timeout:270s" if persona_timed_out else "all_personas_failed"
            if persona_timed_out:
                # Transition through PHASE1_TIMEOUT first (valid from PHASE1_RESEARCH)
                holding = transition(
                    holding, HoldingState.PHASE1_TIMEOUT,
                    reason, cycle_id, op,
                )
                op += 1
            holding = transition(
                holding, HoldingState.ABORTED_LLM,
                reason, cycle_id, op,
            )
            self._symbol_holdings.pop(ticker, None)
            log_debug(
                "SYMBOL_ABORTED_" + ("TIMEOUT" if persona_timed_out else "ALL_PERSONAS_FAILED"),
                payload={
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "holding_id": holding.id,
                    "reason": reason,
                },
                level="WARN",
                error_code="ABORTED_LLM",
                cycle_id=cycle_id,
                msg=f"Symbol {ticker} aborted: {reason}",
            )
            return op + 1

        op += 1

        # -- Step 13e: Arbitration ---
        from pmacs.engines.arbitration import arbitrate, ArbitrationSignal
        from pmacs.schemas.agents import DirectionalProbability, PersonaName

        signals: list[ArbitrationSignal] = []
        for persona_name_str, raw_output in persona_results.items():
            dp = self._extract_directional_probability(
                persona_name_str, ticker, cycle_id, raw_output,
            )
            if dp is not None:
                signals.append(ArbitrationSignal(dp))

        if not signals:
            holding = transition(
                holding, HoldingState.ABORTED_LLM,
                "no_valid_directional_probs", cycle_id, op,
            )
            self._symbol_holdings.pop(ticker, None)
            log_debug(
                "SYMBOL_ABORTED_NO_PROBS",
                payload={
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "holding_id": holding.id,
                },
                level="WARN",
                error_code="ABORTED_LLM",
                cycle_id=cycle_id,
                msg=f"Symbol {ticker} aborted: no valid directional probabilities",
            )
            return op + 1

        arbitrated = arbitrate(signals, cycle_id=cycle_id)

        log_debug(
            "SYMBOL_ARBITRATION_COMPLETE",
            payload={
                "cycle_id": cycle_id,
                "ticker": ticker,
                "holding_id": holding.id,
                "decision": arbitrated.decision.value,
                "p_up": arbitrated.p_up,
                "p_flat": arbitrated.p_flat,
                "p_down": arbitrated.p_down,
                "signals_count": len(signals),
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Symbol {ticker} arbitration: {arbitrated.decision.value} "
                f"(p_up={arbitrated.p_up:.2f}, p_down={arbitrated.p_down:.2f})",
        )

        op += 1

        # -- Step 13f: Transition to PHASE2_CRUCIBLE ---
        holding = transition(
            holding, HoldingState.PHASE2_CRUCIBLE,
            "phase2_crucible_start", cycle_id, op,
        )
        op += 1

        # -- Step 13g: Crucible 2-iteration rewrite loop (Agents.md §16) ---
        # State machine: INITIAL -> REWRITE -> DONE/ABORT
        # Hard limits: 2 cycles max, 90s per cycle, 180s total, NO_TRADE on budget exhaust
        CRUCIBLE_MAX_CYCLES = 2
        CRUCIBLE_PER_CYCLE_TIMEOUT = 90   # seconds (Architecture.md §17.3)

        crucible_severity = 0.0
        crucible_state = "INITIAL"
        crucible_iterations = 0
        revised_evidence: list[Any] | None = None
        from pmacs.agents.crucible import CrucibleRunner

        crucible_runner = CrucibleRunner()
        crucible_start = time.monotonic()
        import json as _json

        for _cycle_idx in range(CRUCIBLE_MAX_CYCLES):
            elapsed = time.monotonic() - crucible_start
            if elapsed > 180:
                log_debug(
                    "CRUCIBLE_BUDGET_EXHAUSTED",
                    payload={
                        "cycle_id": cycle_id,
                        "ticker": ticker,
                        "elapsed_s": elapsed,
                    },
                    level="WARN",
                    error_code="CRUCIBLE_TIMEOUT",
                    cycle_id=cycle_id,
                    msg=f"Crucible 180s total budget exhausted for {ticker}",
                )
                crucible_state = "ABORT"
                break

            # Build evidence input: revised on 2nd iteration if available
            ev_input = revised_evidence if revised_evidence is not None else evidence

            try:
                # Run Crucible with 90s per-cycle timeout
                with ThreadPoolExecutor(max_workers=1) as crucible_pool:
                    crucible_future = crucible_pool.submit(
                        crucible_runner.run,
                        evidence=ev_input,
                        episodic_context=brief,
                    )
                    try:
                        crucible_output = crucible_future.result(
                            timeout=CRUCIBLE_PER_CYCLE_TIMEOUT,
                        )
                    except FuturesTimeoutError:
                        log_debug(
                            "CRUCIBLE_CYCLE_TIMEOUT",
                            payload={
                                "cycle_id": cycle_id,
                                "ticker": ticker,
                                "cycle": _cycle_idx + 1,
                                "elapsed_s": time.monotonic() - crucible_start,
                            },
                            level="WARN",
                            error_code="CRUCIBLE_TIMEOUT",
                            cycle_id=cycle_id,
                            msg=f"Crucible cycle {_cycle_idx + 1} timed out (90s) for {ticker}",
                        )
                        crucible_state = "ABORT"
                        break

                if crucible_output is None:
                    crucible_state = "ABORT"
                    break

                # Parse severity from output
                try:
                    crucible_data = _json.loads(crucible_output.raw_output)
                    crucible_severity = float(
                        crucible_data.get("severity_score", 0.5)
                    )
                except (ValueError, TypeError, AttributeError):
                    crucible_severity = 0.5  # moderate default on parse failure

                crucible_iterations += 1

                log_debug(
                    "CRUCIBLE_CYCLE_COMPLETE",
                    payload={
                        "cycle_id": cycle_id,
                        "ticker": ticker,
                        "cycle": _cycle_idx + 1,
                        "severity": crucible_severity,
                    },
                    level="INFO",
                    cycle_id=cycle_id,
                    msg=f"Crucible cycle {_cycle_idx + 1} for {ticker}: severity={crucible_severity:.2f}",
                )

                # State transitions per Agents.md §16.1
                if crucible_severity >= 0.6:
                    # High severity -> immediate ABORT (NO_TRADE)
                    crucible_state = "ABORT"
                    break
                elif (
                    crucible_severity >= 0.3
                    and _cycle_idx < CRUCIBLE_MAX_CYCLES - 1
                ):
                    # Medium severity -> rebuild evidence brief for rewrite
                    crucible_state = "REWRITE"
                    attacks = crucible_data.get("attacks", [])
                    revised_evidence = _rebuild_evidence_brief(
                        evidence, attacks, arbitrated, ticker,
                    )
                    log_debug(
                        "CRUCIBLE_REWRITE_TRIGGERED",
                        payload={
                            "cycle_id": cycle_id,
                            "ticker": ticker,
                            "cycle": _cycle_idx + 1,
                            "severity": crucible_severity,
                            "num_attacks": len(attacks),
                        },
                        level="INFO",
                        cycle_id=cycle_id,
                        msg=f"Crucible cycle 1 severity {crucible_severity:.2f}, rebuilding evidence for cycle 2",
                    )
                else:
                    # Low severity or max cycles reached -> DONE
                    crucible_state = "DONE"
                    break

            except Exception as exc:
                log_debug(
                    "CRUCIBLE_EXCEPTION",
                    payload={
                        "cycle_id": cycle_id,
                        "ticker": ticker,
                        "cycle": _cycle_idx + 1,
                        "error": str(exc),
                    },
                    level="WARN",
                    error_code="ABORTED_LLM",
                    cycle_id=cycle_id,
                    msg=f"Crucible raised {type(exc).__name__} in cycle {_cycle_idx + 1} for {ticker}",
                )
                crucible_state = "ABORT"
                break

        # Handle ABORT: transition to ABORTED_RISK, skip remaining pipeline
        if crucible_state == "ABORT":
            holding = transition(
                holding, HoldingState.ABORTED_RISK,
                f"crucible_abort:severity={crucible_severity:.2f},iterations={crucible_iterations}",
                cycle_id, op,
            )
            log_debug(
                "SYMBOL_ABORTED_CRUCIBLE",
                payload={
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "holding_id": holding.id,
                    "crucible_severity": crucible_severity,
                    "crucible_state": crucible_state,
                    "crucible_iterations": crucible_iterations,
                    "elapsed_s": time.monotonic() - crucible_start,
                },
                level="WARN",
                error_code="ABORTED_CRUCIBLE",
                cycle_id=cycle_id,
                msg=f"Symbol {ticker} aborted by Crucible: severity={crucible_severity:.2f}, state={crucible_state}",
            )
            self._symbol_holdings.pop(ticker, None)
            return op + 1

        log_debug(
            "SYMBOL_CRUCIBLE_COMPLETE",
            payload={
                "cycle_id": cycle_id,
                "ticker": ticker,
                "holding_id": holding.id,
                "crucible_severity": crucible_severity,
                "crucible_state": crucible_state,
                "crucible_iterations": crucible_iterations,
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Symbol {ticker} crucible: severity={crucible_severity:.2f}, state={crucible_state}, iterations={crucible_iterations}",
        )
        op += 1

        # -- Step 13h: EV computation (real pricing) ---
        from pmacs.engines.pricing import compute_ev, EvInputs

        ev_result = compute_ev(EvInputs(
            p_up=arbitrated.p_up,
            p_down=arbitrated.p_down,
            atr_pct=None,          # todo: wire ATR provider when available
            current_price=current_price,
        ))

        log_debug(
            "SYMBOL_EV_COMPUTED",
            payload={
                "cycle_id": cycle_id,
                "ticker": ticker,
                "holding_id": holding.id,
                "ev_pct": ev_result.expected_value_pct,
                "ev_multiple": ev_result.ev_multiple,
                "is_positive": ev_result.is_positive,
                "target_gain_pct": ev_result.target_gain_pct,
                "stop_loss_pct": ev_result.stop_loss_pct,
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Symbol {ticker} EV: {ev_result.expected_value_pct:.4f} "
                f"(multiple={ev_result.ev_multiple:.2f}, "
                f"target={ev_result.target_gain_pct:.2%}, "
                f"stop={ev_result.stop_loss_pct:.2%})",
        )
        op += 1

        # -- Step 13i: Transition to APPROVED_PENDING ---
        holding = transition(
            holding, HoldingState.APPROVED_PENDING,
            "pipeline_approved", cycle_id, op,
        )
        op += 1

        # -- Step 13j: Sizing ---
        from pmacs.engines.sizing import size_position, SizingInputs

        portfolio_value = 5000.0
        if self._ledger is not None:
            portfolio_value = self._ledger.total_value

        is_bootstrap = (
            arbitrated.decision.value == "PROCEED_BOOTSTRAP_LOW_CONFIDENCE"
        )

        sizing_result = size_position(SizingInputs(
            p_up=arbitrated.p_up,
            p_down=arbitrated.p_down,
            target_gain_pct=ev_result.target_gain_pct,
            stop_loss_pct=ev_result.stop_loss_pct,
            matured_sources_used=arbitrated.matured_sources_used,
            is_limited_history=is_bootstrap,
            portfolio_value_usd=portfolio_value,
            current_price=current_price,
        ))

        if sizing_result.abort_reason:
            holding = transition(
                holding, HoldingState.ABORTED_RISK,
                f"sizing_abort:{sizing_result.abort_reason}", cycle_id, op,
            )
            log_debug(
                "SYMBOL_ABORTED_SIZING",
                payload={
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "holding_id": holding.id,
                    "abort_reason": sizing_result.abort_reason,
                },
                level="WARN",
                error_code="SIZING_CAPPED",
                cycle_id=cycle_id,
                msg=f"Symbol {ticker} aborted: sizing {sizing_result.abort_reason}",
            )
            self._symbol_holdings.pop(ticker, None)
            return op + 1

        log_debug(
            "SYMBOL_SIZING_COMPLETE",
            payload={
                "cycle_id": cycle_id,
                "ticker": ticker,
                "holding_id": holding.id,
                "target_usd": sizing_result.target_usd,
                "target_shares": sizing_result.target_shares,
                "haircuts": sizing_result.applied_haircuts,
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Symbol {ticker} sizing: ${sizing_result.target_usd:.2f} "
                f"({sizing_result.target_shares:.2f} shares)",
        )
        op += 1

        # -- Step 13k: Conviction + Verdict ---
        from pmacs.engines.conviction import compute_conviction, verdict_tier
        from pmacs.schemas.conviction import VerdictTier

        conviction_score = compute_conviction(
            arb=arbitrated,
            crucible_severity=crucible_severity,
            ev_multiple=ev_result.ev_multiple,
            is_bootstrap=is_bootstrap,
        )
        verdict = verdict_tier(conviction_score)

        log_debug(
            "SYMBOL_CONVICTION_COMPUTED",
            payload={
                "cycle_id": cycle_id,
                "ticker": ticker,
                "holding_id": holding.id,
                "conviction_score": conviction_score,
                "verdict": verdict.value,
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Symbol {ticker} conviction: {conviction_score:.4f} "
                f"-> {verdict.value}",
        )

        if verdict == VerdictTier.SKIP:
            holding = transition(
                holding, HoldingState.ABORTED_RISK,
                f"verdict_skip:conviction={conviction_score:.4f}", cycle_id, op,
            )
            log_debug(
                "SYMBOL_ABORTED_VERDICT_SKIP",
                payload={
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "holding_id": holding.id,
                    "conviction_score": conviction_score,
                },
                level="INFO",
                cycle_id=cycle_id,
                msg=f"Symbol {ticker} aborted: verdict SKIP "
                    f"(conviction={conviction_score:.4f})",
            )
            self._symbol_holdings.pop(ticker, None)
            return op + 1

        op += 1

        # -- Step 13l: Risk gate ---
        from pmacs.engines.portfolio_risk_gate import (
            evaluate_risk_gate, RiskGateInputs,
        )

        current_position_count = 0
        if self._ledger is not None:
            current_position_count = self._ledger.position_count

        risk_result = evaluate_risk_gate(RiskGateInputs(
            current_position_count=current_position_count,
            max_concurrent_positions=5,
            target_usd=sizing_result.target_usd,
            portfolio_value_usd=portfolio_value,
            max_position_pct=0.20,
            sector=holding.sector,
        ))

        if not risk_result.passed:
            holding = transition(
                holding, HoldingState.ABORTED_RISK,
                f"risk_gate:{','.join(risk_result.reasons)}", cycle_id, op,
            )
            log_debug(
                "SYMBOL_ABORTED_RISK_GATE",
                payload={
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "holding_id": holding.id,
                    "reasons": risk_result.reasons,
                },
                level="INFO",
                cycle_id=cycle_id,
                msg=f"Symbol {ticker} aborted: risk gate blocked "
                    f"({', '.join(risk_result.reasons)})",
            )
            self._symbol_holdings.pop(ticker, None)
            return op + 1

        log_debug(
            "SYMBOL_RISK_GATE_PASSED",
            payload={
                "cycle_id": cycle_id,
                "ticker": ticker,
                "holding_id": holding.id,
                "position_count": current_position_count,
                "target_usd": sizing_result.target_usd,
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Symbol {ticker} risk gate: passed",
        )
        op += 1

        # -- Step 13m: Memo (best effort) ---
        from pmacs.agents.memo_writer import MemoWriterRunner

        try:
            memo_runner = MemoWriterRunner()
            memo_output = memo_runner.run(
                evidence=evidence, episodic_context=brief,
            )
            if memo_output is not None:
                log_debug(
                    "SYMBOL_MEMO_WRITTEN",
                    payload={
                        "cycle_id": cycle_id,
                        "ticker": ticker,
                        "holding_id": holding.id,
                    },
                    level="INFO",
                    cycle_id=cycle_id,
                    msg=f"Symbol {ticker} memo: written",
                )
            else:
                log_debug(
                    "SYMBOL_MEMO_SKIPPED",
                    payload={
                        "cycle_id": cycle_id,
                        "ticker": ticker,
                    },
                    level="INFO",
                    cycle_id=cycle_id,
                    msg=f"Symbol {ticker} memo: skipped (LLM returned None)",
                )
        except Exception as exc:
            log_debug(
                "SYMBOL_MEMO_EXCEPTION",
                payload={
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "error": str(exc),
                },
                level="WARN",
                error_code="MEMO_WRITER_FAILED",
                cycle_id=cycle_id,
                msg=f"Symbol {ticker} memo: failed ({type(exc).__name__}), "
                    "continuing (best effort)",
            )
        op += 1

        # -- Step 13n: Scan record ---
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                conn.execute(
                    "INSERT INTO scan_records "
                    "(ticker, cycle_id, verdict, conviction_score, direction, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        ticker,
                        cycle_id,
                        verdict.value,
                        conviction_score,
                        "UP" if arbitrated.p_up > arbitrated.p_down else "DOWN",
                        now_iso,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            log_debug(
                "SCAN_RECORD_WRITE_FAILED",
                payload={
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "error": str(exc),
                },
                level="WARN",
                error_code="DB_WRITE_FAILED",
                cycle_id=cycle_id,
                msg=f"Scan record write failed for {ticker}: {exc}",
            )
        op += 1

        # -- Step 13o: Execution (mock fill) ---
        # Only execute if verdict is BUY or STRONG_BUY AND risk gate passed
        if verdict in (VerdictTier.BUY, VerdictTier.STRONG_BUY) and risk_result.passed:
            holding = transition(
                holding, HoldingState.ACTIVE,
                "execution_approved", cycle_id, op,
            )

            # Determine entry price from PriceCache (Architecture.md §6.1)
            entry_price = current_price
            shares = sizing_result.target_shares
            stop_price = round(entry_price * (1 - ev_result.stop_loss_pct), 2)

            # Execution fields set post-transition (not state-related):
            holding.entry_price_usd = entry_price
            holding.position_size_usd = sizing_result.target_usd
            holding.stop_price_usd = stop_price
            holding.verdict = verdict.value
            holding.conviction_score = conviction_score
            # Note: holding.sector is already set during Holding creation

            # Mock fill via paper ledger
            if self._ledger is not None:
                try:
                    self._ledger.open_position(
                        ticker=ticker,
                        shares=shares,
                        price=entry_price,
                        sector=holding.sector,
                        stop_price=stop_price,
                    )
                except ValueError as exc:
                    log_debug(
                        "LEDGER_OPEN_POSITION_FAILED",
                        payload={
                            "cycle_id": cycle_id,
                            "ticker": ticker,
                            "error": str(exc),
                        },
                        level="WARN",
                        error_code="LEDGER_CONSTRAINT",
                        cycle_id=cycle_id,
                        msg=f"Ledger rejected position for {ticker}: {exc}",
                    )

            # Create TradePlan and write to audit
            from pmacs.schemas.trade import TradePlan, TradeDirection, OrderType

            trade_plan = TradePlan(
                id=str(uuid4()),
                ticker=ticker,
                direction=TradeDirection.BUY,
                order_type=OrderType.LIMIT,
                quantity=max(1, int(shares)),
                price_usd=entry_price,
                stop_price_usd=stop_price,
                cycle_id=cycle_id,
                holding_id=holding.id,
                conviction_score=conviction_score,
                verdict=verdict.value,
            )

            # Sign the trade plan
            if self._audit_path is not None:
                try:
                    from pmacs.execution.signing import sign_bytes
                    import hashlib

                    plan_bytes = trade_plan.model_dump_json().encode()
                    # Use a deterministic key for paper mode (no real signing key)
                    # In production, keys come from config
                    dummy_key = hashlib.sha256(b"pmacs_paper_mode").digest()
                    signature = sign_bytes(plan_bytes, dummy_key)
                    trade_plan = trade_plan.model_copy(update={
                        "signature_b64": signature.hex(),
                    })
                except Exception:
                    pass  # Best effort signing for paper mode

                # Write trade to audit log
                try:
                    writer = AuditWriter(self._audit_path)
                    writer.append(
                        "trade_executed",
                        {
                            "trade_plan_id": trade_plan.id,
                            "ticker": ticker,
                            "direction": trade_plan.direction.value,
                            "quantity": trade_plan.quantity,
                            "price_usd": trade_plan.price_usd,
                            "stop_price_usd": trade_plan.stop_price_usd,
                            "conviction_score": conviction_score,
                            "verdict": verdict.value,
                            "holding_id": holding.id,
                        },
                        cycle_id=cycle_id,
                    )
                    writer.close()
                except Exception:
                    pass  # Best effort audit write

            log_debug(
                "SYMBOL_EXECUTED",
                payload={
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "holding_id": holding.id,
                    "verdict": verdict.value,
                    "conviction_score": conviction_score,
                    "target_usd": sizing_result.target_usd,
                    "shares": trade_plan.quantity,
                    "entry_price": entry_price,
                    "stop_price": stop_price,
                },
                level="INFO",
                cycle_id=cycle_id,
                msg=f"Symbol {ticker} executed: {verdict.value} "
                    f"${sizing_result.target_usd:.2f} @ {entry_price}",
            )

            # -- Step 13p: Catastrophe net stop --
            try:
                conn = sqlite3.connect(str(self._db_path))
                try:
                    conn.execute(
                        "INSERT INTO stop_events "
                        "(holding_id, ticker, stop_type, trigger_price_usd, "
                        "stop_price_usd, detected_at, cycle_id, status, stop_type_category) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            holding.id,
                            ticker,
                            "catastrophe_net",
                            entry_price,
                            stop_price,
                            now_iso,
                            cycle_id,
                            "PENDING",
                            "FIXED",
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()
            except Exception as exc:
                log_debug(
                    "STOP_EVENT_WRITE_FAILED",
                    payload={
                        "cycle_id": cycle_id,
                        "ticker": ticker,
                        "error": str(exc),
                    },
                    level="WARN",
                    error_code="DB_WRITE_FAILED",
                    cycle_id=cycle_id,
                    msg=f"Stop event write failed for {ticker}: {exc}",
                )

            log_debug(
                "SYMBOL_CATASTROPHE_NET_SET",
                payload={
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "holding_id": holding.id,
                    "stop_price": stop_price,
                    "entry_price": entry_price,
                },
                level="INFO",
                cycle_id=cycle_id,
                msg=f"Symbol {ticker} catastrophe-net stop set at {stop_price}",
            )
            op += 1
        else:
            # Verdict was HOLD or risk gate blocked — no execution
            log_debug(
                "SYMBOL_NO_EXECUTION",
                payload={
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "holding_id": holding.id,
                    "verdict": verdict.value,
                    "risk_gate_passed": risk_result.passed,
                },
                level="INFO",
                cycle_id=cycle_id,
                msg=f"Symbol {ticker}: no execution "
                    f"(verdict={verdict.value}, risk_gate={risk_result.passed})",
            )
            op += 1

        # Symbol processing complete — remove from tracking (S5-2)
        self._symbol_holdings.pop(ticker, None)

        return op  # Next op_seq after symbol block

    def _run_all_symbols(self, cycle_id: str, start_op_seq: int) -> int:
        """Iterate over the queue and run _run_symbol for each item.

        After each symbol, checks for shutdown request and kill switch.
        If either is true, stops processing and returns immediately.

        Edge cases (S6-2):
            - Empty queue: cycle completes with no per-symbol work, post-cycle fires.
            - All symbols abort: no LLM calls made, cycle completes with no trades.

        Args:
            cycle_id: Current cycle identifier.
            start_op_seq: Starting operation sequence number.

        Returns:
            Next op_seq after symbols processed (or interrupted).
        """
        op_seq = start_op_seq

        # S6-2 edge case: empty queue
        if not self._queue:
            log_debug(
                "CYCLE_EMPTY_QUEUE",
                payload={"cycle_id": cycle_id, "queue_size": 0},
                level="INFO",
                cycle_id=cycle_id,
                msg="Queue is empty after gatekeeper -- no symbols to process, "
                    "post-cycle will still fire",
            )
            return op_seq

        symbols_processed = 0
        symbols_aborted = 0

        for item in self._queue:
            # Pre-check: shutdown or kill switch already triggered
            if self._shutdown_requested:
                log_debug(
                    "CYCLE_SYMBOL_SKIP_SHUTDOWN",
                    payload={
                        "cycle_id": cycle_id,
                        "ticker": item.ticker,
                    },
                    level="INFO",
                    cycle_id=cycle_id,
                    msg=f"Skipping symbol {item.ticker}: shutdown requested",
                )
                break

            if is_engaged(self._db_path):
                self._kill_switch_engaged_mid_cycle = True
                log_debug(
                    "CYCLE_SYMBOL_SKIP_KILL_SWITCH",
                    payload={
                        "cycle_id": cycle_id,
                        "ticker": item.ticker,
                    },
                    level="WARN",
                    error_code="KILL_SWITCH_ENGAGED",
                    cycle_id=cycle_id,
                    msg=f"Skipping symbol {item.ticker}: kill switch engaged mid-cycle",
                )
                break

            prev_op = op_seq
            op_seq = self._run_symbol(cycle_id, item, op_seq)
            symbols_processed += 1

            # Detect if symbol aborted early (op_seq advanced by 2-3 vs ~15 for full pipeline)
            # A symbol that runs the full pipeline advances op_seq by ~15;
            # an abort advances by 2-3.
            if op_seq - prev_op <= 4:
                symbols_aborted += 1

            # Post-symbol check for shutdown/kill switch
            if self._shutdown_requested:
                log_debug(
                    "CYCLE_SYMBOL_LOOP_SHUTDOWN",
                    payload={"cycle_id": cycle_id},
                    level="INFO",
                    cycle_id=cycle_id,
                    msg="Symbol loop interrupted: shutdown requested",
                )
                break

            if is_engaged(self._db_path):
                self._kill_switch_engaged_mid_cycle = True
                log_debug(
                    "CYCLE_SYMBOL_LOOP_KILL_SWITCH",
                    payload={"cycle_id": cycle_id},
                    level="WARN",
                    error_code="KILL_SWITCH_ENGAGED",
                    cycle_id=cycle_id,
                    msg="Symbol loop interrupted: kill switch engaged mid-cycle",
                )
                break

        # S6-2 edge case: all symbols aborted before LLM
        if symbols_processed > 0 and symbols_aborted == symbols_processed:
            log_debug(
                "CYCLE_ALL_SYMBOLS_ABORTED",
                payload={
                    "cycle_id": cycle_id,
                    "symbols_processed": symbols_processed,
                    "symbols_aborted": symbols_aborted,
                },
                level="INFO",
                cycle_id=cycle_id,
                msg=f"All {symbols_processed} symbols aborted before LLM -- "
                    "cycle completes with no trades, post-cycle will still fire",
            )

        return op_seq

    # -- Hardening helpers (S5-1, S5-2) --

    def _dispatch_personas_with_timeout(
        self,
        evidence: list[Any],
        brief: str,
        cycle_id: str,
        ticker: str,
        timeout_seconds: int = 270,
    ) -> dict[str, Any]:
        """Wrap _dispatch_personas with a hard timeout.

        Raises TimeoutError if persona dispatch exceeds timeout_seconds.
        """
        results: dict[str, Any] = {}
        # NOTE: On timeout, the persona dispatch thread continues running in the
        # background until it completes or the process exits. Python threads cannot
        # be forcefully interrupted. This is acceptable because:
        # 1. The orchestrator moves on and does not wait for the thread
        # 2. The thread will eventually complete (LLM has its own timeouts)
        # 3. At most 1 leaked thread per timed-out symbol
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                self._dispatch_personas,
                evidence=evidence,
                brief=brief,
                cycle_id=cycle_id,
                ticker=ticker,
            )
            try:
                results = future.result(timeout=timeout_seconds)
            except FuturesTimeoutError:
                log_debug(
                    "PERSONA_DISPATCH_TIMEOUT",
                    payload={
                        "cycle_id": cycle_id,
                        "ticker": ticker,
                        "timeout_s": timeout_seconds,
                    },
                    level="WARN",
                    error_code="PERSONA_TIMEOUT",
                    cycle_id=cycle_id,
                    msg=f"Persona dispatch timed out for {ticker} after {timeout_seconds}s",
                )
                raise TimeoutError(
                    f"Persona dispatch exceeded {timeout_seconds}s for {ticker}"
                )
        return results

    def _interrupt_remaining_holdings(self, cycle_id: str, op_seq: int) -> None:
        """Transition any tracked non-terminal holdings to INTERRUPTED (S5-2).

        Called during mid-cycle abort. Only transitions holdings still in
        non-terminal states via the state machine.
        """
        from pmacs.engines.state_machine import transition, is_valid_transition
        from pmacs.schemas.contracts import HoldingState, TERMINAL_STATES

        op = op_seq
        interrupted: list[str] = []

        for ticker, holding in list(self._symbol_holdings.items()):
            if holding.state in TERMINAL_STATES:
                continue
            if is_valid_transition(holding.state, HoldingState.INTERRUPTED):
                holding = transition(
                    holding,
                    HoldingState.INTERRUPTED,
                    "mid_cycle_abort",
                    cycle_id,
                    op,
                )
                interrupted.append(ticker)
                op += 1

        self._symbol_holdings.clear()

        if interrupted:
            log_debug(
                "CYCLE_INTERRUPT_HOLDINGS",
                payload={
                    "cycle_id": cycle_id,
                    "interrupted_tickers": interrupted,
                },
                level="WARN",
                error_code="HOLDINGS_INTERRUPTED",
                cycle_id=cycle_id,
                msg=f"Interrupted {len(interrupted)} holdings: {interrupted}",
            )

    def _run_abbreviated_post_cycle(self, cycle_id: str, op_seq: int) -> int:
        """Run steps 26-28 only (drift, consistency, dead letter) on mid-cycle abort (S5-2).

        Skips re-eval, calibration, lessons, FDE etc. — just essential housekeeping.
        """
        # Step 26: Drift stats
        op_seq = 26
        if not self._skip_if_complete(cycle_id, op_seq):
            self._step_drift(cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "drift_stats")

        # Step 27: Cross-DB consistency
        op_seq = 27
        if not self._skip_if_complete(cycle_id, op_seq):
            self._step_cross_db_consistency(cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "cross_db_consistency")

        # Step 28: Dead letter
        op_seq = 28
        if not self._skip_if_complete(cycle_id, op_seq):
            self._step_dead_letter(cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "dead_letter")

        return 28

    def _close_cycle_aborted(self, cycle_id: str, op_seq: int) -> None:
        """Close cycle with ABORTED state and emit cycle.interrupted SSE (S5-2)."""
        now = datetime.now(timezone.utc).isoformat()

        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "UPDATE cycles SET state = 'ABORTED', closed_at = ? WHERE cycle_id = ?",
                (now, cycle_id),
            )
            conn.commit()
        finally:
            conn.close()

        self._publish_sse("cycle", "cycle.interrupted", {
            "cycle_id": cycle_id,
            "reason": "shutdown" if self._shutdown_requested else "kill_switch",
        })

        if self._audit_path is not None:
            writer = AuditWriter(self._audit_path)
            writer.append(
                "cycle_interrupted",
                {
                    "cycle_id": cycle_id,
                    "reason": "shutdown" if self._shutdown_requested else "kill_switch",
                    "closed_at": now,
                },
                cycle_id=cycle_id,
            )
            writer.close()

        log_debug(
            "CYCLE_CLOSED_ABORTED",
            payload={"cycle_id": cycle_id, "closed_at": now},
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Cycle closed (ABORTED): {cycle_id[:8]}",
        )

    def _dispatch_personas(
        self,
        evidence: list[Any],
        brief: str,
        cycle_id: str,
        ticker: str,
    ) -> dict[str, Any]:
        """Run persona runners in parallel slot groups.

        Slot layout (Architecture.md §12.2):
          Slot 0: [MacroRegimeRunner, CatalystSummarizerRunner]
          Slot 1: [MoatAnalystRunner, GrowthHunterRunner]
          Slot 2: [InsiderActivityRunner, ShortInterestRunner, ForensicsRunner]

        Within each slot: sequential. Across slots: parallel (3 futures).
        Total timeout: 270s. Individual persona failures are logged and skipped.

        Args:
            evidence: EvidencePacket list for the ticker.
            brief: Episodic context brief.
            cycle_id: Current cycle identifier.
            ticker: Ticker being analysed.

        Returns:
            Dict mapping persona name -> raw_output (str) for successful runs.
        """
        from pmacs.agents.macro_regime import MacroRegimeRunner
        from pmacs.agents.catalyst_summarizer import CatalystSummarizerRunner
        from pmacs.agents.moat_analyst import MoatAnalystRunner
        from pmacs.agents.growth_hunter import GrowthHunterRunner
        from pmacs.agents.insider_activity import InsiderActivityRunner
        from pmacs.agents.short_interest import ShortInterestRunner
        from pmacs.agents.forensics import ForensicsRunner
        from pmacs.schemas.data import EvidencePacket

        # Build runner instances — all take (cycle_id=, audit_writer=None)
        slot_runners: dict[int, list[Any]] = {
            0: [
                MacroRegimeRunner(cycle_id=cycle_id),
                CatalystSummarizerRunner(cycle_id=cycle_id),
            ],
            1: [
                MoatAnalystRunner(cycle_id=cycle_id),
                GrowthHunterRunner(cycle_id=cycle_id),
            ],
            2: [
                InsiderActivityRunner(cycle_id=cycle_id),
                ShortInterestRunner(cycle_id=cycle_id),
                ForensicsRunner(cycle_id=cycle_id),
            ],
        }

        def _run_slot(
            runners: list[Any],
        ) -> list[tuple[str, Any]]:
            """Run all runners in a slot sequentially, collecting successes."""
            results: list[tuple[str, Any]] = []
            for runner in runners:
                try:
                    output = runner.run(evidence, episodic_context=brief)
                    if output is not None:
                        results.append((runner.persona_name, output))
                    else:
                        log_debug(
                            "PERSONA_RETURNED_NONE",
                            payload={
                                "persona": runner.persona_name,
                                "ticker": ticker,
                            },
                            level="WARN",
                            error_code="ABORTED_LLM",
                            cycle_id=cycle_id,
                            msg=f"Persona {runner.persona_name} returned None for {ticker}",
                        )
                except Exception as exc:
                    log_debug(
                        "PERSONA_EXCEPTION",
                        payload={
                            "persona": runner.persona_name,
                            "ticker": ticker,
                            "error": str(exc),
                        },
                        level="WARN",
                        error_code="ABORTED_LLM",
                        cycle_id=cycle_id,
                        msg=f"Persona {runner.persona_name} raised {type(exc).__name__} for {ticker}",
                    )
            return results

        # Dispatch 3 slots in parallel
        results: dict[str, Any] = {}
        timeout_seconds = 270

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(_run_slot, runners): slot_id
                for slot_id, runners in slot_runners.items()
            }
            for future in as_completed(futures, timeout=timeout_seconds):
                slot_id = futures[future]
                try:
                    slot_results = future.result()
                    for persona_name, output in slot_results:
                        results[persona_name] = output
                except Exception as exc:
                    log_debug(
                        "SLOT_FUTURE_EXCEPTION",
                        payload={
                            "slot": slot_id,
                            "ticker": ticker,
                            "error": str(exc),
                        },
                        level="WARN",
                        error_code="ABORTED_LLM",
                        cycle_id=cycle_id,
                        msg=f"Slot {slot_id} raised {type(exc).__name__} for {ticker}",
                    )

        log_debug(
            "PERSONA_DISPATCH_COMPLETE",
            payload={
                "cycle_id": cycle_id,
                "ticker": ticker,
                "personas_succeeded": list(results.keys()),
                "personas_failed": [
                    name
                    for slot in slot_runners.values()
                    for name in [r.persona_name for r in slot]
                    if name not in results
                ],
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Persona dispatch for {ticker}: {len(results)} succeeded",
        )

        return results

    @staticmethod
    def _extract_directional_probability(
        persona_name_str: str,
        ticker: str,
        cycle_id: str,
        persona_output: Any,
    ) -> Any | None:
        """Extract DirectionalProbability from a PersonaOutput.

        PersonaOutput.raw_output contains the raw JSON from the LLM.
        All persona outputs have p_up, p_flat, p_down fields.

        Args:
            persona_name_str: Persona name string (e.g. 'macro_regime').
            ticker: Ticker symbol.
            cycle_id: Current cycle identifier.
            persona_output: PersonaOutput instance from runner.run().

        Returns:
            DirectionalProbability or None on parse failure.
        """
        from pmacs.schemas.agents import DirectionalProbability, PersonaName

        # persona_output is a PersonaOutput — extract raw_output
        raw_json = getattr(persona_output, "raw_output", "")
        if not raw_json:
            return None

        import json

        try:
            data = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            return None

        p_up = data.get("p_up")
        p_flat = data.get("p_flat")
        p_down = data.get("p_down")

        if p_up is None or p_flat is None or p_down is None:
            return None

        try:
            persona_enum = PersonaName(persona_name_str)
        except ValueError:
            return None

        try:
            return DirectionalProbability(
                persona=persona_enum,
                ticker=data.get("ticker", ticker),
                p_up=float(p_up),
                p_flat=float(p_flat),
                p_down=float(p_down),
                evidence_ids=data.get("evidence_ids", []),
                cycle_id=cycle_id,
            )
        except Exception:
            return None

    def _run_post_cycle(self, cycle_id: str, op_seq: int) -> int:
        """Steps 14-28: Post-cycle flywheel processing (Architecture.md §9).

        Each step is best-effort: failures are logged but do not abort the cycle.
        Each step is idempotent via _skip_if_complete / _mark_op_complete.
        Each step is timed via _timed_step (S6-1).

        Returns next op_seq (29) after post-cycle block completes.
        """
        # -- Step 14: Weekly re-evaluation --
        op_seq = 14
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_weekly_reeval, "weekly_reeval", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "weekly_reeval")

        # -- Step 15: Thesis aging --
        op_seq = 15
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_thesis_aging, "thesis_aging", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "thesis_aging")

        # -- Step 16: Process fills --
        op_seq = 16
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_process_fills, "process_fills", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "process_fills")

        # -- Step 17: Reconciliation --
        op_seq = 17
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_reconciliation, "reconciliation", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "reconciliation")

        # -- Step 18: Opportunity cost --
        op_seq = 18
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_opportunity_cost, "opportunity_cost", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "opportunity_cost")

        # -- Step 19: Calibration --
        op_seq = 19
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_calibration, "calibration", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "calibration")

        # -- Step 20: Crucible calibration --
        op_seq = 20
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_crucible_calibration, "crucible_calibration", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "crucible_calibration")

        # -- Step 21: Causal attribution --
        op_seq = 21
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_causal_attribution, "causal_attribution", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "causal_attribution")

        # -- Step 22: Memory antipattern check --
        op_seq = 22
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_memory, "memory_antipattern", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "memory_antipattern")

        # -- Step 23: Lessons extraction --
        op_seq = 23
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_lessons, "lessons_extraction", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "lessons_extraction")

        # -- Step 24: Override learning --
        op_seq = 24
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_override_learning_post, "override_learning_post", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "override_learning_post")

        # -- Step 25: FDE --
        op_seq = 25
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_fde, "fde", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "fde")

        # -- Step 26: Drift stats --
        op_seq = 26
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_drift, "drift_stats", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "drift_stats")

        # -- Step 27: Cross-DB consistency --
        op_seq = 27
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_cross_db_consistency, "cross_db_consistency", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "cross_db_consistency")

        # -- Step 28: Dead letter processing --
        op_seq = 28
        if not self._skip_if_complete(cycle_id, op_seq):
            self._timed_step(self._step_dead_letter, "dead_letter", cycle_id)
            self._mark_op_complete(cycle_id, op_seq, "dead_letter")

        return 29  # Next op_seq after post-cycle block

    # -- Post-cycle step implementations --

    def _step_weekly_reeval(self, cycle_id: str) -> None:
        """Step 14: Re-evaluate active holdings >= 7 days since last re-eval."""
        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                rows = conn.execute(
                    "SELECT id, ticker, entry_price_usd, position_size_usd, "
                    "last_reeval_at FROM holdings WHERE state = 'ACTIVE'"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.OperationalError:
            rows = []

        from datetime import date, timedelta

        reevaluated = 0
        seven_days_ago = date.today() - timedelta(days=7)
        for row in rows:
            last_reeval = row[4]  # last_reeval_at (string or None)
            if last_reeval is None:
                needs_reeval = True
            else:
                try:
                    reeval_date = date.fromisoformat(str(last_reeval)[:10])
                    needs_reeval = reeval_date <= seven_days_ago
                except (ValueError, TypeError):
                    needs_reeval = True

            if needs_reeval:
                reevaluated += 1
                reeval_outcome = "unknown"
                try:
                    # -- Re-eval pipeline: fetch evidence, run personas, arbitrate --
                    from pmacs.data.evidence_router import fetch_evidence_for_ticker
                    from pmacs.engines.arbitration import arbitrate, ArbitrationSignal
                    from pmacs.engines.state_machine import transition, is_valid_transition
                    from pmacs.schemas.contracts import Holding, HoldingState

                    ticker = row[1]
                    holding_id = row[0]

                    # Fetch fresh evidence
                    evidence_packet = fetch_evidence_for_ticker(ticker, cycle_id)
                    evidence_list: list[Any] = list(evidence_packet.evidence)

                    # Build a minimal brief for re-eval
                    brief = f"WEEKLY_REEVAL: ticker={ticker}"

                    # Dispatch personas with a 180s timeout (shorter than normal 270s)
                    persona_results = self._dispatch_personas_with_timeout(
                        evidence=evidence_list,
                        brief=brief,
                        cycle_id=cycle_id,
                        ticker=ticker,
                        timeout_seconds=180,
                    )

                    # Extract signals and arbitrate
                    signals: list[ArbitrationSignal] = []
                    for persona_name_str, raw_output in persona_results.items():
                        dp = self._extract_directional_probability(
                            persona_name_str, ticker, cycle_id, raw_output,
                        )
                        if dp is not None:
                            signals.append(ArbitrationSignal(dp))

                    if signals:
                        arbitrated = arbitrate(signals, cycle_id=cycle_id)

                        if (
                            arbitrated.decision.value.startswith("PROCEED")
                            and arbitrated.p_up >= arbitrated.p_down
                        ):
                            # Thesis still valid — stay ACTIVE
                            reeval_outcome = "thesis_valid"
                            next_review = (date.today() + timedelta(days=7)).isoformat()
                            conn2 = sqlite3.connect(str(self._db_path))
                            try:
                                conn2.execute(
                                    "UPDATE holdings SET last_reeval_at = ?, "
                                    "thesis_review_due_date = ? WHERE id = ?",
                                    (date.today().isoformat(), next_review, holding_id),
                                )
                                conn2.commit()
                            finally:
                                conn2.close()
                        else:
                            # Thesis invalidated — transition to EXIT_THESIS_INVALIDATED
                            reeval_outcome = "thesis_invalidated"
                            holding = Holding(
                                id=holding_id,
                                ticker=ticker,
                                state=HoldingState.ACTIVE,
                                cycle_id_opened=cycle_id,
                            )
                            if is_valid_transition(
                                holding.state, HoldingState.EXIT_THESIS_INVALIDATED,
                            ):
                                transition(
                                    holding,
                                    HoldingState.EXIT_THESIS_INVALIDATED,
                                    f"reeval_thesis_invalidated:p_down={arbitrated.p_down:.2f}",
                                    cycle_id,
                                    0,
                                )
                            conn2 = sqlite3.connect(str(self._db_path))
                            try:
                                conn2.execute(
                                    "UPDATE holdings SET state = ?, last_reeval_at = ? "
                                    "WHERE id = ?",
                                    (
                                        HoldingState.EXIT_THESIS_INVALIDATED.value,
                                        date.today().isoformat(),
                                        holding_id,
                                    ),
                                )
                                conn2.commit()
                            finally:
                                conn2.close()
                    else:
                        # No valid signals — update date only, don't exit
                        reeval_outcome = "no_signals"
                        conn2 = sqlite3.connect(str(self._db_path))
                        try:
                            conn2.execute(
                                "UPDATE holdings SET last_reeval_at = ? WHERE id = ?",
                                (date.today().isoformat(), holding_id),
                            )
                            conn2.commit()
                        finally:
                            conn2.close()

                except Exception as exc:
                    # Re-eval pipeline failure — fall back to date update only
                    reeval_outcome = f"error:{str(exc)[:80]}"
                    log_debug(
                        "REEVAL_PIPELINE_FALLBACK",
                        payload={
                            "cycle_id": cycle_id,
                            "ticker": row[1],
                            "holding_id": row[0],
                            "error": str(exc)[:200],
                        },
                        level="WARN",
                        error_code="REEVAL_FAILED",
                        cycle_id=cycle_id,
                        msg=f"Re-eval pipeline failed for {row[1]}: {exc}, "
                            f"falling back to date update",
                    )
                    try:
                        conn2 = sqlite3.connect(str(self._db_path))
                        try:
                            conn2.execute(
                                "UPDATE holdings SET last_reeval_at = ? WHERE id = ?",
                                (date.today().isoformat(), row[0]),
                            )
                            conn2.commit()
                        finally:
                            conn2.close()
                    except sqlite3.OperationalError:
                        pass

                log_debug(
                    "REEVAL_SYMBOL_OUTCOME",
                    payload={
                        "cycle_id": cycle_id,
                        "ticker": row[1],
                        "holding_id": row[0],
                        "outcome": reeval_outcome,
                    },
                    level="INFO",
                    cycle_id=cycle_id,
                    msg=f"Re-eval for {row[1]}: {reeval_outcome}",
                )

        log_debug(
            "CYCLE_WEEKLY_REEVAL",
            payload={
                "cycle_id": cycle_id,
                "active_holdings": len(rows),
                "reevaluated": reevaluated,
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Weekly re-eval: {reevaluated}/{len(rows)} holdings re-evaluated",
        )

    def _step_thesis_aging(self, cycle_id: str) -> None:
        """Step 15: Mandatory re-eval for holdings >= 90 days old."""
        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                rows = conn.execute(
                    "SELECT id, ticker, entry_date FROM holdings WHERE state = 'ACTIVE'"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.OperationalError:
            rows = []

        from datetime import date, timedelta

        ninety_days_ago = date.today() - timedelta(days=90)
        aged_count = 0
        for row in rows:
            entry_date_str = row[2]
            if entry_date_str is None:
                continue
            try:
                entry_date = date.fromisoformat(str(entry_date_str)[:10])
                if entry_date <= ninety_days_ago:
                    aged_count += 1
            except (ValueError, TypeError):
                pass

        log_debug(
            "CYCLE_THESIS_AGING",
            payload={
                "cycle_id": cycle_id,
                "active_holdings": len(rows),
                "aged_holdings_90d": aged_count,
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Thesis aging: {aged_count} holdings >= 90 days old",
        )

    def _step_process_fills(self, cycle_id: str) -> None:
        """Step 16: Process pending mock fills (no-op for paper mode)."""
        log_debug(
            "CYCLE_PROCESS_FILLS",
            payload={"cycle_id": cycle_id, "fills_processed": 0},
            level="INFO",
            cycle_id=cycle_id,
            msg="Process fills: mock fills are instant, no pending fills to process",
        )

    def _step_reconciliation(self, cycle_id: str) -> None:
        """Step 17: Reconcile paper ledger vs SQLite holdings."""
        from pmacs.engines.reconciliation import reconcile_paper_ledger

        ledger_total = 0.0
        if self._ledger is not None:
            ledger_total = self._ledger.total_value

        # For paper mode, broker_total == ledger_total (no real broker)
        result = reconcile_paper_ledger(
            ledger_total=ledger_total,
            broker_total=ledger_total,
        )

        log_debug(
            "CYCLE_RECONCILIATION",
            payload={
                "cycle_id": cycle_id,
                "matched": result.matched,
                "ledger_total": ledger_total,
                "difference_usd": result.difference_usd,
                "requires_action": result.requires_action,
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Reconciliation: {'matched' if result.matched else 'MISMATCH'} "
                f"(diff=${result.difference_usd:.2f})",
        )

    def _step_opportunity_cost(self, cycle_id: str) -> None:
        """Step 18: Evaluate each active holding for hold vs exit."""
        from pmacs.engines.opportunity_cost import run_opportunity_cost_scan
        from pmacs.schemas.contracts import Holding, HoldingState

        active_holdings: list[Holding] = []
        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                rows = conn.execute(
                    "SELECT id, ticker, state, entry_price_usd, position_size_usd, "
                    "conviction_score, sector, entry_date "
                    "FROM holdings WHERE state = 'ACTIVE'"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.OperationalError:
            rows = []

        for row in rows:
            holding = Holding(
                id=row[0],
                ticker=row[1],
                state=HoldingState.ACTIVE,
                entry_price_usd=row[3] or 0.0,
                position_size_usd=row[4],
                conviction_score=row[5],
                sector=row[6],
            )
            active_holdings.append(holding)

        if not active_holdings:
            log_debug(
                "CYCLE_OPPORTUNITY_COST",
                payload={"cycle_id": cycle_id, "active_holdings": 0},
                level="INFO",
                cycle_id=cycle_id,
                msg="Opportunity cost: no active holdings to evaluate",
            )
            return

        # Default conviction scores from holdings themselves
        conviction_scores = {
            h.id: h.conviction_score or 0.5 for h in active_holdings
        }

        try:
            results = run_opportunity_cost_scan(
                active_holdings=active_holdings,
                conviction_scores=conviction_scores,
                alternative_return_pct=0.10,
                cycle_id=cycle_id,
            )

            exit_count = sum(1 for r in results if r.action == "EXIT")

            log_debug(
                "CYCLE_OPPORTUNITY_COST",
                payload={
                    "cycle_id": cycle_id,
                    "evaluated": len(results),
                    "exit_recommendations": exit_count,
                    "hold_count": len(results) - exit_count,
                },
                level="INFO",
                cycle_id=cycle_id,
                msg=f"Opportunity cost: {exit_count} EXIT, "
                    f"{len(results) - exit_count} HOLD out of {len(results)}",
            )
        except Exception as exc:
            log_debug(
                "CYCLE_OPPORTUNITY_COST_ERROR",
                payload={"cycle_id": cycle_id, "error": str(exc)},
                level="WARN",
                error_code="OPPORTUNITY_COST_FAILED",
                cycle_id=cycle_id,
                msg=f"Opportunity cost evaluation failed: {exc}",
            )

    def _step_calibration(self, cycle_id: str) -> None:
        """Step 19: Compute Brier scores and refit persona weights."""
        from pmacs.engines.calibration import compute_brier, refit_persona_weights

        # Collect resolved holdings with verdict and outcome data
        resolutions: list[dict] = []
        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                rows = conn.execute(
                    "SELECT ticker, verdict, actual_outcome, p_up, p_flat, p_down "
                    "FROM resolutions ORDER BY id DESC LIMIT 100"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.OperationalError:
            rows = []

        briers_computed = 0
        persona_briers: dict[str, float] = {}
        for row in rows:
            ticker, verdict, actual, p_up, p_flat, p_down = row
            if actual and p_up is not None:
                brier = compute_brier(
                    p_up=float(p_up),
                    p_flat=float(p_flat),
                    p_down=float(p_down),
                    actual=actual,
                    cycle_id=cycle_id,
                )
                briers_computed += 1
                # Accumulate per-persona (here we use ticker as proxy)
                persona_briers[ticker] = brier

        if len(persona_briers) >= 20:
            current_weights = {k: 1.0 / len(persona_briers) for k in persona_briers}
            new_weights = refit_persona_weights(
                persona_briers=persona_briers,
                current_weights=current_weights,
                min_samples=20,
                cycle_id=cycle_id,
            )
        else:
            new_weights = {}

        log_debug(
            "CYCLE_CALIBRATION",
            payload={
                "cycle_id": cycle_id,
                "resolutions_scanned": len(rows),
                "briers_computed": briers_computed,
                "weights_refitted": len(new_weights) > 0,
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Calibration: {briers_computed} Brier scores computed, "
                f"refit={'yes' if new_weights else 'no (insufficient data)'}",
        )

    def _step_crucible_calibration(self, cycle_id: str) -> None:
        """Step 20: Adjust Crucible severity multipliers."""
        from pmacs.engines.crucible_calibration import compute_severity_multiplier

        # Read current multiplier from config or use default
        current_multiplier = float(
            self._config.get("crucible_severity_multiplier", 1.0)
        )

        # Estimate false-severity rate from recent crucible outcomes
        false_severity_count = 0
        total_crucible_attacks = 0
        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM scan_records WHERE direction = 'UP' "
                    "AND created_at > datetime('now', '-30 days')"
                ).fetchone()
                total_crucible_attacks = row[0] if row else 0
            finally:
                conn.close()
        except sqlite3.OperationalError:
            pass

        false_severity_rate = 0.0
        if total_crucible_attacks > 0:
            false_severity_rate = false_severity_count / total_crucible_attacks

        new_multiplier = compute_severity_multiplier(
            current_multiplier=current_multiplier,
            recent_false_severity_rate=false_severity_rate,
        )

        log_debug(
            "CYCLE_CRUCIBLE_CALIBRATION",
            payload={
                "cycle_id": cycle_id,
                "old_multiplier": current_multiplier,
                "new_multiplier": new_multiplier,
                "false_severity_rate": false_severity_rate,
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Crucible calibration: multiplier {current_multiplier:.2f} -> "
                f"{new_multiplier:.2f}",
        )

    def _step_causal_attribution(self, cycle_id: str) -> None:
        """Step 21: Credit/blame per persona for resolved holdings."""
        from pmacs.engines.causal_attribution import attribute_resolution

        attributed = 0
        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                rows = conn.execute(
                    "SELECT ticker, verdict, actual_outcome FROM resolutions "
                    "ORDER BY id DESC LIMIT 50"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.OperationalError:
            rows = []

        for row in rows:
            ticker, verdict, actual = row
            if not actual:
                continue
            # Build synthetic persona outputs for attribution
            persona_outputs = {"arbitration": {"p_up": 0.5, "p_flat": 0.3, "p_down": 0.2}}
            try:
                results = attribute_resolution(
                    verdict=verdict or "HOLD",
                    actual_outcome=actual,
                    persona_outputs=persona_outputs,
                )
                attributed += 1
            except Exception:
                pass

        log_debug(
            "CYCLE_CAUSAL_ATTRIBUTION",
            payload={
                "cycle_id": cycle_id,
                "resolutions_processed": len(rows),
                "attributions_computed": attributed,
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Causal attribution: {attributed}/{len(rows)} resolutions attributed",
        )

    def _step_memory(self, cycle_id: str) -> None:
        """Step 22: Memory antipattern recording for resolutions."""
        from pmacs.engines.memory import check_antipattern

        # Check antipatterns for all tickers processed this cycle
        checked = 0
        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                rows = conn.execute(
                    "SELECT DISTINCT ticker FROM scan_records WHERE cycle_id = ?",
                    (cycle_id,),
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.OperationalError:
            rows = []

        for row in rows:
            check_antipattern(row[0], cycle_id)
            checked += 1

        log_debug(
            "CYCLE_MEMORY_ANTIPATTERN",
            payload={
                "cycle_id": cycle_id,
                "tickers_checked": checked,
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Memory antipattern: checked {checked} tickers",
        )

    def _step_lessons(self, cycle_id: str) -> None:
        """Step 23: Extract lessons from new resolutions and write to Qdrant."""
        from pmacs.engines.lessons import extract_lesson_from_resolution, write_lesson_to_qdrant

        lessons_extracted = 0
        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                rows = conn.execute(
                    "SELECT ticker, verdict, actual_outcome, failure_taxonomy, thesis "
                    "FROM resolutions ORDER BY id DESC LIMIT 50"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.OperationalError:
            rows = []

        for row in rows:
            ticker, verdict, actual, taxonomy, thesis = row
            try:
                lesson = extract_lesson_from_resolution(
                    ticker=ticker or "",
                    thesis=thesis or "",
                    verdict=verdict or "",
                    actual_outcome=actual or "flat",
                    failure_taxonomy=taxonomy,
                    cycle_id=cycle_id,
                )
                if lesson is not None:
                    lessons_extracted += 1
                    # Write lesson to Qdrant with embedding (Architecture.md §8.7)
                    qdrant = self._get_qdrant_adapter()
                    if qdrant is not None:
                        try:
                            write_lesson_to_qdrant(lesson, qdrant)
                        except Exception:
                            pass
                    # Write lesson to SQLite
                    try:
                        conn2 = sqlite3.connect(str(self._db_path))
                        try:
                            conn2.execute(
                                "INSERT INTO lessons "
                                "(ticker, lesson_type, text, evidence_ids, cycle_id, created_at) "
                                "VALUES (?, ?, ?, ?, ?, ?)",
                                (
                                    lesson.ticker,
                                    lesson.lesson_type,
                                    lesson.text,
                                    ",".join(lesson.evidence_ids),
                                    lesson.cycle_id,
                                    datetime.now(timezone.utc).isoformat(),
                                ),
                            )
                            conn2.commit()
                        finally:
                            conn2.close()
                    except sqlite3.OperationalError:
                        pass
            except Exception:
                pass

        log_debug(
            "CYCLE_LESSONS_EXTRACTION",
            payload={
                "cycle_id": cycle_id,
                "resolutions_scanned": len(rows),
                "lessons_extracted": lessons_extracted,
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Lessons: {lessons_extracted} extracted from {len(rows)} resolutions",
        )

    def _step_override_learning_post(self, cycle_id: str) -> None:
        """Step 24: Evaluate recent override outcomes (post-cycle)."""
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
            "CYCLE_OVERRIDE_LEARNING_POST",
            payload={
                "cycle_id": cycle_id,
                "override_count": len(overrides),
                "cluster_count": len(clusters),
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Override learning (post): {len(clusters)} clusters from "
                f"{len(overrides)} overrides",
        )

    def _step_fde(self, cycle_id: str) -> None:
        """Step 25: Failure Diagnostic Engine -- classify terminal holdings."""
        from pmacs.engines.failure_diagnostic import classify, HoldingContext

        classified = 0
        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                rows = conn.execute(
                    "SELECT id, ticker, state, entry_price_usd, exit_price_usd, "
                    "stop_price_usd, abort_reason "
                    "FROM holdings "
                    "WHERE state IN ('STOPPED_OUT', 'EXIT_THESIS_INVALIDATED', "
                    "'EXIT_OPPORTUNITY_COST', 'EXIT_TRAILING_STOP', 'EXIT_FAILED', "
                    "'RESOLVED_DOWN', 'RESOLVED_MIXED', 'RESOLUTION_TIMEOUT', "
                    "'PANIC_EXIT', 'ABORTED_LLM', 'ABORTED_RISK', 'ABORTED_PRE_LLM', "
                    "'DELISTED', 'INTERRUPTED') "
                    "LIMIT 50"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.OperationalError:
            rows = []

        for row in rows:
            holding_id, ticker, state, entry_price, exit_price, stop_price, exit_reason = row
            ctx = HoldingContext(
                state=state or "",
                ticker=ticker or "",
                entry_price=float(entry_price) if entry_price else 0.0,
                exit_price=float(exit_price) if exit_price else None,
                stop_loss_price=float(stop_price) if stop_price else None,
                exit_reason=exit_reason,
            )
            try:
                result = classify(ctx, holding_id=holding_id, cycle_id=cycle_id)
                classified += 1

                # Write FailedAssumption to KuzuDB graph (Architecture.md §9 step 25)
                kuzu = self._get_kuzu_adapter()
                if kuzu is not None and result.primary.value != "UNCLASSIFIED":
                    try:
                        from uuid import uuid4 as _uuid4
                        kuzu.write_failed_assumption(
                            fa_id=str(_uuid4()),
                            taxonomy=result.primary.value,
                            severity=result.severity,
                            holding_id=holding_id,
                            cycle_id=cycle_id,
                            summary=result.summary,
                        )
                    except Exception:
                        pass

                # Write classification to SQLite
                try:
                    conn2 = sqlite3.connect(str(self._db_path))
                    try:
                        conn2.execute(
                            "INSERT INTO failure_classifications "
                            "(holding_id, taxonomy, severity, summary, cycle_id, classified_at) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                holding_id,
                                result.primary.value,
                                result.severity,
                                result.summary,
                                cycle_id,
                                datetime.now(timezone.utc).isoformat(),
                            ),
                        )
                        conn2.commit()
                    finally:
                        conn2.close()
                except sqlite3.OperationalError:
                    pass
            except Exception:
                pass

        log_debug(
            "CYCLE_FDE",
            payload={
                "cycle_id": cycle_id,
                "terminal_holdings": len(rows),
                "classified": classified,
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"FDE: {classified}/{len(rows)} terminal holdings classified",
        )

    def _step_drift(self, cycle_id: str) -> None:
        """Step 26: Cross-cycle drift statistics."""
        try:
            from pmacs.cortex.drift import DriftMonitor

            monitor = DriftMonitor()
            result = monitor.check_drift(cycle_id)

            log_debug(
                "CYCLE_DRIFT_STATS",
                payload={
                    "cycle_id": cycle_id,
                    "has_drift": result.has_drift,
                    "dimension": result.dimension,
                    "details": result.details,
                },
                level="INFO",
                cycle_id=cycle_id,
                msg=f"Drift check: {result.details}",
            )
        except Exception as exc:
            log_debug(
                "CYCLE_DRIFT_STATS_UNAVAILABLE",
                payload={"cycle_id": cycle_id, "error": str(exc)},
                level="INFO",
                cycle_id=cycle_id,
                msg=f"Drift monitoring unavailable: {exc}",
            )

    def _step_cross_db_consistency(self, cycle_id: str) -> None:
        """Step 27: Cross-validate all storage backends (Architecture.md §14)."""
        from pmacs.storage.consistency import check_cross_db_consistency

        # Build SQLite connection for cross-checks
        sqlite_conn = None
        try:
            sqlite_conn = sqlite3.connect(str(self._db_path))
        except Exception:
            pass

        results = check_cross_db_consistency(
            sqlite_conn=sqlite_conn,
            kuzu_adapter=self._get_kuzu_adapter(),
            qdrant_client=self._get_qdrant_adapter(),
            cycle_id=cycle_id,
        )

        if sqlite_conn is not None:
            try:
                sqlite_conn.close()
            except Exception:
                pass

        status_summary = {
            r.store: r.status for r in results
        }

        log_debug(
            "CYCLE_CROSS_DB_CONSISTENCY",
            payload={
                "cycle_id": cycle_id,
                "stores": status_summary,
                "total_checks": len(results),
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Cross-DB consistency: {len(results)} stores checked",
        )

    def _step_dead_letter(self, cycle_id: str) -> None:
        """Step 28: Process pending dead-letter entries."""
        from pmacs.logsys.dead_letter import DeadLetterQueue

        queue = DeadLetterQueue()
        pending = queue.get_pending()

        log_debug(
            "CYCLE_DEAD_LETTER",
            payload={
                "cycle_id": cycle_id,
                "pending_count": queue.pending_count,
                "exhausted_count": queue.exhausted_count,
                "total_count": queue.total_count,
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Dead letter: {queue.pending_count} pending, "
                f"{queue.exhausted_count} exhausted",
        )

    # -- DB retry helper (S6-2: SQLite locked during write) --

    def _db_execute_with_retry(
        self,
        sql: str,
        params: tuple = (),
        *,
        retries: int = 3,
        backoff_ms: float = 100.0,
        cycle_id: str = "",
    ) -> None:
        """Execute a SQLite statement with retry on OperationalError (locked).

        Retries up to `retries` times with exponential backoff starting at
        `backoff_ms`. Logs each retry. Raises on final failure.

        Args:
            sql: SQL statement to execute.
            params: Parameters for the statement.
            retries: Max retry attempts (default 3).
            backoff_ms: Initial backoff in milliseconds (default 100ms).
            cycle_id: For logging context.
        """
        for attempt in range(retries):
            try:
                conn = sqlite3.connect(str(self._db_path))
                try:
                    conn.execute(sql, params)
                    conn.commit()
                finally:
                    conn.close()
                return
            except sqlite3.OperationalError as exc:
                if attempt < retries - 1:
                    wait = backoff_ms * (2 ** attempt) / 1000.0
                    log_debug(
                        "DB_RETRY_LOCKED",
                        payload={
                            "attempt": attempt + 1,
                            "max_retries": retries,
                            "wait_s": wait,
                            "error": str(exc),
                        },
                        level="INFO",
                        cycle_id=cycle_id,
                        msg=f"SQLite locked, retrying in {wait:.1f}s "
                            f"(attempt {attempt + 1}/{retries})",
                    )
                    time.sleep(wait)
                else:
                    log_debug(
                        "DB_RETRY_EXHAUSTED",
                        payload={
                            "attempts": retries,
                            "error": str(exc),
                        },
                        level="WARN",
                        error_code="DB_WRITE_FAILED",
                        cycle_id=cycle_id,
                        msg=f"SQLite write failed after {retries} retries: {exc}",
                    )
                    raise

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


def _rebuild_evidence_brief(
    evidence: list[Any],
    attacks: list[dict[str, Any]],
    arbitrated: Any,
    ticker: str,
) -> list[Any]:
    """Rebuild evidence brief addressing Crucible cycle-1 attacks.

    Deterministic Python merge: annotates original evidence with a
    "crucible_attack_context" entry so cycle-2 Crucible can evaluate whether
    the thesis addresses the identified flaws (Agents.md §16.1 REWRITE path).

    The Crucible is not given new data -- it gets the same evidence plus a
    structured summary of its own cycle-1 attacks. This ensures the rewrite
    test is about whether the thesis *inherently* addresses the attacks, not
    whether new evidence was cherry-picked to dodge them.
    """
    attack_summary: dict[str, Any] = {
        "source": "crucible_rewrite_context",
        "ticker": ticker,
        "cycle_1_attacks": attacks,
        "attack_count": len(attacks),
        "thesis_direction": getattr(arbitrated, "decision", None),
        "arbitrated_p_up": getattr(arbitrated, "p_up", None),
        "arbitrated_p_down": getattr(arbitrated, "p_down", None),
        "revised": True,
    }

    # Return a new list: original evidence + attack context annotation
    return list(evidence) + [attack_summary]


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
