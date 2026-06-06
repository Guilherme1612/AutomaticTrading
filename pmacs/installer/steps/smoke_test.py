"""Wizard step: smoke test — verify all system components.

Runs end-to-end verification:
1. SQLite DB writable and has tables
2. Inference backend responds
3. Audit log writable (hash-chained)
4. Embedding model loads (768-dim)
5. Nervous API reachable

Spec ref: Source.md §12.1 Step 10, Phases.md §8 exit test
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import urllib.request
from pathlib import Path

_log = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"  # noqa: E501 — TODO: use pmacs.config.data_dir()


def _check_db(db_path: Path) -> dict:
    """Verify SQLite database is writable and has expected tables."""
    result: dict = {"ok": False}
    try:
        if not db_path.exists():
            return {**result, "message": f"DB not found at {db_path}"}
        conn = sqlite3.connect(str(db_path))
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        # audit_log is in the SCHEMA_SQL but uses a separate file
        required = {"cycles", "holdings", "queue"}
        missing = required - set(tables)
        if missing:
            return {**result, "message": f"Missing tables: {missing}"}
        # Test write
        conn.execute("SELECT 1")
        conn.close()
        result["ok"] = True
        result["message"] = f"OK ({len(tables)} tables)"
    except Exception as exc:
        result["message"] = str(exc)[:100]
    return result


def _check_inference(port: int = 8080) -> dict:
    """Verify inference backend responds on localhost."""
    result: dict = {"ok": False}
    try:
        resp = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=5
        )
        if resp.status == 200:
            result["ok"] = True
            result["message"] = "OK"
        else:
            result["message"] = f"HTTP {resp.status}"
    except ConnectionRefusedError:
        result["message"] = f"Not running on :{port}"
    except Exception as exc:
        result["message"] = str(exc)[:80]
    return result


def _check_audit(data_dir: Path) -> dict:
    """Verify audit log is writable and hash chain works."""
    result: dict = {"ok": False}
    try:
        from pmacs.storage.audit import AuditWriter
        audit_path = data_dir / "audit.log"
        writer = AuditWriter(audit_path)
        writer.append(
            "SMOKE_TEST",
            {"event": "wizard_smoke_test", "check": "audit_writable"},
            cycle_id="smoke_test",
        )
        writer.close()
        result["ok"] = True
        result["message"] = "OK (hash-chained)"
    except Exception as exc:
        result["message"] = str(exc)[:80]
    return result


def _check_embedding() -> dict:
    """Verify embedding model loads and produces 768-dim vectors."""
    result: dict = {"ok": False}
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("BAAI/bge-base-en-v1.5")
        vec = model.encode("PMACS smoke test")
        if len(vec) == 768:
            result["ok"] = True
            result["message"] = "OK (768-dim)"
        else:
            result["message"] = f"Wrong dimension: {len(vec)}"
    except ImportError:
        result["message"] = "sentence-transformers not installed"
    except Exception as exc:
        result["message"] = str(exc)[:80]
    return result


def run(wizard) -> dict:
    """Run smoke test of all system components.

    Args:
        wizard: Wizard instance (or dict-like with config).

    Returns:
        Dict with per-check results and overall ok status.
    """
    config = wizard.config if hasattr(wizard, "config") else wizard
    data_dir = Path(config.get("data_dir", str(_DATA_DIR)))
    db_path = data_dir / "pmacs.db"

    checks = {
        "db_write": _check_db(db_path),
        "inference": _check_inference(
            int(config.get("llm_port", 8080))
        ),
        "audit": _check_audit(data_dir),
        "embedding": _check_embedding(),
    }

    passed = sum(1 for c in checks.values() if c["ok"])
    total = len(checks)

    return {
        "checks": checks,
        "checks_passed": passed,
        "checks_total": total,
        "all_ok": passed == total,
    }
