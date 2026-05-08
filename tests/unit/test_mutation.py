"""Unit tests for mutation engine components."""
from __future__ import annotations

import json
import os

import pytest

from pmacs.mutation.stat_test import welch_t_test
from pmacs.mutation.candidate_generator import (
    generate_candidates,
    ACTIVATION_THRESHOLD,
)
from pmacs.mutation.ab_runner import ABRunner
from pmacs.mutation.promotion import operator_promote
from pmacs.mutation.rollback import (
    regression_detected,
    execute_rollback,
    flag_for_kill_switch_review,
    AUTO_ROLLBACK_WINDOW,
)
from pmacs.mutation.daemon import mode_too_early


# ---------------------------------------------------------------------------
# Stat test
# ---------------------------------------------------------------------------


class TestStatTestBasic:
    def test_identical_not_significant(self) -> None:
        data = [1.0, 2.0, 3.0] * 10
        result = welch_t_test(data, data)
        assert not result.is_significant

    def test_different_significant(self) -> None:
        result = welch_t_test([1.0] * 30, [100.0] * 30)
        assert result.is_significant


# ---------------------------------------------------------------------------
# Candidate generator
# ---------------------------------------------------------------------------


class TestCandidateGenerator:
    def test_dormant_before_50_cycles(self) -> None:
        clusters = [{"taxonomy": "MOAT_DRIFT_OVERESTIMATE", "count": 10}]
        candidates = generate_candidates(clusters, paper_cycle_count=49)
        assert candidates == []

    def test_generates_from_matching_clusters(self) -> None:
        clusters = [{"taxonomy": "MOAT_DRIFT_OVERESTIMATE", "count": 5}]
        candidates = generate_candidates(clusters, paper_cycle_count=50)
        assert len(candidates) == 1
        assert candidates[0].trigger_taxonomy == "MOAT_DRIFT_OVERESTIMATE"
        assert candidates[0].reversible is True

    def test_no_match_below_min_count(self) -> None:
        clusters = [{"taxonomy": "MOAT_DRIFT_OVERESTIMATE", "count": 4}]
        candidates = generate_candidates(clusters, paper_cycle_count=50)
        assert len(candidates) == 0

    def test_multiple_matching_rules(self) -> None:
        clusters = [
            {"taxonomy": "MOAT_DRIFT_OVERESTIMATE", "count": 6},
            {"taxonomy": "GROWTH_STALL_MISSED", "count": 7},
            {"taxonomy": "STOP_HUNTED", "count": 3},
        ]
        candidates = generate_candidates(clusters, paper_cycle_count=60)
        assert len(candidates) == 3

    def test_exactly_at_threshold(self) -> None:
        clusters = [{"taxonomy": "STOP_HUNTED", "count": 3}]
        candidates = generate_candidates(clusters, paper_cycle_count=50)
        assert len(candidates) == 1

    def test_candidate_has_rollback_config(self) -> None:
        clusters = [{"taxonomy": "STOP_HUNTED", "count": 5}]
        candidates = generate_candidates(clusters, paper_cycle_count=50)
        assert len(candidates) == 1
        c = candidates[0]
        assert c.rollback_config == c.baseline_config

    def test_config_overrides_threshold(self) -> None:
        """When config provided, uses config.min_paper_cycles."""
        clusters = [{"taxonomy": "MOAT_DRIFT_OVERESTIMATE", "count": 10}]

        class FakeConfig:
            min_paper_cycles = 100

        # 50 cycles < 100 threshold -> dormant
        candidates = generate_candidates(clusters, paper_cycle_count=50, config=FakeConfig())
        assert candidates == []

        # 100 cycles >= 100 threshold -> generates
        candidates = generate_candidates(clusters, paper_cycle_count=100, config=FakeConfig())
        assert len(candidates) == 1


# ---------------------------------------------------------------------------
# A/B Runner
# ---------------------------------------------------------------------------


class TestABRunner:
    def test_start_and_record(self) -> None:
        runner = ABRunner()
        assert runner.start("p1")
        runner.record_outcome("p1", "control", 0.5)
        runner.record_outcome("p1", "candidate", 0.7)
        state = runner.get_state("p1")
        assert state is not None
        assert state.control_outcomes == [0.5]
        assert state.candidate_outcomes == [0.7]

    def test_max_concurrent_cap(self) -> None:
        runner = ABRunner(max_concurrent=3)
        assert runner.start("p1")
        assert runner.start("p2")
        assert runner.start("p3")
        assert not runner.start("p4")  # rejected
        assert runner.active_count == 3

    def test_complete_frees_slot(self) -> None:
        runner = ABRunner(max_concurrent=3)
        runner.start("p1")
        runner.start("p2")
        runner.start("p3")
        state = runner.complete("p1")
        assert state is not None
        assert state.status == "COMPLETE"
        assert runner.can_start()

    def test_record_nonexistent_proposal(self) -> None:
        runner = ABRunner()
        runner.record_outcome("nonexistent", "control", 1.0)  # no error

    def test_complete_nonexistent(self) -> None:
        runner = ABRunner()
        assert runner.complete("nonexistent") is None

    def test_can_start_method(self) -> None:
        runner = ABRunner(max_concurrent=2)
        assert runner.can_start()
        runner.start("p1")
        assert runner.can_start()
        runner.start("p2")
        assert not runner.can_start()

    def test_config_sets_max_concurrent(self) -> None:
        class FakeConfig:
            max_ab_tests = 2

        runner = ABRunner(config=FakeConfig())
        assert runner.max_concurrent == 2
        assert runner.start("p1")
        assert runner.start("p2")
        assert not runner.start("p3")

    def test_db_persistence_on_start(self, tmp_path) -> None:
        """A/B start updates SQLite status to RUNNING_AB."""
        from pmacs.storage.sqlite import init_db

        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = __import__("sqlite3").connect(str(db_path))
        conn.execute(
            "INSERT INTO mutation_proposals (id, dimension, target, baseline_value, "
            "candidate_value, status, proposed_at) VALUES (?, ?, ?, ?, ?, 'PROPOSED', ?)",
            ("p1", "prompts", "test.target", "{}", "{}", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        runner = ABRunner(config=type("C", (), {"max_ab_tests": 3})(), db_path=db_path)
        assert runner.start("p1")

        conn = __import__("sqlite3").connect(str(db_path))
        row = conn.execute(
            "SELECT status FROM mutation_proposals WHERE id = 'p1'"
        ).fetchone()
        conn.close()
        assert row[0] == "RUNNING_AB"


# ---------------------------------------------------------------------------
# Promotion (TOTP required)
# ---------------------------------------------------------------------------


class TestPromotion:
    def test_invalid_totp_raises(self) -> None:
        with pytest.raises(PermissionError, match="Invalid TOTP"):
            operator_promote("proposal_1", "000000", totp_secret="JBSWY3DPEHPK3PXP")

    def test_valid_totp_succeeds(self) -> None:
        from pmacs.cortex.totp import generate_totp_secret, compute_totp

        secret = generate_totp_secret()
        code = compute_totp(secret)
        result = operator_promote("proposal_1", code, totp_secret=secret)
        assert result["proposal_id"] == "proposal_1"
        assert result["promoted_by"] == "operator"
        assert "probation_cycles" in result

    def test_callback_based_totp(self) -> None:
        """Verify verify_fn callback works without exposing secret."""
        from pmacs.cortex.totp import generate_totp_secret, compute_totp

        secret = generate_totp_secret()
        code = compute_totp(secret)
        from pmacs.cortex.totp import verify_totp

        verify_fn = lambda c: verify_totp(secret, c)
        result = operator_promote("proposal_1", code, verify_fn=verify_fn)
        assert result["proposal_id"] == "proposal_1"

    def test_callback_invalid_raises(self) -> None:
        verify_fn = lambda c: False
        with pytest.raises(PermissionError, match="Invalid TOTP"):
            operator_promote("proposal_1", "123456", verify_fn=verify_fn)

    def test_promotion_with_registry_apply(self, tmp_path) -> None:
        """Promotion applies to registry when paths provided."""
        from pmacs.cortex.totp import generate_totp_secret, compute_totp
        from pmacs.storage.sqlite import init_db

        secret = generate_totp_secret()
        code = compute_totp(secret)

        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = __import__("sqlite3").connect(str(db_path))
        conn.execute(
            "INSERT INTO mutation_proposals (id, dimension, target, baseline_value, "
            "candidate_value, status, proposed_at) VALUES (?, ?, ?, ?, ?, 'PROPOSED', ?)",
            ("p1", "prompts", "moat_analyst", '{"old": true}', '{"new": true}',
             "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        registry_path = tmp_path / "model_registry.json"
        registry_path.write_text('{"active": "test"}')
        audit_path = tmp_path / "audit.log"

        from pmacs.cortex.totp import verify_totp

        verify_fn = lambda c: verify_totp(secret, c)
        result = operator_promote(
            "p1", code, verify_fn=verify_fn,
            registry_path=registry_path, db_path=db_path, audit_path=audit_path,
            candidate_value='{"new": true}', target="moat_analyst",
            dimension="prompts",
        )
        assert result["proposal_id"] == "p1"
        assert registry_path.exists()
        data = json.loads(registry_path.read_text())
        assert "candidates" in data
        assert "p1" in data["candidates"]


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


class TestRollback:
    def test_no_regression_during_probation(self) -> None:
        # 20 cycles ago promoted, probation is 30
        assert not regression_detected(
            promoted_cycles_ago=20,
            probation_cycles=30,
            post_metric=1.0,
            baseline_metric=0.5,
            lower_is_better=True,
        )

    def test_regression_after_probation_within_window(self) -> None:
        # 40 cycles ago, probation 30, window 50 -> within 30..80
        assert regression_detected(
            promoted_cycles_ago=40,
            probation_cycles=30,
            post_metric=1.0,  # worse
            baseline_metric=0.5,
            lower_is_better=True,
        )

    def test_no_regression_if_metric_improved(self) -> None:
        assert not regression_detected(
            promoted_cycles_ago=40,
            probation_cycles=30,
            post_metric=0.3,  # better
            baseline_metric=0.5,
            lower_is_better=True,
        )

    def test_outside_window_expired(self) -> None:
        # 90 cycles ago, probation 30, window 50 -> beyond 30+50=80
        assert not regression_detected(
            promoted_cycles_ago=90,
            probation_cycles=30,
            post_metric=1.0,
            baseline_metric=0.5,
            lower_is_better=True,
        )

    def test_higher_is_better_regression(self) -> None:
        assert regression_detected(
            promoted_cycles_ago=40,
            probation_cycles=30,
            post_metric=0.5,  # worse (lower)
            baseline_metric=1.0,
            lower_is_better=False,
        )

    def test_execute_rollback_audit(self) -> None:
        result = execute_rollback("p123", "regression detected")
        assert result["proposal_id"] == "p123"
        assert result["status"] == "ROLLED_BACK"
        assert result["reason"] == "regression detected"
        assert "rolled_back_at" in result

    def test_execute_rollback_with_sqlite(self, tmp_path) -> None:
        """Rollback updates SQLite status."""
        from pmacs.storage.sqlite import init_db

        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = __import__("sqlite3").connect(str(db_path))
        conn.execute(
            "INSERT INTO mutation_proposals (id, dimension, target, baseline_value, "
            "candidate_value, status, proposed_at) VALUES (?, ?, ?, ?, ?, 'OPERATOR_PROMOTED', ?)",
            ("p1", "prompts", "test", "{}", "{}", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        result = execute_rollback("p1", "auto regression", db_path=db_path)
        assert result["status"] == "ROLLED_BACK"

        conn = __import__("sqlite3").connect(str(db_path))
        row = conn.execute(
            "SELECT status FROM mutation_proposals WHERE id = 'p1'"
        ).fetchone()
        conn.close()
        assert row[0] == "ROLLED_BACK"

    def test_execute_rollback_with_audit(self, tmp_path) -> None:
        """Rollback writes audit event."""
        from pmacs.storage.sqlite import init_db

        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = __import__("sqlite3").connect(str(db_path))
        conn.execute(
            "INSERT INTO mutation_proposals (id, dimension, target, baseline_value, "
            "candidate_value, status, proposed_at) VALUES (?, ?, ?, ?, ?, 'OPERATOR_PROMOTED', ?)",
            ("p1", "prompts", "test", "{}", "{}", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        audit_path = tmp_path / "audit.log"
        result = execute_rollback(
            "p1", "test_rollback", db_path=db_path, audit_path=audit_path,
            cycle_id="test-cycle",
        )
        assert result["status"] == "ROLLED_BACK"
        assert audit_path.exists()
        content = audit_path.read_text()
        assert "mutation_rollback_executed" in content

    def test_flag_for_kill_switch_review(self) -> None:
        promotions = ["p1", "p2", "p3", "p4", "p5"]
        flagged = flag_for_kill_switch_review(promotions, max_flag=3)
        assert flagged == ["p1", "p2", "p3"]

    def test_flag_fewer_than_max(self) -> None:
        promotions = ["p1"]
        flagged = flag_for_kill_switch_review(promotions, max_flag=3)
        assert flagged == ["p1"]

    def test_custom_rollback_window(self) -> None:
        """Config-driven rollback window overrides default."""
        assert regression_detected(
            promoted_cycles_ago=60,
            probation_cycles=30,
            post_metric=1.0,
            baseline_metric=0.5,
            lower_is_better=True,
            rollback_window=100,  # custom window: 30..130
        )
        assert not regression_detected(
            promoted_cycles_ago=140,
            probation_cycles=30,
            post_metric=1.0,
            baseline_metric=0.5,
            lower_is_better=True,
            rollback_window=100,  # beyond 30+100=130
        )


# ---------------------------------------------------------------------------
# Daemon dormant mode
# ---------------------------------------------------------------------------


class TestDaemonDormant:
    def test_too_early(self) -> None:
        assert mode_too_early(49) is True

    def test_ready(self) -> None:
        assert mode_too_early(50) is False

    def test_config_overrides_threshold(self) -> None:
        class FakeConfig:
            min_paper_cycles = 100

        assert mode_too_early(50, config=FakeConfig()) is True
        assert mode_too_early(100, config=FakeConfig()) is False


# ---------------------------------------------------------------------------
# Atomic write (Critical Issue 3)
# ---------------------------------------------------------------------------


class TestAtomicWriteConfig:
    def test_atomic_write_creates_file(self, tmp_path) -> None:
        from pmacs.nervous.mutation import atomic_write_config

        target = tmp_path / "model_registry.json"
        atomic_write_config(target, {"active": "test", "value": 42})
        content = json.loads(target.read_text())
        assert content["active"] == "test"
        assert content["value"] == 42

    def test_no_temp_files_left_behind(self, tmp_path) -> None:
        from pmacs.nervous.mutation import atomic_write_config

        target = tmp_path / "model_registry.json"
        atomic_write_config(target, {"a": 1})
        temps = list(tmp_path.glob("*.tmp"))
        assert temps == []

    def test_atomic_write_uses_canonical_json(self, tmp_path) -> None:
        from pmacs.nervous.mutation import atomic_write_config

        target = tmp_path / "model_registry.json"
        data = {"z": 1, "a": 2, "m": {"k2": 4, "k1": 3}}
        atomic_write_config(target, data)
        raw = target.read_text()
        # canonical_json sorts keys
        assert raw.index('"a"') < raw.index('"m"') < raw.index('"z"')

    def test_atomic_write_overwrites_existing(self, tmp_path) -> None:
        from pmacs.nervous.mutation import atomic_write_config

        target = tmp_path / "model_registry.json"
        atomic_write_config(target, {"version": 1})
        atomic_write_config(target, {"version": 2})
        content = json.loads(target.read_text())
        assert content["version"] == 2

    def test_readonly_dir_raises_permission_error(self, tmp_path) -> None:
        """Filesystem permission on directory prevents write (Level 1 safety).

        On Unix, a read-only file can be replaced by rename if the directory
        is writable. The real protection is directory-level permissions.
        """
        from pmacs.nervous.mutation import atomic_write_config

        restricted = tmp_path / "restricted"
        restricted.mkdir()
        target = restricted / "model_registry.json"
        target.write_text('{"active": "original"}')

        # Make directory read-only (no write = cannot create temp files or rename)
        os.chmod(str(restricted), 0o555)
        try:
            with pytest.raises(PermissionError):
                atomic_write_config(target, {"active": "hacked"})
            # Content unchanged
            assert json.loads(target.read_text())["active"] == "original"
        finally:
            os.chmod(str(restricted), 0o755)


class TestApplyCandidateToRegistry:
    def test_updates_registry(self, tmp_path) -> None:
        from pmacs.nervous.mutation import apply_candidate_to_registry
        from pmacs.storage.sqlite import init_db

        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = __import__("sqlite3").connect(str(db_path))
        conn.execute(
            "INSERT INTO mutation_proposals (id, dimension, target, baseline_value, "
            "candidate_value, status, proposed_at) VALUES (?, ?, ?, ?, ?, 'PROPOSED', ?)",
            ("p1", "prompts", "moat_analyst", '{"old": true}', '{"new": true}',
             "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        registry_path = tmp_path / "model_registry.json"
        registry_path.write_text('{"active": "llama_server"}')
        audit_path = tmp_path / "audit.log"

        result = apply_candidate_to_registry(
            proposal_id="p1",
            registry_path=registry_path,
            db_path=db_path,
            audit_path=audit_path,
            candidate_value='{"new": true}',
            target="moat_analyst",
            dimension="prompts",
            cycle_id="test-cycle",
        )
        assert result["proposal_id"] == "p1"
        data = json.loads(registry_path.read_text())
        assert "candidates" in data
        assert data["candidates"]["p1"]["target"] == "moat_analyst"

    def test_updates_sqlite_status(self, tmp_path) -> None:
        from pmacs.nervous.mutation import apply_candidate_to_registry
        from pmacs.storage.sqlite import init_db

        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = __import__("sqlite3").connect(str(db_path))
        conn.execute(
            "INSERT INTO mutation_proposals (id, dimension, target, baseline_value, "
            "candidate_value, status, proposed_at) VALUES (?, ?, ?, ?, ?, 'PROPOSED', ?)",
            ("p1", "prompts", "moat", '{}', '{}', "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        registry_path = tmp_path / "model_registry.json"
        registry_path.write_text('{}')
        audit_path = tmp_path / "audit.log"

        apply_candidate_to_registry(
            "p1", registry_path, db_path, audit_path,
            cycle_id="test",
        )

        conn = __import__("sqlite3").connect(str(db_path))
        row = conn.execute(
            "SELECT status FROM mutation_proposals WHERE id = 'p1'"
        ).fetchone()
        conn.close()
        assert row[0] == "OPERATOR_PROMOTED"

    def test_writes_audit_event(self, tmp_path) -> None:
        from pmacs.nervous.mutation import apply_candidate_to_registry
        from pmacs.storage.sqlite import init_db

        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = __import__("sqlite3").connect(str(db_path))
        conn.execute(
            "INSERT INTO mutation_proposals (id, dimension, target, baseline_value, "
            "candidate_value, status, proposed_at) VALUES (?, ?, ?, ?, ?, 'PROPOSED', ?)",
            ("p1", "prompts", "moat", '{}', '{}', "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        registry_path = tmp_path / "model_registry.json"
        registry_path.write_text('{}')
        audit_path = tmp_path / "audit.log"

        apply_candidate_to_registry(
            "p1", registry_path, db_path, audit_path,
            cycle_id="cycle-1",
        )

        content = audit_path.read_text()
        assert "mutation_operator_promoted" in content
        assert "p1" in content
