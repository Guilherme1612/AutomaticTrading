"""Alpaca market data source (CRITICAL)."""
from __future__ import annotations
from datetime import datetime, timezone
from pmacs.data.gateway import DataGateway
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket, EvidenceType


def fetch_bars(ticker: str, gateway: DataGateway, api_key: str, cycle_id: str = "") -> EvidencePacket:
    """Fetch recent bars from Alpaca data API."""
    url = f"https://data.alpaca.markets/v2/stocks/{ticker}/bars"
    response = gateway.fetch("alpaca_data", url, params={"timeframe": "1Day", "limit": 5}, api_key=api_key)
    data = response.json()
    evidence = []
    for bar in data.get("bars", [])[:5]:
        evidence.append(Evidence(
            id=f"alpaca_{ticker}_{bar.get('t', '')}",
            source=DataSource.ALPACA_DATA,
            type=EvidenceType.MARKET_DATA,
            ticker=ticker,
            fetched_at=datetime.now(timezone.utc),
            content_hash=str(hash(str(bar))),
            data=bar,
        ))
    return EvidencePacket(ticker=ticker, cycle_id=cycle_id, evidence=evidence, fetched_at=datetime.now(timezone.utc), source_count=1)
