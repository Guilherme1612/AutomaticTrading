"""Integration tests for episodic context injection (Agents.md §17, Architecture.md §1.13).

Tests:
- Full data brief is non-empty and ≤200 words
- No-history brief is minimal (macro only)
- Content hash is deterministic (same inputs → same hash)
- inject_and_log produces audit event with correct fields
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from pmacs.agents.episodic_context import build_context_brief, inject_and_log
from pmacs.logsys.debug_log import set_log_path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _setup_debug_log(tmp_path: Path):
    """Redirect debug log to a temp file for each test."""
    log_file = tmp_path / "debug.jsonl"
    set_log_path(log_file)
    yield
    # Reset module-level file descriptor
    import pmacs.logsys.debug_log as _mod
    if _mod._log_fd is not None:
        _mod._log_fd.close()
        _mod._log_fd = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFullDataBrief:
    """Context brief with all data sources populated."""

    def test_non_empty(self):
        brief = build_context_brief(
            persona="growth_hunter",
            ticker="AAPL",
            regime="EXPANSION",
            regime_confidence=0.8,
            recent_failures=[
                {"taxonomy": "STOP_HUNTED", "summary": "Stopped out then recovered"},
                {"taxonomy": "CATALYST_TIMING_MISREAD", "summary": "Earnings timing off"},
            ],
            persona_brier=0.22,
            persona_cycle_count=15,
            recent_lessons=["Wait for confirmation before entry", "Size down in uncertain regime"],
            affinity_data={"avg_brier": 0.22, "cycle_count": 15},
            fde_history=[
                {"taxonomy": "STOP_HUNTED", "severity": 0.7},
                {"taxonomy": "ENTRY_TIMING_POOR", "severity": 0.3},
            ],
        )
        assert len(brief) > 0
        assert "EXPANSION" in brief
        assert "STOP_HUNTED" in brief
        assert "Brier" in brief
        assert "LESSONS" in brief
        assert "FAILURE PATTERNS" in brief

    def test_within_word_limit(self):
        brief = build_context_brief(
            persona="moat_analyst",
            ticker="TSLA",
            regime="LATE_CYCLE",
            regime_confidence=0.65,
            recent_failures=[
                {"taxonomy": f"TYPE_{i}", "summary": f"Failure {i} " * 30}
                for i in range(10)
            ],
            recent_lessons=["Lesson " * 50 for _ in range(10)],
            fde_history=[
                {"taxonomy": f"TX_{i}", "severity": 0.5}
                for i in range(10)
            ],
        )
        word_count = len(brief.split())
        assert word_count <= 200, f"Brief has {word_count} words, exceeds 200 limit"


class TestNoHistoryBrief:
    """Context brief with no history data — macro regime only."""

    def test_macro_only(self):
        brief = build_context_brief(
            persona="growth_hunter",
            ticker="NVDA",
            regime="RECOVERY",
            regime_confidence=0.55,
        )
        assert "RECOVERY" in brief
        assert "MACRO CONTEXT" in brief
        # No track record section
        assert "TRACK RECORD" not in brief
        assert "FAILURES" not in brief
        assert "LESSONS" not in brief

    def test_minimal_brief_not_empty(self):
        brief = build_context_brief(
            persona="forensics",
            ticker="XYZ",
        )
        assert len(brief) > 0
        assert "UNCERTAIN" in brief


class TestDeterministicHash:
    """Same inputs → same content hash."""

    def test_deterministic(self):
        kwargs = dict(
            persona="growth_hunter",
            ticker="AAPL",
            regime="EXPANSION",
            regime_confidence=0.8,
            recent_failures=[{"taxonomy": "STOP_HUNTED", "summary": "test"}],
            persona_brier=0.25,
            persona_cycle_count=10,
            recent_lessons=["Wait for confirmation"],
        )
        brief1 = build_context_brief(**kwargs)
        brief2 = build_context_brief(**kwargs)
        hash1 = hashlib.sha256(brief1.encode()).hexdigest()
        hash2 = hashlib.sha256(brief2.encode()).hexdigest()
        assert brief1 == brief2
        assert hash1 == hash2

    def test_different_inputs_different_hash(self):
        brief_a = build_context_brief(persona="growth_hunter", ticker="AAPL", regime="EXPANSION")
        brief_b = build_context_brief(persona="growth_hunter", ticker="AAPL", regime="CONTRACTION")
        hash_a = hashlib.sha256(brief_a.encode()).hexdigest()
        hash_b = hashlib.sha256(brief_b.encode()).hexdigest()
        assert hash_a != hash_b


class TestInjectAndLog:
    """inject_and_log should produce audit event with correct fields."""

    def test_returns_brief_and_hash(self, tmp_path: Path):
        log_file = tmp_path / "debug.jsonl"
        set_log_path(log_file)

        brief, content_hash = inject_and_log(
            persona="moat_analyst",
            ticker="MSFT",
            cycle_id="cycle-001",
            regime="EXPANSION",
            regime_confidence=0.9,
        )
        assert len(brief) > 0
        assert len(content_hash) == 64  # SHA-256 hex digest
        # Verify hash matches
        assert content_hash == hashlib.sha256(brief.encode()).hexdigest()

    def test_logs_audit_event(self, tmp_path: Path):
        log_file = tmp_path / "debug.jsonl"
        set_log_path(log_file)

        inject_and_log(
            persona="growth_hunter",
            ticker="AAPL",
            cycle_id="cycle-042",
            regime="RECOVERY",
        )

        # Read the debug log
        import pmacs.logsys.debug_log as _mod
        if _mod._log_fd is not None:
            _mod._log_fd.close()
            _mod._log_fd = None

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) >= 1

        entry = json.loads(lines[-1])
        assert entry["event"] == "episodic_context_injected"
        assert entry["level"] == "INFO"
        assert entry["cycle_id"] == "cycle-042"
        assert entry["payload"]["persona"] == "growth_hunter"
        assert entry["payload"]["ticker"] == "AAPL"
        assert "content_hash" in entry["payload"]
        assert "word_count" in entry["payload"]

    def test_word_count_logged_correctly(self, tmp_path: Path):
        log_file = tmp_path / "debug.jsonl"
        set_log_path(log_file)

        brief, _ = inject_and_log(
            persona="forensics",
            ticker="XYZ",
            cycle_id="cycle-099",
        )

        import pmacs.logsys.debug_log as _mod
        if _mod._log_fd is not None:
            _mod._log_fd.close()
            _mod._log_fd = None

        entry = json.loads(log_file.read_text().strip().split("\n")[-1])
        assert entry["payload"]["word_count"] == len(brief.split())


class TestAffinityIntegration:
    """affinity_data parameter overrides persona_brier/cycle_count."""

    def test_affinity_overrides(self):
        brief = build_context_brief(
            persona="growth_hunter",
            ticker="AAPL",
            persona_brier=0.5,
            persona_cycle_count=2,
            affinity_data={"avg_brier": 0.20, "cycle_count": 30},
        )
        # affinity_data has cycle_count=30 >= 5 so track record shows
        assert "0.200" in brief
        assert "30 cycles" in brief

    def test_no_affinity_low_cycles_no_track_record(self):
        brief = build_context_brief(
            persona="growth_hunter",
            ticker="AAPL",
            persona_brier=0.25,
            persona_cycle_count=3,  # < 5
        )
        assert "TRACK RECORD" not in brief

    def test_affinity_low_cycles_no_track_record(self):
        brief = build_context_brief(
            persona="growth_hunter",
            ticker="AAPL",
            persona_brier=0.25,
            persona_cycle_count=3,
            affinity_data={"avg_brier": 0.25, "cycle_count": 3},  # < 5
        )
        assert "TRACK RECORD" not in brief
