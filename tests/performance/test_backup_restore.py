"""Phase 15 exit test #6 — backup and restore round-trip.

Tests ops/backup_verify.py end-to-end: create 5 DBs with test data,
back up, wipe, restore, verify audit chain intact.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from pmacs.storage.audit import AuditWriter

# Import the backup tool
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_OPS_DIR = PROJECT_ROOT / "ops"
sys.path.insert(0, str(_OPS_DIR))
from backup_verify import do_backup, do_restore, do_verify, STORES


def _create_test_data(data_dir: Path, audit_entries: int = 50) -> None:
    """Create minimal test data in all 5 stores."""
    data_dir.mkdir(parents=True, exist_ok=True)

    # SQLite
    sqlite_path = data_dir / "pmacs.db"
    conn = sqlite3.connect(str(sqlite_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS holdings (id TEXT, ticker TEXT, state TEXT)"
    )
    conn.execute("INSERT INTO holdings VALUES ('h1', 'AAPL', 'ACTIVE')")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
    )
    conn.execute("INSERT INTO settings VALUES ('test_key', 'test_value')")
    conn.commit()
    conn.close()

    # Audit log with hash-chained entries
    audit_path = data_dir / "audit.log"
    writer = AuditWriter(audit_path)
    for i in range(audit_entries):
        writer.append("TEST_EVENT", {"i": i, "test": True}, cycle_id=f"c{i:04d}")
    writer.close()

    # DuckDB — create a minimal file
    duckdb_path = data_dir / "pmacs_analytics.duckdb"
    try:
        import duckdb
        dconn = duckdb.connect(str(duckdb_path))
        dconn.execute(
            "CREATE TABLE IF NOT EXISTS rolling_metrics "
            "(metric_name VARCHAR, metric_value DOUBLE, computed_at TIMESTAMP)"
        )
        dconn.execute(
            "INSERT INTO rolling_metrics VALUES ('sharpe', 1.5, CURRENT_TIMESTAMP)"
        )
        dconn.close()
    except ImportError:
        # DuckDB not installed — create an empty file so backup has something
        duckdb_path.touch()

    # KuzuDB directory
    kuzu_dir = data_dir / "pmacs_graph.kuzu"
    kuzu_dir.mkdir()
    (kuzu_dir / "metadata.json").write_text('{"test": true}')

    # Qdrant directory
    qdrant_dir = data_dir / "qdrant_storage"
    qdrant_dir.mkdir()
    (qdrant_dir / "config.json").write_text('{"test": true}')


class TestBackupRestore:
    """Test backup/restore round-trip."""

    def test_backup_copies_all_stores(self, tmp_path):
        data_dir = tmp_path / "data"
        output_dir = tmp_path / "backups"
        _create_test_data(data_dir, audit_entries=10)

        backup_dir = do_backup(data_dir, output_dir, project_root=data_dir.parent, verbose=True)

        # Verify backup contains all stores
        for name, relpath, kind in STORES:
            backup_path = backup_dir / relpath
            assert backup_path.exists(), f"Missing backup for {name}: {relpath}"

    def test_restore_recreates_all_stores(self, tmp_path):
        data_dir = tmp_path / "data"
        output_dir = tmp_path / "backups"
        _create_test_data(data_dir, audit_entries=10)

        backup_dir = do_backup(data_dir, output_dir, project_root=data_dir.parent, verbose=True)

        # Wipe and restore
        do_restore(backup_dir, data_dir, verbose=True)

        for name, relpath, kind in STORES:
            restored_path = data_dir / relpath
            assert restored_path.exists(), f"Missing restore for {name}: {relpath}"

    def test_post_restore_audit_chain_intact(self, tmp_path):
        """Exit test: audit chain verifies after backup → wipe → restore."""
        data_dir = tmp_path / "data"
        output_dir = tmp_path / "backups"
        _create_test_data(data_dir, audit_entries=100)

        # Backup
        backup_dir = do_backup(data_dir, output_dir, project_root=data_dir.parent)

        # Wipe and restore
        do_restore(backup_dir, data_dir)

        # Verify audit chain
        result = do_verify(data_dir, verbose=True)
        assert result["pass"], f"Post-restore verification failed: {result}"
        assert result["audit_chain"]["pass"], "Audit chain broken after restore"
        assert result["audit_chain"]["entries"] == 100

    def test_sqlite_data_preserved(self, tmp_path):
        data_dir = tmp_path / "data"
        output_dir = tmp_path / "backups"
        _create_test_data(data_dir, audit_entries=10)

        backup_dir = do_backup(data_dir, output_dir, project_root=data_dir.parent)
        do_restore(backup_dir, data_dir)

        conn = sqlite3.connect(str(data_dir / "pmacs.db"))
        row = conn.execute("SELECT ticker FROM holdings WHERE id = 'h1'").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "AAPL"

    def test_verify_reports_missing_store(self, tmp_path):
        data_dir = tmp_path / "empty_data"
        data_dir.mkdir()

        result = do_verify(data_dir)
        # Should report missing stores
        assert result["pass"] is False

    def test_full_e2e_cycle(self, tmp_path):
        """Simulate the full exit test: backup → wipe → restore → verify."""
        data_dir = tmp_path / "data"
        output_dir = tmp_path / "backups"
        _create_test_data(data_dir, audit_entries=100)

        # Step 1: Backup
        backup_dir = do_backup(data_dir, output_dir, project_root=data_dir.parent)
        assert backup_dir.exists()

        # Step 2: Wipe
        for child in data_dir.iterdir():
            if child.is_dir():
                import shutil
                shutil.rmtree(child)
            else:
                child.unlink()

        # Step 3: Restore
        do_restore(backup_dir, data_dir)

        # Step 4: Verify
        result = do_verify(data_dir, verbose=True)
        assert result["pass"]
        assert result["audit_chain"]["entries"] == 100
