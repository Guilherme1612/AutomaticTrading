"""Integration tests for KuzuDB graph adapter (stub mode).

Validates instantiation, write_failed_assumption, query helpers,
Cypher template patterns, and logging emission.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from pmacs.storage.kuzu import KuzuDBAdapter


def _reset_log_fd():
    """Close and reset the module-level log file descriptor so a new path takes effect."""
    import pmacs.logsys.debug_log as _mod
    if _mod._log_fd is not None:
        _mod._log_fd.close()
        _mod._log_fd = None


# ======================================================================
# Instantiation
# ======================================================================

class TestKuzuInstantiation:
    def test_default_instantiation(self) -> None:
        """KuzuDBAdapter can be created with no arguments."""
        kuzu = KuzuDBAdapter()
        assert kuzu.db_path is None
        assert kuzu._conn is None

    def test_with_db_path(self) -> None:
        """KuzuDBAdapter accepts a db_path parameter."""
        kuzu = KuzuDBAdapter(db_path=Path("/tmp/test.kuzu"))
        assert kuzu.db_path == Path("/tmp/test.kuzu")


# ======================================================================
# write_failed_assumption in stub mode
# ======================================================================

class TestWriteFailedAssumption:
    def test_stub_mode_no_error(self) -> None:
        """write_failed_assumption() executes without error when _conn is None."""
        kuzu = KuzuDBAdapter()
        kuzu.write_failed_assumption(
            fa_id="fa-001",
            taxonomy="CATALYST_FALSE_POSITIVE",
            severity=0.7,
            holding_id="h-001",
            cycle_id="c-001",
            summary="Catalyst resolved but market disagreed",
        )

    def test_stub_mode_logs_warning(self, tmp_path: Path) -> None:
        """In stub mode, emits FAILED_ASSUMPTION_WRITTEN log with stub=True."""
        from pmacs.logsys.debug_log import set_log_path
        _reset_log_fd()
        log_file = tmp_path / "kuzu_fa.log"
        set_log_path(str(log_file))

        kuzu = KuzuDBAdapter()
        kuzu.write_failed_assumption(
            fa_id="fa-002",
            taxonomy="STOP_HUNTED",
            severity=0.8,
            holding_id="h-002",
            cycle_id="c-002",
            summary="Price recovered within 48h",
        )

        _reset_log_fd()
        content = log_file.read_text()
        assert "FAILED_ASSUMPTION_WRITTEN" in content
        # Verify payload contains stub flag
        for line in content.strip().split("\n"):
            entry = json.loads(line)
            if entry.get("event") == "FAILED_ASSUMPTION_WRITTEN":
                payload = entry.get("payload", {})
                assert payload.get("stub") is True
                assert payload.get("fa_id") == "fa-002"
                assert payload.get("taxonomy") == "STOP_HUNTED"
                break

    def test_all_params_passed(self, tmp_path: Path) -> None:
        """Core parameters (fa_id, taxonomy, severity, holding_id) appear in stub log payload."""
        from pmacs.logsys.debug_log import set_log_path
        _reset_log_fd()
        log_file = tmp_path / "kuzu_params.log"
        set_log_path(str(log_file))

        kuzu = KuzuDBAdapter()
        kuzu.write_failed_assumption(
            fa_id="fa-003",
            taxonomy="MOAT_DRIFT_OVERESTIMATE",
            severity=0.5,
            holding_id="h-003",
            cycle_id="c-003",
            summary="Moat scored 0.85 but thesis failed",
        )

        _reset_log_fd()
        content = log_file.read_text()
        for line in content.strip().split("\n"):
            entry = json.loads(line)
            if entry.get("event") == "FAILED_ASSUMPTION_WRITTEN":
                payload = entry.get("payload", {})
                assert payload.get("holding_id") == "h-003"
                assert payload.get("fa_id") == "fa-003"
                assert payload.get("taxonomy") == "MOAT_DRIFT_OVERESTIMATE"
                assert payload.get("severity") == pytest.approx(0.5)
                assert payload.get("stub") is True
                break


# ======================================================================
# Query helpers
# ======================================================================

class TestKuzuQueryHelpers:
    def test_get_failures_for_ticker_empty(self) -> None:
        """get_failures_for_ticker() returns empty list for unknown ticker."""
        kuzu = KuzuDBAdapter()
        result = kuzu.get_failures_for_ticker("UNKNOWN_TICKER")
        assert result == []

    def test_get_failures_for_ticker_with_limit(self) -> None:
        """get_failures_for_ticker() accepts limit parameter."""
        kuzu = KuzuDBAdapter()
        result = kuzu.get_failures_for_ticker("AAPL", limit=5)
        assert isinstance(result, list)

    def test_get_lineage_empty_for_unknown(self) -> None:
        """get_lineage() returns empty dict for unknown holding_id."""
        kuzu = KuzuDBAdapter()
        result = kuzu.get_lineage("nonexistent-holding")
        assert result == {}

    def test_execute_returns_empty_list(self) -> None:
        """execute() returns empty list in stub mode."""
        kuzu = KuzuDBAdapter()
        result = kuzu.execute("MATCH (n) RETURN n", params={"x": 1})
        assert result == []


# ======================================================================
# Cypher template pattern (Architecture.md §9.5)
# ======================================================================

class TestCypherTemplate:
    def test_failed_assumption_cypher_pattern(self) -> None:
        """Verify the Cypher template in write_failed_assumption matches the
        Architecture.md §9.5 pattern: CREATE FailedAssumption node, link to Holding."""
        source = inspect.getsource(KuzuDBAdapter.write_failed_assumption)

        # The template is inside the method source — verify key structural elements
        assert "CREATE (fa:FailedAssumption" in source, "Missing FailedAssumption node creation"
        assert "MATCH (h:Holding" in source, "Missing Holding match"
        assert "CREATE (h)-[:FAILED_ASSUMPTION]->(fa)" in source, "Missing FAILED_ASSUMPTION edge"

    def test_cypher_uses_parameterized_query(self) -> None:
        """Cypher template uses parameterized inputs ($id, $tax, etc.) not string interpolation."""
        source = inspect.getsource(KuzuDBAdapter.write_failed_assumption)
        # Check for parameterized placeholders
        assert "$id" in source or "$hid" in source, "Missing parameterized query placeholders"


# ======================================================================
# Logging emission
# ======================================================================

class TestKuzuLogging:
    def test_execute_emits_kuzu_query_log(self, tmp_path: Path) -> None:
        """execute() emits a KUZU_QUERY log event."""
        from pmacs.logsys.debug_log import set_log_path
        _reset_log_fd()
        log_file = tmp_path / "kuzu_exec.log"
        set_log_path(str(log_file))

        kuzu = KuzuDBAdapter()
        kuzu.execute("MATCH (n:Test) RETURN n")

        _reset_log_fd()
        content = log_file.read_text()
        assert "KUZU_QUERY" in content

    def test_get_failures_emits_log(self, tmp_path: Path) -> None:
        """get_failures_for_ticker() emits a KUZU_FAILURES_RETRIEVED log event."""
        from pmacs.logsys.debug_log import set_log_path
        _reset_log_fd()
        log_file = tmp_path / "kuzu_failures.log"
        set_log_path(str(log_file))

        kuzu = KuzuDBAdapter()
        kuzu.get_failures_for_ticker("AAPL", limit=5)

        _reset_log_fd()
        content = log_file.read_text()
        assert "KUZU_FAILURES_RETRIEVED" in content

    def test_get_lineage_emits_log(self, tmp_path: Path) -> None:
        """get_lineage() emits a KUZU_LINEAGE_RETRIEVED log event."""
        from pmacs.logsys.debug_log import set_log_path
        _reset_log_fd()
        log_file = tmp_path / "kuzu_lineage.log"
        set_log_path(str(log_file))

        kuzu = KuzuDBAdapter()
        kuzu.get_lineage("h-001")

        _reset_log_fd()
        content = log_file.read_text()
        assert "KUZU_LINEAGE_RETRIEVED" in content
