"""E2E test: crash resume via checkpoint/idempotency system.

Verifies that when a cycle is interrupted mid-execution, the checkpoint
system allows it to resume from the last completed operation rather than
restarting from scratch. Architecture.md §12.3.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pmacs.nervous.checkpoint import (
    CheckpointState,
    is_completed,
    load_checkpoint,
    save_checkpoint,
)
from pmacs.storage.sqlite import init_db


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """Create a temporary SQLite database with required tables."""
    db_path = tmp_path / "pmacs.db"
    conn = init_db(db_path)
    conn.close()
    return db_path


class TestCrashResumeCheckpoint:
    """Simulate a mid-cycle crash and verify resume works."""

    def test_empty_cycle_no_checkpoints(self, db: Path):
        """Fresh cycle has no checkpoints — all ops should run."""
        assert load_checkpoint("cycle_001", db) is None
        assert is_completed("cycle_001", 1, db) is False

    def test_partial_cycle_crash(self, db: Path):
        """Simulate crash after completing ops 1-5. Ops 1-5 should be
        marked complete; ops 6+ should be incomplete."""
        cycle_id = "cycle_crash_001"

        # Simulate: ops 0-5 complete before crash
        completed_ops = [
            (0, "initiate_cycle"),
            (1, "clock_drift_check"),
            (2, "checkpoint_resume"),
            (3, "fx_snapshot"),
            (4, "kill_switch_check"),
            (5, "flywheel_health"),
        ]
        for op_seq, op_type in completed_ops:
            save_checkpoint(cycle_id, op_seq, op_type, db)

        # Verify: ops 0-5 are complete
        for op_seq, _ in completed_ops:
            assert is_completed(cycle_id, op_seq, db), (
                f"Op {op_seq} should be complete after crash"
            )

        # Verify: ops 6+ are NOT complete (crash happened before them)
        for op_seq in range(6, 13):
            assert not is_completed(cycle_id, op_seq, db), (
                f"Op {op_seq} should NOT be complete — crash happened before it"
            )

    def test_resume_picks_up_from_last_checkpoint(self, db: Path):
        """After crash, load_checkpoint returns the last completed op.
        Resume should continue from the next op_seq."""
        cycle_id = "cycle_resume_001"

        # Complete ops 0-3 (FX snapshot done, crash before kill switch)
        for op_seq, op_type in [
            (0, "initiate_cycle"),
            (1, "clock_drift_check"),
            (2, "checkpoint_resume"),
            (3, "fx_snapshot"),
        ]:
            save_checkpoint(cycle_id, op_seq, op_type, db)

        # Load checkpoint
        last = load_checkpoint(cycle_id, db)
        assert last is not None
        assert last.op_seq == 3
        assert last.op_type == "fx_snapshot"

        # Resume should start from op_seq 4 (kill switch)
        resume_from = last.op_seq + 1
        assert resume_from == 4

        # Simulate: resume completes ops 4-5
        save_checkpoint(cycle_id, 4, "kill_switch_check", db)
        save_checkpoint(cycle_id, 5, "flywheel_health", db)

        # Verify all ops up to 5 now complete
        for op_seq in range(6):
            assert is_completed(cycle_id, op_seq, db)

    def test_full_cycle_completion(self, db: Path):
        """Complete all 30 ops — verify no gaps."""
        cycle_id = "cycle_full_001"

        for op_seq in range(31):
            save_checkpoint(cycle_id, op_seq, f"op_{op_seq}", db)

        # All 31 ops (0-30) should be complete
        for op_seq in range(31):
            assert is_completed(cycle_id, op_seq, db)

        last = load_checkpoint(cycle_id, db)
        assert last.op_seq == 30

    def test_idempotent_re_save(self, db: Path):
        """Saving the same checkpoint twice is idempotent."""
        cycle_id = "cycle_idem_001"
        save_checkpoint(cycle_id, 1, "first_type", db)
        save_checkpoint(cycle_id, 1, "second_type", db)

        # Should be overwritten (INSERT OR REPLACE)
        assert is_completed(cycle_id, 1, db)

    def test_different_cycles_independent(self, db: Path):
        """Checkpoints from different cycles don't interfere."""
        save_checkpoint("cycle_a", 1, "step_1", db)
        save_checkpoint("cycle_b", 1, "step_1", db)

        assert is_completed("cycle_a", 1, db)
        assert is_completed("cycle_b", 1, db)

        # Add more to cycle_a
        save_checkpoint("cycle_a", 2, "step_2", db)
        assert is_completed("cycle_a", 2, db)
        assert not is_completed("cycle_b", 2, db)

    def test_crash_after_symbol_loop(self, db: Path):
        """Simulate crash after per-symbol processing but before post-cycle.
        Verify pre-cycle and symbol ops are complete, post-cycle ops are not."""
        cycle_id = "cycle_symbol_crash"

        # Complete pre-cycle (ops 0-12)
        for op_seq in range(13):
            save_checkpoint(cycle_id, op_seq, f"pre_cycle_{op_seq}", db)

        # Complete per-symbol processing (ops 13+ depend on queue)
        for op_seq in range(13, 20):
            save_checkpoint(cycle_id, op_seq, f"symbol_{op_seq}", db)

        # Verify: pre-cycle and symbol ops complete
        for op_seq in range(20):
            assert is_completed(cycle_id, op_seq, db)

        # Verify: post-cycle ops NOT complete
        for op_seq in range(20, 31):
            assert not is_completed(cycle_id, op_seq, db)

        # Simulate resume: complete remaining post-cycle ops
        for op_seq in range(20, 31):
            save_checkpoint(cycle_id, op_seq, f"post_cycle_{op_seq}", db)

        last = load_checkpoint(cycle_id, db)
        assert last.op_seq == 30
