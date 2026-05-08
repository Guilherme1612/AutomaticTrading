"""Tests for ops/audit_chain_verify.py -- standalone audit chain verifier."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Project root for resolving the script and imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = PROJECT_ROOT / "ops" / "audit_chain_verify.py"


def _run_cli(*extra_args: str, log_file: str | None = None) -> subprocess.CompletedProcess:
    """Run the CLI script and return CompletedProcess."""
    argv = [sys.executable, str(SCRIPT)]
    if log_file is not None:
        argv.extend(["--log-file", log_file])
    argv.extend(extra_args)
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )


def _write_audit_log(path: Path, n: int = 10) -> None:
    """Create a valid audit log with n entries using AuditWriter."""
    from pmacs.storage.audit import AuditWriter

    writer = AuditWriter(path)
    for i in range(n):
        writer.append("TEST_EVENT", {"seq": i}, cycle_id=f"cycle-{i}")
    writer.close()


def test_full_verify_pass(tmp_path: Path) -> None:
    """Valid audit log should exit 0."""
    log_file = tmp_path / "audit.log"
    _write_audit_log(log_file, n=10)

    result = _run_cli(log_file=str(log_file))
    assert result.returncode == 0, f"stdout={result.stdout} stderr={result.stderr}"
    assert "INTACT" in result.stdout


def test_full_verify_tampered(tmp_path: Path) -> None:
    """Tampered audit log should exit 1."""
    log_file = tmp_path / "audit.log"
    _write_audit_log(log_file, n=10)

    # Tamper: overwrite a character in the middle of the file
    content = log_file.read_text()
    lines = content.strip().split("\n")
    # Mutate the canonical_json payload of line 5 to break the hash
    parts = lines[4].split("\t")
    parts[3] = parts[3].replace('"seq":4', '"seq":999')
    lines[4] = "\t".join(parts)
    log_file.write_text("\n".join(lines) + "\n")

    result = _run_cli(log_file=str(log_file))
    assert result.returncode == 1, f"stdout={result.stdout} stderr={result.stderr}"
    assert "BROKEN" in result.stdout


def test_incremental_verify(tmp_path: Path) -> None:
    """Incremental verify on last 50 of 200 entries should pass."""
    log_file = tmp_path / "audit.log"
    _write_audit_log(log_file, n=200)

    result = _run_cli("--after", "50", log_file=str(log_file))
    assert result.returncode == 0, f"stdout={result.stdout} stderr={result.stderr}"
    assert "INTACT" in result.stdout


def test_file_not_found(tmp_path: Path) -> None:
    """Missing file should exit 2."""
    missing = tmp_path / "nonexistent" / "audit.log"
    result = _run_cli(log_file=str(missing))
    assert result.returncode == 2, f"stdout={result.stdout} stderr={result.stderr}"


def test_json_output(tmp_path: Path) -> None:
    """--json should produce valid JSON with expected structure."""
    log_file = tmp_path / "audit.log"
    _write_audit_log(log_file, n=5)

    result = _run_cli("--json", log_file=str(log_file))
    assert result.returncode == 0, f"stdout={result.stdout} stderr={result.stderr}"

    data = json.loads(result.stdout)
    assert data["pass"] is True
    assert data["entries"] == 5
    assert data["error"] is None
    assert isinstance(data["first_ts"], str)
    assert isinstance(data["last_ts"], str)


def test_verbose_counts_entries(tmp_path: Path) -> None:
    """--verbose should include entry count and timestamp info."""
    log_file = tmp_path / "audit.log"
    _write_audit_log(log_file, n=15)

    result = _run_cli("--verbose", log_file=str(log_file))
    assert result.returncode == 0, f"stdout={result.stdout} stderr={result.stderr}"
    assert "Entries: 15" in result.stdout
    assert "First:" in result.stdout
    assert "Last:" in result.stdout
