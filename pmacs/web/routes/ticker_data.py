"""Ticker Data route — read-only per-ticker fundamentals drill-down.

Spec ref: Source.md §16.8

Renders the *stored* evidence the analysis personas consumed (never re-fetches
fundamentals), and computes derived valuation figures in Python via the
ticker_metrics engine (LLMs never math). Historical period-end prices for the
multi-year multiples are pulled fresh from Polygon — objective market data that is
identical regardless of when queried, so it does not break the accuracy contract.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from pmacs.engines.ticker_metrics import compute_ticker_metrics
from pmacs.web import data as data_layer

router = APIRouter()
_log = logging.getLogger("pmacs.web.ticker_data")


def _num(value):
    """Coerce to float, tolerating strings like '29.6%'; None on failure."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().rstrip("%").replace("+", ""))
    except (TypeError, ValueError):
        return None


def _evidence_by_id(ticker: str) -> dict:
    """Load the stored EvidencePacket for a ticker as {evidence_id: data_dict}."""
    try:
        from pmacs.data.evidence_router import _load_evidence_cache
        return {ev.id: (ev.data or {}) for ev in _load_evidence_cache(ticker)}
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("evidence load failed for %s: %s", ticker, exc)
        return {}


def _series(metrics: dict, key: str) -> list[dict] | None:
    """Return a stored annual series if present and non-empty."""
    s = metrics.get(key)
    return s if isinstance(s, list) and s else None


def _build_evidence_text(ticker: str, ev: dict) -> tuple[str, str]:
    """Concatenate stored evidence text and agent text for deterministic KPI extraction.

    Returns (evidence_text, agent_text). Both are lower-fidelity string dumps;
    only regex-friendly KPI patterns are matched, so formatting is unimportant.
    """
    evidence_parts: list[str] = []
    agent_parts: list[str] = []

    for eid, data in ev.items():
        if not isinstance(data, dict):
            continue
        # Render the data dict as simple key-value text for regex scanning.
        text = "\n".join(f"{k}: {v}" for k, v in data.items() if v is not None)
        if "agent" in eid.lower() or "memo" in eid.lower():
            agent_parts.append(text)
        else:
            evidence_parts.append(text)

    return "\n".join(evidence_parts), "\n".join(agent_parts)


def _extract_analyst(ticker: str, ev: dict) -> dict:
    """Build a normalized analyst-consensus dict from Yahoo or Finnhub evidence."""
    ypt = ev.get(f"yahoo_{ticker}_price_target", {})
    fpt = ev.get(f"finnhub_{ticker}_price_target", {})
    recs = ev.get(f"finnhub_{ticker}_analyst_recommendations", {})

    # Yahoo price target is preferred; Finnhub fallback.
    pt = ypt if ypt else fpt
    return {
        "target_mean": _num(pt.get("target_mean")),
        "target_median": _num(pt.get("target_median")),
        "target_high": _num(pt.get("target_high")),
        "target_low": _num(pt.get("target_low")),
        "num_analysts": (
            int(n) if (n := _num(pt.get("num_analysts") or pt.get("analyst_count"))) is not None else None
        ),
        "current_price": _num(pt.get("current_price")),
        "upside_to_mean_pct": _num(pt.get("upside_to_mean_pct")),
        "strong_buy": _num(recs.get("strong_buy")),
        "buy": _num(recs.get("buy")),
        "hold": _num(recs.get("hold")),
        "sell": _num(recs.get("sell")),
        "strong_sell": _num(recs.get("strong_sell")),
        "total_analysts": _num(recs.get("total_analysts")),
        "consensus": recs.get("consensus"),
    }


def _extract(ticker: str, ev: dict) -> dict:
    """Map stored evidence dicts to the primitives the metrics engine needs."""
    metrics = ev.get(f"fundamentals_{ticker}_metrics", {})
    profile = ev.get(f"fundamentals_{ticker}_profile", {})
    edgar = ev.get(f"edgar_{ticker}_financials", {})

    # Market cap: profile stores it in millions USD for both yfinance and Finnhub.
    market_cap_usd = None
    mcap_raw = profile.get("marketCapitalization")
    if isinstance(mcap_raw, (int, float)) and mcap_raw > 0:
        market_cap_usd = float(mcap_raw) * 1_000_000

    # Current diluted share count: prefer EDGAR's actual count, else profile millions.
    shares_outstanding = None
    edgar_shares = edgar.get("shares_outstanding")
    if isinstance(edgar_shares, dict):
        shares_outstanding = _num(edgar_shares.get("value"))
    if shares_outstanding is None:
        sh_m = _num(profile.get("shareOutstanding"))
        if sh_m is not None:
            shares_outstanding = sh_m * 1_000_000

    # Annual series (yfinance) for multi-year averages.
    eps_series = _series(metrics, "annual_eps")
    fcf_series = _series(metrics, "annual_freeCashFlow")
    revenue_series = _series(metrics, "annual_revenue")
    book_value_series = _series(metrics, "annual_book_value")
    ebitda_series = _series(metrics, "annual_ebitda")
    debt_series = _series(metrics, "annual_total_debt")
    cash_series = _series(metrics, "annual_cash")
    sbc_series = _series(metrics, "annual_sbc")

    latest_fcf_usd = _num(fcf_series[0]["v"]) if fcf_series else None
    fcf_period = fcf_series[0].get("period") if fcf_series else None
    sbc_usd = _num(sbc_series[0]["v"]) if sbc_series else None

    if not fcf_series:
        fcf_obj = edgar.get("free_cash_flow_most_recent")
        if isinstance(fcf_obj, dict):
            latest_fcf_usd = _num(fcf_obj.get("value_usd"))
            fcf_period = fcf_obj.get("period_end")
            if latest_fcf_usd is not None and fcf_period:
                fcf_series = [{"period": fcf_period, "v": latest_fcf_usd}]
    if sbc_usd is None:
        sbc_obj = edgar.get("sbc_most_recent")
        if isinstance(sbc_obj, dict):
            sbc_usd = _num(sbc_obj.get("value_usd"))

    # Period-end prices needed for the multiples: union of all annual periods.
    periods = sorted(
        {str(e.get("period")) for e in (eps_series or []) if e.get("period")}
        | {str(e.get("period")) for e in (fcf_series or []) if e.get("period")}
        | {str(e.get("period")) for e in (revenue_series or []) if e.get("period")}
        | {str(e.get("period")) for e in (book_value_series or []) if e.get("period")}
        | {str(e.get("period")) for e in (ebitda_series or []) if e.get("period")}
    )
    price_by_period = _fetch_period_prices(ticker, periods)

    # Current point-in-time multiples (passthrough to engine for echo/context).
    current_multiples = {
        "pe": _num(metrics.get("peNormalizedAnnual")),
        "forward_pe": _num(metrics.get("forwardPE")),
        "ps": _num(metrics.get("psAnnual")),
        "pb": _num(metrics.get("pbAnnual")),
        "ev_ebitda": _num(metrics.get("evToEbitdaTTM")),
        "peg": _num(metrics.get("pegTTM")),
    }

    evidence_text, agent_text = _build_evidence_text(ticker, ev)

    return {
        "eps_series": eps_series,
        "fcf_series": fcf_series,
        "revenue_series": revenue_series,
        "book_value_series": book_value_series,
        "ebitda_series": ebitda_series,
        "debt_series": debt_series,
        "cash_series": cash_series,
        "price_by_period": price_by_period,
        "shares_outstanding": shares_outstanding,
        "market_cap_usd": market_cap_usd,
        "sbc_usd": sbc_usd,
        "current_multiples": current_multiples,
        "fcf_margin_ttm": _num(metrics.get("fcfMarginTTM")),
        "roic_ttm": _num(metrics.get("roicTTM")),
        "revenue_growth_yoy": _num(metrics.get("revenueGrowthTTMYoy")),
        "revenue_ttm": _num(metrics.get("revenueTTM")),
        "evidence_text": evidence_text,
        "agent_text": agent_text,
        "analyst": _extract_analyst(ticker, ev),
        "most_recent_period": metrics.get("_most_recent_period"),
        "has_stale_data": (_num(metrics.get("_data_age_days")) or 0) > 460,
    }


def _fetch_period_prices(ticker: str, periods: list[str]) -> dict:
    """Fetch period-end closes from Polygon (widened window). Empty dict on failure."""
    if not periods:
        return {}
    try:
        from pmacs.data.sources.technical import fetch_period_end_prices
        from pmacs.data.gateway import DataGateway
        from pmacs.storage.keychain import get_api_key

        key = get_api_key("pmacs.data.polygon", "api_key") or get_api_key(
            "pmacs.credentials", "polygon_key"
        )
        if not key:
            return {}
        with DataGateway() as gw:
            return fetch_period_end_prices(ticker, gw, key, periods)
    except Exception as exc:  # pragma: no cover - network/credential dependent
        _log.info("period-end price fetch failed for %s: %s", ticker, exc)
        return {}


def _display_groups(ticker: str, ev: dict, derived: object) -> list[dict]:
    """Build the read-only display groups straight from stored evidence."""
    m = ev.get(f"fundamentals_{ticker}_metrics", {})
    tech = ev.get(f"technical_{ticker}_moving_averages", {})
    mom = ev.get(f"technical_{ticker}_momentum", {})

    def row(label, value, unit=""):
        return {"label": label, "value": value, "unit": unit}

    groups = [
        {"title": "Valuation", "rows": [
            row("P/E (normalized)", m.get("peNormalizedAnnual")),
            row("Forward P/E", m.get("forwardPE")),
            row("P/S", m.get("psAnnual")),
            row("P/B", m.get("pbAnnual")),
            row("EV/EBITDA", m.get("evToEbitdaTTM")),
            row("PEG", m.get("pegTTM")),
        ]},
        {"title": "Growth", "rows": [
            row("Revenue YoY", m.get("revenueGrowthTTMYoy"), "%"),
            row("Revenue 3Y CAGR", m.get("revenueGrowth3Y"), "%"),
            row("Revenue 5Y CAGR", m.get("revenueGrowth5Y"), "%"),
            row("EPS TTM", m.get("epsTTM")),
            row("FCF margin TTM", m.get("fcfMarginTTM"), "%"),
        ]},
        {"title": "Margins & returns", "rows": [
            row("Gross margin", m.get("grossMarginTTM"), "%"),
            row("Operating margin", m.get("operatingMarginTTM"), "%"),
            row("Net margin", m.get("netProfitMarginTTM"), "%"),
            row("ROE", m.get("roeTTM"), "%"),
            row("ROA", m.get("roaTTM"), "%"),
            row("ROIC", m.get("roicTTM")),
        ]},
        {"title": "Technical", "rows": [
            row("Current price", tech.get("current_price"), "$"),
            row("SMA 50", tech.get("sma_50"), "$"),
            row("SMA 200", tech.get("sma_200"), "$"),
            row("Trend", (tech.get("trend") or "").replace("_", " ").title()),
            row("RSI(14)", mom.get("rsi_14")),
            row("52-week high", m.get("52WeekHigh"), "$"),
            row("52-week low", m.get("52WeekLow"), "$"),
        ]},
    ]

    # Add a Cash flow group with annual series lengths when available.
    cf_rows = []
    if m.get("annual_freeCashFlow"):
        cf_rows.append(row("Annual FCF series", f"{len(m['annual_freeCashFlow'])} yrs"))
    if m.get("annual_sbc"):
        cf_rows.append(row("Annual SBC series", f"{len(m['annual_sbc'])} yrs"))
    if m.get("annual_operating_cashflow"):
        cf_rows.append(row("Annual OCF series", f"{len(m['annual_operating_cashflow'])} yrs"))
    if m.get("annual_capex"):
        cf_rows.append(row("Annual CapEx series", f"{len(m['annual_capex'])} yrs"))
    if cf_rows:
        groups.insert(2, {"title": "Cash flow", "rows": cf_rows})

    return groups


@router.get("/ticker/{ticker}")
async def ticker_data_page(request: Request, ticker: str):
    """Render the per-ticker data drill-down (Source.md §16.8)."""
    from pmacs.web.templating import templates
    ticker = ticker.upper().strip()

    try:
        ev = _evidence_by_id(ticker)
        if not ev:
            return templates.TemplateResponse(
                request=request,
                name="ticker_detail.html",
                context={
                    "page": "ticker",
                    "ticker": ticker,
                    "no_data": True,
                },
            )

        primitives = _extract(ticker, ev)
        derived = compute_ticker_metrics(ticker, **primitives)
        profile = ev.get(f"fundamentals_{ticker}_profile", {})

        return templates.TemplateResponse(
            request=request,
            name="ticker_detail.html",
            context={
                "page": "ticker",
                "ticker": ticker,
                "company_name": profile.get("name"),
                "sector": profile.get("finnhubIndustry"),
                "groups": _display_groups(ticker, ev, derived),
                "derived": derived,
                "no_data": False,
            },
        )
    except Exception as exc:
        _log.error("Ticker data page failed for %s: %s", ticker, exc, exc_info=True)
        return templates.TemplateResponse(
            request=request,
            name="ticker_detail.html",
            context={
                "page": "ticker",
                "ticker": ticker,
                "error": data_layer.build_error_context("ticker", exc),
            },
        )
