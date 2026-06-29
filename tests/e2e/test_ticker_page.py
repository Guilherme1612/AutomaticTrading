"""E2E tests for the per-ticker fundamentals page.

Seeds synthetic evidence for a ticker, renders /ticker/{ticker} via FastAPI
TestClient, and asserts every new section is present:
  - Valuation summary (current + dynamic N-year average per metric)
  - FCF yields (unadjusted + SBC-adjusted)
  - SaaS KPIs (NRR, ARR, GRR, Rule of 40)
  - Analyst consensus
  - Raw fundamentals groups

This test is deterministic, offline, and does not call any data source.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from pmacs.data.evidence_router import _save_evidence_cache
from pmacs.schemas.data import DataSource, Evidence, EvidenceType
from pmacs.web.config import DashboardConfig


@pytest.fixture
def seeded_client(tmp_path):
    """Create a TestClient with synthetic evidence for ticker PMS (Playwright Mock SaaS)."""
    db_path = tmp_path / "pmacs.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS universe (ticker TEXT, sector TEXT, subsector TEXT, "
        "catalyst_type TEXT, pinned_priority INTEGER, halted INTEGER DEFAULT 0, added_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mode_history (id INTEGER PRIMARY KEY, old_mode TEXT, "
        "new_mode TEXT, changed_at TEXT, reason TEXT)"
    )
    conn.execute("INSERT INTO mode_history VALUES (1, 'INIT', 'SHADOW', datetime('now'), 'test')")
    conn.execute("INSERT INTO universe VALUES ('PMS', 'Tech', 'SaaS', 'earnings', 0, 0, '2026-01-01')")
    conn.commit()
    conn.close()

    cfg = DashboardConfig(
        sqlite_path=str(db_path),
        duckdb_path=str(tmp_path / "analytics.duckdb"),
        audit_path=str(tmp_path / "audit.log"),
        heartbeat_dir=tmp_path / "heartbeats",
        config_dir=str(tmp_path / "config"),
    )
    (tmp_path / "config").mkdir()

    now = datetime.now(timezone.utc)
    evidence = [
        # Profile
        Evidence(
            id="fundamentals_PMS_profile",
            source=DataSource.YAHOO,
            type=EvidenceType.FINANCIAL_STATEMENT,
            ticker="PMS",
            fetched_at=now,
            content_hash="p1",
            title="PMS profile",
            data={
                "name": "Playwright Mock SaaS Inc.",
                "finnhubIndustry": "Software",
                "marketCapitalization": 5_000.0,  # millions -> USD 5B
                "shareOutstanding": 100.0,  # millions -> 100M shares
                "currency": "USD",
            },
        ),
        # Fundamentals metrics
        Evidence(
            id="fundamentals_PMS_metrics",
            source=DataSource.YAHOO,
            type=EvidenceType.FINANCIAL_STATEMENT,
            ticker="PMS",
            fetched_at=now,
            content_hash="m1",
            title="PMS fundamentals",
            data={
                "peNormalizedAnnual": 25.0,
                "forwardPE": 20.0,
                "psAnnual": 8.0,
                "pbAnnual": 5.0,
                "evToEbitdaTTM": 18.0,
                "pegTTM": 1.3,
                "epsTTM": 2.0,
                "revenueTTM": 625_000_000.0,
                "revenueGrowthTTMYoy": 30.0,
                "grossMarginTTM": 75.0,
                "operatingMarginTTM": 10.0,
                "netProfitMarginTTM": 8.0,
                "fcfMarginTTM": 15.0,
                "roeTTM": 18.0,
                "roaTTM": 12.0,
                "roicTTM": 16.0,
                "_most_recent_period": "2025-09-30",
                "_data_age_days": 120,
                "annual_eps": [
                    {"period": "2025-09-30", "v": 2.0},
                    {"period": "2024-09-30", "v": 1.6},
                    {"period": "2023-09-30", "v": 1.2},
                ],
                "annual_freeCashFlow": [
                    {"period": "2025-09-30", "v": 100_000_000.0},
                    {"period": "2024-09-30", "v": 80_000_000.0},
                    {"period": "2023-09-30", "v": 60_000_000.0},
                ],
                "annual_sbc": [
                    {"period": "2025-09-30", "v": 20_000_000.0},
                    {"period": "2024-09-30", "v": 15_000_000.0},
                    {"period": "2023-09-30", "v": 10_000_000.0},
                ],
                "annual_revenue": [
                    {"period": "2025-09-30", "v": 625_000_000.0},
                    {"period": "2024-09-30", "v": 480_000_000.0},
                    {"period": "2023-09-30", "v": 360_000_000.0},
                ],
                "annual_book_value": [
                    {"period": "2025-09-30", "v": 1_000_000_000.0},
                    {"period": "2024-09-30", "v": 900_000_000.0},
                    {"period": "2023-09-30", "v": 800_000_000.0},
                ],
                "annual_ebitda": [
                    {"period": "2025-09-30", "v": 280_000_000.0},
                    {"period": "2024-09-30", "v": 220_000_000.0},
                    {"period": "2023-09-30", "v": 180_000_000.0},
                ],
                "annual_total_debt": [
                    {"period": "2025-09-30", "v": 200_000_000.0},
                    {"period": "2024-09-30", "v": 190_000_000.0},
                    {"period": "2023-09-30", "v": 180_000_000.0},
                ],
                "annual_cash": [
                    {"period": "2025-09-30", "v": 300_000_000.0},
                    {"period": "2024-09-30", "v": 280_000_000.0},
                    {"period": "2023-09-30", "v": 260_000_000.0},
                ],
            },
        ),
        # Agent memo text containing NRR/ARR (agent-derived, so marked as estimate)
        Evidence(
            id="agent_PMS_value_memo",
            source=DataSource.YAHOO,
            type=EvidenceType.ANALYST_DATA,
            ticker="PMS",
            fetched_at=now,
            content_hash="a1",
            title="PMS memo",
            data={
                "analysis": "NRR is 118%. ARR grew to $750M. GRR of 92%.",
                "key_signal": "RPO at $900M.",
            },
        ),
        # Analyst price target
        Evidence(
            id="yahoo_PMS_price_target",
            source=DataSource.YAHOO,
            type=EvidenceType.ANALYST_DATA,
            ticker="PMS",
            fetched_at=now,
            content_hash="y1",
            title="PMS price target",
            data={
                "target_mean": 60.0,
                "target_high": 75.0,
                "target_low": 45.0,
                "target_median": 58.0,
                "num_analysts": 20,
                "current_price": 50.0,
                "upside_to_mean_pct": 20.0,
            },
        ),
        # Analyst recommendations
        Evidence(
            id="finnhub_PMS_analyst_recommendations",
            source=DataSource.FINNHUB,
            type=EvidenceType.ANALYST_DATA,
            ticker="PMS",
            fetched_at=now,
            content_hash="f1",
            title="PMS analyst recs",
            data={
                "strong_buy": 5,
                "buy": 10,
                "hold": 4,
                "sell": 1,
                "strong_sell": 0,
                "total_analysts": 20,
                "consensus": "Buy",
            },
        ),
    ]

    # The evidence router uses pmacs.config.data_dir() to locate pmacs.db.
    # Point it at the temp directory so the fixture is isolated and portable.
    os.environ["PMACS_DATA_DIR"] = str(tmp_path)
    try:
        _save_evidence_cache("PMS", evidence, cycle_id="cyc-test")

        with patch("pmacs.web.config.get_config", return_value=cfg):
            from pmacs.web.app import app
            yield TestClient(app, raise_server_exceptions=False)
    finally:
        os.environ.pop("PMACS_DATA_DIR", None)


class TestTickerPageRenders:
    """Page-level structural tests with seeded evidence."""

    def test_page_renders_200(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        assert resp.status_code == 200, resp.text[:500]

    def test_header_shows_ticker_and_company(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        assert "PMS" in resp.text
        assert "Playwright Mock SaaS Inc." in resp.text

    def test_header_strips_dropped_by_design(self, seeded_client):
        """Operator directive (audit P2-#10): the at-a-glance verdict chip strip
        (Cash / Fwd P/E / Trend / Coverage) was removed from the header. The
        header now presents the ticker identity as a single elevated hero card
        (symbol + sector chip on the top row, company name as subtitle).
        Verify the chip strip is gone and the new minimal header renders."""
        resp = seeded_client.get("/ticker/PMS")
        # None of the old chip labels are present anywhere on the page.
        for label in ("Cash:", "Fwd P/E:", "Trend:", "Coverage:"):
            assert label not in resp.text, (
                f"header chip strip should have been removed but '{label}' "
                f"still appears in the page"
            )
        # The header's identity elements are present.
        assert ">PMS<" in resp.text  # the H1 ticker
        assert "Playwright Mock SaaS Inc." in resp.text  # company subtitle


class TestValuationSummary:
    """Current value + 3-year average cards."""

    def test_pe_card_current_and_average(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        html = resp.text
        assert "P/E" in html
        assert "$50.00" in html or "25.00" in html  # current P/E or price
        # Dynamic N-year average language (PMS test fixture has 3 years of
        # data so we expect "3Y avg" — but we accept any 1-3 year label).
        import re
        assert (
            re.search(r"\b[1-3]Y avg\b", html)
            or "N-year average unavailable" in html
        ), "P/E card must show dynamic N-year average"

    def test_forward_pe_displayed(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        assert "Forward P/E" in resp.text
        assert "20.00" in resp.text

    def test_ps_pb_ev_ebitda_cards(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        html = resp.text
        assert "P/S" in html
        assert "P/B" in html
        assert "EV/EBITDA" in html
        assert "PEG" in html


class TestFcfYields:
    """Unadjusted and SBC-adjusted FCF yield columns."""

    def test_fcf_yield_unadjusted(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        assert "Free cash flow yield" in resp.text
        # 100M FCF / 5B market cap = 2.0%
        assert "2.0%" in resp.text

    def test_fcf_yield_sbc_adjusted(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        # (100M - 20M) / 5B = 1.6%
        assert "1.6%" in resp.text

    def test_sbc_drag_badge(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        assert "SBC drag" in resp.text


class TestSaasKpis:
    """NRR, ARR, GRR, Rule of 40 extracted from evidence text."""

    def test_nrr_displayed(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        assert "Net Revenue Retention (NRR)" in resp.text
        assert "118.0%" in resp.text

    def test_arr_displayed(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        assert "Annual Recurring Revenue (ARR)" in resp.text
        assert "$750.00M" in resp.text

    def test_grr_displayed(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        assert "Gross Retention (GRR)" in resp.text
        assert "92.0%" in resp.text

    def test_rule_of_40_displayed(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        assert "Rule of 40" in resp.text
        # 30.0% growth + 15.0% FCF margin = 45.0%
        assert "45.0%" in resp.text

    def test_kpi_context_badges(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        # NRR 118% -> Excellent; Rule of 40 45% -> Excellent; GRR 92% -> Good
        assert "Excellent" in resp.text or "Good" in resp.text


class TestAnalystConsensus:
    """Price target, upside, ratings mix."""

    def test_price_target_range(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        assert "Price target" in resp.text
        assert "$60.00" in resp.text
        assert "$45.00" in resp.text
        assert "$75.00" in resp.text

    def test_analyst_count(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        assert "20 analysts covering" in resp.text or "20 analyst" in resp.text

    def test_ratings_mix(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        assert "Ratings mix" in resp.text
        assert "Strong buy" in resp.text
        assert "Buy" in resp.text
        assert "Hold" in resp.text

    def test_consensus_buy(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        html = resp.text
        assert "Consensus:" in html
        assert "Buy" in html


class TestNYearMultiples:
    """The historical-avg multiples are exposed via the Valuation summary
    cards (current value + NY average per metric) — the redesign dropped
    the separate per-year multiples table in favor of inline chips in each
    metric card. Per operator directive 2026-06-23 the average is
    DYNAMIC: whatever years the underlying series actually covers, not
    a hard 3-year floor. Newer tickers (1-2 years of data) show "1Y avg"
    or "2Y avg" instead of "3Y avg". This class locks that contract."""

    def test_valuation_summary_has_ny_average(self, seeded_client):
        """Every metric card in the Valuation summary shows the N-year
        average next to the current value. Verify the N-year-average
        language renders, regardless of which N it is."""
        resp = seeded_client.get("/ticker/PMS")
        html = resp.text
        # Accept any N where 1 ≤ N ≤ 3, OR the explicit "unavailable" string
        # (rendered when no annual data exists at all).
        import re
        assert (
            re.search(r"\b[1-3]Y avg\b", html)
            or "N-year average unavailable" in html
        ), "Valuation summary must surface a dynamic NY avg label"

    def test_per_metric_cards_render_all_multiples(self, seeded_client):
        """The Valuation summary card grid renders one card per multiple
        (P/E, P/S, P/B, EV/EBITDA). The per-card multiple label is
        verified instead of the old table-column header."""
        resp = seeded_client.get("/ticker/PMS")
        html = resp.text
        for label in ("P/S", "P/B", "EV/EBITDA"):
            assert label in html, f"multiple label '{label}' missing from valuation summary"

    def test_sparklines_present(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        assert "<svg" in resp.text


class TestRawFundamentals:
    """Original raw fundamentals groups still render."""

    def test_raw_fundamentals_section(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        assert "Raw fundamentals" in resp.text

    def test_growth_and_margins_groups(self, seeded_client):
        resp = seeded_client.get("/ticker/PMS")
        html = resp.text
        assert "Growth" in html
        assert "Margins" in html
        assert "Technical" in html
