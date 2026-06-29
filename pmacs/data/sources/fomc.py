"""FOMC statements source (NICE_TO_HAVE)."""
from __future__ import annotations
from datetime import datetime, timezone
from pmacs.data.gateway import DataGateway
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket, EvidenceType


def fetch_latest_statement(gateway: DataGateway, cycle_id: str = "") -> EvidencePacket:
    """Fetch latest FOMC statement metadata."""
    evidence = [Evidence(
        id="fomc_latest",
        source=DataSource.FOMC,
        type=EvidenceType.ECONOMIC_DATA,
        ticker="",
        fetched_at=datetime.now(timezone.utc),
        content_hash="fomc_statement",
        data={"note": "FOMC statement scraping — implement with BeautifulSoup when needed"},
    )]
    return EvidencePacket(ticker="", cycle_id=cycle_id, evidence=evidence, fetched_at=datetime.now(timezone.utc), source_count=1)
