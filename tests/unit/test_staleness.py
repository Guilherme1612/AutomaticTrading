"""Staleness checker tests — Phase 2 exit test #1."""

from datetime import datetime, timedelta, timezone

from pmacs.data.staleness import check_freshness, check_all_freshness
from pmacs.schemas.data import DataSource, EvidenceType, Evidence, EvidencePacket
from pmacs.schemas.freshness import CriticalityLevel, FreshnessStatus


def _make_packet(age_seconds: int = 0) -> EvidencePacket:
    fetched = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return EvidencePacket(
        ticker="AAPL",
        cycle_id="c1",
        evidence=[
            Evidence(
                id="e1",
                source=DataSource.POLYGON,
                type=EvidenceType.MARKET_DATA,
                ticker="AAPL",
                fetched_at=fetched,
                content_hash="abc",
            )
        ],
        fetched_at=fetched,
    )


class TestStaleness:
    def test_fresh_data(self):
        packet = _make_packet(age_seconds=10)
        result = check_freshness(packet, "polygon", CriticalityLevel.CRITICAL, 300)
        assert result.status == FreshnessStatus.FRESH
        assert result.should_abort is False

    def test_stale_critical_aborts(self):
        packet = _make_packet(age_seconds=600)
        result = check_freshness(packet, "polygon", CriticalityLevel.CRITICAL, 300)
        assert result.status == FreshnessStatus.STALE
        assert result.should_abort is True

    def test_stale_important_degrades(self):
        packet = _make_packet(age_seconds=200000)
        result = check_freshness(packet, "finra", CriticalityLevel.IMPORTANT, 86400)
        assert result.status == FreshnessStatus.STALE
        assert result.should_degrade is True
        assert result.should_abort is False

    def test_stale_nice_to_have_proceeds(self):
        packet = _make_packet(age_seconds=200000)
        result = check_freshness(packet, "fred", CriticalityLevel.NICE_TO_HAVE, 86400)
        assert result.status == FreshnessStatus.STALE
        assert result.should_abort is False
        assert result.should_degrade is False
