"""Primary fundamentals source — Yahoo Finance via yfinance (no API key).

Replaces Finnhub as the primary fundamentals provider (operator decision,
2026-06-17). Finnhub's free tier never returned the annual cash-flow series
(FCF margin and the FCF/SBC series were empty for every ticker), and its
percentage-vs-fraction quirks have caused data-corruption bugs. yfinance gives
the full annual cash-flow statement (Free Cash Flow, Stock-Based Compensation,
Operating CF, CapEx — 4 fiscal years) plus valuation, margins and growth.

This is a **drop-in** for `pmacs.data.sources.fundamentals.fetch_fundamentals`:
it emits the same evidence ids (`fundamentals_{ticker}_profile`,
`fundamentals_{ticker}_metrics`) with the same key names and units that existing
consumers expect (agents/base.py, sanity/memo_scorer.py, the pipeline text
formatter, the Ticker Data page), so nothing downstream needs to change — the
numbers are just accurate now. It additionally emits the annual
`annual_freeCashFlow` / `annual_sbc` series the multi-year multiples need.

Unit conventions (match the prior Finnhub evidence so consumers are unaffected):
  - marketCapitalization, shareOutstanding: in MILLIONS
  - margins / growth / ROE: in PERCENT (e.g. 47.9, not 0.479)
  - annual_* series values: in actual USD; eps in $/share
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from pmacs.data.gateway import DataGateway
from pmacs.logsys import log_debug
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket, EvidenceType


def _clean(value) -> float | None:
    """Coerce to float; map None / NaN / non-numeric to None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _pct(value, *, scale: float = 100.0) -> float | None:
    """Fraction → percent (0.479 → 47.9). Already-percent inputs use scale=1."""
    v = _clean(value)
    return round(v * scale, 2) if v is not None else None


def _series(row) -> list[dict]:
    """Convert a yfinance statement row (period→value) to [{period, v}] desc."""
    out: list[dict] = []
    if row is None:
        return out
    try:
        for col, val in row.items():
            v = _clean(val)
            if v is None:
                continue
            try:
                period = col.date().isoformat()
            except AttributeError:
                period = str(col)[:10]
            out.append({"period": period, "v": v})
    except Exception:
        return []
    out.sort(key=lambda e: e["period"], reverse=True)
    return out


def _row(frame, *names):
    """Return the first matching row from a yfinance DataFrame, or None."""
    if frame is None or getattr(frame, "empty", True):
        return None
    for name in names:
        if name in frame.index:
            return frame.loc[name]
    return None


def fetch_fundamentals_yf(
    ticker: str,
    gateway: DataGateway | None = None,
    api_key: str = "",
    cycle_id: str = "",
) -> EvidencePacket:
    """Fetch fundamentals from Yahoo Finance (yfinance). No API key required.

    Signature mirrors `fetch_fundamentals` so it is a drop-in in evidence_router.
    Returns a packet with `fundamentals_{ticker}_profile` and
    `fundamentals_{ticker}_metrics`. On total failure returns an empty packet so
    the router can fall back to Finnhub.
    """
    now = datetime.now(timezone.utc)
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        info = stock.info or {}
        cashflow = stock.cashflow
        income = stock.income_stmt
        balance = stock.balance_sheet
    except Exception as exc:
        log_debug(
            "DATA_UNAVAILABLE",
            payload={"source": "yfinance_fundamentals", "ticker": ticker, "error": str(exc)},
            level="WARN",
            cycle_id=cycle_id,
            error_code="DATA_UNAVAILABLE",
            msg=f"yfinance fundamentals failed for {ticker}: {exc}",
        )
        return EvidencePacket(ticker=ticker, cycle_id=cycle_id, evidence=[], fetched_at=now, source_count=0)

    def _ebitda_row(inc) -> object:
        """Return an EBITDA row; fall back to Operating Income + D&A if needed."""
        if inc is None or getattr(inc, "empty", True):
            return None
        ebitda = _row(inc, "EBITDA", "Normalized EBITDA")
        if ebitda is not None:
            return ebitda
        op = _row(inc, "Operating Income", "Operating Expense")
        da = _row(
            inc,
            "Depreciation Amortization Depletion",
            "Depreciation And Amortization",
            "Reconciled Depreciation",
            "Depreciation Amortization Depletion Income Statement",
        )
        if op is not None and da is not None:
            # Align columns and sum element-wise; drop columns where either is missing.
            try:
                aligned = op.align(da, join="inner")
                return aligned[0].add(aligned[1])
            except Exception:
                return None
        return None

    # ── Annual series (the key win over Finnhub) ────────────────────────────
    fcf_series = _series(_row(cashflow, "Free Cash Flow"))
    sbc_series = _series(_row(cashflow, "Stock Based Compensation"))
    ocf_series = _series(_row(cashflow, "Operating Cash Flow"))
    capex_series = _series(_row(cashflow, "Capital Expenditure"))
    eps_series = _series(_row(income, "Diluted EPS", "Basic EPS"))
    revenue_series = _series(_row(income, "Total Revenue"))
    book_value_series = _series(_row(
        balance,
        "Stockholders Equity",
        "Total Stockholders Equity",
        "Total Stockholder Equity",
        "Stockholder Equity",
        "Common Stock Equity",
    ))
    ebitda_series = _series(_ebitda_row(income))
    debt_series = _series(_row(
        balance,
        "Total Debt",
        "Total Liabilities Net Minority Interest",
        "Long Term Debt",
    ))
    cash_series = _series(_row(
        balance,
        "Cash Cash Equivalents And Short Term Investments",
        "Cash And Cash Equivalents",
        "Cash Financial",
    ))

    most_recent_period = fcf_series[0]["period"] if fcf_series else (
        eps_series[0]["period"] if eps_series else (
            revenue_series[0]["period"] if revenue_series else None
        )
    )
    data_age_days = None
    if most_recent_period:
        try:
            mrp = datetime.fromisoformat(most_recent_period).replace(tzinfo=timezone.utc)
            data_age_days = (now - mrp).days
        except ValueError:
            pass

    # ── Point-in-time metrics (Finnhub-compatible keys + units) ─────────────
    market_cap = _clean(info.get("marketCap"))
    shares = _clean(info.get("sharesOutstanding"))
    revenue_ttm = _clean(info.get("totalRevenue"))
    fcf_ttm = _clean(info.get("freeCashflow"))
    fcf_margin_ttm = None
    if fcf_ttm is not None and revenue_ttm:
        fcf_margin_ttm = round(fcf_ttm / revenue_ttm * 100, 2)

    gross_m = _pct(info.get("grossMargins"))
    op_m = _pct(info.get("operatingMargins"))
    net_m = _pct(info.get("profitMargins"))
    rev_growth = _pct(info.get("revenueGrowth"))

    metrics: dict = {
        # Valuation
        "peNormalizedAnnual": _clean(info.get("trailingPE")),
        "forwardPE": _clean(info.get("forwardPE")),
        "psAnnual": _clean(info.get("priceToSalesTrailing12Months")),
        "pbAnnual": _clean(info.get("priceToBook")),
        "evToEbitdaTTM": _clean(info.get("enterpriseToEbitda")),
        "pegTTM": _clean(info.get("pegRatio")),
        # Earnings / growth
        "epsTTM": _clean(info.get("trailingEps")),
        "revenueTTM": revenue_ttm,
        "revenueGrowthTTMYoy": rev_growth,
        # Margins
        "grossMarginTTM": gross_m,
        "operatingMarginTTM": op_m,
        "netProfitMarginTTM": net_m,
        "fcfMarginTTM": fcf_margin_ttm,
        # Returns
        "roeTTM": _pct(info.get("returnOnEquity")),
        "roaTTM": _pct(info.get("returnOnAssets")),
        "roicTTM": _pct(info.get("returnOnInvestedCapital")),
        # Price
        "52WeekHigh": _clean(info.get("fiftyTwoWeekHigh")),
        "52WeekLow": _clean(info.get("fiftyTwoWeekLow")),
        "beta": _clean(info.get("beta")),
        # Cash flow (TTM) + annual series
        "fcf_ttm_usd": fcf_ttm,
        "annual_freeCashFlow": fcf_series,
        "annual_sbc": sbc_series,
        "annual_operating_cashflow": ocf_series,
        "annual_capex": capex_series,
        "annual_eps": eps_series,
        "annual_revenue": revenue_series,
        "annual_book_value": book_value_series,
        "annual_ebitda": ebitda_series,
        "annual_total_debt": debt_series,
        "annual_cash": cash_series,
        # Provenance
        "_source": "yfinance",
        "_most_recent_period": most_recent_period,
    }
    if data_age_days is not None:
        metrics["_data_age_days"] = data_age_days

    # String "_pct" variants that agents/base.py and memo_scorer.py read.
    if rev_growth is not None:
        metrics["revenueGrowthTTMYoy_pct"] = f"{rev_growth:+.1f}%"
    if gross_m is not None:
        metrics["grossMarginTTM_pct"] = f"{gross_m:.1f}%"
    if op_m is not None:
        metrics["operatingMarginTTM_pct"] = f"{op_m:.1f}%"
    if net_m is not None:
        metrics["netProfitMarginTTM_pct"] = f"{net_m:.1f}%"
    if fcf_margin_ttm is not None:
        metrics["fcfMarginTTM_pct"] = f"{fcf_margin_ttm:.1f}%"

    # Drop empty numeric keys so consumers see absence (N/A) rather than null
    # noise — accuracy over coverage (operator directive: no bad data).
    metrics = {k: v for k, v in metrics.items() if not (v is None or v == [])}

    evidence: list[Evidence] = []
    evidence.append(Evidence(
        id=f"fundamentals_{ticker}_metrics",
        source=DataSource.YAHOO,
        type=EvidenceType.FINANCIAL_STATEMENT,
        ticker=ticker,
        fetched_at=now,
        content_hash=str(hash(str(sorted(metrics.items(), key=lambda x: x[0])))),
        title=f"{ticker} fundamentals (yfinance)",
        data=metrics,
    ))

    # ── Company profile (identity) ──────────────────────────────────────────
    profile: dict = {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName"),
        "finnhubIndustry": info.get("sector") or info.get("industry"),
        "exchange": info.get("exchange") or info.get("fullExchangeName"),
        "currency": info.get("currency"),
        "country": info.get("country"),
        "ipo": None,
        "marketCapitalization": round(market_cap / 1_000_000, 2) if market_cap else None,
        "shareOutstanding": round(shares / 1_000_000, 4) if shares else None,
        "_source": "yfinance",
    }
    profile = {k: v for k, v in profile.items() if v is not None}
    evidence.append(Evidence(
        id=f"fundamentals_{ticker}_profile",
        source=DataSource.YAHOO,
        type=EvidenceType.FINANCIAL_STATEMENT,
        ticker=ticker,
        fetched_at=now,
        content_hash=str(hash(str(sorted(profile.items(), key=lambda x: x[0])))),
        title=f"{ticker} profile (yfinance)",
        data=profile,
    ))

    log_debug(
        "EVIDENCE_FETCHED",
        payload={
            "source": "yfinance_fundamentals",
            "ticker": ticker,
            "fcf_years": len(fcf_series),
            "sbc_years": len(sbc_series),
            "eps_years": len(eps_series),
            "revenue_years": len(revenue_series),
            "book_value_years": len(book_value_series),
            "ebitda_years": len(ebitda_series),
        },
        level="INFO",
        cycle_id=cycle_id,
        msg=f"yfinance fundamentals for {ticker}: {len(fcf_series)}y FCF, {len(eps_series)}y EPS, {len(revenue_series)}y revenue",
    )

    return EvidencePacket(
        ticker=ticker, cycle_id=cycle_id, evidence=evidence,
        fetched_at=now, source_count=1,
    )
