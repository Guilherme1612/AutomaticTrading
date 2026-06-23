"""Unit tests for the ticker metrics engine (Source.md §16.8)."""
from __future__ import annotations

from pmacs.engines.ticker_metrics import compute_fcf_yields, compute_ticker_metrics


# ── FCF yields ──────────────────────────────────────────────────────────────


def test_fcf_yields_unadjusted_and_sbc_adjusted():
    # FCF 100, SBC 40, market cap 2000 → 5.0% unadjusted, 3.0% adjusted.
    unadj, adj = compute_fcf_yields(100.0, 40.0, 2000.0)
    assert unadj == 5.0
    assert adj == 3.0


def test_fcf_yield_sbc_none_means_adjusted_none_not_zero():
    unadj, adj = compute_fcf_yields(100.0, None, 2000.0)
    assert unadj == 5.0
    assert adj is None


def test_fcf_yield_guards_zero_market_cap():
    assert compute_fcf_yields(100.0, 10.0, 0.0) == (None, None)
    assert compute_fcf_yields(None, 10.0, 2000.0) == (None, None)


# ── Multi-year multiples ────────────────────────────────────────────────────


def _eps(*pairs):
    return [{"period": p, "v": v} for p, v in pairs]


def test_pe_ny_average_over_three_years():
    m = compute_ticker_metrics(
        "AAA",
        eps_series=_eps(("2023-12-31", 10.0), ("2022-12-31", 8.0), ("2021-12-31", 5.0)),
        price_by_period={
            "2023-12-31": 200.0,  # PE 20
            "2022-12-31": 160.0,  # PE 20
            "2021-12-31": 150.0,  # PE 30
        },
    )
    assert [y.pe for y in m.per_year] == [20.0, 20.0, 30.0]
    # mean(20, 20, 30) = 23.33
    assert m.pe_ny_avg == 23.33


def test_pe_skips_non_positive_eps_with_note():
    m = compute_ticker_metrics(
        "BBB",
        eps_series=_eps(("2023-12-31", 10.0), ("2022-12-31", -2.0)),
        price_by_period={"2023-12-31": 100.0, "2022-12-31": 50.0},
    )
    assert m.pe_ny_avg == 10.0  # only the positive-EPS year counts
    assert any("EPS <= 0" in n for n in m.notes)


def test_pfcf_uses_historical_shares_when_available():
    m = compute_ticker_metrics(
        "CCC",
        fcf_series=[{"period": "2023-12-31", "v": 1_000.0}],
        price_by_period={"2023-12-31": 50.0},
        shares_by_period={"2023-12-31": 100.0},  # market cap 5000 → P/FCF 5.0
        shares_outstanding=999.0,  # must be ignored
    )
    year = m.per_year[0]
    assert year.pfcf == 5.0
    assert year.shares_approximated is False
    assert m.pfcf_ny_avg == 5.0


def test_pfcf_falls_back_to_current_shares_and_flags_it():
    m = compute_ticker_metrics(
        "DDD",
        fcf_series=[{"period": "2023-12-31", "v": 1_000.0}],
        price_by_period={"2023-12-31": 50.0},
        shares_outstanding=100.0,  # market cap 5000 → P/FCF 5.0
    )
    year = m.per_year[0]
    assert year.pfcf == 5.0
    assert year.shares_approximated is True
    assert any("current share count" in n for n in m.notes)


def test_pfcf_skips_negative_fcf():
    m = compute_ticker_metrics(
        "EEE",
        fcf_series=[{"period": "2023-12-31", "v": -500.0}],
        price_by_period={"2023-12-31": 50.0},
        shares_outstanding=100.0,
    )
    assert m.pfcf_ny_avg is None
    assert any("FCF <= 0" in n for n in m.notes)


def test_latest_fcf_picks_most_recent_period():
    m = compute_ticker_metrics(
        "FFF",
        fcf_series=[
            {"period": "2021-12-31", "v": 100.0},
            {"period": "2023-12-31", "v": 300.0},
            {"period": "2022-12-31", "v": 200.0},
        ],
        market_cap_usd=6000.0,
        sbc_usd=60.0,
    )
    assert m.latest_fcf_usd == 300.0
    assert m.fcf_yield_pct == 5.0  # 300 / 6000
    assert m.fcf_yield_sbc_adjusted_pct == 4.0  # (300 - 60) / 6000


def test_only_three_most_recent_years_used():
    m = compute_ticker_metrics(
        "GGG",
        eps_series=_eps(
            ("2023-12-31", 10.0),
            ("2022-12-31", 10.0),
            ("2021-12-31", 10.0),
            ("2020-12-31", 10.0),  # should be dropped
        ),
        price_by_period={
            "2023-12-31": 100.0,
            "2022-12-31": 100.0,
            "2021-12-31": 100.0,
            "2020-12-31": 999.0,
        },
    )
    assert len(m.per_year) == 3
    assert "2020-12-31" not in [y.period for y in m.per_year]
    assert m.pe_ny_avg == 10.0


def test_partial_history_notes_year_count():
    m = compute_ticker_metrics(
        "HHH",
        eps_series=_eps(("2023-12-31", 10.0)),
        price_by_period={"2023-12-31": 100.0},
    )
    assert m.pe_ny_avg == 10.0
    assert any("1 year" in n for n in m.notes)


def test_non_numeric_values_are_ignored():
    m = compute_ticker_metrics(
        "III",
        eps_series=[{"period": "2023-12-31", "v": "N/A"}, {"period": "2022-12-31", "v": 5.0}],
        price_by_period={"2022-12-31": 50.0},
    )
    assert m.pe_ny_avg == 10.0  # 50 / 5, the bad row dropped


def test_empty_evidence_yields_empty_metrics():
    m = compute_ticker_metrics("JJJ")
    assert m.per_year == []
    assert m.pe_ny_avg is None
    assert m.pfcf_ny_avg is None
    assert m.fcf_yield_pct is None


def _series(*pairs):
    return [{"period": p, "v": v} for p, v in pairs]


# ── Historical averages for other multiples ─────────────────────────────────


def test_ps_ny_average_over_three_years():
    # price 10, shares 100, market cap 1000, revenue 200 -> P/S 5.0
    m = compute_ticker_metrics(
        "KKK",
        revenue_series=_series(
            ("2023-12-31", 200.0), ("2022-12-31", 180.0), ("2021-12-31", 150.0)
        ),
        price_by_period={
            "2023-12-31": 10.0,
            "2022-12-31": 9.0,
            "2021-12-31": 8.0,
        },
        shares_outstanding=100.0,
    )
    assert [y.ps for y in m.per_year] == [5.0, 5.0, 5.33]
    assert m.ps_ny_avg == 5.11


def test_pb_ny_average_over_three_years():
    m = compute_ticker_metrics(
        "LLL",
        book_value_series=_series(
            ("2023-12-31", 500.0), ("2022-12-31", 450.0), ("2021-12-31", 400.0)
        ),
        price_by_period={
            "2023-12-31": 10.0,
            "2022-12-31": 9.0,
            "2021-12-31": 8.0,
        },
        shares_outstanding=100.0,
    )
    # market cap 1000 / book value 500 = 2.0, etc.
    assert [y.pb for y in m.per_year] == [2.0, 2.0, 2.0]
    assert m.pb_ny_avg == 2.0


def test_ev_ebitda_ny_average():
    # market cap 1000 + debt 100 - cash 50 = EV 1050; EBITDA 150 -> 7.0
    m = compute_ticker_metrics(
        "MMM",
        ebitda_series=_series(
            ("2023-12-31", 150.0), ("2022-12-31", 140.0), ("2021-12-31", 130.0)
        ),
        debt_series=_series(
            ("2023-12-31", 100.0), ("2022-12-31", 90.0), ("2021-12-31", 80.0)
        ),
        cash_series=_series(
            ("2023-12-31", 50.0), ("2022-12-31", 45.0), ("2021-12-31", 40.0)
        ),
        price_by_period={
            "2023-12-31": 10.0,
            "2022-12-31": 9.0,
            "2021-12-31": 8.0,
        },
        shares_outstanding=100.0,
    )
    assert [round(y.ev_ebitda, 2) for y in m.per_year] == [7.0, 6.75, 6.46]
    assert m.ev_ebitda_ny_avg == 6.74


# ── SaaS KPI extraction ───────────────────────────────────────────────────────


def test_extract_saas_kpis_from_evidence_text():
    text = "NRR is 118%. GRR of 92%. ARR grew to $1.2B. RPO at $800M."
    kpis = compute_ticker_metrics("NNN", evidence_text=text).saas_kpis
    assert kpis.nrr_pct == 118.0
    assert kpis.grr_pct == 92.0
    assert kpis.arr_usd == 1_200_000_000.0
    assert kpis.rpo_usd == 800_000_000.0
    assert not kpis.nrr_from_agent
    assert not kpis.arr_from_agent


def test_extract_saas_kpis_marks_agent_values():
    ev = "ARR is $500M."
    agent = "NRR of 110%."
    kpis = compute_ticker_metrics("OOO", evidence_text=ev, agent_text=agent).saas_kpis
    assert kpis.arr_usd == 500_000_000.0
    assert not kpis.arr_from_agent
    assert kpis.nrr_pct == 110.0
    assert kpis.nrr_from_agent


def test_extract_saas_kpis_arr_fallback_to_revenue_ttm():
    kpis = compute_ticker_metrics("PPP", revenue_ttm=400_000_000.0).saas_kpis
    assert kpis.arr_usd == 400_000_000.0
    assert kpis.arr_is_approximation
    assert "TTM revenue" in " ".join(kpis.notes)


# ── explicit_kpis (EDGAR narrative override) ─────────────────────────────────


def test_explicit_kpis_override_regex_and_mark_not_from_agent():
    # Regex would find NRR 118% in evidence text; explicit NRR must win and be
    # flagged as a primary filing disclosure (from_agent=False) with a note.
    explicit = {
        "nrr_pct": 124.0,
        "grr_pct": 91.0,
        "arr_usd": 1_500_000_000.0,
        "rpo_usd": 900_000_000.0,
        "provenance": {
            "nrr": {"form": "6-K", "filed": "2026-05-12"},
            "arr": {"form": "6-K", "filed": "2026-05-12"},
        },
    }
    text = "NRR is 118%. ARR grew to $1.2B."
    kpis = compute_ticker_metrics("EEE", evidence_text=text, explicit_kpis=explicit).saas_kpis
    assert kpis.nrr_pct == 124.0            # explicit overrides regex (118)
    assert kpis.grr_pct == 91.0
    assert kpis.arr_usd == 1_500_000_000.0   # explicit overrides regex (1.2B)
    assert kpis.rpo_usd == 900_000_000.0
    assert not kpis.nrr_from_agent
    assert not kpis.grr_from_agent
    assert not kpis.arr_from_agent
    assert not kpis.rpo_from_agent
    assert not kpis.arr_is_approximation     # explicit ARR is exact, not approx
    notes = " ".join(kpis.notes)
    assert "NRR 124.0% from EDGAR 6-K filed 2026-05-12" in notes
    assert "ARR $1.50B from EDGAR 6-K filed 2026-05-12" in notes


def test_explicit_kpis_none_fields_leave_regex_intact():
    # Explicit provides only NRR; the other fields come from the regex scan.
    explicit = {"nrr_pct": 130.0, "provenance": {"nrr": {"form": "10-K", "filed": "2025-12-31"}}}
    text = "NRR is 118%. ARR grew to $1.2B."
    kpis = compute_ticker_metrics("FFF", evidence_text=text, explicit_kpis=explicit).saas_kpis
    assert kpis.nrr_pct == 130.0            # explicit
    assert kpis.arr_usd == 1_200_000_000.0   # regex (not overridden)
    assert kpis.arr_from_agent is False      # found in evidence text
    assert kpis.grr_pct is None


def test_explicit_arr_overrides_ttm_revenue_approximation():
    # Without explicit ARR, revenue_ttm would approximate ARR and set the flag.
    # With explicit ARR, the approximation must NOT be used and the flag cleared.
    explicit = {"arr_usd": 1_500_000_000.0, "provenance": {"arr": {"form": "20-F", "filed": "2025-03-15"}}}
    kpis = compute_ticker_metrics("GGG", revenue_ttm=400_000_000.0, explicit_kpis=explicit).saas_kpis
    assert kpis.arr_usd == 1_500_000_000.0
    assert not kpis.arr_is_approximation
    assert not any("TTM revenue" in n for n in kpis.notes)


def test_explicit_kpis_empty_or_none_does_nothing():
    # Passing None or an all-None dict must behave like the regex-only path.
    text = "NRR is 118%."
    base = compute_ticker_metrics("HHH", evidence_text=text).saas_kpis
    none_arg = compute_ticker_metrics("HHH", evidence_text=text, explicit_kpis=None).saas_kpis
    empty = compute_ticker_metrics(
        "HHH", evidence_text=text, explicit_kpis={"nrr_pct": None}
    ).saas_kpis
    assert none_arg.nrr_pct == base.nrr_pct == empty.nrr_pct == 118.0
    assert none_arg.nrr_from_agent == base.nrr_from_agent == empty.nrr_from_agent


# ── Rule of 40 and passthroughs ───────────────────────────────────────────────


def test_rule_of_40_growth_plus_fcf_margin():
    m = compute_ticker_metrics(
        "QQQ",
        revenue_growth_yoy=25.0,
        fcf_margin_ttm=15.0,
    )
    assert m.saas_kpis.rule_of_40 == 40.0


def test_current_multiples_passthrough():
    m = compute_ticker_metrics(
        "RRR",
        current_multiples={"pe": 20.0, "forward_pe": 18.0, "ps": 5.0, "pb": 3.0, "ev_ebitda": 12.0, "peg": 1.2},
    )
    assert m.current.pe == 20.0
    assert m.current.forward_pe == 18.0
    assert m.current.ps == 5.0
    assert m.current.pb == 3.0
    assert m.current.ev_ebitda == 12.0
    assert m.current.peg == 1.2


def test_analyst_passthrough():
    m = compute_ticker_metrics(
        "SSS",
        analyst={
            "target_mean": 150.0,
            "target_low": 120.0,
            "target_high": 180.0,
            "num_analysts": 12,
            "current_price": 130.0,
            "upside_to_mean_pct": 15.4,
            "strong_buy": 3,
            "buy": 6,
            "hold": 3,
            "total_analysts": 12,
            "consensus": "Buy",
        },
    )
    assert m.analyst.target_mean == 150.0
    assert m.analyst.num_analysts == 12
    assert m.analyst.buy == 6
    assert m.analyst.consensus == "Buy"
