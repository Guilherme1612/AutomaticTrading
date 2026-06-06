#!/usr/bin/env python3
"""System performance profiler (Architecture.md §20).

Profiles all major PMACS subsystems and reports timing against budgets.
Complements ops/profile_cycle.py (cycle throughput) with component-level profiling.

Usage:
    python ops/profile_system.py              # Run all profiles
    python ops/profile_system.py --json       # JSON output for CI
    python ops/profile_system.py --storage    # Storage only
    python ops/profile_system.py --llm        # LLM inference only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ProfileResult:
    component: str
    operation: str
    iterations: int
    total_ms: float
    avg_ms: float
    min_ms: float
    max_ms: float
    budget_ms: float
    pass_: bool
    notes: str = ""


def profile_sqlite(db_path: str | None = None) -> list[ProfileResult]:
    """Profile SQLite read/write operations."""
    import sqlite3
    import tempfile

    results = []
    path = db_path or str(Path(tempfile.mkdtemp()) / "profile.db")
    should_cleanup = db_path is None

    # Write throughput
    conn = sqlite3.connect(path)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS profile_test (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id TEXT, ticker TEXT, verdict TEXT, conviction REAL,
            payload TEXT, created_at TEXT DEFAULT (datetime('now'))
        )""")
        conn.commit()

        n = 1000
        t0 = time.perf_counter()
        for i in range(n):
            conn.execute(
                "INSERT INTO profile_test (cycle_id, ticker, verdict, conviction, payload) VALUES (?, ?, ?, ?, ?)",
                (f"cycle-{i}", "AAPL", "BUY", 0.75, '{"test": true}'),
            )
        conn.commit()
        write_ms = (time.perf_counter() - t0) * 1000

        results.append(ProfileResult(
            component="SQLite", operation="write_1k_rows",
            iterations=n, total_ms=write_ms, avg_ms=write_ms / n,
            min_ms=0, max_ms=0, budget_ms=5000,
            pass_=write_ms < 5000,
            notes="1K sequential inserts with commit",
        ))

        # Read throughput
        t0 = time.perf_counter()
        for _ in range(n):
            conn.execute("SELECT * FROM profile_test WHERE ticker = ? LIMIT 10", ("AAPL",)).fetchall()
        read_ms = (time.perf_counter() - t0) * 1000

        results.append(ProfileResult(
            component="SQLite", operation="read_1k_queries",
            iterations=n, total_ms=read_ms, avg_ms=read_ms / n,
            min_ms=0, max_ms=0, budget_ms=3000,
            pass_=read_ms < 3000,
            notes="1K SELECT queries with parameterized WHERE",
        ))

        # Audit write (hash-chained)
        t0 = time.perf_counter()
        for i in range(100):
            conn.execute(
                "INSERT INTO profile_test (cycle_id, ticker, verdict, conviction, payload) VALUES (?, ?, ?, ?, ?)",
                (f"audit-{i}", "TSLA", "HOLD", 0.5, '{"prev_sha": "abc123"}'),
            )
        conn.commit()
        audit_ms = (time.perf_counter() - t0) * 1000

        results.append(ProfileResult(
            component="SQLite", operation="audit_100_writes",
            iterations=100, total_ms=audit_ms, avg_ms=audit_ms / 100,
            min_ms=0, max_ms=0, budget_ms=1000,
            pass_=audit_ms < 1000,
            notes="100 hash-chained audit writes (simulated)",
        ))

        conn.execute("DROP TABLE profile_test")
    finally:
        conn.close()
        if should_cleanup:
            Path(path).unlink(missing_ok=True)

    return results


def profile_storage_adapters() -> list[ProfileResult]:
    """Profile storage adapter operations."""
    results = []

    # Qdrant embedding (uses model if available, hash fallback otherwise)
    from pmacs.storage.qdrant import QdrantAdapter
    qa = QdrantAdapter()

    n = 100
    t0 = time.perf_counter()
    for i in range(n):
        qa.get_embedding(f"test embedding {i}")
    embed_ms = (time.perf_counter() - t0) * 1000

    # Budget depends on whether model is loaded or using hash fallback
    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401
        embed_budget = 15000  # Real model inference: ~100ms/call is typical
        notes = "100 embeddings with sentence-transformers model"
    except ImportError:
        embed_budget = 1000  # Hash fallback should be fast
        notes = "100 hash-based dummy embeddings (no model)"

    results.append(ProfileResult(
        component="Qdrant", operation="embedding_generation",
        iterations=n, total_ms=embed_ms, avg_ms=embed_ms / n,
        min_ms=0, max_ms=0, budget_ms=embed_budget,
        pass_=embed_ms < embed_budget,
        notes=notes,
    ))

    # KuzuDB stub mode
    from pmacs.storage.kuzu import KuzuDBAdapter
    ka = KuzuDBAdapter()

    n = 100
    t0 = time.perf_counter()
    for i in range(n):
        ka.write_failed_assumption(
            fa_id=f"fa_prof_{i}", taxonomy="CATALYST_MISMATCH",
            severity=0.5, holding_id="h1", cycle_id="c1", summary="profile test",
        )
    kuzu_ms = (time.perf_counter() - t0) * 1000

    results.append(ProfileResult(
        component="KuzuDB", operation="failed_assumption_write_stub",
        iterations=n, total_ms=kuzu_ms, avg_ms=kuzu_ms / n,
        min_ms=0, max_ms=0, budget_ms=500,
        pass_=kuzu_ms < 500,
        notes="100 FailedAssumption writes in stub mode",
    ))

    # DuckDB stub mode
    from pmacs.storage.duckdb import DuckDBAdapter
    da = DuckDBAdapter()

    t0 = time.perf_counter()
    da.init_tables()
    duckdb_init_ms = (time.perf_counter() - t0) * 1000

    results.append(ProfileResult(
        component="DuckDB", operation="init_tables",
        iterations=1, total_ms=duckdb_init_ms, avg_ms=duckdb_init_ms,
        min_ms=0, max_ms=0, budget_ms=5000,
        pass_=duckdb_init_ms < 5000,
        notes="Create 4 analytics tables (first run)",
    ))

    return results


def profile_engines() -> list[ProfileResult]:
    """Profile deterministic engine computations."""
    results = []

    # Conviction engine
    from pmacs.engines.conviction import compute_conviction
    from pmacs.schemas.arbitration import Arbitrated
    import random

    n = 1000
    t0 = time.perf_counter()
    for i in range(n):
        p_up = random.uniform(0.3, 0.7)
        p_down = random.uniform(0.05, 0.2)
        p_flat = 1.0 - p_up - p_down
        arb = Arbitrated(
            ticker="AAPL",
            cycle_id=f"cycle-{i}",
            p_up=p_up,
            p_flat=p_flat,
            p_down=p_down,
        )
        compute_conviction(arb, crucible_severity=0.5, ev_multiple=1.0)
    conviction_ms = (time.perf_counter() - t0) * 1000

    results.append(ProfileResult(
        component="ConvictionEngine", operation="compute_conviction_1k",
        iterations=n, total_ms=conviction_ms, avg_ms=conviction_ms / n,
        min_ms=0, max_ms=0, budget_ms=500,
        pass_=conviction_ms < 500,
        notes="1K conviction computations with random signals",
    ))

    # Pricing / EV engine
    from pmacs.engines.pricing import EvInputs, compute_ev

    n = 500
    t0 = time.perf_counter()
    for _ in range(n):
        compute_ev(EvInputs(
            p_up=random.uniform(0.3, 0.7),
            p_down=random.uniform(0.1, 0.3),
            target_gain_pct=random.uniform(0.05, 0.15),
            stop_loss_pct=random.uniform(0.05, 0.10),
        ))
    pricing_ms = (time.perf_counter() - t0) * 1000

    results.append(ProfileResult(
        component="PricingEngine", operation="compute_ev_500",
        iterations=n, total_ms=pricing_ms, avg_ms=pricing_ms / n,
        min_ms=0, max_ms=0, budget_ms=500,
        pass_=pricing_ms < 500,
        notes="500 EV calculations",
    ))

    return results


def profile_all() -> list[ProfileResult]:
    """Run all profiling suites."""
    results = []
    results.extend(profile_sqlite())
    results.extend(profile_storage_adapters())
    results.extend(profile_engines())
    return results


def format_report(results: list[ProfileResult]) -> str:
    lines = ["PMACS System Performance Profile", "=" * 70, ""]

    by_component: dict[str, list[ProfileResult]] = {}
    for r in results:
        by_component.setdefault(r.component, []).append(r)

    for component, items in by_component.items():
        lines.append(f"  {component}")
        lines.append("-" * 50)
        for r in items:
            status = "PASS" if r.pass_ else "FAIL"
            lines.append(f"    [{status}] {r.operation}")
            lines.append(f"           {r.iterations}x  avg: {r.avg_ms:.2f}ms  total: {r.total_ms:.0f}ms  budget: {r.budget_ms:.0f}ms")
            if r.notes:
                lines.append(f"           {r.notes}")
        lines.append("")

    all_pass = all(r.pass_ for r in results)
    lines.append(f"  [{'PASS' if all_pass else 'FAIL'}] Overall: {len(results)} benchmarks")
    return "\n".join(lines)


def format_json(results: list[ProfileResult]) -> str:
    return json.dumps({
        "pass": all(r.pass_ for r in results),
        "total_benchmarks": len(results),
        "results": [
            {
                "component": r.component,
                "operation": r.operation,
                "iterations": r.iterations,
                "avg_ms": round(r.avg_ms, 3),
                "total_ms": round(r.total_ms, 1),
                "budget_ms": r.budget_ms,
                "pass": r.pass_,
                "notes": r.notes,
            }
            for r in results
        ],
    }, indent=2)


def main():
    parser = argparse.ArgumentParser(description="PMACS system performance profiler")
    parser.add_argument("--json", action="store_true", help="JSON output for CI")
    parser.add_argument("--storage", action="store_true", help="Storage adapters only")
    parser.add_argument("--llm", action="store_true", help="LLM inference only (requires llama-server)")
    parser.add_argument("--engines", action="store_true", help="Deterministic engines only")
    args = parser.parse_args()

    if args.storage:
        results = profile_storage_adapters()
    elif args.engines:
        results = profile_engines()
    else:
        results = profile_all()

    if args.json:
        print(format_json(results))
    else:
        print(format_report(results))

    sys.exit(0 if all(r.pass_ for r in results) else 1)


if __name__ == "__main__":
    main()
