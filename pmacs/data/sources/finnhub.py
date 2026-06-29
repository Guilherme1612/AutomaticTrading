"""Finnhub market data source (CRITICAL)."""
from __future__ import annotations
from datetime import datetime, timezone
from pmacs.data.gateway import DataGateway
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket, EvidenceType


def fetch_quote(ticker: str, gateway: DataGateway, api_key: str, cycle_id: str = "") -> EvidencePacket:
    """Fetch real-time quote from Finnhub."""
    url = "https://finnhub.io/api/v1/quote"
    response = gateway.fetch("finnhub", url, params={"symbol": ticker}, api_key=api_key)
    data = response.json()
    evidence = [Evidence(
        id=f"finnhub_{ticker}_quote",
        source=DataSource.FINNHUB,
        type=EvidenceType.MARKET_DATA,
        ticker=ticker,
        fetched_at=datetime.now(timezone.utc),
        content_hash=str(hash(str(data))),
        data=data,
    )]
    return EvidencePacket(ticker=ticker, cycle_id=cycle_id, evidence=evidence, fetched_at=datetime.now(timezone.utc), source_count=1)


def fetch_earnings_data(ticker: str, gateway: DataGateway, api_key: str, cycle_id: str = "") -> EvidencePacket:
    """Fetch earnings history (actual vs estimate) and upcoming earnings date.

    Provides two evidence items:
      - finnhub_{ticker}_earnings_history  — last 4 quarters: actual EPS, estimate, surprise %
      - finnhub_{ticker}_earnings_calendar — next expected earnings date (if available)

    This is critical for:
      - Catalyst: upcoming earnings date and beat/miss setup
      - Growth: whether company consistently beats/misses revenue/EPS estimates
      - Forward-looking: next quarter consensus vs trailing actuals
    """
    now = datetime.now(timezone.utc)
    base_params: dict = {"symbol": ticker}
    if api_key:
        base_params["token"] = api_key

    evidence: list[Evidence] = []

    # ── 1. Earnings surprise history (last 4 quarters) ────────────────────────
    try:
        resp = gateway.fetch(
            "finnhub", "https://finnhub.io/api/v1/stock/earnings",
            params=base_params, api_key=api_key,
        )
        raw = resp.json() if resp and resp.status_code == 200 else []
        if isinstance(raw, list) and raw:
            # Summarise into structured fields — keep last 4 quarters
            history = []
            for q in raw[:4]:
                actual = q.get("actual")
                estimate = q.get("estimate")
                surprise_pct = None
                if actual is not None and estimate is not None and estimate != 0:
                    surprise_pct = round((actual - estimate) / abs(estimate) * 100, 1)
                history.append({
                    "period": q.get("period", ""),
                    "actual_eps": actual,
                    "estimate_eps": estimate,
                    "surprise_pct": surprise_pct,
                    "revenue_actual": q.get("revenueActual"),
                    "revenue_estimate": q.get("revenueEstimate"),
                })
            # Compute EPS beat rate
            beats = sum(1 for h in history if h.get("surprise_pct") is not None and h["surprise_pct"] > 0)
            beat_rate = f"{beats}/{len(history)} quarters beat EPS estimate" if history else "N/A"
            # Compute revenue beat rate (where both actuals and estimates are available)
            rev_beats = sum(
                1 for h in history
                if h.get("revenue_actual") and h.get("revenue_estimate")
                and h["revenue_actual"] > h["revenue_estimate"]
            )
            rev_beat_quarters = sum(
                1 for h in history
                if h.get("revenue_actual") and h.get("revenue_estimate")
            )
            rev_beat_rate = (
                f"{rev_beats}/{rev_beat_quarters} quarters beat revenue estimate"
                if rev_beat_quarters > 0 else "N/A"
            )
            earnings_data = {"history": history, "beat_rate": beat_rate, "revenue_beat_rate": rev_beat_rate}
            evidence.append(Evidence(
                id=f"finnhub_{ticker}_earnings_history",
                source=DataSource.FINNHUB,
                type=EvidenceType.FINANCIAL_STATEMENT,
                ticker=ticker,
                fetched_at=now,
                content_hash=str(hash(str(earnings_data))),
                title=f"{ticker} earnings history — {beat_rate}",
                data=earnings_data,
            ))
    except Exception:
        pass

    # ── 2. Upcoming earnings date ─────────────────────────────────────────────
    try:
        from datetime import date, timedelta
        today = date.today()
        three_months = today + timedelta(days=90)
        resp = gateway.fetch(
            "finnhub", "https://finnhub.io/api/v1/calendar/earnings",
            params={**base_params, "from": str(today), "to": str(three_months)},
            api_key=api_key,
        )
        raw = resp.json() if resp and resp.status_code == 200 else {}
        items = raw.get("earningsCalendar", []) if isinstance(raw, dict) else []
        ticker_events = [e for e in items if e.get("symbol") == ticker]
        if ticker_events:
            next_event = ticker_events[0]
            cal_data = {
                "next_earnings_date": next_event.get("date", ""),
                "eps_estimate": next_event.get("epsEstimate"),
                "revenue_estimate": next_event.get("revenueEstimate"),
                "quarter": next_event.get("quarter", ""),
                "year": next_event.get("year", ""),
            }
            evidence.append(Evidence(
                id=f"finnhub_{ticker}_earnings_calendar",
                source=DataSource.FINNHUB,
                type=EvidenceType.FINANCIAL_STATEMENT,
                ticker=ticker,
                fetched_at=now,
                content_hash=str(hash(str(cal_data))),
                title=f"{ticker} next earnings: {cal_data['next_earnings_date']}",
                data=cal_data,
            ))
    except Exception:
        pass

    return EvidencePacket(
        ticker=ticker, cycle_id=cycle_id, evidence=evidence,
        fetched_at=now, source_count=1,
    )


def fetch_consensus_estimates(
    ticker: str, gateway: DataGateway, api_key: str, cycle_id: str = "",
) -> EvidencePacket:
    """Fetch forward-looking metrics and estimate revision trend.

    Provides:
      - finnhub_{ticker}_consensus_estimates — forward P/E, PEG, EPS growth,
        revenue growth, and key valuation/growth metrics from /stock/metric
      - finnhub_{ticker}_estimate_revisions — direction of estimate revisions
        (rising/falling/stable) derived from earnings surprise pattern

    This is critical for:
      - GrowthHunter: forward P/E, growth rates
      - CatalystSummarizer: earnings setup (rising estimates = tailwind)
      - Arbitration: forward-looking conviction anchor
    """
    now = datetime.now(timezone.utc)
    base_params: dict = {"symbol": ticker, "metric": "all"}
    if api_key:
        base_params["token"] = api_key

    evidence: list[Evidence] = []

    # ── 1. Forward metrics from /stock/metric ────────────────────────────────
    try:
        resp = gateway.fetch(
            "finnhub", "https://finnhub.io/api/v1/stock/metric",
            params=base_params, api_key=api_key,
        )
        raw = resp.json() if resp and resp.status_code == 200 else {}
        if isinstance(raw, dict):
            metric = raw.get("metric", {})
            data_: dict = {}

            # Forward valuation metrics
            _FWD_KEYS = {
                "forwardPE": "forward_pe",
                "forwardPEG": "forward_peg",
                "epsTTM": "eps_ttm",
                "epsGrowthTTMYoy": "eps_growth_ttm_yoy",
                "epsGrowthQuarterlyYoy": "eps_growth_q_yoy",
                "revenueGrowthTTMYoy": "revenue_growth_ttm_yoy",
                "revenueGrowthQuarterlyYoy": "revenue_growth_q_yoy",
                "revenueGrowth3Y": "revenue_growth_3y",
                "revenueGrowth5Y": "revenue_growth_5y",
                "revenuePerShareTTM": "revenue_per_share_ttm",
                "peTTM": "pe_ttm",
                "psTTM": "ps_ttm",
                "pb": "pb",
                "evRevenueTTM": "ev_revenue_ttm",
                "dividendGrowthRate5Y": "div_growth_5y",
                "netProfitMarginTTM": "net_margin_ttm",
            }
            for src, dst in _FWD_KEYS.items():
                v = metric.get(src)
                if v is not None:
                    data_[dst] = v

            # Extract annual series for trend analysis
            series = raw.get("series", {})
            annual = series.get("annual", {})
            # Pull recent annual margins/growth for trend
            for series_key in ("grossMargin", "netMargin", "operatingMargin", "fcfMargin"):
                vals = annual.get(series_key, [])
                if vals and len(vals) >= 2:
                    # Store last 3 values (most recent last)
                    recent = vals[-3:] if len(vals) >= 3 else vals
                    data_[f"annual_{series_key}_trend"] = [
                        {"period": v.get("period"), "value": v.get("v")} for v in recent
                    ]

            if data_:
                title_parts = []
                if data_.get("forward_pe"):
                    title_parts.append(f"fwd P/E {data_['forward_pe']:.1f}")
                if data_.get("eps_growth_ttm_yoy"):
                    title_parts.append(f"EPS growth {data_['eps_growth_ttm_yoy']:.1f}%")
                if data_.get("revenue_growth_ttm_yoy"):
                    title_parts.append(f"Rev growth {data_['revenue_growth_ttm_yoy']:.1f}%")
                title = f"{ticker} forward metrics — {', '.join(title_parts)}" if title_parts else f"{ticker} forward metrics"

                evidence.append(Evidence(
                    id=f"finnhub_{ticker}_consensus_estimates",
                    source=DataSource.FINNHUB,
                    type=EvidenceType.FINANCIAL_STATEMENT,
                    ticker=ticker,
                    fetched_at=now,
                    content_hash=str(hash(str(data_))),
                    title=title,
                    data=data_,
                ))
    except Exception:
        pass

    # ── 2. Estimate revision trend (from earnings surprise direction) ─────────
    try:
        base_params_earnings = {"symbol": ticker}
        if api_key:
            base_params_earnings["token"] = api_key
        resp = gateway.fetch(
            "finnhub", "https://finnhub.io/api/v1/stock/earnings",
            params=base_params_earnings, api_key=api_key,
        )
        raw = resp.json() if resp and resp.status_code == 200 else []
        if isinstance(raw, list) and len(raw) >= 2:
            surprises = []
            for q in raw[:4]:
                actual = q.get("actual")
                estimate = q.get("estimate")
                if actual is not None and estimate is not None and estimate != 0:
                    surprises.append((actual - estimate) / abs(estimate))
            if surprises:
                avg_surprise = sum(surprises) / len(surprises)
                positive_surprises = sum(1 for s in surprises if s > 0)
                if positive_surprises >= 3:
                    revision_trend = "RISING"
                elif positive_surprises <= 1:
                    revision_trend = "FALLING"
                else:
                    revision_trend = "STABLE"
                rev_data = {
                    "revision_trend": revision_trend,
                    "avg_eps_surprise_pct": round(avg_surprise * 100, 1),
                    "positive_surprise_quarters": f"{positive_surprises}/{len(surprises)}",
                    "interpretation": (
                        f"Analysts likely {revision_trend.lower()} estimates — "
                        f"company beat EPS in {positive_surprises}/{len(surprises)} recent quarters "
                        f"by avg {avg_surprise*100:.1f}%"
                    ),
                }
                evidence.append(Evidence(
                    id=f"finnhub_{ticker}_estimate_revisions",
                    source=DataSource.FINNHUB,
                    type=EvidenceType.FINANCIAL_STATEMENT,
                    ticker=ticker,
                    fetched_at=now,
                    content_hash=str(hash(str(rev_data))),
                    title=f"{ticker} estimate revision trend: {revision_trend} ({positive_surprises}/{len(surprises)} beats)",
                    data=rev_data,
                ))
    except Exception:
        pass

    return EvidencePacket(
        ticker=ticker, cycle_id=cycle_id, evidence=evidence,
        fetched_at=now, source_count=1,
    )


def fetch_analyst_data(ticker: str, gateway: DataGateway, api_key: str, cycle_id: str = "") -> EvidencePacket:
    """Fetch sell-side analyst recommendations and consensus price target.

    Provides two evidence items:
      - finnhub_{ticker}_analyst_recommendations — buy/hold/sell consensus counts + trend
      - finnhub_{ticker}_price_target — consensus price target from analyst community

    Critical for:
      - Catalyst: analyst upgrades/initiations are near-term catalysts
      - Valuation: consensus price target anchors fair value discussion
      - Conviction: strong buy consensus on low-expectation stock = bullish setup
    """
    now = datetime.now(timezone.utc)
    base_params: dict = {"symbol": ticker}
    if api_key:
        base_params["token"] = api_key

    evidence: list[Evidence] = []

    # ── 1. Analyst recommendations (buy/hold/sell counts, most recent period) ─
    try:
        resp = gateway.fetch(
            "finnhub", "https://finnhub.io/api/v1/stock/recommendation",
            params=base_params, api_key=api_key,
        )
        raw = resp.json() if resp and resp.status_code == 200 else []
        if isinstance(raw, list) and raw:
            # Most recent period first
            latest = raw[0]
            rec_data = {
                "period": latest.get("period", ""),
                "strong_buy": latest.get("strongBuy", 0),
                "buy": latest.get("buy", 0),
                "hold": latest.get("hold", 0),
                "sell": latest.get("sell", 0),
                "strong_sell": latest.get("strongSell", 0),
            }
            total = sum(rec_data[k] for k in ("strong_buy", "buy", "hold", "sell", "strong_sell"))
            bullish = rec_data["strong_buy"] + rec_data["buy"]
            bearish = rec_data["sell"] + rec_data["strong_sell"]
            if total > 0:
                rec_data["bullish_pct"] = round(bullish / total * 100, 1)
                rec_data["bearish_pct"] = round(bearish / total * 100, 1)
                rec_data["total_analysts"] = total
                consensus = (
                    "STRONG_BUY" if rec_data["bullish_pct"] >= 70
                    else "BUY" if rec_data["bullish_pct"] >= 55
                    else "HOLD" if rec_data["bullish_pct"] >= 40
                    else "SELL"
                )
                rec_data["consensus"] = consensus
            # ── Analyst recommendation TREND (multiple periods) ─────────────
            trend_data = None
            if isinstance(raw, list) and len(raw) >= 2:
                periods = raw[:4]  # Most recent 4 periods
                bullish_trend = []
                for p in periods:
                    t = sum(p.get(k, 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell"))
                    b = p.get("strongBuy", 0) + p.get("buy", 0)
                    bullish_trend.append(round(b / t * 100, 1) if t > 0 else None)

                # Trend direction: compare most recent to 2 periods ago
                if len(bullish_trend) >= 2 and bullish_trend[0] is not None and bullish_trend[1] is not None:
                    delta = bullish_trend[0] - bullish_trend[-1] if bullish_trend[-1] is not None else 0
                    if delta > 5:
                        trend = "UPGRADE_CYCLE"
                    elif delta < -5:
                        trend = "DOWNGRADE_CYCLE"
                    else:
                        trend = "STABLE"
                    trend_data = {
                        "trend": trend,
                        "bullish_pct_by_period": bullish_trend,
                        "periods": [p.get("period", "") for p in periods],
                        "delta_pct": round(delta, 1),
                    }
                    rec_data["trend"] = trend
                    rec_data["trend_delta_pct"] = round(delta, 1)

            evidence.append(Evidence(
                id=f"finnhub_{ticker}_analyst_recommendations",
                source=DataSource.FINNHUB,
                type=EvidenceType.FINANCIAL_STATEMENT,
                ticker=ticker,
                fetched_at=now,
                content_hash=str(hash(str(rec_data))),
                title=f"{ticker} analyst consensus: {rec_data.get('consensus', 'N/A')} ({bullish}/{total} bullish)"
                      + (f", {trend_data['trend']}" if trend_data else ""),
                data=rec_data,
            ))
            # Separate trend evidence for agent consumption
            if trend_data:
                evidence.append(Evidence(
                    id=f"finnhub_{ticker}_analyst_trend",
                    source=DataSource.FINNHUB,
                    type=EvidenceType.FINANCIAL_STATEMENT,
                    ticker=ticker,
                    fetched_at=now,
                    content_hash=str(hash(str(trend_data))),
                    title=f"{ticker} analyst trend: {trend_data['trend']} ({trend_data['delta_pct']:+.1f}% bullish shift)",
                    data=trend_data,
                ))
    except Exception:
        pass

    # ── 2. Consensus price target ─────────────────────────────────────────────
    try:
        resp = gateway.fetch(
            "finnhub", "https://finnhub.io/api/v1/stock/price-target",
            params=base_params, api_key=api_key,
        )
        raw = resp.json() if resp and resp.status_code == 200 else {}
        if isinstance(raw, dict) and raw.get("targetMean"):
            pt_data = {
                "target_mean": raw.get("targetMean"),
                "target_high": raw.get("targetHigh"),
                "target_low": raw.get("targetLow"),
                "target_median": raw.get("targetMedian"),
                "analyst_count": raw.get("numberOfAnalystOpinions") or raw.get("analystCount") or "",
            }
            evidence.append(Evidence(
                id=f"finnhub_{ticker}_price_target",
                source=DataSource.FINNHUB,
                type=EvidenceType.FINANCIAL_STATEMENT,
                ticker=ticker,
                fetched_at=now,
                content_hash=str(hash(str(pt_data))),
                title=f"{ticker} analyst price target: ${pt_data['target_mean']:.2f} mean (${pt_data['target_low']:.2f}-${pt_data['target_high']:.2f})",
                data=pt_data,
            ))
    except Exception:
        pass

    return EvidencePacket(
        ticker=ticker, cycle_id=cycle_id, evidence=evidence,
        fetched_at=now, source_count=1,
    )
