"""Fundamentals data source (IMPORTANT).

Fetches two complementary Finnhub endpoints:
  1. /stock/profile2  — company identity (name, exchange, sector, market cap)
  2. /stock/metric?metric=all — live financial KPIs (revenue growth, margins, EPS, etc.)

These are merged into a single EvidencePacket so every persona that uses
DataSource.FUNDAMENTALS gets real numbers, not just company metadata.

Data Validation Layer:
  Finnhub frequently returns corrupted metrics (7000%+ margins, 50000%+ growth)
  for small-cap and foreign-listed tickers. We apply sanity bounds and flag
  anomalous values so agents can trust the numbers or explicitly ignore them.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from pmacs.data.gateway import DataGateway
from pmacs.logsys import log_debug
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket, EvidenceType

# ---------------------------------------------------------------------------
# Sanity bounds for Finnhub metrics — values outside these ranges are
# almost certainly data errors, not real financial metrics.
# ---------------------------------------------------------------------------
_SANITY_BOUNDS: dict[str, tuple[float, float]] = {
    # Margins: Finnhub returns percentages (e.g., 68.31 = 68.31%)
    "grossMarginTTM": (-100.0, 100.0),
    "netProfitMarginTTM": (-100.0, 100.0),
    "operatingMarginTTM": (-100.0, 100.0),
    "ebitdaMarginTTM": (-100.0, 100.0),
    "fcfMarginTTM": (-100.0, 100.0),
    # Revenue growth: Finnhub returns percentages. Hypergrowth up to 1000%.
    "revenueGrowthTTMYoy": (-90.0, 1000.0),
    "revenueGrowth3Y": (-90.0, 500.0),
    "revenueGrowth5Y": (-90.0, 300.0),
    # EPS growth: Finnhub returns percentages.
    "epsGrowthTTMYoy": (-90.0, 1000.0),
    "epsGrowth3Y": (-90.0, 500.0),
    # Returns: Finnhub returns percentages.
    "roeTTM": (-100.0, 200.0),
    "roaTTM": (-100.0, 100.0),
    "roicTTM": (-100.0, 200.0),
    # Price return: Finnhub returns percentages.
    "52WeekPriceReturnDaily": (-100.0, 5000.0),
}


def _validate_metric(key: str, raw_value: float, ticker: str, cycle_id: str) -> tuple[float | None, bool]:
    """Validate a single metric against sanity bounds.

    Returns (value, is_anomalous). If anomalous, the value is clamped to the
    bound and flagged so agents know to distrust it.
    """
    if not isinstance(raw_value, (int, float)) or math.isnan(raw_value) or math.isinf(raw_value):
        return None, True

    bounds = _SANITY_BOUNDS.get(key)
    if bounds is None:
        return raw_value, False

    lo, hi = bounds
    if lo <= raw_value <= hi:
        return raw_value, False

    # Anomalous — clamp and flag
    clamped = max(lo, min(hi, raw_value))
    log_debug(
        "METRIC_ANOMALY_CLAMPED",
        payload={
            "ticker": ticker,
            "metric": key,
            "raw_value": raw_value,
            "clamped_to": clamped,
            "bound": f"[{lo}, {hi}]",
        },
        level="WARN",
        error_code="DATA_UNAVAILABLE",
        cycle_id=cycle_id,
        msg=f"Anomalous {key}={raw_value} for {ticker}, clamped to {clamped}",
    )
    return clamped, True

# Finnhub metric keys we care about — subset of the ~120 returned by metric=all
_METRIC_KEYS = [
    # Revenue
    "revenueGrowthTTMYoy",        # TTM revenue YoY growth (e.g. 22.0 = 22%)
    "revenueGrowth3Y",            # 3-year revenue CAGR
    "revenueGrowth5Y",            # 5-year revenue CAGR
    "revenueTTM",                 # TTM revenue ($)
    "revenuePerShareTTM",         # revenue per share
    # Earnings
    "epsTTM",                     # TTM EPS
    "epsGrowthTTMYoy",            # TTM EPS YoY growth
    "epsGrowth3Y",                # 3-year EPS CAGR
    # Margins (Finnhub returns percentages, e.g. 68.31 = 68.31%)
    "grossMarginTTM",             # gross margin TTM
    "netProfitMarginTTM",         # net margin TTM
    "operatingMarginTTM",         # operating margin TTM
    "ebitdaMarginTTM",            # EBITDA margin TTM
    "fcfMarginTTM",               # free cash flow margin TTM
    # Returns
    "roeTTM",                     # return on equity TTM
    "roaTTM",                     # return on assets TTM
    "roicTTM",                    # return on invested capital TTM
    # Valuation
    "peNormalizedAnnual",         # normalized P/E
    "pbAnnual",                   # price-to-book
    "psAnnual",                   # price-to-sales
    "evToEbitdaTTM",              # EV/EBITDA TTM
    # Leverage / liquidity
    "totalDebtToEquityAnnual",    # debt-to-equity
    "currentRatioAnnual",         # current ratio
    "netDebtAnnual",              # net debt ($)
    # Price / momentum
    "52WeekHigh",
    "52WeekLow",
    "52WeekPriceReturnDaily",     # 52-week price return
    "beta",
]


def fetch_fundamentals(
    ticker: str,
    gateway: DataGateway,
    api_key: str = "",
    cycle_id: str = "",
) -> EvidencePacket:
    """Fetch company profile + financial KPIs from Finnhub.

    Returns two Evidence items:
      - fundamentals_{ticker}_profile  → company identity
      - fundamentals_{ticker}_metrics  → live financial metrics (growth, margins, etc.)
    """
    now = datetime.now(timezone.utc)
    base_params = {"symbol": ticker}
    if api_key:
        base_params["token"] = api_key

    evidence: list[Evidence] = []

    # ── 1. Company profile (name, sector, market cap, IPO date) ─────────────
    try:
        resp = gateway.fetch(
            "fundamentals",
            "https://finnhub.io/api/v1/stock/profile2",
            params=base_params,
        )
        profile = resp.json() if resp and resp.status_code == 200 else {}
    except Exception:
        profile = {}

    if profile:
        evidence.append(Evidence(
            id=f"fundamentals_{ticker}_profile",
            source=DataSource.FUNDAMENTALS,
            type=EvidenceType.FINANCIAL_STATEMENT,
            ticker=ticker,
            fetched_at=now,
            content_hash=str(hash(str(profile))),
            title=f"{profile.get('name', ticker)} — company profile",
            data=profile,
        ))

    # ── 2. Financial metrics (growth, margins, valuation, leverage) ──────────
    try:
        resp = gateway.fetch(
            "fundamentals",
            "https://finnhub.io/api/v1/stock/metric",
            params={**base_params, "metric": "all"},
        )
        raw = resp.json() if resp and resp.status_code == 200 else {}
    except Exception:
        raw = {}

    raw_metrics = raw.get("metric", {}) if raw else {}

    if raw_metrics:
        # Extract only the keys we care about (filter noise, keep precision)
        # Apply validation layer — flag and clamp anomalous values
        metrics: dict[str, object] = {}
        anomaly_flags: list[str] = []

        for k in _METRIC_KEYS:
            v = raw_metrics.get(k)
            if v is not None and isinstance(v, (int, float)):
                validated, is_anomalous = _validate_metric(k, v, ticker, cycle_id)
                if validated is not None:
                    metrics[k] = round(validated, 4)
                    if is_anomalous:
                        anomaly_flags.append(k)
            elif v is not None:
                metrics[k] = v  # non-numeric fields pass through

        # Record anomaly flags so agents can see which metrics are unreliable
        if anomaly_flags:
            metrics["_anomalous_fields"] = anomaly_flags
            metrics["_data_quality_warning"] = (
                f"WARNING: {len(anomaly_flags)} metrics flagged as anomalous "
                f"(likely Finnhub data corruption): {', '.join(anomaly_flags)}. "
                f"Clamped to sanity bounds. Prefer EDGAR XBRL data if available."
            )

        # Compute human-readable percentage strings for clarity
        # NOTE: Finnhub returns these values as percentages (e.g., 68.31 = 68.31%),
        # so do NOT multiply by 100.
        if "revenueGrowthTTMYoy" in metrics:
            metrics["revenueGrowthTTMYoy_pct"] = f"{float(metrics['revenueGrowthTTMYoy']):+.1f}%"
        if "epsGrowthTTMYoy" in metrics:
            metrics["epsGrowthTTMYoy_pct"] = f"{float(metrics['epsGrowthTTMYoy']):+.1f}%"
        if "grossMarginTTM" in metrics:
            metrics["grossMarginTTM_pct"] = f"{float(metrics['grossMarginTTM']):.1f}%"
        if "netProfitMarginTTM" in metrics:
            metrics["netProfitMarginTTM_pct"] = f"{float(metrics['netProfitMarginTTM']):.1f}%"
        if "fcfMarginTTM" in metrics:
            metrics["fcfMarginTTM_pct"] = f"{float(metrics['fcfMarginTTM']):.1f}%"

        # Series data (annual financials from Finnhub) — recent 4 periods
        series = raw.get("series", {})
        annual = series.get("annual", {}) if series else {}
        most_recent_period: str | None = None
        for field in ("revenue", "eps", "grossProfit", "netIncome", "freeCashFlow"):
            entries = annual.get(field, [])
            if entries:
                # Most recent 4 periods, newest first
                recent = sorted(entries, key=lambda x: x.get("period", ""), reverse=True)[:4]
                metrics[f"annual_{field}"] = [
                    {"period": e.get("period"), "v": e.get("v")} for e in recent
                ]
                # Track most recent reporting period for freshness
                if recent and recent[0].get("period"):
                    p = recent[0]["period"]
                    if most_recent_period is None or p > most_recent_period:
                        most_recent_period = p

        # ── Data freshness validation ─────────────────────────────────────────
        # Finnhub annual series are often 1+ year stale. Flag if the most recent
        # period is older than 15 months so agents can distrust stale figures.
        if most_recent_period:
            metrics["_most_recent_period"] = most_recent_period
            try:
                # Period format is usually "YYYY-MM-DD" or "YYYY"
                from datetime import date
                period_str = most_recent_period[:10]  # take date portion
                period_date = date.fromisoformat(period_str)
                age_days = (date.today() - period_date).days
                metrics["_data_age_days"] = age_days
                if age_days > 450:  # >15 months — data is very stale
                    metrics["_freshness_warning"] = (
                        f"STALE DATA: Most recent financial period is {most_recent_period} "
                        f"({age_days} days ago). Revenue and absolute dollar figures may be "
                        f"significantly outdated. Prefer Yahoo Finance or EDGAR for current data."
                    )
                elif age_days > 365:  # >12 months
                    metrics["_freshness_warning"] = (
                        f"WARNING: Most recent financial period is {most_recent_period} "
                        f"({age_days} days ago). Data may be one fiscal year behind. "
                        f"Cross-reference with Yahoo Finance or EDGAR."
                    )
            except (ValueError, IndexError):
                pass

        evidence.append(Evidence(
            id=f"fundamentals_{ticker}_metrics",
            source=DataSource.FUNDAMENTALS,
            type=EvidenceType.FINANCIAL_STATEMENT,
            ticker=ticker,
            fetched_at=now,
            content_hash=str(hash(str(metrics))),
            title=(
                f"{ticker} financial metrics — "
                f"revenue growth {metrics.get('revenueGrowthTTMYoy_pct', 'N/A')} TTM YoY, "
                f"gross margin {metrics.get('grossMarginTTM_pct', 'N/A')}"
            ),
            data=metrics,
        ))

    return EvidencePacket(
        ticker=ticker,
        cycle_id=cycle_id,
        evidence=evidence,
        fetched_at=now,
        source_count=1,
    )
