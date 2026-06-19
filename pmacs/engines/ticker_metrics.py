"""Ticker metrics engine — derives valuation figures for the Ticker Data page.

Spec ref: Source.md §16.8

Pure, deterministic Python. Takes already-extracted primitives (annual EPS/FCF
series, period-end prices, share counts, market cap, SBC) and computes:

  - FCF yield, unadjusted and SBC-adjusted
  - 3-year average P/E, P/FCF, P/S, P/B, EV/EBITDA
  - Current point-in-time multiples (passthrough)
  - SaaS KPIs extracted from stored evidence text
  - Rule of 40 from revenue growth + FCF margin
  - Analyst consensus (passthrough)

The web layer is responsible for pulling these primitives out of the stored
EvidencePacket. Keeping extraction out of this module makes the math trivially
unit-testable and guarantees no network access here.

Five Non-Negotiables: LLMs never math. All arithmetic lives here.
"""
from __future__ import annotations

import re

from pmacs.schemas.ticker_metrics import (
    AnalystConsensus,
    CurrentMultiples,
    SaasKpis,
    TickerDerivedMetrics,
    YearMultiple,
)

# Number of most-recent fiscal years to average over for the "3-year" figures.
_LOOKBACK_YEARS = 3


def _to_float(value: object) -> float | None:
    """Coerce a stored value to float, returning None on anything non-numeric."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _series_by_period(series: list[dict] | None) -> dict[str, float]:
    """Map a Finnhub-style ``[{\"period\": ..., \"v\": ...}]`` list to {period: value}.

    Periods that are missing or whose value is non-numeric are dropped.
    """
    out: dict[str, float] = {}
    for entry in series or []:
        period = entry.get("period")
        val = _to_float(entry.get("v"))
        if period and val is not None:
            out[str(period)] = val
    return out


def compute_fcf_yields(
    latest_fcf_usd: float | None,
    sbc_usd: float | None,
    market_cap_usd: float | None,
) -> tuple[float | None, float | None]:
    """Return (unadjusted_yield_pct, sbc_adjusted_yield_pct).

    Unadjusted = FCF / market cap. SBC-adjusted treats stock-based compensation as
    a real cash-equivalent cost: (FCF - SBC) / market cap. SBC of None means we
    have no SBC figure, so the adjusted column is also None (not silently 0).
    """
    if market_cap_usd is None or market_cap_usd <= 0 or latest_fcf_usd is None:
        return None, None

    unadjusted = round(latest_fcf_usd / market_cap_usd * 100, 2)

    if sbc_usd is None:
        return unadjusted, None
    adjusted = round((latest_fcf_usd - sbc_usd) / market_cap_usd * 100, 2)
    return unadjusted, adjusted


# ── SaaS KPI extraction ─────────────────────────────────────────────────────


_AMT_RE = r'\$?(\d[\d,.]*\s*[BMTK](?:illion|n)?)'
_OF_RE = r'(?:\s+(?:of|is|at|grew\s+to)\s+|[:\s]+)'


def _pct_from_text(raw: str) -> float | None:
    """Strip trailing '%' and convert; return None on failure."""
    val = _to_float(str(raw).strip().rstrip("%"))
    return val


def _fmt_arr(v: float) -> str:
    """Format a USD amount compactly for provenance notes (e.g. 1.20B, 800.00M)."""
    if v >= 1e9:
        return f"{v/1e9:.2f}B"
    if v >= 1e6:
        return f"{v/1e6:.2f}M"
    return f"{v:.0f}"


def _usd_from_text(raw: str) -> float | None:
    """Parse a string like '$1.2B' or '1.2B' into USD."""
    s = str(raw).strip().upper().lstrip("$").replace(",", "")
    if not s:
        return None
    # Split numeric part from suffix
    m = re.match(r'([\d.]+)\s*([BMTK]?)', s)
    if not m:
        return None
    try:
        num = float(m.group(1))
    except ValueError:
        return None
    mult = {"B": 1e9, "M": 1e6, "T": 1e12, "K": 1e3, "": 1}.get(m.group(2), 1)
    return round(num * mult, 2)


def _extract_saas_kpis(
    evidence_text: str = "",
    agent_text: str = "",
    quarterly_revenue_series: list[dict] | None = None,
    revenue_ttm: float | None = None,
    revenue_growth_yoy: float | None = None,
    fcf_margin_ttm: float | None = None,
    explicit_kpis: dict | None = None,
) -> SaasKpis:
    """Extract NRR/ARR/GRR/RPO from stored evidence text and derive Rule of 40.

    Values found in raw evidence text are marked as authoritative; values only
    found in agent narrative are marked as estimates.

    ``explicit_kpis`` carries authoritative values lifted from EDGAR filing narrative
    (see pmacs.data.sources.edgar_kpi). When present, each provided field OVERRIDES the
    regex/approximation result, is flagged ``*_from_agent=False`` (it's a primary filing
    disclosure, not an estimate), and a provenance note is appended. The regex scan still
    runs and only fills fields the explicit source left None — precedence is
    EDGAR narrative > regex-over-prose > TTM-revenue ARR approximation.
    """
    evidence_lower = (evidence_text or "").lower()
    all_text = (evidence_text or "") + "\n" + (agent_text or "")
    text_lower = all_text.lower()
    notes: list[str] = []

    nrr: float | None = None
    nrr_from_agent = False
    m = re.search(
        r'(?:NRR|net\s+revenue\s+retention)' + _OF_RE + r'(\d{2,3}[.\d]*%)',
        all_text,
        re.IGNORECASE,
    )
    if m:
        nrr = _pct_from_text(m.group(1))
        val_lower = m.group(1).lower()
        nrr_from_agent = val_lower not in evidence_lower

    grr: float | None = None
    grr_from_agent = False
    m = re.search(
        r'(?:GRR|gross\s+(?:revenue\s+)?retention)' + _OF_RE + r'(\d{2,3}%)',
        all_text,
        re.IGNORECASE,
    )
    if m:
        grr = _pct_from_text(m.group(1))
        val_lower = m.group(1).lower()
        grr_from_agent = val_lower not in evidence_lower

    arr: float | None = None
    arr_from_agent = False
    arr_is_approximation = False
    m = re.search(
        r'(?:ARR|annual\s+recurring\s+revenue)' + _OF_RE + _AMT_RE,
        all_text,
        re.IGNORECASE,
    )
    if m:
        arr = _usd_from_text(m.group(1).strip())
        val_lower = m.group(1).strip().lower()
        arr_from_agent = val_lower not in evidence_lower

    if arr is None and quarterly_revenue_series:
        # Implied ARR = most recent quarter × 4
        qrev = _series_by_period(quarterly_revenue_series)
        if qrev:
            latest_q = max(qrev)
            arr = round(qrev[latest_q] * 4, 2)
            arr_is_approximation = True
            notes.append("ARR approximated from most recent quarterly revenue × 4.")

    if arr is None and revenue_ttm is not None:
        arr = round(revenue_ttm, 2)
        arr_is_approximation = True
        notes.append("ARR approximated from TTM revenue (no explicit ARR disclosure).")

    rpo: float | None = None
    rpo_from_agent = False
    m = re.search(
        r'(?:RPO|remaining\s+performance\s+obligations?)' + _OF_RE + _AMT_RE,
        all_text,
        re.IGNORECASE,
    )
    if m:
        rpo = _usd_from_text(m.group(1).strip())
        val_lower = m.group(1).strip().lower()
        rpo_from_agent = val_lower not in evidence_lower

    rule_of_40: float | None = None
    if revenue_growth_yoy is not None and fcf_margin_ttm is not None:
        rule_of_40 = round(revenue_growth_yoy + fcf_margin_ttm, 2)

    # ── Explicit (authoritative) KPIs from EDGAR filing narrative override ──
    if explicit_kpis:
        prov = explicit_kpis.get("provenance", {}) or {}

        def _prov_note(key: str, label: str, unit: str) -> str:
            p = prov.get(key)
            if not isinstance(p, dict):
                return f"{label} from EDGAR filing narrative."
            form = p.get("form", "filing")
            filed = p.get("filed", "")
            filed_part = f" filed {filed}" if filed else ""
            return f"{label} from EDGAR {form}{filed_part}."

        ex_nrr = explicit_kpis.get("nrr_pct")
        if ex_nrr is not None:
            nrr = float(ex_nrr)
            nrr_from_agent = False
            notes.append(_prov_note("nrr", f"NRR {nrr:.1f}%", "%"))
        ex_grr = explicit_kpis.get("grr_pct")
        if ex_grr is not None:
            grr = float(ex_grr)
            grr_from_agent = False
            notes.append(_prov_note("grr", f"GRR {grr:.1f}%", "%"))
        ex_arr = explicit_kpis.get("arr_usd")
        if ex_arr is not None:
            arr = float(ex_arr)
            arr_from_agent = False
            arr_is_approximation = False
            # The regex/approximation pass may have appended an ARR-approximation
            # note; the explicit filing disclosure supersedes it, so drop those.
            notes = [n for n in notes if "ARR approximated" not in n]
            notes.append(_prov_note("arr", f"ARR ${_fmt_arr(arr)}", "$"))
        ex_rpo = explicit_kpis.get("rpo_usd")
        if ex_rpo is not None:
            rpo = float(ex_rpo)
            rpo_from_agent = False
            notes.append(_prov_note("rpo", f"RPO ${_fmt_arr(rpo)}", "$"))

    return SaasKpis(
        nrr_pct=nrr,
        grr_pct=grr,
        arr_usd=arr,
        rpo_usd=rpo,
        rule_of_40=rule_of_40,
        arr_is_approximation=arr_is_approximation,
        nrr_from_agent=nrr_from_agent,
        grr_from_agent=grr_from_agent,
        arr_from_agent=arr_from_agent,
        rpo_from_agent=rpo_from_agent,
        notes=notes,
    )


# ── Main metrics computation ────────────────────────────────────────────────


def compute_ticker_metrics(
    ticker: str,
    *,
    eps_series: list[dict] | None = None,
    fcf_series: list[dict] | None = None,
    revenue_series: list[dict] | None = None,
    book_value_series: list[dict] | None = None,
    ebitda_series: list[dict] | None = None,
    debt_series: list[dict] | None = None,
    cash_series: list[dict] | None = None,
    price_by_period: dict[str, float] | None = None,
    shares_by_period: dict[str, float] | None = None,
    shares_outstanding: float | None = None,
    market_cap_usd: float | None = None,
    sbc_usd: float | None = None,
    current_multiples: dict | None = None,
    fcf_margin_ttm: float | None = None,
    roic_ttm: float | None = None,
    revenue_growth_yoy: float | None = None,
    revenue_ttm: float | None = None,
    quarterly_revenue_series: list[dict] | None = None,
    evidence_text: str = "",
    agent_text: str = "",
    explicit_kpis: dict | None = None,
    analyst: dict | None = None,
    most_recent_period: str | None = None,
    has_stale_data: bool = False,
) -> TickerDerivedMetrics:
    """Derive valuation metrics from stored evidence primitives (Source.md §16.8).

    eps_series / fcf_series / revenue_series: Finnhub-style annual lists,
        each ``[{\"period\": \"YYYY-MM-DD\", \"v\": <number>}, ...]``.
    price_by_period: fiscal-period-end close for each period, from the widened
        Polygon fetch. Keys must match the series ``period`` values.
    shares_by_period: diluted share count per period (EDGAR). Optional; when a
        period is missing, ``shares_outstanding`` is used and the year is flagged.
    """
    eps_by_period = _series_by_period(eps_series)
    fcf_by_period = _series_by_period(fcf_series)
    revenue_by_period = _series_by_period(revenue_series)
    book_value_by_period = _series_by_period(book_value_series)
    ebitda_by_period = _series_by_period(ebitda_series)
    debt_by_period = _series_by_period(debt_series)
    cash_by_period = _series_by_period(cash_series)
    price_by_period = price_by_period or {}
    shares_by_period = shares_by_period or {}
    notes: list[str] = []

    # Latest FCF = most recent fiscal year present in the FCF series.
    latest_fcf_usd: float | None = None
    if fcf_by_period:
        latest_fcf_usd = fcf_by_period[max(fcf_by_period)]

    fcf_yield_pct, fcf_yield_sbc_pct = compute_fcf_yields(
        latest_fcf_usd, sbc_usd, market_cap_usd
    )
    if sbc_usd is None and latest_fcf_usd is not None:
        notes.append("SBC-adjusted FCF yield unavailable — no SBC figure in evidence.")

    # The fiscal years we report on: the union of periods that have any series,
    # most recent first, capped at the lookback window.
    candidate_periods = sorted(
        set(eps_by_period)
        | set(fcf_by_period)
        | set(revenue_by_period)
        | set(book_value_by_period)
        | set(ebitda_by_period),
        reverse=True,
    )[:_LOOKBACK_YEARS]

    per_year: list[YearMultiple] = []
    pe_values: list[float] = []
    pfcf_values: list[float] = []
    ps_values: list[float] = []
    pb_values: list[float] = []
    ev_ebitda_values: list[float] = []

    for period in candidate_periods:
        eps = eps_by_period.get(period)
        fcf = fcf_by_period.get(period)
        revenue = revenue_by_period.get(period)
        book_value = book_value_by_period.get(period)
        ebitda = ebitda_by_period.get(period)
        debt = debt_by_period.get(period)
        cash = cash_by_period.get(period)
        price = price_by_period.get(period)
        shares_approximated = False
        shares = shares_by_period.get(period)
        if shares is None:
            shares = shares_outstanding
            shares_approximated = shares is not None

        # ── P/E for the year ─────────────────────────────────────────────────
        pe: float | None = None
        if price is not None and eps is not None:
            if eps > 0:
                pe = round(price / eps, 2)
                pe_values.append(pe)
            else:
                notes.append(f"{period}: P/E skipped (EPS <= 0).")

        # ── P/FCF for the year ─────────────────────────────────────────────────
        pfcf: float | None = None
        if price is not None and fcf is not None and shares is not None and shares > 0:
            if fcf > 0:
                market_cap_period = price * shares
                pfcf = round(market_cap_period / fcf, 2)
                pfcf_values.append(pfcf)
                if shares_approximated:
                    notes.append(
                        f"{period}: P/FCF uses current share count "
                        "(historical shares unavailable)."
                    )
            else:
                notes.append(f"{period}: P/FCF skipped (FCF <= 0).")

        # ── P/S for the year ───────────────────────────────────────────────────
        ps: float | None = None
        if price is not None and revenue is not None and shares is not None and shares > 0:
            if revenue > 0:
                market_cap_period = price * shares
                ps = round(market_cap_period / revenue, 2)
                ps_values.append(ps)
            else:
                notes.append(f"{period}: P/S skipped (revenue <= 0).")

        # ── P/B for the year ───────────────────────────────────────────────────
        pb: float | None = None
        if price is not None and book_value is not None and shares is not None and shares > 0:
            if book_value > 0:
                market_cap_period = price * shares
                pb = round(market_cap_period / book_value, 2)
                pb_values.append(pb)
            else:
                notes.append(f"{period}: P/B skipped (book value <= 0).")

        # ── EV/EBITDA for the year ─────────────────────────────────────────────
        ev: float | None = None
        ev_ebitda: float | None = None
        if (
            price is not None
            and ebitda is not None
            and shares is not None
            and shares > 0
        ):
            market_cap_period = price * shares
            total_debt = debt if debt is not None else 0.0
            total_cash = cash if cash is not None else 0.0
            ev = round(market_cap_period + total_debt - total_cash, 2)
            if ebitda > 0:
                ev_ebitda = round(ev / ebitda, 2)
                ev_ebitda_values.append(ev_ebitda)
            else:
                notes.append(f"{period}: EV/EBITDA skipped (EBITDA <= 0).")

        per_year.append(
            YearMultiple(
                period=period,
                eps=eps,
                fcf_usd=fcf,
                revenue_usd=revenue,
                book_value_usd=book_value,
                ebitda_usd=ebitda,
                total_debt_usd=debt,
                cash_usd=cash,
                price=price,
                shares=shares,
                pe=pe,
                pfcf=pfcf,
                ps=ps,
                pb=pb,
                ev_usd=ev,
                ev_ebitda=ev_ebitda,
                shares_approximated=shares_approximated,
            )
        )

    pe_3y_avg = round(sum(pe_values) / len(pe_values), 2) if pe_values else None
    pfcf_3y_avg = (
        round(sum(pfcf_values) / len(pfcf_values), 2) if pfcf_values else None
    )
    ps_3y_avg = round(sum(ps_values) / len(ps_values), 2) if ps_values else None
    pb_3y_avg = round(sum(pb_values) / len(pb_values), 2) if pb_values else None
    ev_ebitda_3y_avg = (
        round(sum(ev_ebitda_values) / len(ev_ebitda_values), 2)
        if ev_ebitda_values
        else None
    )

    if 0 < len(pe_values) < _LOOKBACK_YEARS:
        notes.append(
            f"3Y average P/E computed over {len(pe_values)} year(s) of available data."
        )
    if 0 < len(pfcf_values) < _LOOKBACK_YEARS:
        notes.append(
            f"3Y FCF multiple computed over {len(pfcf_values)} year(s) of available data."
        )
    if 0 < len(ps_values) < _LOOKBACK_YEARS:
        notes.append(
            f"3Y P/S computed over {len(ps_values)} year(s) of available data."
        )
    if 0 < len(pb_values) < _LOOKBACK_YEARS:
        notes.append(
            f"3Y P/B computed over {len(pb_values)} year(s) of available data."
        )
    if 0 < len(ev_ebitda_values) < _LOOKBACK_YEARS:
        notes.append(
            f"3Y EV/EBITDA computed over {len(ev_ebitda_values)} year(s) of available data."
        )

    saas_kpis = _extract_saas_kpis(
        evidence_text=evidence_text,
        agent_text=agent_text,
        quarterly_revenue_series=quarterly_revenue_series,
        revenue_ttm=revenue_ttm,
        revenue_growth_yoy=revenue_growth_yoy,
        fcf_margin_ttm=fcf_margin_ttm,
        explicit_kpis=explicit_kpis,
    )
    # If no explicit ARR from text, approximate from latest FCF/revenue? No,
    # the engine's _extract_saas_kpis already falls back to quarterly/TTM revenue.

    current = CurrentMultiples(
        pe=_to_float((current_multiples or {}).get("pe")),
        forward_pe=_to_float((current_multiples or {}).get("forward_pe")),
        ps=_to_float((current_multiples or {}).get("ps")),
        pb=_to_float((current_multiples or {}).get("pb")),
        ev_ebitda=_to_float((current_multiples or {}).get("ev_ebitda")),
        peg=_to_float((current_multiples or {}).get("peg")),
    )

    analyst_obj = AnalystConsensus(
        target_mean=_to_float((analyst or {}).get("target_mean")),
        target_median=_to_float((analyst or {}).get("target_median")),
        target_high=_to_float((analyst or {}).get("target_high")),
        target_low=_to_float((analyst or {}).get("target_low")),
        num_analysts=(int(v) if (v := (analyst or {}).get("num_analysts")) is not None else None),
        current_price=_to_float((analyst or {}).get("current_price")),
        upside_to_mean_pct=_to_float((analyst or {}).get("upside_to_mean_pct")),
        strong_buy=(int(v) if (v := (analyst or {}).get("strong_buy")) is not None else None),
        buy=(int(v) if (v := (analyst or {}).get("buy")) is not None else None),
        hold=(int(v) if (v := (analyst or {}).get("hold")) is not None else None),
        sell=(int(v) if (v := (analyst or {}).get("sell")) is not None else None),
        strong_sell=(int(v) if (v := (analyst or {}).get("strong_sell")) is not None else None),
        total_analysts=(int(v) if (v := (analyst or {}).get("total_analysts")) is not None else None),
        consensus=(analyst or {}).get("consensus"),
    )

    return TickerDerivedMetrics(
        ticker=ticker,
        market_cap_usd=market_cap_usd,
        latest_fcf_usd=latest_fcf_usd,
        sbc_usd=sbc_usd,
        fcf_yield_pct=fcf_yield_pct,
        fcf_yield_sbc_adjusted_pct=fcf_yield_sbc_pct,
        per_year=per_year,
        pe_3y_avg=pe_3y_avg,
        pfcf_3y_avg=pfcf_3y_avg,
        ps_3y_avg=ps_3y_avg,
        pb_3y_avg=pb_3y_avg,
        ev_ebitda_3y_avg=ev_ebitda_3y_avg,
        current=current,
        fcf_margin_ttm=fcf_margin_ttm,
        roic_ttm=roic_ttm,
        saas_kpis=saas_kpis,
        analyst=analyst_obj,
        most_recent_period=most_recent_period,
        has_stale_data=has_stale_data,
        notes=notes,
    )
