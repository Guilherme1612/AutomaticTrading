"""Phase 15 exit test #2 — cycle throughput profiling.

Verifies ops/profile_cycle.py budget checking logic and that the profiler
framework produces valid output for CI integration.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILER = PROJECT_ROOT / "ops" / "profile_cycle.py"


class TestCycleThroughputProfiler:
    """Test the cycle throughput profiler produces valid output."""

    def test_profiler_runs_and_exits_zero(self):
        result = subprocess.run(
            [sys.executable, str(PROFILER)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"Profiler failed:\n{result.stderr}"
        assert "PASS" in result.stdout

    def test_profiler_json_output(self):
        result = subprocess.run(
            [sys.executable, str(PROFILER), "--json"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["pass"] is True
        assert "phases" in data
        assert len(data["phases"]) >= 5  # At least 5 budget phases

    def test_profiler_with_mutation(self):
        result = subprocess.run(
            [sys.executable, str(PROFILER), "--with-mutation", "--json"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["pass"] is True
        phase_names = [p["name"] for p in data["phases"]]
        assert any("Mutation" in n for n in phase_names)

    def test_budget_values_match_spec(self):
        """Verify budget constants match Architecture.md §20.1."""
        from ops.profile_cycle import PHASE_BUDGETS, TOTAL_BUDGET_S

        # Gatekeeper ≤ 5s
        assert PHASE_BUDGETS["Phase 0: Gatekeeper"]["budget_s"] <= 5
        # Per symbol ≤ 270s
        assert PHASE_BUDGETS["Phase 1: Per symbol (7 personas, 3 slots)"]["budget_s"] <= 270
        # Total cycle ≤ 10800s (3 hours)
        assert TOTAL_BUDGET_S <= 10800
        # Crucible ≤ 900s
        assert PHASE_BUDGETS["Crucible (15 active)"]["budget_s"] <= 900

    def test_simulate_returns_results(self):
        from ops.profile_cycle import simulate_cycle

        results = simulate_cycle(admitted_symbols=16, mutation_active=False)
        assert len(results) >= 5
        assert all(r.pass_ for r in results)

    def test_simulate_16_ticker_universe(self):
        """Verify 16-ticker config (exit test spec says 16 tickers)."""
        from ops.profile_cycle import simulate_cycle

        results = simulate_cycle(admitted_symbols=16, mutation_active=False)
        # Find the Phase 1 total entry
        phase1_total = [r for r in results if "Total" in r.name]
        assert len(phase1_total) == 1
        # Budget should scale: 16 symbols, not 20
        # The profiler uses the admitted_symbols param
        assert phase1_total[0].pass_ is True
