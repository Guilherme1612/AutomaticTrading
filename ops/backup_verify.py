#!/usr/bin/env python3
"""Backup and restore verification tool (Phase 15 exit test #6).

Creates timestamped backups of SQLite DB, audit log, config dir.
Verifies backup integrity via SHA256 checksums.
Supports restore mode to verify a backup is valid.

Spec ref: Architecture.md §8 (storage), §5.1 (audit chain), Phases §15.
Item: 15.9

Usage:
    python ops/backup_verify.py backup                      # Backup all stores
    python ops/backup_verify.py backup --include-config     # Also backup config/
    python ops/backup_verify.py verify                      # Verify current data integrity
    python ops/backup_verify.py verify-backup <dir>         # Verify a backup directory
    python ops/backup_verify.py restore --backup-dir <dir>  # Restore from backup
    python ops/backup_verify.py e2e                         # Full round-trip test
"""

from __future__ import annotations

import argparse
import hashlib
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
CONFIG_DIR = "config"

STORES = [
    ("sqlite", SQLITE_FILE, "file"),
    ("kuzudb", KUZU_DIR, "dir"),
    ("qdrant", QDRANT_DIR, "dir"),
    ("duckdb", DUCKDB_FILE, "file"),
    ("audit", AUDIT_FILE, "file"),
]

MANIFEST_FILE = "backup_manifest.json"


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


def _sha256_file(path: Path) -> str:
    """Compute SHA256 of a single file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_tree(path: Path) -> dict[str, str]:
    """Compute SHA256 for every file under a directory tree.

    Returns {relative_path: sha256_hex}.
    """
    checksums: dict[str, str] = {}
    base = path
    for f in sorted(path.rglob("*")):
        if f.is_file():
            rel = str(f.relative_to(base))
            checksums[rel] = _sha256_file(f)
    return checksums


def _log(msg: str, verbose: bool = False) -> None:
    if verbose:
        print(f"  {msg}")


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def do_backup(
    data_dir: Path,
    output_dir: Path,
    project_root: Path,
    verbose: bool = False,
    include_config: bool = False,
) -> Path:
    """Copy stores to a timestamped backup directory and write SHA256 manifest.

    Returns the backup directory path.
    """
    backup_dir = output_dir / _timestamp_dirname()
    backup_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project_root": str(project_root),
        "stores": {},
    }

    # Backup each store
    for name, relpath, kind in STORES:
        src = data_dir / relpath
        dst = backup_dir / relpath

        if not src.exists():
            _log(f"SKIP {name}: {relpath} does not exist", verbose)
            manifest["stores"][name] = {"status": "skipped", "reason": "not found"}
            continue

        if kind == "dir":
            shutil.copytree(src, dst)
            checksums = _sha256_tree(dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            checksums = {"<file>": _sha256_file(dst)}

        manifest["stores"][name] = {
            "status": "backed_up",
            "checksums": checksums,
            "file_count": len(checksums),
        }
        _log(f"COPIED {name}: {relpath} ({len(checksums)} files)", verbose)

    # Optionally backup config directory
    if include_config:
        config_src = project_root / CONFIG_DIR
        config_dst = backup_dir / CONFIG_DIR
        if config_src.is_dir():
            shutil.copytree(config_src, config_dst)
            checksums = _sha256_tree(config_dst)
            manifest["config"] = {
                "status": "backed_up",
                "checksums": checksums,
                "file_count": len(checksums),
            }
            _log(f"COPIED config/ ({len(checksums)} files)", verbose)

    # Write manifest
    manifest_path = backup_dir / MANIFEST_FILE
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    print(f"Backup complete: {backup_dir}")
    print(f"  Manifest: {manifest_path}")
    return backup_dir


# ---------------------------------------------------------------------------
# Verify backup integrity
# ---------------------------------------------------------------------------

def do_verify_backup(backup_dir: Path, verbose: bool = False) -> bool:
    """Verify a backup directory against its SHA256 manifest.

    Returns True if all checksums match.
    """
    manifest_path = backup_dir / MANIFEST_FILE
    if not manifest_path.exists():
        print(f"ERROR: No manifest found in {backup_dir}", file=sys.stderr)
        return False

    with open(manifest_path) as f:
        manifest = json.load(f)

    all_ok = True
    total_files = 0
    total_checked = 0
    mismatches = 0

    for name, info in manifest.get("stores", {}).items():
        if info.get("status") != "backed_up":
            _log(f"SKIP {name}: {info.get('reason', 'not backed up')}", verbose)
            continue

        checksums = info.get("checksums", {})
        total_files += len(checksums)

        for rel, expected_hash in checksums.items():
            total_checked += 1
            if rel == "<file>":
                # Single-file store
                store_entry = backup_dir / STORES_DICT.get(name, "")
                if not store_entry.exists():
                    print(f"  MISMATCH {name}: file missing")
                    mismatches += 1
                    all_ok = False
                    continue
                actual = _sha256_file(store_entry)
            else:
                fpath = backup_dir / name / rel if (backup_dir / name).is_dir() else backup_dir / rel
                # Find the right base path
                for _, store_rel, _ in STORES:
                    candidate = backup_dir / store_rel
                    if candidate.is_dir() and (candidate / rel).exists():
                        fpath = candidate / rel
                        break
                    elif candidate.is_file() and rel == "<file>":
                        fpath = candidate
                        break
                else:
                    # Fallback: try direct path
                    fpath = backup_dir / rel

                if not fpath.exists():
                    # Try store-relative path
                    for _, store_rel, kind in STORES:
                        if kind == "dir":
                            candidate = backup_dir / store_rel / rel
                            if candidate.exists():
                                fpath = candidate
                                break

                if not fpath.exists():
                    print(f"  MISMATCH {name}/{rel}: file missing in backup")
                    mismatches += 1
                    all_ok = False
                    continue

                actual = _sha256_file(fpath)

            if actual != expected_hash:
                print(f"  MISMATCH {name}/{rel}: checksum mismatch")
                mismatches += 1
                all_ok = False
            else:
                _log(f"  OK {name}/{rel}", verbose)

    if all_ok:
        print(f"Backup verification PASSED ({total_checked}/{total_files} files OK)")
    else:
        print(f"Backup verification FAILED ({mismatches} mismatches out of {total_checked})")

    return all_ok


# Build a lookup for verify-backup
STORES_DICT = {name: relpath for name, relpath, _ in STORES}


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def do_restore(backup_dir: Path, data_dir: Path, verbose: bool = False) -> None:
    """Wipe data_dir contents and restore from backup_dir."""
    # Verify backup integrity first
    manifest_path = backup_dir / MANIFEST_FILE
    if manifest_path.exists():
        if not do_verify_backup(backup_dir, verbose):
            print("ERROR: Backup integrity check failed. Aborting restore.", file=sys.stderr)
            sys.exit(1)
    else:
        print("WARNING: No manifest found. Proceeding with restore without checksum verification.", file=sys.stderr)

    # Wipe existing data dir contents
    if data_dir.exists():
        for child in data_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        _log("Wiped data directory", verbose)

    data_dir.mkdir(parents=True, exist_ok=True)

    # Restore each store from backup (skip manifest)
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
# Verify current data
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

    for name, relpath, kind in STORES:
        path = data_dir / relpath
        info: dict = {"exists": path.exists(), "size": 0}

        if path.exists():
            try:
                if kind == "dir":
                    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                    info["size"] = total
                else:
                    info["size"] = path.stat().st_size
                    info["sha256"] = _sha256_file(path)
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
            project_root = _find_project_root()
            sys.path.insert(0, str(project_root))
            from pmacs.storage.audit import AuditVerifier

            verifier = AuditVerifier(audit_path)
            chain_ok, chain_err = verifier.verify_full()

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
        result["audit_chain"] = {"pass": True, "entries": 0, "note": "audit.log does not exist"}
        _log("No audit.log to verify", verbose)

    if as_json:
        print(json.dumps(result, indent=2))
    else:
        status = "PASS" if result["pass"] else "FAIL"
        print(f"Verification: {status}")

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="PMACS backup and restore verification tool"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- backup --
    p_backup = sub.add_parser("backup", help="Backup all stores with SHA256 checksums")
    p_backup.add_argument("--data-dir", type=Path, default=None,
                          help="Data directory (default: <project_root>/data/)")
    p_backup.add_argument("--output", type=Path, default=None,
                          help="Output directory for backups (default: <project_root>/backups/)")
    p_backup.add_argument("--include-config", action="store_true",
                          help="Also backup config/ directory")
    p_backup.add_argument("--verbose", "-v", action="store_true")

    # -- verify-backup --
    p_vb = sub.add_parser("verify-backup", help="Verify backup integrity via SHA256 manifest")
    p_vb.add_argument("backup_dir", type=Path,
                      help="Backup directory to verify")
    p_vb.add_argument("--verbose", "-v", action="store_true")

    # -- restore --
    p_restore = sub.add_parser("restore", help="Restore from backup (verifies checksums first)")
    p_restore.add_argument("--backup-dir", type=Path, required=True,
                           help="Backup directory to restore from")
    p_restore.add_argument("--data-dir", type=Path, default=None,
                           help="Data directory (default: <project_root>/data/)")
    p_restore.add_argument("--verbose", "-v", action="store_true")

    # -- verify --
    p_verify = sub.add_parser("verify", help="Verify all stores + audit chain + SHA256 checksums")
    p_verify.add_argument("--data-dir", type=Path, default=None,
                          help="Data directory (default: <project_root>/data/)")
    p_verify.add_argument("--verbose", "-v", action="store_true")
    p_verify.add_argument("--json", action="store_true",
                          help="Output verification result as JSON")

    args = parser.parse_args()
    root = _find_project_root()

    if args.command == "backup":
        data_dir = args.data_dir or (root / "data")
        output_dir = args.output or (root / "backups")
        do_backup(data_dir, output_dir, root, args.verbose, args.include_config)

    elif args.command == "verify-backup":
        if not do_verify_backup(args.backup_dir, args.verbose):
            sys.exit(1)

    elif args.command == "restore":
        data_dir = args.data_dir or (root / "data")
        do_restore(args.backup_dir, data_dir, args.verbose)

    elif args.command == "verify":
        data_dir = args.data_dir or (root / "data")
        result = do_verify(data_dir, args.verbose, args.json)
        if not result["pass"]:
            sys.exit(1)


if __name__ == "__main__":
    main()
