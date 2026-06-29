"""ECB exchange rate source (NICE_TO_HAVE)."""
from __future__ import annotations
from datetime import datetime, timezone
from pmacs.data.fx import fetch_ecb_rate
from pmacs.data.gateway import DataGateway
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket, EvidenceType


def fetch_fx_rate(gateway: DataGateway, cycle_id: str = "") -> EvidencePacket:
    """Fetch ECB EUR/USD reference rate as EvidencePacket."""
    try:
        rate = fetch_ecb_rate()
        evidence = [Evidence(
            id=f"ecb_fx_{rate.business_date}",
            source=DataSource.ECB,
            type=EvidenceType.ECONOMIC_DATA,
            ticker="",
            fetched_at=datetime.now(timezone.utc),
            content_hash=str(hash(rate.usd_per_eur)),
            data={"usd_per_eur": rate.usd_per_eur, "business_date": str(rate.business_date)},
        )]
    except Exception:
        evidence = []
    return EvidencePacket(ticker="", cycle_id=cycle_id, evidence=evidence, fetched_at=datetime.now(timezone.utc), source_count=1)
