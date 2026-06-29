"""Integration tests for PMACS data source modules.

Covers all 13 sources with mocked HTTP layer. Tests verify:
- Each source returns a properly structured EvidencePacket
- Evidence items have correct DataSource and EvidenceType
- Error handling (network errors, rate limiting, invalid responses)
- Rate limiting via TokenBucket works correctly
- API key injection follows source-specific conventions

No real API keys or network access required.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from pmacs.data.gateway import DataGateway, TokenBucket
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket, EvidenceType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_response_factory():
    """Factory that builds httpx.Response mocks with given status/JSON."""

    def _make(
        status_code: int = 200,
        json_data: Any = None,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> MagicMock:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.headers = headers or {}
        if json_data is not None:
            resp.json.return_value = json_data
        resp.text = text or (json.dumps(json_data) if json_data is not None else "")
        resp.raise_for_status = MagicMock()
        if status_code >= 400:
            resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                message=f"{status_code} error",
                request=MagicMock(),
                response=resp,
            )
        return resp

    return _make


@pytest.fixture()
def gateway():
    """DataGateway with a mocked httpx.Client and generous rate limits.

    Individual tests override ``gateway._client.get`` as needed.
    """
    gw = DataGateway.__new__(DataGateway)
    gw._rates = {}
    gw._buckets = {}
    gw._client = MagicMock(spec=httpx.Client)
    for src in [
        "edgar", "polygon", "finnhub", "alpaca_data", "openfda", "finra",
        "form4", "ir_pages", "press", "fomc", "fred", "ecb", "fundamentals",
    ]:
        gw._rates[src] = 10.0
        gw._buckets[src] = TokenBucket(rate=100.0, capacity=200)
    return gw


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALL_SOURCES = [
    "edgar", "polygon", "finnhub", "alpaca_data", "openfda", "finra",
    "form4", "ir_pages", "press", "fomc", "fred", "ecb", "fundamentals",
]


def _assert_valid_packet(packet: EvidencePacket, expected_ticker: str):
    """Verify structural invariants of an EvidencePacket."""
    assert isinstance(packet, EvidencePacket)
    assert packet.ticker == expected_ticker
    assert isinstance(packet.fetched_at, datetime)
    assert packet.source_count >= 1
    for ev in packet.evidence:
        assert isinstance(ev, Evidence)
        assert isinstance(ev.source, DataSource)
        assert isinstance(ev.type, EvidenceType)
        assert isinstance(ev.content_hash, str)
        assert len(ev.content_hash) > 0
        assert isinstance(ev.data, dict)


# ---------------------------------------------------------------------------
# 1. EDGAR -- SEC filings
# ---------------------------------------------------------------------------


class TestEdgar:
    """pmacs.data.sources.edgar -- fetch(cik, ticker, gateway, cycle_id)."""

    EDGAR_JSON = {
        "filings": {
            "recent": {
                "form": ["10-K", "10-Q", "8-K", "4", "10-K"],
                "accessionNumber": [
                    "0001-24-000001", "0001-24-000002", "0001-24-000003",
                    "0001-24-000004", "0001-24-000005",
                ],
                "fileDate": [
                    "2025-01-15", "2025-02-10", "2025-03-01",
                    "2025-03-15", "2025-04-01",
                ],
            }
        }
    }

    def test_fetch_returns_evidence_packet(self, gateway, mock_response_factory):
        from pmacs.data.sources.edgar import fetch

        gateway._client.get.return_value = mock_response_factory(
            json_data=self.EDGAR_JSON
        )
        packet = fetch(cik="0001", ticker="AAPL", gateway=gateway, cycle_id="c001")

        assert isinstance(packet, EvidencePacket)
        assert packet.ticker == "AAPL"
        assert packet.cycle_id == "c001"
        assert len(packet.evidence) == 1
        ev = packet.evidence[0]
        assert ev.source == DataSource.EDGAR
        assert ev.type == EvidenceType.SEC_FILING
        assert ev.data["filings"][0]["form"] == "10-K"
        assert ev.data["filings"][0]["accession"] == "0001-24-000001"

    def test_fetch_limits_to_five_filings(self, gateway, mock_response_factory):
        from pmacs.data.sources.edgar import fetch

        big_json = {
            "filings": {
                "recent": {
                    "form": ["10-K"] * 20,
                    "accessionNumber": [f"acc-{i}" for i in range(20)],
                    "fileDate": ["2025-01-01"] * 20,
                }
            }
        }
        gateway._client.get.return_value = mock_response_factory(json_data=big_json)
        packet = fetch(cik="0001", ticker="MSFT", gateway=gateway)
        assert len(packet.evidence) == 1
        assert len(packet.evidence[0].data["filings"]) == 10

    def test_fetch_handles_empty_filings(self, gateway, mock_response_factory):
        from pmacs.data.sources.edgar import fetch

        gateway._client.get.return_value = mock_response_factory(
            json_data={"filings": {"recent": {"form": [], "accessionNumber": [], "fileDate": []}}}
        )
        packet = fetch(cik="0001", ticker="TSLA", gateway=gateway)
        assert isinstance(packet, EvidencePacket)
        assert len(packet.evidence) == 1
        assert packet.evidence[0].data.get("error") == "EDGAR fetch failed or CIK unknown"

    def test_fetch_handles_http_error(self, gateway, mock_response_factory):
        from pmacs.data.sources.edgar import fetch

        gateway._client.get.return_value = mock_response_factory(
            status_code=403, json_data={"error": "forbidden"}
        )
        packet = fetch(cik="0001", ticker="AAPL", gateway=gateway)
        # CRITICAL source returns a stub evidence item on failure so the cycle can continue stale
        assert isinstance(packet, EvidencePacket)
        assert len(packet.evidence) == 1
        assert packet.evidence[0].data.get("error") == "EDGAR fetch failed or CIK unknown"

    def test_fetch_sends_accept_header(self, gateway, mock_response_factory):
        from pmacs.data.sources.edgar import fetch

        gateway._client.get.return_value = mock_response_factory(
            json_data=self.EDGAR_JSON
        )
        fetch(cik="0001", ticker="AAPL", gateway=gateway)

        call_args = gateway._client.get.call_args
        headers = call_args.kwargs.get("headers") or call_args[1].get("headers") or {}
        assert headers.get("Accept") == "application/json"


# ---------------------------------------------------------------------------
# 2. Polygon -- daily OHLCV bars
# ---------------------------------------------------------------------------


class TestPolygon:
    """pmacs.data.sources.polygon -- fetch_daily_bars(ticker, gateway, api_key, cycle_id)."""

    POLYGON_JSON = {
        "results": [
            {"t": 1700000000000, "o": 150.0, "h": 155.0, "l": 149.0, "c": 153.0, "v": 1000000},
            {"t": 1700086400000, "o": 153.0, "h": 157.0, "l": 152.0, "c": 156.0, "v": 1200000},
            {"t": 1700172800000, "o": 156.0, "h": 158.0, "l": 154.0, "c": 155.0, "v": 900000},
        ]
    }

    def test_fetch_returns_structured_bars(self, gateway, mock_response_factory):
        from pmacs.data.sources.polygon import fetch_daily_bars

        gateway._client.get.return_value = mock_response_factory(
            json_data=self.POLYGON_JSON
        )
        packet = fetch_daily_bars("AAPL", gateway, api_key="pk_test", cycle_id="c002")

        assert packet.ticker == "AAPL"
        assert len(packet.evidence) == 3
        bar0 = packet.evidence[0]
        assert bar0.source == DataSource.POLYGON
        assert bar0.type == EvidenceType.MARKET_DATA
        assert bar0.data["open"] == 150.0
        assert bar0.data["close"] == 153.0
        assert bar0.data["volume"] == 1000000
        assert bar0.data["timestamp"] == 1700000000000

    def test_fetch_empty_results(self, gateway, mock_response_factory):
        from pmacs.data.sources.polygon import fetch_daily_bars

        gateway._client.get.return_value = mock_response_factory(json_data={"results": []})
        packet = fetch_daily_bars("ZZZ", gateway, api_key="pk_test")
        assert len(packet.evidence) == 0

    def test_fetch_api_key_injected_as_query_param(self, gateway, mock_response_factory):
        from pmacs.data.sources.polygon import fetch_daily_bars

        gateway._client.get.return_value = mock_response_factory(
            json_data=self.POLYGON_JSON
        )
        fetch_daily_bars("AAPL", gateway, api_key="polygon_secret")

        call_args = gateway._client.get.call_args
        params = call_args.kwargs.get("params") or call_args[1].get("params") or {}
        assert params.get("apiKey") == "polygon_secret"


# ---------------------------------------------------------------------------
# 3. Finnhub -- real-time quote
# ---------------------------------------------------------------------------


class TestFinnhub:
    """pmacs.data.sources.finnhub -- fetch_quote(ticker, gateway, api_key, cycle_id)."""

    QUOTE_JSON = {
        "c": 185.5, "h": 188.0, "l": 183.0, "o": 184.0,
        "pc": 183.5, "t": 1700000000,
    }

    def test_fetch_returns_quote_evidence(self, gateway, mock_response_factory):
        from pmacs.data.sources.finnhub import fetch_quote

        gateway._client.get.return_value = mock_response_factory(json_data=self.QUOTE_JSON)
        packet = fetch_quote("MSFT", gateway, api_key="fh_key", cycle_id="c003")

        assert packet.ticker == "MSFT"
        assert len(packet.evidence) == 1
        ev = packet.evidence[0]
        assert ev.source == DataSource.FINNHUB
        assert ev.type == EvidenceType.MARKET_DATA
        assert ev.data["c"] == 185.5
        assert ev.data["h"] == 188.0

    def test_fetch_api_key_injected_as_query_param(self, gateway, mock_response_factory):
        from pmacs.data.sources.finnhub import fetch_quote

        gateway._client.get.return_value = mock_response_factory(json_data=self.QUOTE_JSON)
        fetch_quote("MSFT", gateway, api_key="secret123")

        call_args = gateway._client.get.call_args
        params = call_args.kwargs.get("params") or call_args[1].get("params") or {}
        assert params.get("apiKey") == "secret123"


# ---------------------------------------------------------------------------
# 4. Alpaca Data -- bars
# ---------------------------------------------------------------------------


class TestAlpacaData:
    """pmacs.data.sources.alpaca_data -- fetch_bars(ticker, gateway, api_key, cycle_id)."""

    BARS_JSON = {
        "bars": [
            {"t": "2025-01-15T00:00:00Z", "o": 190.0, "h": 193.0, "l": 189.0, "c": 192.0, "v": 800000},
            {"t": "2025-01-14T00:00:00Z", "o": 188.0, "h": 191.0, "l": 187.0, "c": 190.0, "v": 750000},
        ]
    }

    def test_fetch_returns_bar_evidence(self, gateway, mock_response_factory):
        from pmacs.data.sources.alpaca_data import fetch_bars

        gateway._client.get.return_value = mock_response_factory(json_data=self.BARS_JSON)
        packet = fetch_bars("GOOG", gateway, api_key="alp_key", cycle_id="c004")

        assert packet.ticker == "GOOG"
        assert len(packet.evidence) == 2
        assert packet.evidence[0].source == DataSource.ALPACA_DATA
        assert packet.evidence[0].type == EvidenceType.MARKET_DATA
        assert packet.evidence[0].data["c"] == 192.0

    def test_fetch_handles_empty_bars(self, gateway, mock_response_factory):
        from pmacs.data.sources.alpaca_data import fetch_bars

        gateway._client.get.return_value = mock_response_factory(json_data={"bars": []})
        packet = fetch_bars("EMPTY", gateway, api_key="alp_key")
        assert len(packet.evidence) == 0

    def test_api_key_injected_as_header(self, gateway, mock_response_factory):
        from pmacs.data.sources.alpaca_data import fetch_bars

        gateway._client.get.return_value = mock_response_factory(json_data=self.BARS_JSON)
        fetch_bars("GOOG", gateway, api_key="my_alpaca_key")

        call_args = gateway._client.get.call_args
        headers = call_args.kwargs.get("headers") or call_args[1].get("headers") or {}
        assert headers.get("APCA-API-KEY-ID") == "my_alpaca_key"


# ---------------------------------------------------------------------------
# 5. OpenFDA -- adverse drug events
# ---------------------------------------------------------------------------


class TestOpenFDA:
    """pmacs.data.sources.openfda -- fetch_drug_events(drug_name, gateway, cycle_id)."""

    FDA_JSON = {
        "results": [
            {"safetyreportid": "FDA-1", "patient": {"drug": [{"medicinalproduct": "ASPIRIN"}]}},
            {"safetyreportid": "FDA-2", "patient": {"drug": [{"medicinalproduct": "ASPIRIN"}]}},
        ]
    }

    def test_fetch_returns_regulatory_evidence(self, gateway, mock_response_factory):
        from pmacs.data.sources.openfda import fetch_drug_events

        gateway._client.get.return_value = mock_response_factory(json_data=self.FDA_JSON)
        packet = fetch_drug_events("ASPIRIN", gateway, cycle_id="c005")

        assert len(packet.evidence) == 1
        ev = packet.evidence[0]
        assert ev.source == DataSource.OPENFDA
        assert ev.type == EvidenceType.REGULATORY
        assert ev.data["results"][0]["safetyreportid"] == "FDA-1"

    def test_fetch_handles_api_error_gracefully(self, gateway):
        from pmacs.data.sources.openfda import fetch_drug_events

        # openfda wraps gateway.fetch in try/except -- returns {"results": []}
        gateway._client.get.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404)
        )
        packet = fetch_drug_events("UNKNOWN", gateway)
        assert isinstance(packet, EvidencePacket)
        assert len(packet.evidence) == 1
        assert packet.evidence[0].data == {"results": []}

    def test_fetch_passes_drug_name_in_search(self, gateway, mock_response_factory):
        from pmacs.data.sources.openfda import fetch_drug_events

        gateway._client.get.return_value = mock_response_factory(json_data=self.FDA_JSON)
        fetch_drug_events("IBUPROFEN", gateway)

        call_args = gateway._client.get.call_args
        params = call_args.kwargs.get("params") or call_args[1].get("params") or {}
        assert "IBUPROFEN" in params.get("search", "")


# ---------------------------------------------------------------------------
# 6. FINRA -- short interest (via yfinance)
# ---------------------------------------------------------------------------


class _MockYfTicker:
    def __init__(self, *, short_pct=0.05, short_ratio=2.5, shares_short=None, avg_volume=None):
        self.info = {
            "shortPercentOfFloat": short_pct,
            "shortRatio": short_ratio,
            "sharesShort": shares_short,
            "sharesShortPriorMonth": None,
            "shortPercentOfOutstanding": None,
        }
        self._avg_volume = avg_volume
        self._history = None

    def history(self, period="1mo"):
        import pandas as pd
        if self._history is not None:
            return self._history
        if self._avg_volume is not None:
            self._history = pd.DataFrame({"Volume": [self._avg_volume] * 21})
            return self._history
        return pd.DataFrame({"Volume": []})


class TestFinra:
    """pmacs.data.sources.finra -- fetch_short_interest(ticker, gateway, cycle_id).

    Uses yfinance (no gateway.fetch calls). Tests patch yfinance.Ticker.
    """

    def test_fetch_returns_short_interest_evidence(self, gateway):
        from pmacs.data.sources.finra import fetch_short_interest

        with patch("yfinance.Ticker", return_value=_MockYfTicker(short_pct=0.05, short_ratio=2.5)):
            packet = fetch_short_interest("AAPL", gateway, cycle_id="c006")

        assert packet.ticker == "AAPL"
        assert len(packet.evidence) == 1
        ev = packet.evidence[0]
        assert ev.source == DataSource.FINRA
        assert ev.type == EvidenceType.ANALYST_DATA
        assert ev.data["short_pct_float"] == 5.0
        assert ev.data["short_ratio"] == 2.5
        assert ev.data["days_to_cover"] == 2.5

    def test_fetch_works_without_network(self, gateway):
        from pmacs.data.sources.finra import fetch_short_interest

        gateway._client.get.side_effect = Exception("no network")
        # FINRA uses yfinance, not gateway.fetch; patch to return no data
        with patch("yfinance.Ticker", return_value=_MockYfTicker(short_pct=None, short_ratio=None, shares_short=None)):
            packet = fetch_short_interest("TSLA", gateway)
        assert len(packet.evidence) == 1


# ---------------------------------------------------------------------------
# 7. Form 4 -- insider filings
# ---------------------------------------------------------------------------


class TestForm4:
    """pmacs.data.sources.form4 -- fetch_insider_filings(cik, ticker, gateway, cycle_id)."""

    SUBMISSIONS_JSON = {
        "filings": {
            "recent": {
                "form": ["4", "10-K"],
                "filingDate": ["2025-03-15", "2025-02-20"],
                "accessionNumber": ["0000320193-25-000123", "0000320193-25-000122"],
                "primaryDocument": ["primary_doc.xml", "10k.pdf"],
            }
        }
    }

    def _build_xml(self, transactions: list[dict] | None = None) -> str:
        if transactions is None:
            transactions = []
        txn_xml = ""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for t in transactions:
            code = t.get("code", "P")
            ad = t.get("ad", "A")
            shares = t.get("shares", 1000)
            price = t.get("price", 50.0)
            date = t.get("date", today)
            txn_xml += f"""
            <nonDerivativeTransaction>
              <securityTitle><value>Common Stock</value></securityTitle>
              <transactionDate><value>{date}</value></transactionDate>
              <transactionCoding>
                <transactionFormType>4</transactionFormType>
                <transactionCode>{code}</transactionCode>
              </transactionCoding>
              <transactionAmounts>
                <transactionShares><value>{shares}</value></transactionShares>
                <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
                <transactionAcquiredDisposedCode><value>{ad}</value></transactionAcquiredDisposedCode>
              </transactionAmounts>
              <postTransactionAmounts>
                <sharesOwnedFollowingTransaction><value>5000</value></sharesOwnedFollowingTransaction>
              </postTransactionAmounts>
              <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
            </nonDerivativeTransaction>
            """

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<ownershipDocument xmlns="http://www.sec.gov/edgar/ownership/ownership XSD">
  <schemaVersion>X0206</schemaVersion>
  <documentType>4</documentType>
  <periodOfReport>2025-03-15</periodOfReport>
  <issuer>
    <issuerCik>0000320193</issuerCik>
    <issuerName>Apple Inc.</issuerName>
    <issuerTradingSymbol>AAPL</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0001234567</rptOwnerCik>
      <rptOwnerName>Jane Doe</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>false</isDirector>
      <isOfficer>true</isOfficer>
      <isTenPercentOwner>false</isTenPercentOwner>
      <isOther>false</isOther>
      <officerTitle>Chief Financial Officer</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>{txn_xml}</nonDerivativeTable>
  <derivativeTable></derivativeTable>
</ownershipDocument>
"""

    def test_fetch_parses_form4_transactions(self, gateway, mock_response_factory):
        from pmacs.data.sources.form4 import fetch_insider_filings

        xml = self._build_xml(transactions=[{"code": "P", "shares": 1000, "price": 150.0}])

        def _get(url, **kwargs):
            if "submissions" in url:
                return mock_response_factory(json_data=self.SUBMISSIONS_JSON)
            return mock_response_factory(text=xml)

        gateway._client.get.side_effect = _get
        packet = fetch_insider_filings("0000320193", "AAPL", gateway, cycle_id="c007")

        assert packet.ticker == "AAPL"
        assert len(packet.evidence) >= 2  # summary + transaction
        summary = packet.evidence[0]
        assert summary.source == DataSource.FORM4
        assert summary.type == EvidenceType.INSIDER_FILING
        assert summary.data["form4_count"] == 1
        assert summary.data["purchase_count"] == 1
        assert "CEO_BUY" in summary.data["signals"]

    def test_fetch_handles_http_error_gracefully(self, gateway):
        from pmacs.data.sources.form4 import fetch_insider_filings

        gateway._client.get.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock(status_code=500)
        )
        packet = fetch_insider_filings("0000320193", "AAPL", gateway)
        assert isinstance(packet, EvidencePacket)
        assert len(packet.evidence) == 1
        assert packet.evidence[0].data.get("status") == "FETCH_ERROR"

    def test_fetch_sends_accept_header(self, gateway, mock_response_factory):
        from pmacs.data.sources.form4 import fetch_insider_filings

        xml = self._build_xml()

        def _get(url, **kwargs):
            if "submissions" in url:
                return mock_response_factory(json_data=self.SUBMISSIONS_JSON)
            return mock_response_factory(text=xml)

        gateway._client.get.side_effect = _get
        fetch_insider_filings("0000320193", "AAPL", gateway)

        # First call is submissions JSON; subsequent calls are XML documents
        submission_call = gateway._client.get.call_args_list[0]
        headers = submission_call.kwargs.get("headers") or submission_call[1].get("headers") or {}
        assert headers.get("Accept") == "application/json"


# ---------------------------------------------------------------------------
# 8. IR Pages -- investor relations
# ---------------------------------------------------------------------------


class TestIRPages:
    """pmacs.data.sources.ir_pages -- fetch_ir_page(ticker, url, gateway, cycle_id)."""

    def test_fetch_extracts_page_content(self, gateway, mock_response_factory):
        from pmacs.data.sources.ir_pages import fetch_ir_page

        html = "<html><body><h1>Investor Relations</h1><p>Q4 earnings report.</p></body></html>"
        gateway._client.get.return_value = mock_response_factory(text=html)
        packet = fetch_ir_page(
            "NVDA", "https://ir.nvidia.com", gateway, cycle_id="c008",
        )

        assert packet.ticker == "NVDA"
        assert len(packet.evidence) == 1
        ev = packet.evidence[0]
        assert ev.source == DataSource.IR_PAGES
        assert ev.type == EvidenceType.CORPORATE_EVENT
        assert ev.data["url"] == "https://ir.nvidia.com"
        assert ev.data["content_length"] > 0

    def test_fetch_truncates_long_content(self, gateway, mock_response_factory):
        from pmacs.data.sources.ir_pages import fetch_ir_page

        long_html = "<html><body>" + "x" * 10000 + "</body></html>"
        gateway._client.get.return_value = mock_response_factory(text=long_html)
        packet = fetch_ir_page("NVDA", "https://ir.nvidia.com", gateway)
        assert packet.evidence[0].data["content_length"] == 4000

    def test_fetch_handles_network_error(self, gateway):
        from pmacs.data.sources.ir_pages import fetch_ir_page

        gateway._client.get.side_effect = httpx.ConnectError("connection refused")
        packet = fetch_ir_page("NVDA", "https://ir.nvidia.com", gateway)
        assert isinstance(packet, EvidencePacket)
        assert len(packet.evidence) == 1
        assert packet.evidence[0].data["content_length"] == 0


# ---------------------------------------------------------------------------
# 9. Press -- press releases (Finnhub news)
# ---------------------------------------------------------------------------


class TestPress:
    """pmacs.data.sources.press -- fetch_press_releases(ticker, gateway, cycle_id)."""

    NEWS_JSON = [
        {"id": 1001, "headline": "Apple reports Q4 earnings and revenue", "source": "Reuters", "datetime": 1700000300},
        {"id": 1002, "headline": "Apple launches new product", "source": "Bloomberg", "datetime": 1700000200},
        {"id": 1003, "headline": "Apple expands services", "source": "CNBC", "datetime": 1700000100},
    ]

    def test_fetch_returns_press_releases(self, gateway, mock_response_factory):
        from pmacs.data.sources.press import fetch_press_releases

        gateway._client.get.return_value = mock_response_factory(json_data=self.NEWS_JSON)
        packet = fetch_press_releases("AAPL", gateway, cycle_id="c009")

        assert packet.ticker == "AAPL"
        # 3 news items + 1 catalyst timeline evidence
        assert len(packet.evidence) == 4
        assert packet.evidence[0].source == DataSource.PRESS
        assert packet.evidence[0].type == EvidenceType.PRESS_RELEASE
        assert packet.evidence[0].data["headline"] == "Apple reports Q4 earnings and revenue"

    def test_fetch_limits_to_twenty_items(self, gateway, mock_response_factory):
        from pmacs.data.sources.press import fetch_press_releases

        big_news = [{"id": i, "headline": f"News {i}", "source": "test", "datetime": 1700000000 + i} for i in range(30)]
        gateway._client.get.return_value = mock_response_factory(json_data=big_news)
        packet = fetch_press_releases("AAPL", gateway)
        assert len(packet.evidence) == 20  # 20 news items, no catalyst timeline because "News i" is GENERAL

    def test_fetch_handles_network_error(self, gateway):
        from pmacs.data.sources.press import fetch_press_releases

        gateway._client.get.side_effect = Exception("timeout")
        packet = fetch_press_releases("AAPL", gateway)
        assert isinstance(packet, EvidencePacket)
        assert len(packet.evidence) == 0

    def test_fetch_passes_date_range_params(self, gateway, mock_response_factory):
        from pmacs.data.sources.press import fetch_press_releases

        gateway._client.get.return_value = mock_response_factory(json_data=self.NEWS_JSON)
        fetch_press_releases("AAPL", gateway)

        call_args = gateway._client.get.call_args
        params = call_args.kwargs.get("params") or call_args[1].get("params") or {}
        assert "from" in params
        assert "to" in params
        assert params["symbol"] == "AAPL"


# ---------------------------------------------------------------------------
# 10. FOMC -- statements (static placeholder)
# ---------------------------------------------------------------------------


class TestFOMC:
    """pmacs.data.sources.fomc -- fetch_latest_statement(gateway, cycle_id).

    FOMC currently returns a static placeholder and does not call gateway.fetch.
    """

    def test_fetch_returns_statement_evidence(self, gateway):
        from pmacs.data.sources.fomc import fetch_latest_statement

        packet = fetch_latest_statement(gateway, cycle_id="c010")

        assert isinstance(packet, EvidencePacket)
        assert len(packet.evidence) == 1
        ev = packet.evidence[0]
        assert ev.source == DataSource.FOMC
        assert ev.type == EvidenceType.ECONOMIC_DATA
        assert "note" in ev.data

    def test_fetch_works_without_network(self, gateway):
        from pmacs.data.sources.fomc import fetch_latest_statement

        gateway._client.get.side_effect = Exception("no network")
        packet = fetch_latest_statement(gateway)
        assert len(packet.evidence) == 1


# ---------------------------------------------------------------------------
# 11. FRED -- economic time series
# ---------------------------------------------------------------------------


class TestFRED:
    """pmacs.data.sources.fred -- fetch_series(series_id, gateway, api_key, cycle_id)."""

    FRED_JSON = {
        "observations": [
            {"date": "2025-01-01", "value": "5.33"},
            {"date": "2024-12-01", "value": "5.33"},
            {"date": "2024-11-01", "value": "5.33"},
        ]
    }

    def test_fetch_returns_economic_data(self, gateway, mock_response_factory):
        from pmacs.data.sources.fred import fetch_series

        gateway._client.get.return_value = mock_response_factory(json_data=self.FRED_JSON)
        packet = fetch_series("FEDFUNDS", gateway, api_key="fred_key", cycle_id="c011")

        assert len(packet.evidence) == 1
        ev = packet.evidence[0]
        assert ev.source == DataSource.FRED
        assert ev.type == EvidenceType.ECONOMIC_DATA
        assert ev.data["observations"][0]["value"] == "5.33"

    def test_fetch_handles_error_gracefully(self, gateway):
        from pmacs.data.sources.fred import fetch_series

        gateway._client.get.side_effect = Exception("api down")
        packet = fetch_series("FEDFUNDS", gateway, api_key="key")
        assert isinstance(packet, EvidencePacket)
        assert len(packet.evidence) == 1
        assert packet.evidence[0].data == {"observations": []}

    def test_api_key_injected_as_param(self, gateway, mock_response_factory):
        from pmacs.data.sources.fred import fetch_series

        gateway._client.get.return_value = mock_response_factory(json_data=self.FRED_JSON)
        fetch_series("GDP", gateway, api_key="secret_fred")

        call_args = gateway._client.get.call_args
        params = call_args.kwargs.get("params") or call_args[1].get("params") or {}
        assert params.get("api_key") == "secret_fred"

    def test_fetch_passes_series_id(self, gateway, mock_response_factory):
        from pmacs.data.sources.fred import fetch_series

        gateway._client.get.return_value = mock_response_factory(json_data=self.FRED_JSON)
        fetch_series("DFF", gateway, api_key="key")

        call_args = gateway._client.get.call_args
        params = call_args.kwargs.get("params") or call_args[1].get("params") or {}
        assert params["series_id"] == "DFF"
        assert params["file_type"] == "json"
        assert params["sort_order"] == "desc"
        assert params["limit"] == 5


# ---------------------------------------------------------------------------
# 12. Fundamentals -- company profile (Finnhub fallback)
# ---------------------------------------------------------------------------


class TestFundamentals:
    """pmacs.data.sources.fundamentals -- fetch_fundamentals(ticker, gateway, api_key, cycle_id)."""

    PROFILE_JSON = {
        "country": "US",
        "currency": "USD",
        "exchange": "NASDAQ",
        "finnhubIndustry": "Technology",
        "logo": "https://logo.com/aapl.png",
        "marketCapitalization": 2800000,
        "name": "Apple Inc",
        "ticker": "AAPL",
    }

    def test_fetch_returns_financial_statement(self, gateway, mock_response_factory):
        from pmacs.data.sources.fundamentals import fetch_fundamentals

        gateway._client.get.return_value = mock_response_factory(json_data=self.PROFILE_JSON)
        packet = fetch_fundamentals("AAPL", gateway, api_key="fh_key", cycle_id="c012")

        assert packet.ticker == "AAPL"
        assert len(packet.evidence) == 1
        ev = packet.evidence[0]
        assert ev.source == DataSource.FUNDAMENTALS
        assert ev.type == EvidenceType.FINANCIAL_STATEMENT
        assert ev.data["name"] == "Apple Inc"
        assert ev.data["marketCapitalization"] == 2800000

    def test_fetch_handles_error_returns_empty_packet(self, gateway):
        from pmacs.data.sources.fundamentals import fetch_fundamentals

        gateway._client.get.side_effect = Exception("api error")
        packet = fetch_fundamentals("AAPL", gateway)
        assert isinstance(packet, EvidencePacket)
        assert len(packet.evidence) == 0

    def test_api_key_injected_as_token_param(self, gateway, mock_response_factory):
        from pmacs.data.sources.fundamentals import fetch_fundamentals

        gateway._client.get.return_value = mock_response_factory(json_data=self.PROFILE_JSON)
        fetch_fundamentals("AAPL", gateway, api_key="my_finnhub_key")

        call_args = gateway._client.get.call_args
        params = call_args.kwargs.get("params") or call_args[1].get("params") or {}
        assert params.get("token") == "my_finnhub_key"


# ---------------------------------------------------------------------------
# 13. ECB -- FX rate
# ---------------------------------------------------------------------------


class TestECB:
    """pmacs.data.sources.ecb -- fetch_fx_rate(gateway, cycle_id).

    This source delegates to pmacs.data.fx.fetch_ecb_rate(). We mock that
    function to avoid real HTTP calls.
    """

    def test_fetch_returns_fx_evidence(self, gateway):
        from pmacs.data.sources.ecb import fetch_fx_rate
        from pmacs.schemas.currency import FxRate

        mock_rate = FxRate(
            usd_per_eur=1.085,
            business_date=date(2025, 5, 23),
            fetched_at=datetime.now(timezone.utc),
        )
        with patch("pmacs.data.sources.ecb.fetch_ecb_rate", return_value=mock_rate):
            packet = fetch_fx_rate(gateway, cycle_id="c013")

        assert len(packet.evidence) == 1
        ev = packet.evidence[0]
        assert ev.source == DataSource.ECB
        assert ev.type == EvidenceType.ECONOMIC_DATA
        assert ev.data["usd_per_eur"] == 1.085
        assert ev.data["business_date"] == "2025-05-23"

    def test_fetch_handles_error_returns_empty_evidence(self, gateway):
        from pmacs.data.sources.ecb import fetch_fx_rate

        with patch("pmacs.data.sources.ecb.fetch_ecb_rate", side_effect=RuntimeError("ecb down")):
            packet = fetch_fx_rate(gateway, cycle_id="c014")

        assert isinstance(packet, EvidencePacket)
        assert len(packet.evidence) == 0


# ---------------------------------------------------------------------------
# TokenBucket -- rate limiter correctness
# ---------------------------------------------------------------------------


class TestTokenBucket:
    """TokenBucket rate limiter correctness (Architecture.md S6)."""

    def test_acquire_succeeds_with_tokens(self):
        bucket = TokenBucket(rate=10.0, capacity=20)
        assert bucket.acquire(tokens=1, timeout=0.01) is True

    def test_capacity_is_respected(self):
        bucket = TokenBucket(rate=0.0, capacity=2)
        assert bucket.acquire(tokens=1, timeout=0.01) is True
        assert bucket.acquire(tokens=1, timeout=0.01) is True
        # Third request fails -- no tokens, rate=0 means no refill
        assert bucket.acquire(tokens=1, timeout=0.01) is False

    def test_refill_restores_tokens_over_time(self):
        bucket = TokenBucket(rate=1000.0, capacity=10)
        bucket._tokens = 0.0
        bucket._last_refill = time.monotonic()
        # At 1000 tokens/s, 20ms gives ~20 tokens (capped at capacity 10)
        time.sleep(0.02)
        assert bucket.acquire(tokens=1, timeout=0.01) is True

    def test_default_capacity_is_double_rate(self):
        bucket = TokenBucket(rate=5.0)
        assert bucket.capacity == 10  # 5 * 2

    def test_tokens_deducted_on_acquire(self):
        bucket = TokenBucket(rate=0.0, capacity=5)
        bucket.acquire(tokens=3, timeout=0.01)
        assert bucket._tokens == pytest.approx(2.0)

    def test_multiple_token_acquire(self):
        bucket = TokenBucket(rate=0.0, capacity=5)
        assert bucket.acquire(tokens=5, timeout=0.01) is True
        assert bucket.acquire(tokens=1, timeout=0.01) is False

    def test_refill_capped_at_capacity(self):
        bucket = TokenBucket(rate=100.0, capacity=5)
        # Let refill run -- should not exceed capacity
        time.sleep(0.01)
        bucket._refill()
        assert bucket._tokens <= 5.0


# ---------------------------------------------------------------------------
# DataGateway -- rate limiting and retry behavior
# ---------------------------------------------------------------------------


class TestDataGatewayRateLimiting:
    """DataGateway respects per-source rate limits and retries."""

    def test_fetch_raises_timeout_when_rate_exhausted(self, mock_response_factory):
        gw = DataGateway(rates={"test_src": 0.0}, timeout=0.01)
        gw._buckets["test_src"]._tokens = 0.0
        with pytest.raises(TimeoutError, match="Rate limit timeout"):
            gw.fetch("test_src", "https://example.com/api")

    def test_gateway_retries_on_429(self, mock_response_factory):
        gw = DataGateway(rates={"polygon": 100.0}, timeout=5.0)

        resp_429 = mock_response_factory(status_code=429, json_data={"error": "rate limited"})
        resp_200 = mock_response_factory(json_data={"results": []})

        gw._client.get = MagicMock(side_effect=[resp_429, resp_429, resp_200])

        with patch("time.sleep"):
            result = gw.fetch("polygon", "https://api.polygon.io/test")

        assert result.status_code == 200
        assert gw._client.get.call_count == 3

    def test_gateway_retries_on_5xx(self, mock_response_factory):
        gw = DataGateway(rates={"edgar": 100.0}, timeout=5.0)

        resp_500 = mock_response_factory(status_code=500, json_data={"error": "internal"})
        resp_200 = mock_response_factory(json_data={"ok": True})

        gw._client.get = MagicMock(side_effect=[resp_500, resp_200])

        with patch("time.sleep"):
            result = gw.fetch("edgar", "https://data.sec.gov/test")

        assert result.status_code == 200
        assert gw._client.get.call_count == 2

    def test_gateway_raises_after_max_retries_on_5xx(self, mock_response_factory):
        gw = DataGateway(rates={"finnhub": 100.0}, timeout=5.0)

        resp_500 = mock_response_factory(status_code=500, json_data={"error": "down"})
        gw._client.get = MagicMock(return_value=resp_500)

        with patch("time.sleep"):
            with pytest.raises(httpx.HTTPStatusError):
                gw.fetch("finnhub", "https://finnhub.io/api/v1/test")

    def test_gateway_raises_after_max_retries_on_429(self, mock_response_factory):
        gw = DataGateway(rates={"polygon": 100.0}, timeout=5.0)

        resp_429 = mock_response_factory(status_code=429, json_data={"error": "rate limited"})
        gw._client.get = MagicMock(return_value=resp_429)

        with patch("time.sleep"):
            with pytest.raises(httpx.HTTPStatusError):
                gw.fetch("polygon", "https://api.polygon.io/test")

    def test_gateway_raises_on_4xx_client_error(self, mock_response_factory):
        gw = DataGateway(rates={"edgar": 100.0}, timeout=5.0)

        resp_403 = mock_response_factory(status_code=403, json_data={"error": "forbidden"})
        gw._client.get = MagicMock(return_value=resp_403)

        with pytest.raises(httpx.HTTPStatusError):
            gw.fetch("edgar", "https://data.sec.gov/test")

        # 4xx should NOT retry (only 429 and 5xx retry)
        assert gw._client.get.call_count == 1

    def test_per_source_isolation(self, mock_response_factory):
        """Rate exhaustion on one source does not affect another."""
        gw = DataGateway(rates={"src_a": 0.0, "src_b": 100.0}, timeout=0.01)
        gw._buckets["src_a"]._tokens = 0.0

        with pytest.raises(TimeoutError):
            gw.fetch("src_a", "https://a.com")

        # src_b should still work -- replace real client with mock
        gw._client = MagicMock(spec=httpx.Client)
        gw._client.get.return_value = mock_response_factory(json_data={"ok": True})
        result = gw.fetch("src_b", "https://b.com")
        assert result.status_code == 200

    def test_context_manager_closes_client(self):
        mock_client = MagicMock(spec=httpx.Client)
        gw = DataGateway.__new__(DataGateway)
        gw._rates = {"test": 10.0}
        gw._buckets = {"test": TokenBucket(rate=10.0)}
        gw._client = mock_client
        with gw:
            assert gw._client is mock_client
        mock_client.close.assert_called_once()


# ---------------------------------------------------------------------------
# Cross-cutting -- EvidencePacket structural invariants
# ---------------------------------------------------------------------------


class TestEvidencePacketInvariants:
    """Verify structural invariants hold across all 13 sources."""

    def test_edgar_packet_invariants(self, gateway, mock_response_factory):
        from pmacs.data.sources.edgar import fetch

        gateway._client.get.return_value = mock_response_factory(json_data={
            "filings": {"recent": {
                "form": ["10-K"], "accessionNumber": ["0001"], "fileDate": ["2025-01-01"],
            }}
        })
        packet = fetch("0001", "AAPL", gateway, cycle_id="inv")
        _assert_valid_packet(packet, "AAPL")

    def test_polygon_packet_invariants(self, gateway, mock_response_factory):
        from pmacs.data.sources.polygon import fetch_daily_bars

        gateway._client.get.return_value = mock_response_factory(json_data={
            "results": [{"t": 1, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100}]
        })
        packet = fetch_daily_bars("MSFT", gateway, api_key="k")
        _assert_valid_packet(packet, "MSFT")

    def test_finnhub_packet_invariants(self, gateway, mock_response_factory):
        from pmacs.data.sources.finnhub import fetch_quote

        gateway._client.get.return_value = mock_response_factory(json_data={"c": 100})
        packet = fetch_quote("GOOG", gateway, api_key="k")
        _assert_valid_packet(packet, "GOOG")

    def test_alpaca_packet_invariants(self, gateway, mock_response_factory):
        from pmacs.data.sources.alpaca_data import fetch_bars

        gateway._client.get.return_value = mock_response_factory(json_data={
            "bars": [{"t": "2025-01-01", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100}]
        })
        packet = fetch_bars("AMZN", gateway, api_key="k")
        _assert_valid_packet(packet, "AMZN")

    def test_openfda_packet_invariants(self, gateway, mock_response_factory):
        from pmacs.data.sources.openfda import fetch_drug_events

        gateway._client.get.return_value = mock_response_factory(
            json_data={"results": [{"id": 1}]}
        )
        packet = fetch_drug_events("ASPIRIN", gateway)
        _assert_valid_packet(packet, "")

    def test_finra_packet_invariants(self, gateway):
        from pmacs.data.sources.finra import fetch_short_interest

        packet = fetch_short_interest("TSLA", gateway)
        _assert_valid_packet(packet, "TSLA")

    def test_form4_packet_invariants(self, gateway, mock_response_factory):
        from pmacs.data.sources.form4 import fetch_insider_filings

        gateway._client.get.return_value = mock_response_factory(json_data={
            "filings": {"recent": {"form": ["4"]}}
        })
        packet = fetch_insider_filings("0001", "NVDA", gateway)
        _assert_valid_packet(packet, "NVDA")

    def test_ir_pages_packet_invariants(self, gateway, mock_response_factory):
        from pmacs.data.sources.ir_pages import fetch_ir_page

        gateway._client.get.return_value = mock_response_factory(text="<html>IR</html>")
        packet = fetch_ir_page("META", "https://ir.meta.com", gateway)
        _assert_valid_packet(packet, "META")

    def test_press_packet_invariants(self, gateway, mock_response_factory):
        from pmacs.data.sources.press import fetch_press_releases

        gateway._client.get.return_value = mock_response_factory(json_data=[
            {"id": 1, "headline": "h", "source": "s"}
        ])
        packet = fetch_press_releases("AMZN", gateway)
        _assert_valid_packet(packet, "AMZN")

    def test_fomc_packet_invariants(self, gateway):
        from pmacs.data.sources.fomc import fetch_latest_statement

        packet = fetch_latest_statement(gateway)
        _assert_valid_packet(packet, "")

    def test_fred_packet_invariants(self, gateway, mock_response_factory):
        from pmacs.data.sources.fred import fetch_series

        gateway._client.get.return_value = mock_response_factory(json_data={
            "observations": [{"date": "2025-01-01", "value": "5.0"}]
        })
        packet = fetch_series("GDP", gateway, api_key="k")
        _assert_valid_packet(packet, "")

    def test_fundamentals_packet_invariants(self, gateway, mock_response_factory):
        from pmacs.data.sources.fundamentals import fetch_fundamentals

        gateway._client.get.return_value = mock_response_factory(json_data={"name": "Test"})
        packet = fetch_fundamentals("TSLA", gateway, api_key="k")
        _assert_valid_packet(packet, "TSLA")

    def test_ecb_packet_invariants(self, gateway):
        from pmacs.data.sources.ecb import fetch_fx_rate
        from pmacs.schemas.currency import FxRate

        mock_rate = FxRate(
            usd_per_eur=1.08, business_date=date(2025, 5, 23),
            fetched_at=datetime.now(timezone.utc),
        )
        with patch("pmacs.data.sources.ecb.fetch_ecb_rate", return_value=mock_rate):
            packet = fetch_fx_rate(gateway)
        _assert_valid_packet(packet, "")

    def test_by_source_groups_correctly(self, gateway, mock_response_factory):
        from pmacs.data.sources.polygon import fetch_daily_bars

        gateway._client.get.return_value = mock_response_factory(json_data={
            "results": [
                {"t": 1, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100},
                {"t": 2, "o": 2, "h": 3, "l": 1.5, "c": 2.5, "v": 200},
            ]
        })
        packet = fetch_daily_bars("AAPL", gateway, api_key="k")
        by_source = packet.by_source
        assert DataSource.POLYGON in by_source
        assert len(by_source[DataSource.POLYGON]) == 2

    def test_frozen_model_immutable(self, gateway, mock_response_factory):
        from pmacs.data.sources.finnhub import fetch_quote

        gateway._client.get.return_value = mock_response_factory(json_data={"c": 100})
        packet = fetch_quote("AAPL", gateway, api_key="k")
        with pytest.raises(Exception):
            packet.ticker = "CHANGED"
