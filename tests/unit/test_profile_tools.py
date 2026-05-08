"""Tests for ops/profile_cycle.py and ops/profile_memory.py — Phase 15 profiling tools."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent


class TestProfileCycle:
    """Cycle throughput profiler tests."""

    def test_simulate_passes(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "ops" / "profile_cycle.py")],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "PASS" in result.stdout

    def test_json_output(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "ops" / "profile_cycle.py"), "--json"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "pass" in data
        assert "phases" in data
        assert "total" in data
        assert len(data["phases"]) >= 5

    def test_with_mutation(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "ops" / "profile_cycle.py"), "--with-mutation"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "Mutation A/B" in result.stdout

    def test_custom_symbols(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "ops" / "profile_cycle.py"), "--symbols", "10"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0


class TestProfileMemory:
    """Memory profiler tests."""

    def test_runs_passes(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "ops" / "profile_memory.py")],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "PASS" in result.stdout

    def test_json_output(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "ops" / "profile_memory.py"), "--json"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "pass" in data
        assert "components" in data
        assert "peak_budget_gb" in data
        assert data["peak_budget_gb"] == 50.0

    def test_budget_table_present(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "ops" / "profile_memory.py")],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "Qwen3.6-35B-A3B" in result.stdout
        assert "49.0GB" in result.stdout or "49." in result.stdout
