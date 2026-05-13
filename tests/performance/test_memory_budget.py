"""Phase 15 exit test #3 — memory budget profiling.

Verifies ops/profile_memory.py budget checking logic and reporting.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILER = PROJECT_ROOT / "ops" / "profile_memory.py"


class TestMemoryBudgetProfiler:
    """Test the memory profiler produces valid output."""

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
        assert "components" in data
        assert "peak_budget_gb" in data

    def test_budget_values_match_spec(self):
        """Verify budget constants match Architecture.md §20.2."""
        from ops.profile_memory import (
            MEMORY_BUDGETS,
            PEAK_BUDGET_GB,
            TOTAL_USED_GB,
            TOTAL_HEADROOM_GB,
        )

        # Peak budget = 50GB (exit test threshold)
        assert PEAK_BUDGET_GB <= 50.0
        # Total used < 50GB
        assert TOTAL_USED_GB <= 50.0
        # Python processes combined < 3GB
        python_key = "All pmacs-* Python processes combined"
        assert MEMORY_BUDGETS[python_key]["budget_gb"] <= 3.0
        # DB buffers < 6GB
        db_key = "KuzuDB / Qdrant / DuckDB / SQLite buffers"
        assert MEMORY_BUDGETS[db_key]["budget_gb"] <= 6.0
        # Model + KV + overhead ~31GB
        model_budgets = sum(
            v["budget_gb"]
            for k, v in MEMORY_BUDGETS.items()
            if v["process"] == "pmacs-inference"
        )
        assert 20.0 <= model_budgets <= 35.0

    def test_measure_memory_structure(self):
        from ops.profile_memory import measure_memory

        data = measure_memory()
        assert "components" in data
        assert "pass" in data
        assert "total_system_gb" in data
        assert data["total_system_gb"] > 0

    def test_current_process_rss_reported(self):
        """The profiler should report actual RSS for the Python process component."""
        from ops.profile_memory import measure_memory

        data = measure_memory()
        python_component = [
            c for c in data["components"]
            if "pmacs-*" in c["name"]
        ]
        assert len(python_component) == 1
        assert python_component[0]["actual_gb"] is not None
        assert python_component[0]["actual_gb"] >= 0
