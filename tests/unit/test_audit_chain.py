"""Audit chain tests — Phase 1 exit test #2."""

import tempfile
from pathlib import Path

from pmacs.storage.audit import AuditWriter, AuditVerifier


class TestAuditChain:
    def test_genesis_and_append(self, tmp_path):
        """Genesis → 100 appends → verify passes."""
        log_path = tmp_path / "audit.log"
        writer = AuditWriter(log_path)

        for i in range(100):
            sha = writer.append("test_event", {"index": i, "data": f"event_{i}"}, cycle_id="c1")
            assert len(sha) == 64

        writer.close()

        verifier = AuditVerifier(log_path)
        ok, msg = verifier.verify_full()
        assert ok, f"Full verification failed: {msg}"

    def test_tamper_detection(self, tmp_path):
        """Tamper one line → verify catches it."""
        log_path = tmp_path / "audit.log"
        writer = AuditWriter(log_path)

        for i in range(10):
            writer.append("test_event", {"idx": i}, cycle_id="c1")
        writer.close()

        # Tamper with a line
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 10
        parts = lines[5].split("\t")
        # Corrupt the canonical JSON payload
        parts[3] = parts[3].replace("idx", "TAMPERED")
        lines[5] = "\t".join(parts)
        log_path.write_text("\n".join(lines) + "\n")

        verifier = AuditVerifier(log_path)
        ok, msg = verifier.verify_full()
        assert not ok, "Tampered chain should fail verification"
        assert "tampered" in msg.lower() or "mismatch" in msg.lower()

    def test_incremental_verify(self, tmp_path):
        """Incremental verification passes on valid chain."""
        log_path = tmp_path / "audit.log"
        writer = AuditWriter(log_path)

        for i in range(50):
            writer.append("test_event", {"i": i}, cycle_id="c1")
        writer.close()

        verifier = AuditVerifier(log_path)
        ok, msg = verifier.verify_incremental(last_n=20)
        assert ok, f"Incremental verification failed: {msg}"

    def test_empty_log_verifies(self, tmp_path):
        """Empty log file verifies successfully."""
        log_path = tmp_path / "audit.log"
        log_path.touch()

        verifier = AuditVerifier(log_path)
        ok, msg = verifier.verify_full()
        assert ok

    def test_writer_recover_sha(self, tmp_path):
        """Writer recovers last SHA from existing file."""
        log_path = tmp_path / "audit.log"

        writer1 = AuditWriter(log_path)
        sha1 = writer1.append("event_a", {"x": 1})
        sha2 = writer1.append("event_b", {"x": 2})
        writer1.close()

        # Reopen and append — should chain from sha2
        writer2 = AuditWriter(log_path)
        sha3 = writer2.append("event_c", {"x": 3})
        writer2.close()

        verifier = AuditVerifier(log_path)
        ok, msg = verifier.verify_full()
        assert ok, f"Recovery verification failed: {msg}"
