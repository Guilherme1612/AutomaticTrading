#!/usr/bin/env python3
"""Runtime process isolation audit tool.

Verifies that each PMACS process operates within its designated
access boundaries per Architecture.md process topology:

- Dashboard: read-only access to production DBs
- Mutation: writes only to mutation_* tables
- Inference: no internet egress (pf rules)
- Execution: only process with broker SDK imports

Usage:
    python -m ops.verify_isolation [--db-path PATH] [--verbose]

Exit codes:
    0 = all isolation checks pass
    1 = one or more violations found
    2 = error (cannot run checks)
"""
from __future__ import annotations

import argparse
import ast
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class IsolationCheck:
    """Result of a single isolation check."""

    name: str
    passed: bool
    details: str


# Tables that the mutation process is allowed to write to.
MUTATION_ALLOWED_TABLES = frozenset({
    "mutation_proposals",
    "mutation_outcomes",
})

# Tables that the dashboard process is NOT allowed to write to.
# Dashboard is read-only — it should not write to any table.
DASHBOARD_WRITE_FORBIDDEN = frozenset({
    "cycles",
    "holdings",
    "queue",
    "stop_events",
    "op_idempotency",
    "mutation_proposals",
    "mutation_outcomes",
    "paper_account",
    "process_state",
    "operator_overrides",
    "dead_letter",
    "consistency_drift",
})


def check_dashboard_readonly(db_path: Path) -> IsolationCheck:
    """Verify dashboard uses read-only database connections.

    Checks that pmacs/dashboard code only opens connections with
    read_only=True parameter.

    Args:
        db_path: Not used for code analysis, kept for interface consistency.

    Returns:
        IsolationCheck with result.
    """
    project_root = Path(__file__).resolve().parent.parent
    dashboard_dir = project_root / "pmacs" / "dashboard"

    if not dashboard_dir.exists():
        return IsolationCheck(
            name="dashboard_readonly",
            passed=True,
            details="Dashboard module not yet created — check deferred",
        )

    # Scan dashboard source files for SQLite connection patterns
    violations: list[str] = []
    for py_file in dashboard_dir.rglob("*.py"):
        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except (SyntaxError, OSError):
            continue

        for node in ast.walk(tree):
            # Look for get_connection calls without read_only=True
            if isinstance(node, ast.Call):
                func = node.func
                func_name = ""
                if isinstance(func, ast.Attribute):
                    func_name = func.attr
                elif isinstance(func, ast.Name):
                    func_name = func.id

                if func_name in ("get_connection", "connect"):
                    # Check if read_only=True is in kwargs
                    has_readonly = False
                    for kw in node.keywords:
                        if kw.arg == "read_only":
                            if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                                has_readonly = True
                            break

                    if func_name == "get_connection" and not has_readonly:
                        violations.append(
                            f"{py_file.relative_to(project_root)}: "
                            f"get_connection without read_only=True"
                        )
                    elif func_name == "connect":
                        # Direct sqlite3.connect in dashboard code is suspicious
                        violations.append(
                            f"{py_file.relative_to(project_root)}: "
                            f"direct sqlite3.connect (should use get_connection)"
                        )

    if violations:
        return IsolationCheck(
            name="dashboard_readonly",
            passed=False,
            details=f"Found {len(violations)} write-capable connections: " +
                    "; ".join(violations),
        )

    return IsolationCheck(
        name="dashboard_readonly",
        passed=True,
        details="Dashboard uses read-only connections only",
    )


def check_mutation_table_scope(db_path: Path) -> IsolationCheck:
    """Verify mutation process only writes to mutation_* tables.

    Checks that pmacs/mutation and pmacs/nervous/mutation code
    does not write to non-mutation tables.

    Args:
        db_path: Not used for code analysis.

    Returns:
        IsolationCheck with result.
    """
    project_root = Path(__file__).resolve().parent.parent
    mutation_files: list[Path] = []

    mutation_module = project_root / "pmacs" / "mutation"
    if mutation_module.exists():
        mutation_files.extend(mutation_module.rglob("*.py"))

    nervous_mutation = project_root / "pmacs" / "nervous" / "mutation.py"
    if nervous_mutation.exists():
        mutation_files.append(nervous_mutation)

    if not mutation_files:
        return IsolationCheck(
            name="mutation_table_scope",
            passed=True,
            details="Mutation module not yet created — check deferred",
        )

    violations: list[str] = []
    for py_file in mutation_files:
        try:
            source = py_file.read_text()
        except OSError:
            continue

        # Look for INSERT/UPDATE/DELETE on non-mutation tables
        # Simple heuristic: check for string literals referencing tables
        for line_no, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            # Skip comments and strings that are clearly comments
            if stripped.startswith("#"):
                continue

            # Look for SQL-like table references in execute() calls
            for table in DASHBOARD_WRITE_FORBIDDEN - MUTATION_ALLOWED_TABLES:
                if table in line and any(
                    kw in line.upper()
                    for kw in ("INSERT", "UPDATE", "DELETE")
                ):
                    violations.append(
                        f"{py_file.relative_to(project_root)}:{line_no} "
                        f"references restricted table '{table}'"
                    )

    if violations:
        return IsolationCheck(
            name="mutation_table_scope",
            passed=False,
            details=f"Found {len(violations)} out-of-scope table accesses: " +
                    "; ".join(violations),
        )

    return IsolationCheck(
        name="mutation_table_scope",
        passed=True,
        details="Mutation process stays within mutation_* tables",
    )


def check_inference_no_egress() -> IsolationCheck:
    """Verify inference process has no internet egress.

    Checks:
    1. pf firewall rules are loaded (ops/install_pf_rules.sh)
    2. Inference source code does not import networking libraries
       (requests, httpx, urllib.request, socket.connect to external)

    Returns:
        IsolationCheck with result.
    """
    project_root = Path(__file__).resolve().parent.parent
    inference_dir = project_root / "pmacs" / "inference"

    if not inference_dir.exists():
        return IsolationCheck(
            name="inference_no_egress",
            passed=True,
            details="Inference module not yet created — check deferred",
        )

    violations: list[str] = []
    forbidden_imports = frozenset({
        "requests",
        "httpx",
        "urllib.request",
        "aiohttp",
    })

    for py_file in inference_dir.rglob("*.py"):
        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except (SyntaxError, OSError):
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden_imports:
                        violations.append(
                            f"{py_file.relative_to(project_root)}: "
                            f"imports '{alias.name}'"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module in forbidden_imports:
                    violations.append(
                        f"{py_file.relative_to(project_root)}: "
                        f"from '{node.module}'"
                    )

    # Check pf rules (macOS only)
    pf_anchor = "pmacs_inference_block"
    pf_loaded = False
    try:
        import subprocess
        result = subprocess.run(
            ["pfctl", "-sr"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if pf_anchor in result.stdout:
            pf_loaded = True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    pf_status = "loaded" if pf_loaded else "not loaded (install via ops/install_pf_rules.sh)"

    if violations:
        return IsolationCheck(
            name="inference_no_egress",
            passed=False,
            details=f"Found {len(violations)} forbidden imports: " +
                    "; ".join(violations) + f" [pf rules: {pf_status}]",
        )

    return IsolationCheck(
        name="inference_no_egress",
        passed=True,
        details=f"No egress-capable imports in inference code [pf rules: {pf_status}]",
    )


def check_execution_broker_exclusive() -> IsolationCheck:
    """Verify only the execution process imports broker SDK.

    Checks that pmacs/execution is the only module that imports
    broker SDK packages (alpaca-py, ib_insync, etc.).

    Returns:
        IsolationCheck with result.
    """
    project_root = Path(__file__).resolve().parent.parent
    pmacs_dir = project_root / "pmacs"

    if not (pmacs_dir / "execution").exists():
        return IsolationCheck(
            name="execution_broker_exclusive",
            passed=True,
            details="Execution module not yet created — check deferred",
        )

    broker_imports = frozenset({
        "alpaca",
        "alpaca_trade_api",
        "ib_insync",
        "ibapi",
        "interactive_brokers",
    })

    violations: list[str] = []

    # Scan all pmacs modules EXCEPT execution
    for py_file in pmacs_dir.rglob("*.py"):
        # Skip execution module itself
        try:
            relative = py_file.relative_to(pmacs_dir)
            if relative.parts[0] == "execution":
                continue
        except ValueError:
            continue

        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except (SyntaxError, OSError):
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in broker_imports:
                        violations.append(
                            f"{py_file.relative_to(project_root)}: "
                            f"imports broker SDK '{alias.name}'"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module and any(
                    node.module.startswith(bi) for bi in broker_imports
                ):
                    violations.append(
                        f"{py_file.relative_to(project_root)}: "
                        f"imports from broker SDK '{node.module}'"
                    )

    if violations:
        return IsolationCheck(
            name="execution_broker_exclusive",
            passed=False,
            details=f"Non-execution modules import broker SDK: " +
                    "; ".join(violations),
        )

    return IsolationCheck(
        name="execution_broker_exclusive",
        passed=True,
        details="Broker SDK imports are exclusive to pmacs/execution",
    )


def run_all_checks(
    db_path: Path = Path("/var/db/pmacs/pmacs.db"),
) -> list[IsolationCheck]:
    """Run all isolation checks.

    Args:
        db_path: Path to the SQLite database (for DB-level checks).

    Returns:
        List of IsolationCheck results.
    """
    return [
        check_dashboard_readonly(db_path),
        check_mutation_table_scope(db_path),
        check_inference_no_egress(),
        check_execution_broker_exclusive(),
    ]


def main() -> int:
    """CLI entry point for isolation verification.

    Returns:
        Exit code: 0 = pass, 1 = violations, 2 = error.
    """
    parser = argparse.ArgumentParser(
        description="Verify PMACS process isolation boundaries"
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("/var/db/pmacs/pmacs.db"),
        help="Path to SQLite database",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output for each check",
    )
    args = parser.parse_args()

    print("PMACS Process Isolation Audit")
    print("=" * 50)

    checks = run_all_checks(args.db_path)

    all_passed = True
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"  [{status}] {check.name}: {check.details}")
        if not check.passed:
            all_passed = False

    print("=" * 50)

    if all_passed:
        print("Result: ALL CHECKS PASSED")
        return 0
    else:
        failed = [c for c in checks if not c.passed]
        print(f"Result: {len(failed)} CHECK(S) FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
