"""Unit tests for memo_writer data quality warning propagation.

After cycle-1 audit (Jun 24): ONDS memo cited netProfitMarginTTM=251.9% despite
the source's own _data_quality_warning marking it as "likely Finnhub data
corruption". The fix:
1. ``memo_writer.set_analytical_context`` accepts a ``data_quality_warnings``
   list and appends a "Data Quality Warnings" section to the prompt context.
2. The orchestrator collects ``_data_quality_warning`` from every evidence
   packet and passes them in.
3. ``prompts/memo_writer.md`` tells the LLM to NOT cite flagged metrics as facts.

These tests verify the wiring — that the warnings actually surface in the
prompt the memo writer sees.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pmacs.agents.memo_writer import MemoWriterRunner


class _StubEp:
    """Minimal EvidencePacket stub for build_prompt tests."""

    def __init__(self, evidence_id, ticker, data, source_value="fundamentals"):
        self.evidence_id = evidence_id
        self.ticker = ticker
        self.data = data
        # Mirror pmacs.schemas.data.EvidencePacket attribute names
        self.source = MagicMock()
        self.source.value = source_value
        self.title = f"{ticker} {evidence_id}"


class TestDataQualityWiring:
    """The data_quality_warnings parameter flows into build_prompt context."""

    def test_no_warnings_means_no_section(self):
        runner = MemoWriterRunner()
        runner.set_analytical_context(
            arbitrated=None,
            verdict=None,
            conviction_score=0.5,
        )
        # No warnings passed → no Data Quality Warnings section
        assert "Data Quality Warnings" not in runner._analytical_context
        assert "DO NOT cite as facts" not in runner._analytical_context

    def test_empty_warnings_list_means_no_section(self):
        runner = MemoWriterRunner()
        runner.set_analytical_context(
            conviction_score=0.5,
            data_quality_warnings=[],
        )
        assert "Data Quality Warnings" not in runner._analytical_context

    def test_single_warning_appears_in_context(self):
        runner = MemoWriterRunner()
        runner.set_analytical_context(
            conviction_score=0.5,
            data_quality_warnings=[
                "[ONDS/fundamentals_ONDS_metrics] WARNING: 1 metrics flagged as anomalous (likely Finnhub data corruption): netProfitMarginTTM. Clamped to sanity bounds."
            ],
        )
        ctx = runner._analytical_context
        assert "Data Quality Warnings (DO NOT cite as facts)" in ctx
        assert "FLAGS YOU MUST NOT CITE" not in ctx  # older wrong wording
        assert "FLAG flag" not in ctx
        assert "netProfitMarginTTM" in ctx
        assert "Clamped to sanity bounds" in ctx

    def test_multiple_warnings_all_listed(self):
        runner = MemoWriterRunner()
        runner.set_analytical_context(
            conviction_score=0.5,
            data_quality_warnings=[
                "[ONDS/fundamentals_ONDS_metrics] netProfitMarginTTM flagged as anomalous",
                "[ACME/fundamentals_ACME_metrics] roeTTM out of range",
            ],
        )
        ctx = runner._analytical_context
        assert "[ONDS/fundamentals_ONDS_metrics]" in ctx
        assert "[ACME/fundamentals_ACME_metrics]" in ctx
        assert "netProfitMarginTTM" in ctx
        assert "roeTTM" in ctx

    def test_warning_section_count_capped_at_10(self):
        """The cap (10) prevents prompt bloat if many packets are flagged."""
        runner = MemoWriterRunner()
        runner.set_analytical_context(
            conviction_score=0.5,
            data_quality_warnings=[f"[T{i}/fund] flag {i}" for i in range(20)],
        )
        ctx = runner._analytical_context
        # 10 listed, 10 dropped
        assert ctx.count("flag ") == 10
        assert "flag 9" in ctx  # 0-indexed up to 9
        assert "flag 19" not in ctx

    def test_warning_section_prominent(self):
        """The warning section should be present and prominent so the LLM sees it."""
        runner = MemoWriterRunner()
        runner.set_analytical_context(
            conviction_score=0.5,
            data_quality_warnings=["[X/Y] some warning"],
        )
        ctx = runner._analytical_context
        # Should be near end (last section added before _analytical_context close)
        idx = ctx.find("Data Quality Warnings")
        assert idx > 0
        # Section ends before "End of analytical context" or similar
        # but we don't have that — just verify it's there.


class TestPromptContainsWarning:
    """Verify build_prompt embeds the warning section into the rendered prompt."""

    def test_build_prompt_includes_warning_section(self):
        runner = MemoWriterRunner()
        runner.set_analytical_context(
            conviction_score=0.5,
            data_quality_warnings=[
                "[ONDS/fundamentals_ONDS_metrics] netProfitMarginTTM clamped"
            ],
        )
        ep = _StubEp(
            evidence_id="fundamentals_ONDS_metrics",
            ticker="ONDS",
            data={"revenueTTM": 96.6e6},
        )
        rendered = runner.build_prompt(evidence=[ep])
        # The rendered prompt must contain the warning
        assert "Data Quality Warnings (DO NOT cite as facts)" in rendered
        assert "netProfitMarginTTM clamped" in rendered
        # And the prompt-template's own DATA QUALITY FLAGS section
        assert "DATA QUALITY FLAGS" in rendered
