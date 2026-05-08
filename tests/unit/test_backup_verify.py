"""Tests for ops/backup_verify.py -- backup and restore verification."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure project root is importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ops.backup_verify import (
    AUDIT_FILE,
    DUCKDB_FILE,
    KUZU_DIR,
    QDRANT_DIR,
    SQLITE_FILE,
    STORES,
    do_backup,
    do_e2e,
    do_restore,
    do_verify,
)
from pmacs.storage.audit import AuditWriter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """Create a fake data directory with all 5 stores populated."""
    d = tmp_path / "data"
    d.mkdir()

    # SQLite
    (d / SQLITE_FILE).write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)

    # KuzuDB directory
    kuzu = d / KUZU_DIR
    kuzu.mkdir()
    (kuzu / "nodes.csv").write_text("id,name\n1,test\n")

    # Qdrant directory
    qdrant = d / QDRANT_DIR
    qdrant.mkdir()
    (qdrant / "storage.sqlite").write_bytes(b"\x00" * 50)

    # DuckDB
    (d / DUCKDB_FILE).write_bytes(b"\x00" * 80)

    # Audit log (valid chain)
    writer = AuditWriter(d / AUDIT_FILE)
    writer.append("CYCLE_START", {"cycle_id": "c001"})
    writer.append("CYCLE_END", {"cycle_id": "c001", "state": "COMPLETE"})
    writer.close()

    return d


@pytest.fixture()
def output_dir(tmp_path: Path) -> Path:
    d = tmp_path / "backups"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBackupCopiesAllStores:
    """backup copies all 5 stores to a timestamped directory."""

    def test_all_stores_copied(self, data_dir: Path, output_dir: Path) -> None:
        backup_dir = do_backup(data_dir, output_dir, verbose=True)

        for name, relpath, kind in STORES:
            backed = backup_dir / relpath
            assert backed.exists(), f"Missing backup for {name}: {relpath}"

    def test_file_sizes_match(self, data_dir: Path, output_dir: Path) -> None:
        backup_dir = do_backup(data_dir, output_dir)

        # Check SQLite
        src_size = (data_dir / SQLITE_FILE).stat().st_size
        dst_size = (backup_dir / SQLITE_FILE).stat().st_size
        assert src_size == dst_size

        # Check DuckDB
        src_size = (data_dir / DUCKDB_FILE).stat().st_size
        dst_size = (backup_dir / DUCKDB_FILE).stat().st_size
        assert src_size == dst_size

    def test_directory_contents_copied(self, data_dir: Path, output_dir: Path) -> None:
        backup_dir = do_backup(data_dir, output_dir)

        # KuzuDB
        assert (backup_dir / KUZU_DIR / "nodes.csv").exists()
        assert (backup_dir / KUZU_DIR / "nodes.csv").read_text() == "id,name\n1,test\n"

        # Qdrant
        assert (backup_dir / QDRANT_DIR / "storage.sqlite").exists()


class TestRestoreReplacesData:
    """restore wipes data dir and replaces from backup."""

    def test_files_present_after_restore(self, data_dir: Path, output_dir: Path) -> None:
        backup_dir = do_backup(data_dir, output_dir)

        # Wipe data dir manually
        for child in data_dir.iterdir():
            if child.is_dir():
                child.rmdir() if not any(child.iterdir()) else None
                import shutil
                shutil.rmtree(child)
            else:
                child.unlink()

        # Nothing left
        assert not any(data_dir.iterdir())

        # Restore
        do_restore(backup_dir, data_dir)

        for name, relpath, kind in STORES:
            assert (data_dir / relpath).exists(), f"Missing after restore: {name}"


class TestVerifyPassesOnValidData:
    """verify returns pass=True on valid data directory."""

    def test_pass(self, data_dir: Path) -> None:
        result = do_verify(data_dir, verbose=True)
        assert result["pass"] is True

    def test_all_stores_reported(self, data_dir: Path) -> None:
        result = do_verify(data_dir)
        for name, _, _ in STORES:
            assert name in result["stores"]
            assert result["stores"][name]["exists"] is True

    def test_audit_chain_passes(self, data_dir: Path) -> None:
        result = do_verify(data_dir)
        assert result["audit_chain"]["pass"] is True
        assert result["audit_chain"]["entries"] == 2


class TestVerifyFailsOnMissingStore:
    """verify detects a missing store."""

    def test_missing_sqlite(self, data_dir: Path) -> None:
        (data_dir / SQLITE_FILE).unlink()
        result = do_verify(data_dir)
        assert result["pass"] is False
        assert result["stores"]["sqlite"]["exists"] is False

    def test_missing_kuzudb(self, data_dir: Path) -> None:
        import shutil
        shutil.rmtree(data_dir / KUZU_DIR)
        result = do_verify(data_dir)
        assert result["pass"] is False
        assert result["stores"]["kuzudb"]["exists"] is False


class TestVerifyAuditChain:
    """verify checks audit chain integrity."""

    def test_valid_chain(self, data_dir: Path) -> None:
        result = do_verify(data_dir)
        assert result["audit_chain"]["pass"] is True

    def test_tampered_chain(self, data_dir: Path) -> None:
        # Tamper with the second line
        audit_path = data_dir / AUDIT_FILE
        lines = audit_path.read_text().strip().split("\n")
        assert len(lines) == 2

        # Corrupt the hash on line 2
        parts = lines[1].split("\t")
        parts[4] = "f" * 64  # bogus hash
        lines[1] = "\t".join(parts)
        audit_path.write_text("\n".join(lines) + "\n")

        result = do_verify(data_dir)
        assert result["audit_chain"]["pass"] is False
        assert result["pass"] is False

    def test_chain_intact_after_backup_restore(self, data_dir: Path, output_dir: Path) -> None:
        backup_dir = do_backup(data_dir, output_dir)

        # Read original audit content
        original = (data_dir / AUDIT_FILE).read_text()

        # Wipe and restore
        for child in data_dir.iterdir():
            if child.is_dir():
                import shutil
                shutil.rmtree(child)
            else:
                child.unlink()

        do_restore(backup_dir, data_dir)

        # Content should be identical
        restored = (data_dir / AUDIT_FILE).read_text()
        assert original == restored

        # Chain should still pass
        result = do_verify(data_dir)
        assert result["audit_chain"]["pass"] is True
        assert result["audit_chain"]["entries"] == 2


class TestE2EFullCycle:
    """Full backup -> wipe -> restore -> verify cycle."""

    def test_e2e_succeeds(self, data_dir: Path, output_dir: Path, monkeypatch) -> None:
        # Monkeypatch to use our output_dir for backups
        import ops.backup_verify as bv

        original_tempfile_mkdtemp = None
        # We need to intercept the tempfile.TemporaryDirectory to use output_dir
        # Simpler: just run e2e which creates its own temp dir
        # The e2e function exits with sys.exit(1) on failure, so we check it doesn't

        # Since do_e2e uses tempfile.TemporaryDirectory internally, and data_dir
        # is on tmp_path, this is safe
        do_e2e(data_dir, verbose=True)


class TestJSONOutput:
    """verify --json produces valid JSON."""

    def test_json_structure(self, data_dir: Path, capsys) -> None:
        result = do_verify(data_dir, as_json=True)

        captured = capsys.readouterr()
        parsed = json.loads(captured.out)

        assert "pass" in parsed
        assert "stores" in parsed
        assert "audit_chain" in parsed
        assert parsed["pass"] is True

    def test_json_on_failure(self, data_dir: Path, capsys) -> None:
        (data_dir / SQLITE_FILE).unlink()
        result = do_verify(data_dir, as_json=True)

        captured = capsys.readouterr()
        parsed = json.loads(captured.out)

        assert parsed["pass"] is False
        assert parsed["stores"]["sqlite"]["exists"] is False
        assert parsed["error"] is None
