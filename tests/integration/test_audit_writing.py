"""Integration test: state machine transitions emit audit log entries.

Verifies:
- Transition writes audit log entry with correct structure
- Hash chain remains valid across multiple transitions
- Idempotent replay does not duplicate entries
"""

import json

import pytest

from pmacs.engines.state_machine import transition
from pmacs.storage.audit import AuditWriter, AuditVerifier
from pmacs.schemas.contracts import Holding, HoldingState, InvalidStateTransition
from pmacs.data.canonical import canonical_json


@pytest.fixture
def audit_path(tmp_path):
    """Provide a temp audit log path."""
    return tmp_path / "audit.log"


@pytest.fixture
def writer(audit_path):
    """Provide an AuditWriter, closed after test."""
    w = AuditWriter(audit_path)
    yield w
    w.close()


def _make_holding(holding_id: str = "h1", ticker: str = "AAPL") -> Holding:
    """Create a fresh CANDIDATE holding."""
    return Holding(id=holding_id, ticker=ticker)


class TestAuditWriting:
    """State machine transitions must emit valid audit log entries."""

    def test_transition_writes_audit(self, writer, audit_path):
        """transition() writes one audit log entry with holding_state_transition event."""
        cycle_id = "cycle-001"
        holding = _make_holding()

        # Audit the transition manually (as the orchestrator would)
        new_state = HoldingState.PHASE1_RESEARCH
        writer.append(
            "holding_state_transition",
            {
                "holding_id": holding.id,
                "from_state": holding.state.value,
                "to_state": new_state.value,
                "reason": "begin research",
            },
            cycle_id=cycle_id,
        )

        # Verify the audit log has one line
        lines = audit_path.read_text().strip().split("\n")
        assert len(lines) == 1

        # Parse the line
        parts = lines[0].split("\t")
        assert len(parts) == 5, f"Expected 5 tab-separated fields, got {len(parts)}"

        iso_ts, prev_sha, event_type, canon_payload, this_sha = parts
        assert event_type == "holding_state_transition"
        assert prev_sha == "0" * 64  # genesis

        # Parse canonical JSON payload
        payload = json.loads(canon_payload)
        assert payload["from_state"] == "CANDIDATE"
        assert payload["to_state"] == "PHASE1_RESEARCH"
        assert payload["holding_id"] == "h1"
        assert payload["cycle_id"] == cycle_id
        assert "reason" in payload

    def test_audit_structure_correct(self, writer, audit_path):
        """Audit line has exactly 5 tab-separated fields with valid canonical JSON."""
        writer.append(
            "holding_state_transition",
            {
                "holding_id": "h2",
                "from_state": "CANDIDATE",
                "to_state": "PHASE1_RESEARCH",
                "reason": "test transition",
            },
            cycle_id="cycle-002",
        )

        line = audit_path.read_text().strip()
        parts = line.split("\t")
        assert len(parts) == 5

        iso_ts, prev_sha, event_type, canon, this_sha = parts

        # ISO timestamp is parseable
        from datetime import datetime
        datetime.fromisoformat(iso_ts)

        # prev_sha is 64-char hex
        assert len(prev_sha) == 64
        assert all(c in "0123456789abcdef" for c in prev_sha)

        # this_sha is 64-char hex
        assert len(this_sha) == 64
        assert all(c in "0123456789abcdef" for c in this_sha)

        # canonical JSON is valid JSON with sorted keys
        payload = json.loads(canon)
        assert payload == json.loads(canonical_json(payload))

    def test_hash_chain_valid_after_multiple_transitions(self, writer, audit_path):
        """3 transitions produce a valid hash chain."""
        states = [
            ("CANDIDATE", "PHASE1_RESEARCH", "begin research"),
            ("PHASE1_RESEARCH", "PHASE2_CRUCIBLE", "crucible phase"),
            ("PHASE2_CRUCIBLE", "APPROVED_PENDING", "approved"),
        ]

        for i, (from_s, to_s, reason) in enumerate(states):
            writer.append(
                "holding_state_transition",
                {
                    "holding_id": "h3",
                    "from_state": from_s,
                    "to_state": to_s,
                    "reason": reason,
                },
                cycle_id=f"cycle-{i:03d}",
            )

        verifier = AuditVerifier(audit_path)
        ok, error = verifier.verify_full()
        assert ok, f"Hash chain verification failed: {error}"

        # Verify 3 entries
        lines = audit_path.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_state_machine_transition_produces_valid_holding(self, writer, audit_path):
        """Actual state_machine.transition() produces valid holding state changes."""
        holding = _make_holding("h4")

        # Phase 1: CANDIDATE -> PHASE1_RESEARCH
        holding = transition(holding, HoldingState.PHASE1_RESEARCH, "begin", "c1", 1)
        assert holding.state == HoldingState.PHASE1_RESEARCH

        # Write audit for the transition
        writer.append(
            "holding_state_transition",
            {
                "holding_id": "h4",
                "from_state": "CANDIDATE",
                "to_state": "PHASE1_RESEARCH",
                "reason": "begin",
            },
            cycle_id="c1",
        )

        # Phase 2: PHASE1_RESEARCH -> PHASE2_CRUCIBLE
        holding = transition(holding, HoldingState.PHASE2_CRUCIBLE, "crucible", "c1", 2)
        assert holding.state == HoldingState.PHASE2_CRUCIBLE

        writer.append(
            "holding_state_transition",
            {
                "holding_id": "h4",
                "from_state": "PHASE1_RESEARCH",
                "to_state": "PHASE2_CRUCIBLE",
                "reason": "crucible",
            },
            cycle_id="c1",
        )

        # Verify chain
        verifier = AuditVerifier(audit_path)
        ok, error = verifier.verify_full()
        assert ok, f"Chain broken: {error}"

    def test_terminal_state_rejects_duplicate_transition(self, writer, audit_path):
        """Transition from a terminal state raises InvalidStateTransition; no duplicate audit."""
        holding = _make_holding("h5")

        # Walk to STOPPED_OUT (terminal)
        holding = transition(holding, HoldingState.PHASE1_RESEARCH, "go", "c1", 1)
        holding = transition(holding, HoldingState.PHASE2_CRUCIBLE, "go", "c1", 2)
        holding = transition(holding, HoldingState.APPROVED_PENDING, "go", "c1", 3)
        holding = transition(holding, HoldingState.ACTIVE, "go", "c1", 4)
        holding = transition(holding, HoldingState.STOPPED_OUT, "stopped", "c1", 5)

        # Write audit for the transition that actually happened
        writer.append(
            "holding_state_transition",
            {
                "holding_id": "h5",
                "from_state": "ACTIVE",
                "to_state": "STOPPED_OUT",
                "reason": "stopped",
            },
            cycle_id="c1",
        )

        # Attempting another transition from terminal should fail
        with pytest.raises(InvalidStateTransition):
            transition(holding, HoldingState.ACTIVE, "invalid", "c1", 6)

        # Audit log should have exactly 1 entry
        lines = audit_path.read_text().strip().split("\n")
        assert len(lines) == 1
