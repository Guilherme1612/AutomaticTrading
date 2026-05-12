#!/usr/bin/env python3
"""Backup and restore verification tool (Phase 15 exit test #6).

Backs up all 5 PMACS stores, verifies restore integrity.
Spec: Architecture.md S8 (storage), S5.1 (audit chain).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SQLITE_FILE = "pmacs.db"
KUZU_DIR = "pmacs_graph.kuzu"
QDRANT_DIR = "qdrant_storage"
DUCKDB_FILE = "pmacs_analytics.duckdb"
AUDIT_FILE = "audit.log"

STORES = [
    ("sqlite", SQLITE_FILE, "file"),
    ("kuzudb", KUZU_DIR, "dir"),
    ("qdrant", QDRANT_DIR, "dir"),
    ("duckdb", DUCKDB_FILE, "file"),
    ("audit", AUDIT_FILE, "file"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_project_root() -> Path:
    """Walk upward from cwd until we find a directory containing pmacs/."""
    candidate = Path.cwd()
    for _ in range(20):
        if (candidate / "pmacs").is_dir():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    print("ERROR: Cannot locate project root (no pmacs/ directory found).", file=sys.stderr)
    sys.exit(1)


def _timestamp_dirname() -> str:
    return datetime.now(timezone.utc).strftime("pmacs_backup_%Y%m%dT%H%M%SZ")


def _log(msg: str, verbose: bool = False) -> None:
    if verbose:
        print(f"  {msg}")


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def do_backup(data_dir: Path, output_dir: Path, verbose: bool = False) -> Path:
    """Copy all 5 stores to a timestamped backup directory.

    Returns the backup directory path.
    """
    backup_dir = output_dir / _timestamp_dirname()
    backup_dir.mkdir(parents=True, exist_ok=True)

    for name, relpath, kind in STORES:
        src = data_dir / relpath
        dst = backup_dir / relpath

        if not src.exists():
            _log(f"SKIP {name}: {relpath} does not exist", verbose)
            continue

        if kind == "dir":
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        _log(f"COPIED {name}: {relpath}", verbose)

    print(f"Backup complete: {backup_dir}")
    return backup_dir


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def do_restore(backup_dir: Path, data_dir: Path, verbose: bool = False) -> None:
    """Wipe data_dir contents and restore from backup_dir."""
    # Wipe existing data dir contents (the dir itself is kept)
    if data_dir.exists():
        for child in data_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        _log("Wiped data directory", verbose)

    data_dir.mkdir(parents=True, exist_ok=True)

    for name, relpath, kind in STORES:
        src = backup_dir / relpath
        dst = data_dir / relpath

        if not src.exists():
            _log(f"SKIP {name}: {relpath} not in backup", verbose)
            continue

        if kind == "dir":
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        _log(f"RESTORED {name}: {relpath}", verbose)

    print(f"Restore complete: {backup_dir} -> {data_dir}")


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

def do_verify(data_dir: Path, verbose: bool = False, as_json: bool = False) -> dict:
    """Check all stores are readable and audit chain is intact.

    Returns a result dict with pass/fail and per-store details.
    """
    result: dict = {
        "pass": True,
        "stores": {},
        "audit_chain": {"pass": True, "entries": 0},
        "error": None,
    }

    # Check each store exists and is readable
    for name, relpath, kind in STORES:
        path = data_dir / relpath
        info: dict = {"exists": path.exists(), "size": 0}

        if path.exists():
            try:
                if kind == "dir":
                    # Sum file sizes in directory
                    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                    info["size"] = total
                else:
                    info["size"] = path.stat().st_size
                _log(f"OK {name}: {relpath} ({info['size']} bytes)", verbose)
            except OSError as exc:
                info["error"] = str(exc)
                result["pass"] = False
                _log(f"FAIL {name}: {exc}", verbose)
        else:
            result["pass"] = False
            _log(f"MISSING {name}: {relpath}", verbose)

        result["stores"][name] = info

    # Verify audit chain integrity
    audit_path = data_dir / AUDIT_FILE
    if audit_path.exists():
        try:
            # Import here so the module works even if pmacs isn't on sys.path
            sys.path.insert(0, str(_find_project_root()))
            from pmacs.storage.audit import AuditVerifier

            verifier = AuditVerifier(audit_path)
            chain_ok, chain_err = verifier.verify_full()

            # Count entries
            entry_count = 0
            with open(audit_path) as f:
                for line in f:
                    if line.strip():
                        entry_count += 1

            result["audit_chain"] = {
                "pass": chain_ok,
                "entries": entry_count,
                "error": chain_err if not chain_ok else None,
            }
            if not chain_ok:
                result["pass"] = False
            _log(f"Audit chain: {'OK' if chain_ok else 'BROKEN'} ({entry_count} entries)", verbose)
            if not chain_ok:
                _log(f"  Chain error: {chain_err}", verbose)
        except Exception as exc:
            result["audit_chain"] = {"pass": False, "entries": 0, "error": str(exc)}
            result["pass"] = False
            _log(f"Audit chain check failed: {exc}", verbose)
    else:
        # No audit log is not necessarily a failure for the chain check
        # (it may just not exist yet), but we note it
        result["audit_chain"] = {"pass": True, "entries": 0, "note": "audit.log does not exist"}
        _log("No audit.log to verify", verbose)

    if as_json:
        print(json.dumps(result, indent=2))
    else:
        status = "PASS" if result["pass"] else "FAIL"
        print(f"Verification: {status}")

    return result


# ---------------------------------------------------------------------------
# E2E
# ---------------------------------------------------------------------------

def do_e2e(data_dir: Path, verbose: bool = False) -> None:
    """Full end-to-end: backup -> wipe -> restore -> verify."""
    import tempfile

    print("=== E2E: Backup -> Wipe -> Restore -> Verify ===")

    # Step 1: Backup
    print("\n--- Step 1: Backup ---")
    with tempfile.TemporaryDirectory() as tmp:
        backup_dir = do_backup(data_dir, Path(tmp), verbose)

        # Safety: verify backup has content before wiping
        backup_contents = list(backup_dir.iterdir())
        if not backup_contents:
            print("ERROR: Backup directory is empty. Aborting E2E to prevent data loss.", file=sys.stderr)
            sys.exit(1)

        _log(f"Backup contains {len(backup_contents)} items", verbose)

        # Step 2: Wipe data dir
        print("\n--- Step 2: Wipe data directory ---")
        try:
            if data_dir.exists():
                for child in data_dir.iterdir():
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                _log("Data directory wiped", verbose)

            # Step 3: Restore
            print("\n--- Step 3: Restore ---")
            do_restore(backup_dir, data_dir, verbose)
        except Exception:
            print("ERROR: Restore failed! Attempting to recover from backup...", file=sys.stderr)
            try:
                do_restore(backup_dir, data_dir, verbose)
                print("Recovery successful.", file=sys.stderr)
            except Exception as recover_err:
                print(f"CRITICAL: Recovery also failed: {recover_err}", file=sys.stderr)
            raise

    # Step 4: Verify
    print("\n--- Step 4: Verify ---")
    result = do_verify(data_dir, verbose)

    # Report
    print("\n=== E2E Result ===")
    if result["pass"]:
        print("PASS: All stores restored, audit chain intact.")
    else:
        print("FAIL: Issues detected after restore.")
        if result["error"]:
            print(f"  Error: {result['error']}")
        for name, info in result["stores"].items():
            if not info["exists"]:
                print(f"  Missing: {name}")
            elif info.get("error"):
                print(f"  Error in {name}: {info['error']}")
        if not result["audit_chain"]["pass"]:
            print(f"  Audit chain: {result['audit_chain'].get('error', 'broken')}")

        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="PMACS backup and restore verification tool"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- backup --
    p_backup = sub.add_parser("backup", help="Backup all 5 stores")
    p_backup.add_argument("--data-dir", type=Path, default=None,
                          help="Data directory (default: <project_root>/data/)")
    p_backup.add_argument("--output", type=Path, default=None,
                          help="Output directory for backups (default: <project_root>/backups/)")
    p_backup.add_argument("--verbose", "-v", action="store_true")

    # -- restore --
    p_restore = sub.add_parser("restore", help="Restore from backup")
    p_restore.add_argument("--backup-dir", type=Path, required=True,
                           help="Backup directory to restore from")
    p_restore.add_argument("--data-dir", type=Path, default=None,
                           help="Data directory (default: <project_root>/data/)")
    p_restore.add_argument("--verbose", "-v", action="store_true")

    # -- verify --
    p_verify = sub.add_parser("verify", help="Verify all stores + audit chain")
    p_verify.add_argument("--data-dir", type=Path, default=None,
                          help="Data directory (default: <project_root>/data/)")
    p_verify.add_argument("--verbose", "-v", action="store_true")
    p_verify.add_argument("--json", action="store_true",
                          help="Output verification result as JSON")

    # -- e2e --
    p_e2e = sub.add_parser("e2e", help="Full backup->wipe->restore->verify cycle")
    p_e2e.add_argument("--data-dir", type=Path, default=None,
                       help="Data directory (default: <project_root>/data/)")
    p_e2e.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    root = _find_project_root()

    if args.command == "backup":
        data_dir = args.data_dir or (root / "data")
        output_dir = args.output or (root / "backups")
        do_backup(data_dir, output_dir, args.verbose)

    elif args.command == "restore":
        data_dir = args.data_dir or (root / "data")
        do_restore(args.backup_dir, data_dir, args.verbose)

    elif args.command == "verify":
        data_dir = args.data_dir or (root / "data")
        result = do_verify(data_dir, args.verbose, args.json)
        if not result["pass"]:
            sys.exit(1)

    elif args.command == "e2e":
        data_dir = args.data_dir or (root / "data")
        do_e2e(data_dir, args.verbose)


if __name__ == "__main__":
    main()
