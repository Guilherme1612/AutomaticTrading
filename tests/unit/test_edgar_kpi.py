"""Unit tests for the EDGAR filing-narrative SaaS-KPI source.

Tests the deterministic extraction (regex), plausibility filtering, provenance
capture, and the mocked-network ``fetch`` path. No real network calls.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pmacs.data.sources import edgar_kpi
from pmacs.data.sources.edgar_kpi import (
    _extract_kpi,
    _money_to_usd,
    _strip_html,
    fetch,
)


# ── Numeric coercion ──────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,suffix,expected", [
    ("1.2", "B", 1.2e9),
    ("1.2", "billion", 1.2e9),
    ("800", "M", 800e6),
    ("950", "million", 950e6),
    ("1", "T", 1e12),
    ("12.5", "billion", 12.5e9),
])
def test_money_to_usd(raw, suffix, expected):
    assert _money_to_usd(raw, suffix) == pytest.approx(expected, rel=1e-6)


def test_money_to_usd_no_suffix_is_raw_dollars():
    assert _money_to_usd("1234", None) == 1234.0


# ── KPI extraction: positive fixtures ─────────────────────────────────────


def test_nrr_after_phrase():
    spec = next(s for s in edgar_kpi._KPI_SPECS if s["key"] == "nrr")
    val, snippet = _extract_kpi("Our net revenue retention was 118% for the quarter.", spec)
    assert val == 118.0
    assert "net revenue retention" in snippet


def test_nrr_before_phrase():
    spec = next(s for s in edgar_kpi._KPI_SPECS if s["key"] == "nrr")
    val, _ = _extract_kpi("We delivered 124% NRR this period.", spec)
    assert val == 124.0


def test_nrr_dollar_based_net_retention_phrasing():
    spec = next(s for s in edgar_kpi._KPI_SPECS if s["key"] == "nrr")
    val, _ = _extract_kpi("Dollar-based net retention of 117%.", spec)
    assert val == 117.0


def test_arr_after_phrase_with_billion():
    spec = next(s for s in edgar_kpi._KPI_SPECS if s["key"] == "arr")
    val, _ = _extract_kpi("Annual recurring revenue reached $1.50 billion.", spec)
    assert val == 1.5e9


def test_rpo_capture():
    spec = next(s for s in edgar_kpi._KPI_SPECS if s["key"] == "rpo")
    val, _ = _extract_kpi("Remaining performance obligations of $800M.", spec)
    assert val == 800e6


def test_grr_capture():
    spec = next(s for s in edgar_kpi._KPI_SPECS if s["key"] == "grr")
    val, _ = _extract_kpi("Gross retention of 92%.", spec)
    assert val == 92.0


# ── KPI extraction: negative / plausibility fixtures ───────────────────────


def test_kpi_absent_returns_none():
    spec = next(s for s in edgar_kpi._KPI_SPECS if s["key"] == "nrr")
    val, snippet = _extract_kpi("No retention metrics disclosed in this filing.", spec)
    assert val is None
    assert snippet is None


def test_kpi_phrase_without_adjacent_number_returns_none():
    spec = next(s for s in edgar_kpi._KPI_SPECS if s["key"] == "nrr")
    # "NRR" present but the only nearby number (3) is too low to be a retention %.
    val, _ = _extract_kpi("NRR is a key metric we track across 3 segments.", spec)
    assert val is None


def test_nrr_out_of_plausible_range_rejected():
    # 40% is below the 50% floor; 250% is above the 200% ceiling.
    spec = next(s for s in edgar_kpi._KPI_SPECS if s["key"] == "nrr")
    assert _extract_kpi("NRR of 40%.", spec)[0] is None
    assert _extract_kpi("NRR of 250%.", spec)[0] is None


def test_arr_below_floor_rejected():
    # $50K is below the $1M floor — not a credible ARR disclosure.
    spec = next(s for s in edgar_kpi._KPI_SPECS if s["key"] == "arr")
    assert _extract_kpi("ARR of $50K.", spec)[0] is None


def test_arr_definition_multiplier_not_misread_as_money():
    # Regression: "We calculate ARR by taking MRR and multiplying it by 12. MRR
    # is ..." must NOT yield $12M. The 'M' in "MRR" is not a million suffix, and 12
    # (the MRR→ARR multiplier) is not an ARR disclosure. Must return None.
    spec = next(s for s in edgar_kpi._KPI_SPECS if s["key"] == "arr")
    prose = ("We calculate ARR by taking the monthly recurring revenue, or MRR, "
            "and multiplying it by 12. MRR is the annualized value of active contracts.")
    assert _extract_kpi(prose, spec)[0] is None


def test_money_single_letter_suffix_not_swallowed_by_following_word():
    # "M" / "B" only counts as a suffix when not the start of a longer word.
    spec = next(s for s in edgar_kpi._KPI_SPECS if s["key"] == "rpo")
    # "800M." → 800 million (suffix followed by punctuation). Real.
    assert _extract_kpi("Remaining performance obligations of $800M.", spec)[0] == 800e6
    # "800 Months" → 'M' is the start of "Months", not millions → 800 raw, out of
    # the $1M floor → None (not a bogus $800M).
    assert _extract_kpi("RPO of 800 Months remaining.", spec)[0] is None


# ── HTML stripping ────────────────────────────────────────────────────────


def test_strip_html_removes_tags_and_collapse_whitespace():
    html = "<p>Net revenue retention of <b>118%</b> for the quarter.</p>"
    assert _strip_html(html) == "Net revenue retention of 118% for the quarter."


def test_strip_html_drops_scripts_and_styles():
    html = "<style>x{}</style><script>bad()</script>NRR of 120%."
    assert "120%" in _strip_html(html)
    assert "script" not in _strip_html(html)
    assert "style" not in _strip_html(html)


# ── Mocked fetch (no network) ─────────────────────────────────────────────


def _resp(json_body=None, text="", status=200):
    r = SimpleNamespace(status_code=status, text=text)
    r.json = MagicMock(return_value=json_body or {})
    return r


def test_fetch_extracts_kpis_from_mocked_filings():
    """Submissions JSON → filing docs → KPI evidence, end to end with a fake gateway."""
    submissions = {
        "filings": {"recent": {
            "form": ["6-K", "20-F", "4"],
            "accessionNumber": ["0001111111-26-000010", "0001111111-25-000003", "0001111111-25-000002"],
            "primaryDocument": ["dlod-6k.htm", "dlod-20f.htm", "dlod-form4.xml"],
            "filingDate": ["2026-05-12", "2025-03-15", "2025-02-01"],
        }},
    }
    # The 6-K primary doc carries the NRR disclosure; its index lists an ex99-1
    # with the ARR disclosure.
    primary_6k = "We are pleased to report net revenue retention of 118% this quarter."
    ex99_6k = "Annualized recurring revenue reached $1.20 billion."
    filing_6k_index = {
        "directory": {"item": [
            {"name": "dlod-6k.htm"},
            {"name": "ex99-1.htm"},
        ]},
    }

    def fake_fetch(source, url, headers=None, params=None, api_key=None):
        if url.endswith("submissions/CIK0001111111.json"):
            return _resp(json_body=submissions)
        if url.endswith("/dlod-6k.htm"):
            return _resp(text=primary_6k)
        if url.endswith("/index.json") and "000111111126000010" in url:
            return _resp(json_body=filing_6k_index)
        if url.endswith("/ex99-1.htm"):
            return _resp(text=ex99_6k)
        # Other filings: empty content so they contribute nothing.
        return _resp(text="", status=200)

    gw = MagicMock()
    gw.fetch.side_effect = fake_fetch

    packet = fetch("1111111", "DLO", gw, cycle_id="test")
    assert len(packet.evidence) == 1
    ev = packet.evidence[0]
    assert ev.id == "edgar_kpi_DLO"
    assert ev.source.value == "edgar_kpi"
    assert ev.data["nrr_pct"] == 118.0
    assert ev.data["arr_usd"] == 1.2e9
    prov = ev.data["provenance"]
    assert prov["nrr"]["form"] == "6-K"
    assert prov["nrr"]["filed"] == "2026-05-12"
    assert "net revenue retention" in prov["nrr"]["snippet"]
    assert prov["arr"]["form"] == "6-K"


def test_fetch_no_kpis_returns_empty_packet():
    """When no KPI is disclosed, fetch returns an empty packet (never a stub)."""
    submissions = {"filings": {"recent": {
        "form": ["6-K"], "accessionNumber": ["0001111111-26-000010"],
        "primaryDocument": ["dlod-6k.htm"], "filingDate": ["2026-05-12"],
    }}}

    def fake_fetch(source, url, headers=None, params=None, api_key=None):
        if url.endswith("submissions/CIK0001111111.json"):
            return _resp(json_body=submissions)
        return _resp(text="No relevant metrics in this filing.", status=200)

    gw = MagicMock()
    gw.fetch.side_effect = fake_fetch

    packet = fetch("1111111", "DLO", gw, cycle_id="test")
    assert packet.evidence == []
    assert packet.source_count == 0


def test_fetch_invalid_cik_returns_empty():
    gw = MagicMock()
    packet = fetch("not-a-cik", "XXX", gw, cycle_id="test")
    assert packet.evidence == []


def test_fetch_submissions_failure_returns_empty():
    """A network failure (no submissions JSON) must not raise; empty packet."""
    gw = MagicMock()
    gw.fetch.side_effect = RuntimeError("network down")
    packet = fetch("1111111", "DLO", gw, cycle_id="test")
    assert packet.evidence == []


# ── Table-aware extraction ────────────────────────────────────────────────

from pmacs.data.sources.edgar_kpi import (
    _classify_miss,
    _extract_kpi_from_tables,
    _table_rows,
)


def test_table_rows_pairs_label_and_value_cells():
    html = """
    <table><tr><th>Metric</th><th>FY24</th></tr>
    <tr><td>Net revenue retention</td><td>118%</td><td>121%</td></tr></table>
    """
    rows = _table_rows(html)
    assert any("Net revenue retention" in r[0] for r in rows)
    # value cell present alongside label
    label_row = [r for r in rows if "Net revenue retention" in r[0]][0]
    assert "118%" in label_row


def test_extract_kpi_from_tables_finds_value_in_sibling_cell():
    spec = next(s for s in edgar_kpi._KPI_SPECS if s["key"] == "nrr")
    html = ("<table><tr><td>Net revenue retention</td><td>117%</td></tr></table>"
            "<p>no prose number here</p>")
    val, snippet = _extract_kpi_from_tables(html, spec)
    assert val == 117.0
    assert "Net revenue retention" in snippet


def test_extract_kpi_from_tables_rejects_out_of_range_value():
    spec = next(s for s in edgar_kpi._KPI_SPECS if s["key"] == "nrr")
    # 30% is below the 50% floor — must not be accepted from a table either.
    html = "<table><tr><td>Net revenue retention</td><td>30%</td></tr></table>"
    assert _extract_kpi_from_tables(html, spec)[0] is None


def test_extract_kpi_from_tables_ignores_value_in_label_cell():
    spec = next(s for s in edgar_kpi._KPI_SPECS if s["key"] == "arr")
    # Label cell contains "ARR" and a number; the value must come from a SIBLING
    # cell, not the label cell itself.
    html = "<table><tr><td>ARR target 2x</td><td>$1.50 billion</td></tr></table>"
    val, _ = _extract_kpi_from_tables(html, spec)
    assert val == 1.5e9


def test_table_fallback_used_when_prose_has_no_adjacent_number():
    """A KPI disclosed only in a table is captured via the table pass.

    Here the label cell is followed by a long descriptive cell that pushes the
    figure beyond the 80-char prose window, so the prose scan misses it but the
    table scan (sibling-cell pairing) catches it.
    """
    submissions = {"filings": {"recent": {
        "form": ["8-K"], "accessionNumber": ["0001111111-26-000010"],
        "primaryDocument": ["ex99-1.htm"], "filingDate": ["2026-05-12"],
    }}}
    filing_html = (
        "<p>We track net revenue retention as a key metric.</p>"
        "<table><tr>"
        "<td>Net revenue retention</td>"
        "<td>reflecting expansion across our enterprise customer base net of "
        "churn and downgrades and consistent with prior period cohort behaviour</td>"
        "<td>119%</td>"
        "</tr></table>"
    )
    filing_index = {"directory": {"item": [{"name": "ex99-1.htm"}]}}

    def fake_fetch(source, url, headers=None, params=None, api_key=None):
        if url.endswith("submissions/CIK0001111111.json"):
            return _resp(json_body=submissions)
        if url.endswith("/ex99-1.htm"):
            return _resp(text=filing_html)
        if url.endswith("/index.json"):
            return _resp(json_body=filing_index)
        return _resp(text="", status=200)

    gw = MagicMock()
    gw.fetch.side_effect = fake_fetch
    packet = fetch("1111111", "DLO", gw, cycle_id="test")
    ev = packet.evidence[0]
    assert ev.data["nrr_pct"] == 119.0
    assert ev.data["provenance"]["nrr"]["via"] == "table"


def test_prose_hit_keeps_via_prose_tag():
    """A normal prose hit is tagged via='prose' in provenance."""
    submissions = {"filings": {"recent": {
        "form": ["6-K"], "accessionNumber": ["0001111111-26-000010"],
        "primaryDocument": ["dlod-6k.htm"], "filingDate": ["2026-05-12"],
    }}}

    def fake_fetch(source, url, headers=None, params=None, api_key=None):
        if url.endswith("submissions/CIK0001111111.json"):
            return _resp(json_body=submissions)
        if url.endswith("/dlod-6k.htm"):
            return _resp(text="Net revenue retention of 118% this quarter.")
        return _resp(text="", status=200)

    gw = MagicMock()
    gw.fetch.side_effect = fake_fetch
    packet = fetch("1111111", "DLO", gw, cycle_id="test")
    assert packet.evidence[0].data["provenance"]["nrr"]["via"] == "prose"


# ── Miss classification (diagnostic only) ─────────────────────────────────


def _spec(key):
    return next(s for s in edgar_kpi._KPI_SPECS if s["key"] == key)


def test_classify_miss_term_absent():
    # Neither NRR phrase nor "retention" appears → genuine non-disclosure.
    docs = [("x.htm", "We grew revenue 30% year over year.", "")]
    assert _classify_miss(_spec("nrr"), docs)[0] == "TERM_ABSENT"


def test_classify_miss_term_variant():
    # "retention" appears but no exact NRR phrase → phrasing gap.
    docs = [("x.htm", "Our logo retention remains strong across cohorts.", "")]
    cat, sample = _classify_miss(_spec("nrr"), docs)
    assert cat == "TERM_VARIANT"
    assert "retention" in sample


def test_classify_miss_number_far():
    # Exact phrase present, plausible number within ±150 but beyond the 80-char
    # window used for live extraction → window gap.
    prose = "NRR is a metric we define carefully. " + ("x " * 30) + "It was 118% for the quarter."
    docs = [("x.htm", prose, "")]
    cat, _ = _classify_miss(_spec("nrr"), docs)
    assert cat == "NUMBER_FAR"


def test_classify_miss_number_out_of_range():
    # Exact phrase present, only an out-of-range number (30%) nearby.
    docs = [("x.htm", "NRR of 30% this quarter.", "")]
    cat, _ = _classify_miss(_spec("nrr"), docs)
    assert cat == "NUMBER_OUT_OF_RANGE"


def test_classify_miss_number_none_near():
    # Exact phrase present, no number anywhere nearby.
    docs = [("x.htm", "NRR is a key operating metric for our business model.", "")]
    cat, _ = _classify_miss(_spec("nrr"), docs)
    assert cat == "NUMBER_NONE_NEAR"
