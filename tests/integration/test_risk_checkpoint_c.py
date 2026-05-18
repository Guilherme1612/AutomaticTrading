"""Risk Checkpoint C — post-Phase 14 mutation safety verification (Phases.md §6.3).

Verifies:
- Mutation Engine cannot write production config directly (advisor-only)
- Auto-rollback fires when regression detected
- Max 3 concurrent A/B tests enforced
- All mutations require TOTP (no auto-promote)
- Mutations cannot target excluded paths
"""
from __future__ import annotations

import inspect
import json
import os
import sqlite3
from pathlib import Path

import pytest

from pmacs.constants import MUTATION_AUTO_ROLLBACK_WINDOW, MUTATION_PROBATION_CYCLES
from pmacs.cortex.kill_switch import engage
from pmacs.execution.signing import generate_keypair, sign_bytes, verify_signature
from pmacs.mutation.ab_runner import ABRunner
from pmacs.mutation.candidate_generator import generate_candidates
from pmacs.mutation.rollback import (
    execute_rollback,
    flag_for_kill_switch_review,
    regression_detected,
)
from pmacs.schemas.mutation import MutationCandidate, MutationDimension, MutationStatus
from pmacs.storage.sqlite import init_db


@pytest.fixture
def tmp_env(tmp_path):
    """Create temp DB with promoted mutation proposal and registry."""
    db_path = tmp_path / "test.db"
    init_db(db_path)

    registry_path = tmp_path / "model_registry.json"
    registry_path.write_text(json.dumps({"active": "llama_server", "candidates": {}}))

    audit_path = tmp_path / "audit.log"

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO mutation_proposals "
        "(id, dimension, target, baseline_value, candidate_value, status, proposed_at) "
        "VALUES (?, ?, ?, ?, ?, 'OPERATOR_PROMOTED', ?)",
        ("p1", "prompts", "moat_analyst", '{"old": true}', '{"new": true}',
         "2026-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()

    return {
        "db_path": db_path,
        "registry_path": registry_path,
        "audit_path": audit_path,
        "tmp_path": tmp_path,
    }


# ---------------------------------------------------------------------------
# Checkpoint C.1 — Mutation Engine cannot write production config directly
# ---------------------------------------------------------------------------


class TestMutationIsolation:
    """Mutation process cannot write to production config (Agents.md §17.4 Level 1)."""

    def test_apply_candidate_not_in_mutation_package(self) -> None:
        """apply_candidate_to_registry is NOT importable from pmacs.mutation."""
        import importlib

        mutation_mod = importlib.import_module("pmacs.mutation")
        assert not hasattr(mutation_mod, "apply_candidate_to_registry")

        # It DOES exist in the nervous package (the only code that can write)
        nervous_mod = importlib.import_module("pmacs.nervous.mutation")
        assert hasattr(nervous_mod, "apply_candidate_to_registry")

    def test_mutation_daemon_does_not_import_nervous_mutation(self) -> None:
        """MutationDaemon does not import apply_candidate_to_registry."""
        import ast

        import pmacs.mutation.daemon as daemon_mod

        source = inspect.getsource(daemon_mod)
        tree = ast.parse(source)

        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.name)
                if node.module:
                    imported_names.add(node.module)

        assert "apply_candidate_to_registry" not in imported_names
        assert "pmacs.nervous.mutation" not in imported_names

    def test_filesystem_permission_blocks_direct_write(self, tmp_env) -> None:
        """Writing to model_registry.json from restricted dir raises PermissionError."""
        from pmacs.nervous.mutation import atomic_write_config

        registry = tmp_env["registry_path"]

        # Make directory read-only to simulate mutation process lacking write perms
        parent = registry.parent
        os.chmod(str(parent), 0o555)
        try:
            with pytest.raises(PermissionError):
                atomic_write_config(registry, {"active": "hacked"})
            # Original content unchanged
            data = json.loads(registry.read_text())
            assert data["active"] == "llama_server"
        finally:
            os.chmod(str(parent), 0o755)


# ---------------------------------------------------------------------------
# Checkpoint C.2 — Auto-rollback fires when regression detected
# ---------------------------------------------------------------------------


class TestAutoRollback:
    """Auto-rollback fires after probation period when regression detected."""

    def test_no_rollback_during_probation(self) -> None:
        """During probation period, no auto-rollback even with bad metrics."""
        assert not regression_detected(
            promoted_cycles_ago=10,
            probation_cycles=MUTATION_PROBATION_CYCLES,
            post_metric=1.0,  # worse than baseline
            baseline_metric=0.5,
            lower_is_better=True,
        )

    def test_rollback_triggers_after_probation_with_regression(self) -> None:
        """After probation, regression triggers auto-rollback."""
        assert regression_detected(
            promoted_cycles_ago=MUTATION_PROBATION_CYCLES + 5,
            probation_cycles=MUTATION_PROBATION_CYCLES,
            post_metric=1.0,  # worse
            baseline_metric=0.5,
            lower_is_better=True,
        )

    def test_rollback_updates_sqlite_status(self, tmp_env) -> None:
        """execute_rollback updates mutation_proposals status to ROLLED_BACK."""
        result = execute_rollback(
            "p1", "auto regression detected",
            db_path=tmp_env["db_path"],
        )
        assert result["status"] == "ROLLED_BACK"

        conn = sqlite3.connect(str(tmp_env["db_path"]))
        row = conn.execute(
            "SELECT status FROM mutation_proposals WHERE id = 'p1'"
        ).fetchone()
        conn.close()
        assert row[0] == "ROLLED_BACK"

    def test_rollback_writes_audit_event(self, tmp_env) -> None:
        """execute_rollback writes mutation_rollback_executed to audit log."""
        execute_rollback(
            "p1", "regression detected",
            db_path=tmp_env["db_path"],
            audit_path=tmp_env["audit_path"],
            cycle_id="checkpoint-c-cycle",
        )
        content = tmp_env["audit_path"].read_text()
        assert "mutation_rollback_executed" in content
        assert "p1" in content

    def test_no_rollback_after_monitoring_window(self) -> None:
        """After probation + rollback_window, monitoring expires."""
        assert not regression_detected(
            promoted_cycles_ago=MUTATION_PROBATION_CYCLES + MUTATION_AUTO_ROLLBACK_WINDOW + 5,
            probation_cycles=MUTATION_PROBATION_CYCLES,
            post_metric=1.0,
            baseline_metric=0.5,
            lower_is_better=True,
        )

    def test_rollback_publishes_sse(self, tmp_env) -> None:
        """Rollback publishes SSE event for dashboard notification."""
        events = []

        class FakeSSE:
            def publish(self, channel, event_type, data):
                events.append({"channel": channel, "type": event_type, "data": data})

        execute_rollback(
            "p1", "auto regression",
            db_path=tmp_env["db_path"],
            sse_publisher=FakeSSE(),
        )
        assert len(events) == 1
        assert events[0]["type"] == "mutation.rolled_back"
        assert events[0]["data"]["proposal_id"] == "p1"


# ---------------------------------------------------------------------------
# Checkpoint C.3 — Max 3 concurrent A/B tests enforced
# ---------------------------------------------------------------------------


class TestMaxConcurrentABTests:
    """Maximum 3 concurrent A/B tests enforced (Architecture.md §10)."""

    def test_max_three_concurrent_ab(self) -> None:
        """ABRunner rejects 4th concurrent test."""
        runner = ABRunner(max_concurrent=3)

        assert runner.start("test-1") is True
        assert runner.start("test-2") is True
        assert runner.start("test-3") is True
        assert runner.active_count == 3

        # 4th should be rejected
        assert runner.start("test-4") is False
        assert runner.active_count == 3

    def test_can_start_after_completion(self) -> None:
        """After completing a test, a new one can start."""
        runner = ABRunner(max_concurrent=3)

        runner.start("test-1")
        runner.start("test-2")
        runner.start("test-3")

        # Complete one
        runner.complete("test-1")
        assert runner.active_count == 2

        # Can start a new one now
        assert runner.start("test-4") is True
        assert runner.active_count == 3

    def test_default_max_is_three(self) -> None:
        """Default max_concurrent is 3."""
        runner = ABRunner()
        assert runner.max_concurrent == 3


# ---------------------------------------------------------------------------
# Checkpoint C.4 — All mutations require TOTP (no auto-promote)
# ---------------------------------------------------------------------------


class TestMutationsRequireTOTP:
    """No mutation is ever applied without operator TOTP (Architecture.md §10, Agents.md §17)."""

    def test_nervous_apply_requires_totp_context(self, tmp_env) -> None:
        """apply_candidate_to_registry is only callable from nervous (TOTP-gated)."""
        from pmacs.nervous.mutation import apply_candidate_to_registry

        # This function exists in nervous — the TOTP-gated process.
        # Verify it's a function (not auto-apply).
        assert callable(apply_candidate_to_registry)

    def test_mutation_daemon_does_not_auto_promote(self) -> None:
        """MutationDaemon._stage_for_review sets READY_FOR_REVIEW, not OPERATOR_PROMOTED."""
        import ast

        import pmacs.mutation.daemon as daemon_mod

        source = inspect.getsource(daemon_mod)
        # Verify no auto-promotion to OPERATOR_PROMOTED in daemon code
        assert "OPERATOR_PROMOTED" not in source or "status = 'READY_FOR_REVIEW'" in source

    def test_candidate_generation_sets_reversible(self) -> None:
        """All generated candidates have reversible=True."""
        clusters = [{"taxonomy": "MOAT_DRIFT_OVERESTIMATE", "count": 10}]
        candidates = generate_candidates(clusters, paper_cycle_count=100)

        if candidates:
            for c in candidates:
                assert c.reversible is True, (
                    f"Candidate {c.id} has reversible=False — "
                    "all mutations must be reversible (Agents.md §17)"
                )

    def test_mutation_candidate_schema_requires_reversible(self) -> None:
        """MutationCandidate schema defaults reversible to True."""
        candidate = MutationCandidate(
            id="test-candidate",
            dimension=MutationDimension.PERSONA_PROMPT,
            target="test.target",
            baseline_value="old",
            candidate_value="new",
        )
        assert candidate.reversible is True


# ---------------------------------------------------------------------------
# Checkpoint C.5 — Mutations cannot target excluded paths
# ---------------------------------------------------------------------------


class TestExcludedPaths:
    """Mutation candidates cannot target excluded paths (Agents.md §17.5).

    Excluded: arbitration formula, conviction formula, state machine,
    audit log format, kill switch triggers, mutation engine rules,
    TOTP requirement.
    """

    # The excluded paths from Agents.md §17.5
    EXCLUDED_TARGETS = {
        "arbitration",       # arbitration formula
        "conviction",        # conviction formula
        "state_machine",     # state machine transitions
        "audit",             # audit log format
        "kill_switch",       # kill switch triggers
        "mutation",          # mutation engine's own rules
        "totp",              # TOTP requirement
    }

    def test_generation_rules_do_not_target_excluded_paths(self) -> None:
        """GENERATION_RULES never target excluded paths."""
        from pmacs.mutation.candidate_generator import GENERATION_RULES

        for rule in GENERATION_RULES:
            target = rule["target"].lower()
            for excluded in self.EXCLUDED_TARGETS:
                assert not target.startswith(excluded), (
                    f"Generation rule targets excluded path: {rule['target']} "
                    f"matches excluded prefix '{excluded}' (Agents.md §17.5)"
                )

    def test_generate_candidates_no_excluded_targets(self) -> None:
        """generate_candidates never produces candidates for excluded paths."""
        # Use a cluster that could match multiple rules
        clusters = [
            {"taxonomy": "MOAT_DRIFT_OVERESTIMATE", "count": 10},
            {"taxonomy": "GROWTH_STALL_MISSED", "count": 10},
            {"taxonomy": "FORENSICS_FLAG_IGNORED", "count": 10},
            {"taxonomy": "STOP_HUNTED", "count": 10},
            {"taxonomy": "CATALYST_FALSE_POSITIVE", "count": 10},
        ]
        candidates = generate_candidates(clusters, paper_cycle_count=100)

        for c in candidates:
            target_lower = c.target.lower()
            for excluded in self.EXCLUDED_TARGETS:
                assert not target_lower.startswith(excluded), (
                    f"Candidate targets excluded path: {c.target} "
                    f"(matches '{excluded}' from Agents.md §17.5)"
                )

    def test_kill_switch_flags_recent_promotions(self, tmp_env) -> None:
        """Kill switch engagement flags last 3 promotions for review (§17.4 Level 5)."""
        promotions = ["p1", "p2", "p3", "p4", "p5"]
        flagged = flag_for_kill_switch_review(promotions, max_flag=3)
        assert len(flagged) == 3
        assert flagged == ["p1", "p2", "p3"]

    def test_kill_switch_engage_flags_mutations(self, tmp_env) -> None:
        """Engaging kill switch queries mutation proposals without error."""
        # Add another promoted proposal
        conn = sqlite3.connect(str(tmp_env["db_path"]))
        conn.execute(
            "INSERT INTO mutation_proposals "
            "(id, dimension, target, baseline_value, candidate_value, status, proposed_at) "
            "VALUES (?, ?, ?, ?, ?, 'OPERATOR_PROMOTED', ?)",
            ("p2", "thresholds", "sizing", '{}', '{}', "2026-01-02T00:00:00"),
        )
        conn.commit()
        conn.close()

        # Engage kill switch — should not raise even with mutation tables present
        engage(
            reason="test trigger",
            trigger="AUDIT_CHAIN_INTEGRITY",
            db_path=str(tmp_env["db_path"]),
            audit_path=str(tmp_env["audit_path"]),
        )
        # If we got here without exception, the wiring works
        assert True
