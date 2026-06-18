"""Polygon.io market data source (CRITICAL)."""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from pmacs.data.gateway import DataGateway
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket, EvidenceType


def fetch_daily_bars(ticker: str, gateway: DataGateway, api_key: str, cycle_id: str = "") -> EvidencePacket:
    """Fetch daily OHLCV bars from Polygon (last 30 trading days).

    Extended bars (200+ days) and technical indicators are computed
    separately in pmacs/data/sources/technical.py.
    """
    today = date.today()
    month_ago = today - timedelta(days=45)  # ~30 trading days
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{month_ago}/{today}"
    response = gateway.fetch("polygon", url, api_key=api_key)
    data = response.json()
    evidence = []
    for bar in data.get("results", [])[:30]:
        evidence.append(Evidence(
            id=f"polygon_{ticker}_{bar.get('t', 0)}",
            source=DataSource.POLYGON,
            type=EvidenceType.MARKET_DATA,
            ticker=ticker,
            fetched_at=datetime.now(timezone.utc),
            content_hash=str(hash(str(bar))),
            data={"open": bar.get("o"), "high": bar.get("h"), "low": bar.get("l"),
                  "close": bar.get("c"), "volume": bar.get("v"), "timestamp": bar.get("t")},
        ))
    return EvidencePacket(ticker=ticker, cycle_id=cycle_id, evidence=evidence, fetched_at=datetime.now(timezone.utc), source_count=1)
