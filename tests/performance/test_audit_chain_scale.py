"""Phase 15 exit test #4 — audit chain verification at scale.

Generates 100+ synthetic audit entries via canonical_json + hash-chain,
runs the verifier, and asserts chain integrity. Also verifies tamper
detection.
"""

from __future__ import annotations

import hashlib
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pmacs.data.canonical import canonical_json
from pmacs.constants import AUDIT_GENESIS_PREV_SHA
from pmacs.storage.audit import AuditVerifier, AuditWriter


def _generate_entries(path: Path, count: int) -> list[str]:
    """Generate `count` synthetic audit entries and return their SHAs."""
    writer = AuditWriter(path)
    shas = []
    for i in range(count):
        payload = {
            "cycle_id": f"cycle-{i:04d}",
            "event": f"test_event_{i}",
            "tickers": ["AAPL", "MSFT"],
            "action": "analyze",
        }
        sha = writer.append("CYCLE_START", payload, cycle_id=f"cycle-{i:04d}")
        shas.append(sha)
    writer.close()
    return shas


class TestAuditChainScale:
    """Test audit chain integrity with 100+ entries."""

    def test_chain_verifies_100_entries(self, tmp_path):
        """Exit test: Audit chain verifies after 100+ cycles of accumulated data."""
        log_path = tmp_path / "audit.log"
        _generate_entries(log_path, 150)

        verifier = AuditVerifier(log_path)
        ok, error = verifier.verify_full()
        assert ok, f"Chain verification failed: {error}"

    def test_incremental_verify_100_entries(self, tmp_path):
        log_path = tmp_path / "audit.log"
        _generate_entries(log_path, 150)

        verifier = AuditVerifier(log_path)
        ok, error = verifier.verify_incremental(last_n=100)
        assert ok, f"Incremental verify failed: {error}"

    def test_tamper_detection(self, tmp_path):
        """Tamper with entry 50 → chain breaks at entry 51."""
        log_path = tmp_path / "audit.log"
        _generate_entries(log_path, 100)

        # Tamper: modify a line
        lines = log_path.read_text().splitlines(keepends=True)
        assert len(lines) >= 50

        # Modify the canonical_json field of line 49 (0-indexed)
        parts = lines[49].strip().split("\t")
        assert len(parts) == 5
        parts[3] = canonical_json({"tampered": True})  # Replace canonical payload
        lines[49] = "\t".join(parts) + "\n"
        log_path.write_text("".join(lines))

        verifier = AuditVerifier(log_path)
        ok, error = verifier.verify_full()
        assert not ok, "Chain should be broken after tampering"
        assert "50" in error or "51" in error  # Break at or near tampered entry

    def test_tamper_prev_sha_detection(self, tmp_path):
        """Modifying prev_sha field also detected."""
        log_path = tmp_path / "audit.log"
        _generate_entries(log_path, 100)

        lines = log_path.read_text().splitlines(keepends=True)
        parts = lines[49].strip().split("\t")
        parts[1] = "0" * 64  # Corrupt prev_sha
        lines[49] = "\t".join(parts) + "\n"
        log_path.write_text("".join(lines))

        verifier = AuditVerifier(log_path)
        ok, error = verifier.verify_full()
        assert not ok

    def test_empty_log_verifies(self, tmp_path):
        log_path = tmp_path / "audit.log"
        log_path.touch()

        verifier = AuditVerifier(log_path)
        ok, error = verifier.verify_full()
        assert ok

    def test_nonexistent_log_verifies(self, tmp_path):
        log_path = tmp_path / "nonexistent.log"

        verifier = AuditVerifier(log_path)
        ok, error = verifier.verify_full()
        assert ok

    def test_writer_produces_valid_chain(self, tmp_path):
        """AuditWriter produces a chain that AuditVerifier accepts."""
        log_path = tmp_path / "audit.log"

        writer = AuditWriter(log_path)
        for i in range(200):
            writer.append("TEST_EVENT", {"i": i}, cycle_id=f"c{i}")
        writer.close()

        verifier = AuditVerifier(log_path)
        ok, error = verifier.verify_full()
        assert ok, f"Writer→Verifier chain broken: {error}"

        # Count entries
        with open(log_path) as f:
            count = sum(1 for line in f if line.strip())
        assert count == 200
