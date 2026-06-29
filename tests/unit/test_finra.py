"""Unit tests for FINRA short-interest source."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pmacs.data.gateway import DataGateway
from pmacs.data.sources.finra import fetch_short_interest
from pmacs.schemas.data import DataSource, EvidenceType


@pytest.fixture()
def gateway():
    gw = DataGateway.__new__(DataGateway)
    gw._rates = {}
    gw._buckets = {}
    gw._client = MagicMock()
    return gw


class _MockTicker:
    def __init__(
        self,
        *,
        short_pct: float | None = 0.05,
        short_ratio: float | None = 2.5,
        shares_short: int | None = None,
        shares_short_prior: int | None = None,
        avg_volume: float | None = None,
    ):
        self.info = {
            "shortPercentOfFloat": short_pct,
            "shortRatio": short_ratio,
            "sharesShort": shares_short,
            "sharesShortPriorMonth": shares_short_prior,
        }
        self._avg_volume = avg_volume
        self.history_df = None

    def history(self, period: str = "1mo"):
        import pandas as pd
        if self.history_df is not None:
            return self.history_df
        if self._avg_volume is not None:
            self.history_df = pd.DataFrame({"Volume": [self._avg_volume] * 21})
            return self.history_df
        return pd.DataFrame({"Volume": []})


def test_days_to_cover_alias_when_short_ratio_present(gateway):
    with patch("yfinance.Ticker", return_value=_MockTicker(short_pct=0.05, short_ratio=2.5)):
        packet = fetch_short_interest("AAPL", gateway, cycle_id="c001")

    assert len(packet.evidence) == 1
    ev = packet.evidence[0]
    assert ev.source == DataSource.FINRA
    assert ev.type == EvidenceType.ANALYST_DATA
    assert ev.data["short_ratio"] == 2.5
    assert ev.data["days_to_cover"] == 2.5
    assert ev.data["short_pct_float"] == 5.0


def test_days_to_cover_computed_from_volume_when_ratio_missing(gateway):
    ticker = _MockTicker(
        short_pct=0.10,
        short_ratio=None,
        shares_short=1_000_000,
        avg_volume=200_000.0,
    )
    with patch("yfinance.Ticker", return_value=ticker):
        packet = fetch_short_interest("TSLA", gateway, cycle_id="c002")

    ev = packet.evidence[0]
    assert ev.data["days_to_cover"] == pytest.approx(5.0)
    assert ev.data["days_to_cover_source"] == "shares_short / 1mo_avg_volume"


def test_insufficient_data_when_no_short_fields(gateway):
    ticker = _MockTicker(short_pct=None, short_ratio=None, shares_short=None)
    with patch("yfinance.Ticker", return_value=ticker):
        packet = fetch_short_interest("ZZZZ", gateway, cycle_id="c003")

    assert len(packet.evidence) == 1
    ev = packet.evidence[0]
    assert ev.data.get("status") == "INSUFFICIENT_DATA"
    assert ev.data["days_to_cover"] is None
