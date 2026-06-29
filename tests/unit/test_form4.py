"""Unit tests for Form 4 insider-filing source (XML parsing path)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from pmacs.data.gateway import DataGateway
from pmacs.data.sources.form4 import fetch_insider_filings
from pmacs.schemas.data import DataSource, EvidenceType


@pytest.fixture()
def gateway():
    gw = DataGateway.__new__(DataGateway)
    gw._rates = {}
    gw._buckets = {}
    gw._client = MagicMock()
    return gw


# Submissions JSON with a single Form 4 filing (used by most tests)
_SUBMISSIONS_JSON_ONE = {
    "filings": {
        "recent": {
            "form": ["4", "10-K"],
            "filingDate": ["2025-03-15", "2025-02-20"],
            "accessionNumber": ["0000320193-25-000123", "0000320193-25-000122"],
            "primaryDocument": ["primary_doc.xml", "10k.pdf"],
        }
    }
}

# Submissions JSON with three Form 4 filings (used by cluster test)
_SUBMISSIONS_JSON_CLUSTER = {
    "filings": {
        "recent": {
            "form": ["4", "4", "4"],
            "filingDate": ["2025-03-15", "2025-03-14", "2025-03-13"],
            "accessionNumber": ["0000320193-25-000123", "0000320193-25-000121", "0000320193-25-000120"],
            "primaryDocument": ["primary_doc.xml", "primary_doc.xml", "primary_doc.xml"],
        }
    }
}


def _build_xml(
    owner_name: str = "Jane Doe",
    officer_title: str = "Chief Financial Officer",
    owner_cik: str = "0001234567",
    transactions: list[dict] | None = None,
    period: str = "2025-03-15",
) -> str:
    if transactions is None:
        transactions = []

    txn_xml = ""
    for t in transactions:
        code = t.get("code", "P")
        ad = t.get("ad", "A")
        shares = t.get("shares", 1000)
        price = t.get("price", 50.0)
        date_ = t.get("date", "2025-03-15")
        txn_xml += f"""
        <nonDerivativeTransaction>
          <securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>{date_}</value></transactionDate>
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
  <periodOfReport>{period}</periodOfReport>
  <issuer>
    <issuerCik>0000320193</issuerCik>
    <issuerName>Apple Inc.</issuerName>
    <issuerTradingSymbol>AAPL</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>{owner_cik}</rptOwnerCik>
      <rptOwnerName>{owner_name}</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>false</isDirector>
      <isOfficer>true</isOfficer>
      <isTenPercentOwner>false</isTenPercentOwner>
      <isOther>false</isOther>
      <officerTitle>{officer_title}</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>{txn_xml}</nonDerivativeTable>
  <derivativeTable></derivativeTable>
  <ownerSignature>
    <signatureName>{owner_name}</signatureName>
    <signatureDate>{period}</signatureDate>
  </ownerSignature>
</ownershipDocument>
"""


class _MockResponse:
    def __init__(self, json_data=None, text="", status_code=200):
        self.json_data = json_data
        self.text = text
        self.status_code = status_code

    def json(self):
        return self.json_data

    def raise_for_status(self):
        return None


def test_fetch_returns_summary_and_transaction_evidence(gateway):
    xml = _build_xml(transactions=[{"code": "P", "shares": 1000, "price": 50.0}])
    gateway._client.get.side_effect = lambda url, **kwargs: (
        _MockResponse(json_data=_SUBMISSIONS_JSON_ONE) if "submissions" in url else _MockResponse(text=xml)
    )
    packet = fetch_insider_filings("0000320193", "AAPL", gateway, cycle_id="c001")

    assert packet.ticker == "AAPL"
    assert len(packet.evidence) >= 2
    summary = packet.evidence[0]
    assert summary.source == DataSource.FORM4
    assert summary.type == EvidenceType.INSIDER_FILING
    assert summary.data["purchase_count"] == 1
    assert summary.data["sale_count"] == 0


def test_ceo_buy_signal_detected(gateway):
    xml = _build_xml(
        owner_name="Tim Cook",
        officer_title="Chief Executive Officer",
        owner_cik="0000000001",
        transactions=[{"code": "P", "shares": 10000, "price": 150.0}],
    )
    gateway._client.get.side_effect = lambda url, **kwargs: (
        _MockResponse(json_data=_SUBMISSIONS_JSON_ONE) if "submissions" in url else _MockResponse(text=xml)
    )
    packet = fetch_insider_filings("0000320193", "AAPL", gateway, cycle_id="c002")

    summary = packet.evidence[0]
    assert "CEO_BUY" in summary.data["signals"]
    assert summary.data["purchase_count"] == 1


def test_cluster_buy_signal_requires_three_distinct_insiders(gateway):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    xml_jane = _build_xml(
        owner_name="Jane Doe", owner_cik="0000000001", officer_title="CFO",
        transactions=[{"code": "P", "shares": 1000, "price": 100.0, "date": today}],
    )
    xml_john = _build_xml(
        owner_name="John Smith", owner_cik="0000000002", officer_title="CTO",
        transactions=[{"code": "P", "shares": 1000, "price": 100.0, "date": today}],
    )
    xml_alice = _build_xml(
        owner_name="Alice Wong", owner_cik="0000000003", officer_title="Director",
        transactions=[{"code": "P", "shares": 1000, "price": 100.0, "date": today}],
    )

    responses = {
        "submissions": _MockResponse(json_data=_SUBMISSIONS_JSON_CLUSTER),
        "000032019325000123": xml_jane,
        "000032019325000121": xml_john,
        "000032019325000120": xml_alice,
    }

    def _get(url: str, **kwargs):
        if "submissions" in url:
            return responses["submissions"]
        for key, resp in responses.items():
            if key in url:
                return _MockResponse(text=resp)
        return _MockResponse(text=_build_xml())

    gateway._client.get.side_effect = _get
    packet = fetch_insider_filings("0000320193", "AAPL", gateway, cycle_id="c003")

    summary = packet.evidence[0]
    assert "CLUSTER_BUY" in summary.data["signals"]
    assert summary.data["distinct_buyers_30d"] == 3


def test_large_buy_threshold(gateway):
    xml = _build_xml(
        owner_name="Big Buyer", owner_cik="0000000004", officer_title="Director",
        transactions=[{"code": "P", "shares": 10_000, "price": 60.0}],  # $600K
    )
    gateway._client.get.side_effect = lambda url, **kwargs: (
        _MockResponse(json_data=_SUBMISSIONS_JSON_ONE) if "submissions" in url else _MockResponse(text=xml)
    )
    packet = fetch_insider_filings("0000320193", "AAPL", gateway, cycle_id="c004")

    summary = packet.evidence[0]
    assert "LARGE_BUY" in summary.data["signals"]
    assert summary.data["large_buy_count"] == 1


def test_large_sell_threshold(gateway):
    xml = _build_xml(
        owner_name="Big Seller", owner_cik="0000000005", officer_title="Director",
        transactions=[{"code": "S", "ad": "D", "shares": 20_000, "price": 60.0}],  # $1.2M sale
    )
    gateway._client.get.side_effect = lambda url, **kwargs: (
        _MockResponse(json_data=_SUBMISSIONS_JSON_ONE) if "submissions" in url else _MockResponse(text=xml)
    )
    packet = fetch_insider_filings("0000320193", "AAPL", gateway, cycle_id="c005")

    summary = packet.evidence[0]
    assert "LARGE_SELL" in summary.data["signals"]
    assert summary.data["large_sell_count"] == 1


def test_fetch_error_returns_error_evidence(gateway):
    from httpx import HTTPStatusError

    request = MagicMock()
    response = MagicMock()
    response.status_code = 500
    gateway._client.get.side_effect = HTTPStatusError("500", request=request, response=response)
    packet = fetch_insider_filings("0000320193", "AAPL", gateway, cycle_id="c006")

    assert len(packet.evidence) == 1
    assert packet.evidence[0].data["status"] == "FETCH_ERROR"


def test_transaction_evidence_contains_required_fields(gateway):
    xml = _build_xml(
        owner_name="Jane Doe", owner_cik="0000000006", officer_title="Chief Financial Officer",
        transactions=[{"code": "P", "shares": 5000, "price": 25.0}],
    )
    gateway._client.get.side_effect = lambda url, **kwargs: (
        _MockResponse(json_data=_SUBMISSIONS_JSON_ONE) if "submissions" in url else _MockResponse(text=xml)
    )
    packet = fetch_insider_filings("0000320193", "AAPL", gateway, cycle_id="c007")

    txn_ev = packet.evidence[1]
    data = txn_ev.data
    assert data["transaction_type"] == "PURCHASE"
    assert data["transaction_code"] == "P"
    assert data["shares"] == 5000.0
    assert data["price_per_share"] == 25.0
    assert data["dollar_value"] == 5000.0 * 25.0
    assert data["officer_title"] == "Chief Financial Officer"
    assert data["reporting_owner_name"] == "Jane Doe"


def test_routine_only_no_purchase_signals(gateway):
    xml = _build_xml(
        owner_name="Jane Doe", owner_cik="0000000007", officer_title="CFO",
        transactions=[{"code": "M", "shares": 1000, "price": 0.0, "ad": "A"}],
    )
    gateway._client.get.side_effect = lambda url, **kwargs: (
        _MockResponse(json_data=_SUBMISSIONS_JSON_ONE) if "submissions" in url else _MockResponse(text=xml)
    )
    packet = fetch_insider_filings("0000320193", "AAPL", gateway, cycle_id="c008")

    summary = packet.evidence[0]
    assert summary.data["purchase_count"] == 0
    assert summary.data["routine_count"] == 1
    assert "CEO_BUY" not in summary.data["signals"]
    assert "LARGE_BUY" not in summary.data["signals"]
