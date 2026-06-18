"""Unit tests for the yfinance fundamentals source (offline, mocked).

Verifies the drop-in evidence shape: Finnhub-compatible keys/units plus the
annual FCF/SBC series. yfinance is mocked so no network is touched.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pmacs.data.sources import yfinance_fundamentals as yfm


class _FakeTicker:
    def __init__(self, info, cashflow, income, balance):
        self.info = info
        self.cashflow = cashflow
        self.income_stmt = income
        self.balance_sheet = balance


def _frame(rows: dict[str, list], periods: list[str]) -> pd.DataFrame:
    cols = [pd.Timestamp(p) for p in periods]
    return pd.DataFrame(rows, index=list(rows.keys()), columns=None).set_axis(cols, axis=1)


@pytest.fixture
def patched_yf(monkeypatch):
    periods = ["2025-09-30", "2024-09-30", "2023-09-30"]
    cashflow = pd.DataFrame(
        {
            pd.Timestamp("2025-09-30"): [100.0, 30.0, 120.0, -20.0],
            pd.Timestamp("2024-09-30"): [90.0, 25.0, 110.0, -20.0],
            pd.Timestamp("2023-09-30"): [80.0, 20.0, 100.0, -20.0],
        },
        index=["Free Cash Flow", "Stock Based Compensation", "Operating Cash Flow", "Capital Expenditure"],
    )
    income = pd.DataFrame(
        {
            pd.Timestamp("2025-09-30"): [10.0, 1000.0, 150.0],
            pd.Timestamp("2024-09-30"): [8.0, 900.0, 135.0],
            pd.Timestamp("2023-09-30"): [np.nan, 800.0, 120.0],  # missing EPS year
        },
        index=["Diluted EPS", "Total Revenue", "EBITDA"],
    )
    balance = pd.DataFrame(
        {
            pd.Timestamp("2025-09-30"): [400.0, 100.0, 50.0],
            pd.Timestamp("2024-09-30"): [350.0, 90.0, 45.0],
            pd.Timestamp("2023-09-30"): [300.0, 80.0, 40.0],
        },
        index=["Stockholders Equity", "Total Debt", "Cash And Cash Equivalents"],
    )
    info = {
        "longName": "Test Corp",
        "sector": "Technology",
        "marketCap": 2_000_000_000.0,
        "sharesOutstanding": 100_000_000.0,
        "totalRevenue": 1_000.0,
        "freeCashflow": 100.0,
        "trailingPE": 20.0,
        "forwardPE": 18.0,
        "priceToBook": 5.0,
        "pegRatio": 1.2,
        "grossMargins": 0.40,
        "operatingMargins": 0.25,
        "profitMargins": 0.15,
        "revenueGrowth": 0.12,
        "returnOnEquity": 0.30,
        "returnOnInvestedCapital": 0.22,
        "fiftyTwoWeekHigh": 120.0,
        "fiftyTwoWeekLow": 60.0,
    }

    def _fake_ticker(symbol):
        return _FakeTicker(info, cashflow, income, balance)

    import yfinance
    monkeypatch.setattr(yfinance, "Ticker", _fake_ticker)
    return periods


def _metrics(pkt):
    return next(e.data for e in pkt.evidence if e.id.endswith("_metrics"))


def _profile(pkt):
    return next(e.data for e in pkt.evidence if e.id.endswith("_profile"))


def test_drop_in_keys_and_units(patched_yf):
    pkt = yfm.fetch_fundamentals_yf("TST")
    m = _metrics(pkt)
    # Valuation passthrough
    assert m["peNormalizedAnnual"] == 20.0
    assert m["forwardPE"] == 18.0
    assert m["pegTTM"] == 1.2
    assert m["pbAnnual"] == 5.0
    # Margins converted fraction -> percent
    assert m["grossMarginTTM"] == 40.0
    assert m["operatingMarginTTM"] == 25.0
    assert m["revenueGrowthTTMYoy"] == 12.0
    # FCF margin computed from fcf/revenue
    assert m["fcfMarginTTM"] == 10.0  # 100 / 1000
    # Returns
    assert m["roicTTM"] == 22.0
    # String _pct variants present for agent consumers
    assert m["grossMarginTTM_pct"] == "40.0%"
    assert m["revenueGrowthTTMYoy_pct"] == "+12.0%"


def test_annual_series_parsed_sorted_and_nan_dropped(patched_yf):
    m = _metrics(yfm.fetch_fundamentals_yf("TST"))
    fcf = m["annual_freeCashFlow"]
    assert [e["period"] for e in fcf] == ["2025-09-30", "2024-09-30", "2023-09-30"]
    assert fcf[0]["v"] == 100.0
    assert [e["v"] for e in m["annual_sbc"]] == [30.0, 25.0, 20.0]
    # EPS 2023 was NaN -> dropped
    assert [e["period"] for e in m["annual_eps"]] == ["2025-09-30", "2024-09-30"]
    assert m["_most_recent_period"] == "2025-09-30"
    # New balance-sheet / EBITDA series for historical P/B and EV/EBITDA
    assert [e["v"] for e in m["annual_revenue"]] == [1000.0, 900.0, 800.0]
    assert [e["v"] for e in m["annual_book_value"]] == [400.0, 350.0, 300.0]
    assert [e["v"] for e in m["annual_ebitda"]] == [150.0, 135.0, 120.0]
    assert [e["v"] for e in m["annual_total_debt"]] == [100.0, 90.0, 80.0]
    assert [e["v"] for e in m["annual_cash"]] == [50.0, 45.0, 40.0]


def test_profile_units_in_millions(patched_yf):
    p = _profile(yfm.fetch_fundamentals_yf("TST"))
    assert p["name"] == "Test Corp"
    assert p["finnhubIndustry"] == "Technology"
    assert p["marketCapitalization"] == 2000.0  # 2e9 / 1e6
    assert p["shareOutstanding"] == 100.0       # 1e8 / 1e6


def test_total_failure_returns_empty_packet(monkeypatch):
    import yfinance

    def _boom(symbol):
        raise RuntimeError("network down")

    monkeypatch.setattr(yfinance, "Ticker", _boom)
    pkt = yfm.fetch_fundamentals_yf("TST")
    assert pkt.evidence == []


def test_missing_numeric_keys_are_omitted(monkeypatch):
    import yfinance
    empty_cf = pd.DataFrame()
    empty_inc = pd.DataFrame()
    empty_bs = pd.DataFrame()
    monkeypatch.setattr(
        yfinance, "Ticker",
        lambda s: _FakeTicker({"longName": "X"}, empty_cf, empty_inc, empty_bs),
    )
    m = _metrics(yfm.fetch_fundamentals_yf("TST"))
    # No FCF/PE data -> those keys absent (N/A), not None noise
    assert "peNormalizedAnnual" not in m
    assert "annual_freeCashFlow" not in m
    assert "annual_book_value" not in m
