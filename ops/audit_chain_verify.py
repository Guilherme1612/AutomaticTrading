#!/usr/bin/env python3
"""Standalone audit chain verification tool (Phase 15 exit test #4).

Usage:
    python ops/audit_chain_verify.py              # Full verification
    python ops/audit_chain_verify.py --after 100  # Last 100 entries
    python ops/audit_chain_verify.py --json       # JSON output for CI
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _find_project_root() -> Path:
    """Walk upward from cwd to find directory containing pmacs/."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / "pmacs").is_dir():
            return parent
    return cwd


def _parse_log_metadata(path: Path) -> tuple[int, str | None, str | None]:
    """Count entries and extract first/last timestamps from audit log."""
    count = 0
    first_ts = None
    last_ts = None
    if not path.exists():
        return 0, None, None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 5:
                count += 1
                ts = parts[0]
                if first_ts is None:
                    first_ts = ts
                last_ts = ts
    return count, first_ts, last_ts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify PMACS audit log hash chain integrity",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Path to audit.log (default: data/audit.log relative to project root)",
    )
    parser.add_argument(
        "--after",
        type=int,
        default=None,
        metavar="N",
        help="Verify only the last N entries (incremental verify)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-entry status and summary counts",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output results as JSON",
    )
    args = parser.parse_args(argv)

    # Resolve log file path
    if args.log_file:
        log_path = Path(args.log_file)
    else:
        project_root = _find_project_root()
        log_path = project_root / "data" / "audit.log"

    if not log_path.exists():
        if args.json_output:
            print(json.dumps({
                "pass": False,
                "entries": 0,
                "first_ts": None,
                "last_ts": None,
                "error": f"File not found: {log_path}",
            }))
        else:
            print(f"ERROR: File not found: {log_path}", file=sys.stderr)
        return 2

    # Lazy import — project root must be on sys.path for pmacs imports
    project_root = _find_project_root()
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from pmacs.storage.audit import AuditVerifier

    verifier = AuditVerifier(log_path)

    if args.after is not None:
        ok, error = verifier.verify_incremental(last_n=args.after)
    else:
        ok, error = verifier.verify_full()

    entries, first_ts, last_ts = _parse_log_metadata(log_path)

    if args.json_output:
        print(json.dumps({
            "pass": ok,
            "entries": entries,
            "first_ts": first_ts,
            "last_ts": last_ts,
            "error": error or None,
        }))
    elif args.verbose:
        if ok:
            print(f"AUDIT CHAIN INTACT")
        else:
            print(f"AUDIT CHAIN BROKEN: {error}")
        print(f"  Entries: {entries}")
        if first_ts:
            print(f"  First:   {first_ts}")
        if last_ts:
            print(f"  Last:    {last_ts}")
    else:
        if ok:
            print(f"AUDIT CHAIN INTACT ({entries} entries)")
        else:
            print(f"AUDIT CHAIN BROKEN: {error}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
