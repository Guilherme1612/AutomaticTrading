"""FRED economic data source (NICE_TO_HAVE)."""
from __future__ import annotations
from datetime import datetime, timezone
from pmacs.data.gateway import DataGateway
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket, EvidenceType


def fetch_series(series_id: str, gateway: DataGateway, api_key: str = "", cycle_id: str = "") -> EvidencePacket:
    """Fetch FRED time series data."""
    url = f"https://api.stlouisfed.org/fred/series/observations"
    params = {"series_id": series_id, "file_type": "json", "sort_order": "desc", "limit": 5}
    if api_key:
        params["api_key"] = api_key
    try:
        response = gateway.fetch("fred", url, params=params)
        data = response.json()
    except Exception:
        data = {"observations": []}
    evidence = [Evidence(
        id=f"fred_{series_id}",
        source=DataSource.FRED,
        type=EvidenceType.ECONOMIC_DATA,
        ticker="",
        fetched_at=datetime.now(timezone.utc),
        content_hash=str(hash(str(data))),
        data=data,
    )]
    return EvidencePacket(ticker="", cycle_id=cycle_id, evidence=evidence, fetched_at=datetime.now(timezone.utc), source_count=1)
