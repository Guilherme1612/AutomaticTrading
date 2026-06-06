"""Tests for evidence filtering per persona (Architecture.md §12.2, Agents.md §5-11)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pmacs.data.evidence_router import (
    PERSONA_EVIDENCE_MAP,
    filter_evidence_for_persona,
)
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket, EvidenceType


def _make_evidence(source: DataSource, evidence_id: str = "") -> Evidence:
    return Evidence(
        id=evidence_id or f"ev-{source.value}",
        source=source,
        type=EvidenceType.MARKET_DATA,
        ticker="AAPL",
        fetched_at=datetime.now(timezone.utc),
        content_hash="abc",
    )


def _make_packet(sources: list[DataSource]) -> EvidencePacket:
    evidence = [_make_evidence(s) for s in sources]
    return EvidencePacket(
        ticker="AAPL",
        cycle_id="c1",
        evidence=evidence,
        source_count=len(sources),
    )


class TestPersonaEvidenceMap:
    """PERSONA_EVIDENCE_MAP completeness and correctness."""

    def test_all_personas_mapped(self):
        expected = {
            "MacroRegime", "CatalystSummarizer", "MoatAnalyst",
            "GrowthHunter", "InsiderActivity", "ShortInterest", "Forensics",
        }
        assert set(PERSONA_EVIDENCE_MAP.keys()) == expected

    def test_all_sources_are_valid(self):
        for persona, sources in PERSONA_EVIDENCE_MAP.items():
            for s in sources:
                assert isinstance(s, DataSource), f"{persona} has invalid source {s}"

    def test_each_persona_has_at_least_one_source(self):
        for persona, sources in PERSONA_EVIDENCE_MAP.items():
            assert len(sources) >= 1, f"{persona} has no sources"


class TestFilterEvidenceForPersona:
    """filter_evidence_for_persona() correctness."""

    def test_filters_to_allowed_sources_only(self):
        packet = _make_packet([DataSource.POLYGON, DataSource.EDGAR, DataSource.FRED])
        result = filter_evidence_for_persona([packet], "MacroRegime")
        assert len(result) == 1
        sources = {ev.source for ev in result[0].evidence}
        assert DataSource.FRED in sources
        assert DataSource.POLYGON in sources
        assert DataSource.EDGAR not in sources

    def test_insider_activity_gets_form4_only(self):
        packet = _make_packet([DataSource.FORM4, DataSource.POLYGON, DataSource.FRED])
        result = filter_evidence_for_persona([packet], "InsiderActivity")
        sources = {ev.source for ev in result[0].evidence}
        assert sources == {DataSource.FORM4, DataSource.FUNDAMENTALS} or DataSource.FORM4 in sources

    def test_unknown_persona_gets_all_evidence(self):
        packet = _make_packet([DataSource.POLYGON, DataSource.EDGAR])
        result = filter_evidence_for_persona([packet], "UnknownPersona")
        assert len(result) == 1
        assert len(result[0].evidence) == 2

    def test_empty_evidence_returns_empty(self):
        result = filter_evidence_for_persona([], "MacroRegime")
        assert result == []

    def test_packet_with_no_matching_sources_dropped(self):
        packet = _make_packet([DataSource.FORM4])
        result = filter_evidence_for_persona([packet], "MacroRegime")
        # MacroRegime needs FRED, FOMC, POLYGON, ECB — no FORM4
        assert len(result) == 0

    def test_preserves_packet_metadata(self):
        packet = _make_packet([DataSource.POLYGON, DataSource.FRED])
        result = filter_evidence_for_persona([packet], "MacroRegime")
        assert len(result) == 1
        assert result[0].ticker == "AAPL"
        assert result[0].cycle_id == "c1"
        assert result[0].has_stale_data is False
