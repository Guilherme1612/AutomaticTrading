"""Ticker Data route — read-only per-ticker fundamentals drill-down.

Spec ref: Source.md §16.8

Renders the *stored* evidence the analysis personas consumed (never re-fetches
fundamentals), and computes derived valuation figures in Python via the
ticker_metrics engine (LLMs never math). Historical period-end prices for the
multi-year multiples are pulled fresh from Polygon — objective market data that is
identical regardless of when queried, so it does not break the accuracy contract.

Lazy fetch (operator directive 2026-06-19): when the operator opens
`/ticker/{ticker}` for a universe ticker with no recent evidence, the route
warms the cache from yfinance + Polygon + EDGAR in the background and the
operator reloads to see the populated page. Subsequent cycles accumulate from
cache (so this fetch satisfies `accumulated_fresh` for those slots).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from pmacs.engines.ticker_metrics import compute_ticker_metrics
from pmacs.web import data as data_layer

router = APIRouter()
_log = logging.getLogger("pmacs.web.ticker_data")

# Per-process set of tickers whose lazy fetch is currently in flight. Prevents
# N concurrent page loads from each spawning their own fetch. Cleared in the
# task itself when the fetch completes (success or failure).
_LAZY_FETCH_IN_FLIGHT: set[str] = set()

# TTL below which cached evidence is considered fresh enough that the page
# view should NOT trigger another lazy fetch. Six hours is intentionally
# longer than the per-source staleness budgets inside `evidence_router` — we
# only want to warm the cache on first view or after long idle, not on every
# page reload (the inner freshness check still flags individual rows stale).
_LAZY_FETCH_TTL_SECONDS = 6 * 3600


def _is_universe_ticker(ticker: str) -> bool:
    """True iff `ticker` is in the operator's active universe.

    Used to gate the lazy-fetch trigger: only universe tickers get warmed.
    Random tickers (test fakes, URL guesses) keep the original "No data" state
    so the existing accessibility test for the empty-state branch stays green.
    """
    try:
        from pmacs.config import data_dir
        import sqlite3
        db = data_dir() / "pmacs.db"
        if not db.exists():
            return False
        con = sqlite3.connect(db)
        try:
            row = con.execute(
                "SELECT 1 FROM universe WHERE ticker = ? AND halted = 0 AND delisted = 0 LIMIT 1",
                (ticker,),
            ).fetchone()
            return row is not None
        finally:
            con.close()
    except Exception as exc:
        _log.info("universe membership check failed for %s: %s", ticker, exc)
        return False


def _evidence_fresh_enough(ticker: str) -> bool:
    """True iff `ticker` has any cached evidence row fresher than the lazy TTL.

    Used as a cheap freshness gate: a stale fetch is fine to retry on every
    page view, but a recent fetch should not be redone until at least the
    TTL elapses.
    """
    try:
        from pmacs.config import data_dir
        import sqlite3
        db = data_dir() / "pmacs.db"
        if not db.exists():
            return False
        con = sqlite3.connect(db)
        try:
            row = con.execute(
                "SELECT MAX(fetched_at) FROM evidence_cache WHERE ticker = ?",
                (ticker,),
            ).fetchone()
        finally:
            con.close()
        ts = row[0] if row else None
        if not ts:
            return False
        try:
            fetched_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
            return age < _LAZY_FETCH_TTL_SECONDS
        except ValueError:
            return False
    except Exception as exc:
        _log.info("freshness check failed for %s: %s", ticker, exc)
        return False


def _maybe_warm_evidence_cache(ticker: str) -> bool:
    """Dispatch a background fetch for `ticker` if conditions are met.

    Returns True if a fetch was dispatched (or was already in flight).
    Returns False if the fetch was skipped (non-universe, fresh evidence,
    or the inner function errored).
    """
    if ticker in _LAZY_FETCH_IN_FLIGHT:
        _log.info("LAZY_EVIDENCE_FETCH_DEDUPED ticker=%s (already in flight)", ticker)
        return True

    if _evidence_fresh_enough(ticker):
        return False

    cycle_id = f"LAZY-{ticker}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

    async def _runner():
        # Mark in-flight so concurrent reloads dedupe, then run the blocking
        # fetch in a worker thread so the event loop stays responsive.
        _LAZY_FETCH_IN_FLIGHT.add(ticker)
        try:
            _log.info(
                "LAZY_EVIDENCE_FETCH_DISPATCHED ticker=%s cycle_id=%s",
                ticker, cycle_id,
            )
            loop = asyncio.get_running_loop()
            from pmacs.data.evidence_router import fetch_minimal_evidence_for_ticker
            rows = await loop.run_in_executor(
                None, fetch_minimal_evidence_for_ticker, ticker, cycle_id,
            )
            _log.info(
                "LAZY_EVIDENCE_FETCH_COMPLETE ticker=%s rows=%d cycle_id=%s",
                ticker, rows, cycle_id,
            )
        except Exception as exc:
            _log.warning(
                "LAZY_EVIDENCE_FETCH_FAILED ticker=%s error=%s", ticker, exc,
            )
        finally:
            _LAZY_FETCH_IN_FLIGHT.discard(ticker)

    try:
        asyncio.create_task(_runner())
        return True
    except RuntimeError as exc:
        # No running loop (e.g. unit tests using TestClient without an event
        # loop) — fall back to a synchronous fetch so behavior is still
        # best-effort populated.
        _log.info(
            "LAZY_EVIDENCE_FETCH_FALLBACK_SYNC ticker=%s reason=%s", ticker, exc,
        )
        try:
            from pmacs.data.evidence_router import fetch_minimal_evidence_for_ticker
            fetch_minimal_evidence_for_ticker(ticker, cycle_id)
        except Exception as inner:
            _log.warning(
                "LAZY_EVIDENCE_FETCH_SYNC_FAILED ticker=%s error=%s", ticker, inner,
            )
        return False


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


# Sanity bound for valuation multiples. yfinance returns `forwardPE` as a raw
# price/EPS ratio — when forward EPS is near zero or negative (NBIS, OUST,
# TEM, CRWV all hit this), the value explodes to triple digits or deep
# negatives. 200× is the cap used throughout this codebase as the "no longer
# meaningful" band (tone_class('multiple') already implicitly assumes this).
_FWD_PE_SANITY_MAX_ABS = 200.0


def _sanitize_multiple(value, *, label: str, max_abs: float = _FWD_PE_SANITY_MAX_ABS):
    """Coerce to float; return None when |value| exceeds the sane bound.

    Used to suppress valuation multiples that are mathematically computable
    but operationally meaningless — e.g. Fwd P/E of -1006 because forward
    EPS rounded to a near-zero negative. Setting the value to None lets the
    template's existing `is none` branches render an em-dash.
    """
    n = _num(value)
    if n is None:
        return None
    if abs(n) > max_abs:
        _log.info(
            "suppressing %s=%s (|value|>%s; forward EPS near zero yields unreliable ratio)",
            label, n, max_abs,
        )
        return None
    return n


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
    """Build a normalized analyst-consensus dict from Yahoo or Finnhub evidence.

    Recommendations are read primarily from the yfinance source (the Finnhub
    `stock/recommendation` endpoint silently fails for almost every ticker —
    see `pmacs/data/sources/finnhub.py:391`). Finnhub is kept as a fallback for
    any ticker where the yfinance packet is absent.
    """
    ypt = ev.get(f"yahoo_{ticker}_price_target", {})
    fpt = ev.get(f"finnhub_{ticker}_price_target", {})
    recs = (
        ev.get(f"yfinance_{ticker}_analyst_recommendations", {})
        or ev.get(f"finnhub_{ticker}_analyst_recommendations", {})
    )

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
    # Fwd P/E is magnitude-sanitized because yfinance can return extreme ratios
    # when forward EPS is near zero or negative; the engine has its own
    # per-year-multiple guards but no current-multiples guard.
    current_multiples = {
        "pe": _num(metrics.get("peNormalizedAnnual")),
        "forward_pe": _sanitize_multiple(metrics.get("forwardPE"), label="forwardPE"),
        "ps": _num(metrics.get("psAnnual")),
        "pb": _num(metrics.get("pbAnnual")),
        "ev_ebitda": _num(metrics.get("evToEbitdaTTM")),
        "peg": _num(metrics.get("pegTTM")),
    }

    evidence_text, agent_text = _build_evidence_text(ticker, ev)

    # Authoritative SaaS KPIs lifted from EDGAR filing narrative (deterministic,
    # no LLM). Override the regex/TTM-revenue fallback in the engine.
    kpi = ev.get(f"edgar_kpi_{ticker}", {})
    explicit_kpis: dict | None = None
    if isinstance(kpi, dict) and any(kpi.get(k) is not None
                                     for k in ("nrr_pct", "grr_pct", "arr_usd", "rpo_usd")):
        explicit_kpis = {
            "nrr_pct": _num(kpi.get("nrr_pct")),
            "grr_pct": _num(kpi.get("grr_pct")),
            "arr_usd": _num(kpi.get("arr_usd")),
            "rpo_usd": _num(kpi.get("rpo_usd")),
            "provenance": kpi.get("provenance", {}),
        }

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
        "fcf_ttm_usd": _num(metrics.get("fcf_ttm_usd")),
        "fcf_margin_ttm": _num(metrics.get("fcfMarginTTM")),
        "roic_ttm": _num(metrics.get("roicTTM")),
        "revenue_growth_yoy": _num(metrics.get("revenueGrowthTTMYoy")),
        "revenue_ttm": _num(metrics.get("revenueTTM")),
        "evidence_text": evidence_text,
        "agent_text": agent_text,
        "explicit_kpis": explicit_kpis,
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


def _display_groups(ticker: str, ev: dict, derived: object, sector: str = "") -> list[dict]:
    """Build the read-only display groups straight from stored evidence.

    ``sector`` (optional) lets us tag fields where a known business N/A applies —
    e.g. a financials / bank's gross margin is a meaningless number for that
    business model. We surface those as a discreet ``n/a`` badge so the operator
    can tell "the data isn't here" apart from "this metric doesn't apply".
    """
    m = ev.get(f"fundamentals_{ticker}_metrics", {})

    # Heuristic: financials / banks / holding companies report fundamentally
    # different P&L structures (Net Interest Income instead of Gross Margin).
    # Treating their gross / operating margin as 0% is more misleading than
    # tagging it as a known business N/A.
    sector_l = (sector or "").lower()
    is_financial = any(
        kw in sector_l for kw in ("financial", "bank", "insurance", "capital")
    )

    def row(label, value, unit="", state="value"):
        return {"label": label, "value": value, "unit": unit, "state": state}

    # Technical info is rendered in its own dedicated section (with RSI colour
    # coding and 52w distance), so we omit it from the raw-fundamentals grid.
    groups = [
        {"title": "Valuation", "rows": [
            row("P/E (normalized)", m.get("peNormalizedAnnual")),
            row("Forward P/E", _sanitize_multiple(m.get("forwardPE"), label="forwardPE")),
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
            row("Gross margin", m.get("grossMarginTTM"), "%",
                state="na" if is_financial else "value"),
            row("Operating margin", m.get("operatingMarginTTM"), "%"),
            row("Net margin", m.get("netProfitMarginTTM"), "%"),
            row("ROE", m.get("roeTTM"), "%"),
            row("ROA", m.get("roaTTM"), "%"),
            row("ROIC", m.get("roicTTM")),
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


def _workspace_context(ticker: str) -> dict:
    """Gather cross-store context for the unified ticker workspace tabs
    (Source.md §16.6 single-ticker drawer): Memo, Personas, Lineage, Failures.

    Best-effort — every source is independently wrapped so a missing or
    not-yet-initialized store never breaks the fundamentals page. Returns a
    dict of `ws_*` keys consumed by ticker_detail.html.
    """
    ctx: dict = {
        "ws_holding": None,
        "ws_memo": None,
        "ws_agent_results": [],
        "ws_crucible": None,
        "ws_persona_affinity": [],
        "ws_ticker_decisions": [],
        "ws_stop_events": [],
        "ws_failures": [],
        "ws_days_held": None,
    }

    from pmacs.web.config import get_config as _get_config
    try:
        cfg = _get_config()
    except Exception:
        return ctx

    # SQLite: holding, latest memo, per-ticker decisions, stop-event lineage.
    try:
        from pmacs.web.routes import memo as _memo
        db = data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            ctx["ws_holding"] = _memo._get_holding_for_ticker(db, ticker)
            ctx["ws_memo"] = _memo._get_latest_memo_from_table(db, ticker)
            decisions = data_layer.get_recent_decisions(db, limit=50)
            ctx["ws_ticker_decisions"] = [d for d in decisions if d["ticker"] == ticker][:10]
            # Days held (lineage summary) from the holding entry date.
            ctx["ws_days_held"] = None
            if ctx["ws_holding"] and ctx["ws_holding"].get("entry_date"):
                try:
                    ed = datetime.fromisoformat(
                        ctx["ws_holding"]["entry_date"].replace("Z", "+00:00")
                    )
                    ctx["ws_days_held"] = (datetime.now(timezone.utc) - ed).days
                except Exception:
                    ctx["ws_days_held"] = None
            hid = ctx["ws_holding"].get("id") if ctx["ws_holding"] else None
            if hid:
                try:
                    rows = db.execute(
                        "SELECT stop_type, trigger_price_usd, stop_price_usd, "
                        "detected_at, status FROM stop_events "
                        "WHERE holding_id = ? ORDER BY detected_at DESC LIMIT 20",
                        (hid,),
                    ).fetchall()
                    ctx["ws_stop_events"] = [
                        {"stop_type": r[0], "trigger_price_usd": r[1],
                         "stop_price_usd": r[2], "detected_at": r[3], "status": r[4]}
                        for r in rows
                    ]
                except Exception:
                    ctx["ws_stop_events"] = []
        finally:
            db.close()
    except Exception:
        pass

    # In-memory per-persona results from the most recent cycle (pipeline.py).
    try:
        from pmacs.web.routes import pipeline as _pipeline
        ctx["ws_agent_results"] = list(_pipeline._last_cycle_agent_results.get(ticker, []))
        ctx["ws_crucible"] = _pipeline._last_cycle_crucible_results.get(ticker)
    except Exception:
        pass

    # DuckDB: persona-ticker affinity (rolling Brier per persona).
    try:
        from pmacs.storage.duckdb import DuckDBAdapter
        duck = DuckDBAdapter(db_path=cfg.duckdb_path)
        try:
            rows = duck.execute(
                "SELECT persona, avg_brier, cycle_count "
                "FROM persona_ticker_affinity WHERE ticker = ? "
                "ORDER BY avg_brier ASC",
                [ticker],
            )
            ctx["ws_persona_affinity"] = rows or []
        finally:
            try:
                duck.close()
            except Exception:
                pass
    except Exception:
        ctx["ws_persona_affinity"] = []

    # KuzuDB: FailedAssumption nodes (failure-history tab, Agents.md FDE).
    try:
        from pmacs.storage.kuzu import KuzuDBAdapter
        from pmacs.config import data_dir as _data_dir
        kuzu = KuzuDBAdapter(db_path=_data_dir() / "pmacs.kuzu")
        ctx["ws_failures"] = kuzu.get_failures_for_ticker(ticker, limit=10)
    except Exception:
        ctx["ws_failures"] = []

    return ctx


@router.get("/ticker/{ticker}")
async def ticker_data_page(request: Request, ticker: str):
    """Render the per-ticker data drill-down (Source.md §16.8 / §16.6 workspace)."""
    from pmacs.web.templating import templates
    ticker = ticker.upper().strip()

    try:
        ev = _evidence_by_id(ticker)
        if not ev:
            # Lazy fetch (operator directive 2026-06-19): for universe tickers
            # with no recent evidence, warm the cache in the background so the
            # operator can reload to see the populated page. Non-universe
            # tickers (test fakes, URL typos) keep the original empty state.
            warming = _maybe_warm_evidence_cache(ticker) if _is_universe_ticker(ticker) else False
            return templates.TemplateResponse(
                request=request,
                name="ticker_detail.html",
                context={
                    "page": "ticker",
                    "ticker": ticker,
                    "no_data": True,
                    "warming": warming,
                    "company_name": "",
                    "sector": "",
                },
            )

        primitives = _extract(ticker, ev)
        derived = compute_ticker_metrics(ticker, **primitives)
        profile = ev.get(f"fundamentals_{ticker}_profile", {})

        # Build a dedicated Technical view from the technical evidence packets.
        # We pass it through as a separate context variable rather than mutating
        # the derived Pydantic model (which is frozen by spec).
        tech_ma = ev.get(f"technical_{ticker}_moving_averages", {})
        tech_mom = ev.get(f"technical_{ticker}_momentum", {})
        m52 = ev.get(f"fundamentals_{ticker}_metrics", {})
        # `derived` is a frozen TickerDerivedMetrics Pydantic model — read via
        # attribute access (Jinja + Python both support this).
        analyst_obj = getattr(derived, "analyst", None)
        analyst_dict = analyst_obj.model_dump() if analyst_obj is not None else {}
        tech_current = (
            _num(tech_ma.get("current_price"))
            or _num(m52.get("currentPrice"))
            or _num(analyst_dict.get("current_price"))
        )
        tech_high_52w = _num(tech_ma.get("high_52w")) or _num(m52.get("52WeekHigh"))
        tech_low_52w = _num(tech_ma.get("low_52w")) or _num(m52.get("52WeekLow"))
        technical = {
            "current_price": tech_current,
            "sma_50": _num(tech_ma.get("sma_50")),
            "sma_200": _num(tech_ma.get("sma_200")),
            "trend": tech_ma.get("trend"),
            "dist_from_sma50_pct": _num(tech_ma.get("dist_from_sma50_pct")),
            "dist_from_sma200_pct": _num(tech_ma.get("dist_from_sma200_pct")),
            "high_52w": tech_high_52w,
            "low_52w": tech_low_52w,
            "dist_from_high_52w_pct": _num(tech_ma.get("dist_from_high_52w_pct")),
            "dist_from_low_52w_pct": _num(tech_ma.get("dist_from_low_52w_pct")),
            "rsi_14": _num(tech_mom.get("rsi_14")),
            "roc_20d_pct": _num(tech_mom.get("roc_20d_pct")),
            "roc_50d_pct": _num(tech_mom.get("roc_50d_pct")),
        }

        return templates.TemplateResponse(
            request=request,
            name="ticker_detail.html",
            context={
                "page": "ticker",
                "ticker": ticker,
                "company_name": profile.get("name"),
                "sector": profile.get("finnhubIndustry"),
                "groups": _display_groups(ticker, ev, derived, sector=profile.get("finnhubIndustry") or ""),
                "derived": derived,
                "technical": technical,
                # Authoritative current price: the same 3-source fallback chain
                # the Technical section uses (tech MA → yfinance metrics → analyst
                # price-target packet). Surfaced as a top-level var so the Analyst
                # consensus "Current price" card doesn't go blank just because the
                # price-target evidence packet lacks a current_price — the live price
                # lives in the technical packet, not the analyst one.
                "current_price": tech_current,
                "no_data": False,
                **_workspace_context(ticker),
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
