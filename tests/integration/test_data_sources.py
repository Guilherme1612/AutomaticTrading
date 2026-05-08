"""Data sources integration test — Phase 2 exit test #3.

Requires real API keys in macOS Keychain.
At least 10/13 sources must return a valid EvidencePacket.
3 failures allowed for NICE_TO_HAVE sources.
"""

import pytest
from datetime import datetime, timezone

from pmacs.data.gateway import DataGateway
from pmacs.schemas.data import EvidencePacket


# CI-skip marker: these tests require real API keys
pytestmark = pytest.mark.integration


SOURCES_TO_TEST = [
    "polygon",
    "finnhub",
    "edgar",
    "alpaca_data",
    "fundamentals",
    "press",
    "form4",
    "openfda",
    "finra",
    "ir_pages",
    "fred",
    "fomc",
    "ecb",
]

MIN_SOURCES_PASSING = 10


def _get_api_key(service: str) -> str | None:
    """Try to get API key from Keychain, return None if not found."""
    try:
        from pmacs.storage.keychain import get_api_key
        return get_api_key("pmacs", service)
    except Exception:
        return None


@pytest.fixture
def gateway():
    with DataGateway() as gw:
        yield gw


class TestDataSourcesIntegration:
    """Test each data source returns a valid EvidencePacket."""

    def test_polygon(self, gateway):
        key = _get_api_key("polygon")
        if key is None:
            pytest.skip("No polygon API key in Keychain")
        from pmacs.data.sources import polygon
        result = polygon.fetch_daily_bars("AAPL", gateway, key)
        assert isinstance(result, EvidencePacket)
        assert result.ticker == "AAPL"

    def test_finnhub(self, gateway):
        key = _get_api_key("finnhub")
        if key is None:
            pytest.skip("No finnhub API key in Keychain")
        from pmacs.data.sources import finnhub
        result = finnhub.fetch_quote("AAPL", gateway, key)
        assert isinstance(result, EvidencePacket)

    def test_edgar(self, gateway):
        from pmacs.data.sources import edgar
        result = edgar.fetch("0000320193", "AAPL", gateway)
        assert isinstance(result, EvidencePacket)

    def test_alpaca_data(self, gateway):
        key = _get_api_key("alpaca")
        if key is None:
            pytest.skip("No alpaca API key in Keychain")
        from pmacs.data.sources import alpaca_data
        result = alpaca_data.fetch_bars("AAPL", gateway, key)
        assert isinstance(result, EvidencePacket)

    def test_openfda(self, gateway):
        from pmacs.data.sources import openfda
        result = openfda.fetch_drug_events("aspirin", gateway)
        assert isinstance(result, EvidencePacket)

    def test_finra(self, gateway):
        from pmacs.data.sources import finra
        result = finra.fetch_short_interest("AAPL", gateway)
        assert isinstance(result, EvidencePacket)

    def test_form4(self, gateway):
        from pmacs.data.sources import form4
        result = form4.fetch_insider_filings("0000320193", "AAPL", gateway)
        assert isinstance(result, EvidencePacket)

    def test_ir_pages(self, gateway):
        from pmacs.data.sources import ir_pages
        result = ir_pages.fetch_ir_page("AAPL", "https://investor.apple.com", gateway)
        assert isinstance(result, EvidencePacket)

    def test_press(self, gateway):
        key = _get_api_key("finnhub")
        if key is None:
            pytest.skip("No finnhub API key in Keychain")
        from pmacs.data.sources import press
        result = press.fetch_press_releases("AAPL", gateway)
        assert isinstance(result, EvidencePacket)

    def test_fomc(self, gateway):
        from pmacs.data.sources import fomc
        result = fomc.fetch_latest_statement(gateway)
        assert isinstance(result, EvidencePacket)

    def test_fred(self, gateway):
        from pmacs.data.sources import fred
        result = fred.fetch_series("DFF", gateway)
        assert isinstance(result, EvidencePacket)

    def test_ecb(self, gateway):
        from pmacs.data.sources import ecb
        result = ecb.fetch_fx_rate(gateway)
        assert isinstance(result, EvidencePacket)

    def test_fundamentals(self, gateway):
        key = _get_api_key("finnhub")
        if key is None:
            pytest.skip("No finnhub API key in Keychain")
        from pmacs.data.sources import fundamentals
        result = fundamentals.fetch_fundamentals("AAPL", gateway, key)
        assert isinstance(result, EvidencePacket)
