"""Technical analysis data source (IMPORTANT).

Fetches extended OHLCV bars from Polygon (200+ days) and computes
technical indicators: SMA(50), SMA(200), RSI(14), price vs MAs,
and trend classification. No external API key beyond Polygon.

Critical for:
  - GrowthHunter: trend confirmation, entry/exit timing context
  - MemoWriter: fair value anchored to technical levels
  - Conviction: price above SMA200 = structural uptrend confirmation
"""
from __future__ import annotations

from datetime import datetime, timezone

from pmacs.data.gateway import DataGateway
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket, EvidenceType


def _compute_sma(closes: list[float], period: int) -> float | None:
    """Compute simple moving average over the last `period` closes."""
    if len(closes) < period:
        return None
    return round(sum(closes[-period:]) / period, 2)


def _compute_rsi(closes: list[float], period: int = 14) -> float | None:
    """Compute RSI from a series of closing prices."""
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))

    if len(gains) < period:
        return None

    # Use Wilder's smoothing
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 1)


def _classify_trend(
    current_price: float,
    sma50: float | None,
    sma200: float | None,
) -> str:
    """Classify the price trend based on MA positioning."""
    if sma50 is None or sma200 is None:
        return "INSUFFICIENT_DATA"
    if current_price > sma50 > sma200:
        return "STRONG_UPTREND"
    if current_price > sma50:
        return "UPTREND"
    if current_price > sma200:
        return "NEUTRAL_ABOVE_200DMA"
    if current_price < sma50 < sma200:
        return "STRONG_DOWNTREND"
    if current_price < sma50:
        return "DOWNTREND"
    return "BELOW_200DMA"


def fetch_period_end_prices(
    ticker: str,
    gateway: DataGateway,
    api_key: str,
    periods: list[str],
    *,
    lookback_years: int = 4,
) -> dict[str, float]:
    """Return the closing price on-or-before each fiscal period-end date.

    Powers the multi-year multiples on the Ticker Data page (Source.md §16.8).
    This is an objective market-data fetch (price history is identical regardless
    of when queried), so it does not violate the "render stored evidence" contract
    that governs the persona-consumed fundamentals.

    Args:
        periods: fiscal period-end dates as "YYYY-MM-DD" strings.
        lookback_years: how far back to pull daily bars (default 4y for 3 fiscal
            years of headroom).

    Returns:
        {period_end: close}. Periods with no bar on-or-before them are omitted.
    """
    from datetime import date, datetime as _dt, timedelta

    if not periods:
        return {}

    today = date.today()
    start = today - timedelta(days=lookback_years * 366)
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start}/{today}?adjusted=true&sort=asc&limit=50000"
    )
    try:
        response = gateway.fetch("polygon", url, api_key=api_key)
        bars = response.json().get("results", [])
    except Exception:
        return {}

    # (epoch_ms_date, close) ascending — Polygon `t` is ms since epoch.
    dated: list[tuple[date, float]] = []
    for b in bars:
        t = b.get("t")
        c = b.get("c")
        if t is None or c is None:
            continue
        dated.append((_dt.fromtimestamp(t / 1000, tz=timezone.utc).date(), float(c)))
    dated.sort(key=lambda x: x[0])
    if not dated:
        return {}

    out: dict[str, float] = {}
    for period in periods:
        try:
            target = _dt.strptime(period[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        # Last bar on-or-before the period end.
        close = None
        for d, c in dated:
            if d <= target:
                close = c
            else:
                break
        if close is not None:
            out[period] = round(close, 2)
    return out


def fetch_technical(
    ticker: str, gateway: DataGateway, api_key: str, cycle_id: str = "",
) -> EvidencePacket:
    """Fetch extended daily bars and compute technical indicators.

    Returns two evidence items:
      - technical_{ticker}_moving_averages — SMA50, SMA200, price vs MAs, trend
      - technical_{ticker}_momentum — RSI, rate-of-change, overbought/oversold
    """
    now = datetime.now(timezone.utc)
    evidence: list[Evidence] = []

    # Fetch 250+ trading days (~1 year) for reliable SMA200
    from datetime import date, timedelta
    today = date.today()
    year_ago = today - timedelta(days=400)  # ~250 trading days
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{year_ago}/{today}"
    try:
        response = gateway.fetch("polygon", url, api_key=api_key)
        data = response.json()
        bars = data.get("results", [])
    except Exception:
        bars = []

    if not bars:
        return EvidencePacket(
            ticker=ticker, cycle_id=cycle_id, evidence=[],
            fetched_at=now, source_count=0,
        )

    closes = [float(b.get("c", 0)) for b in bars if b.get("c")]
    if not closes:
        return EvidencePacket(
            ticker=ticker, cycle_id=cycle_id, evidence=[],
            fetched_at=now, source_count=0,
        )

    current_price = closes[-1]
    sma50 = _compute_sma(closes, 50)
    sma200 = _compute_sma(closes, 200)
    rsi = _compute_rsi(closes, 14)
    trend = _classify_trend(current_price, sma50, sma200)

    # Compute 20-day and 50-day rate of change
    roc_20 = None
    roc_50 = None
    if len(closes) >= 21:
        roc_20 = round((current_price / closes[-21] - 1) * 100, 1)
    if len(closes) >= 51:
        roc_50 = round((current_price / closes[-51] - 1) * 100, 1)

    # 52-week high/low from the bars
    high_52w = max(float(b.get("h", 0)) for b in bars) if bars else None
    low_52w = min(float(b.get("l", float("inf"))) for b in bars) if bars else None

    # Distance from 52-week high/low
    dist_from_high = round((current_price / high_52w - 1) * 100, 1) if high_52w else None
    dist_from_low = round((current_price / low_52w - 1) * 100, 1) if low_52w else None

    # ── Evidence 1: Moving averages + trend ─────────────────────────────────
    ma_data: dict = {
        "current_price": round(current_price, 2),
        "sma_50": sma50,
        "sma_200": sma200,
        "trend": trend,
        "dist_from_sma50_pct": round((current_price / sma50 - 1) * 100, 1) if sma50 else None,
        "dist_from_sma200_pct": round((current_price / sma200 - 1) * 100, 1) if sma200 else None,
        "high_52w": round(high_52w, 2) if high_52w else None,
        "low_52w": round(low_52w, 2) if low_52w else None,
        "dist_from_high_52w_pct": dist_from_high,
        "dist_from_low_52w_pct": dist_from_low,
        "bar_count": len(bars),
    }

    trend_desc = trend.replace("_", " ").title()
    ma_title = f"{ticker} technical — ${current_price:.2f} | SMA50=${sma50:.2f}" if sma50 else f"{ticker} technical — ${current_price:.2f}"
    if sma200:
        ma_title += f" | SMA200=${sma200:.2f}"
    ma_title += f" | {trend_desc}"

    evidence.append(Evidence(
        id=f"technical_{ticker}_moving_averages",
        source=DataSource.TECHNICAL,
        type=EvidenceType.MARKET_DATA,
        ticker=ticker,
        fetched_at=now,
        content_hash=str(hash(str(ma_data))),
        title=ma_title,
        data=ma_data,
    ))

    # ── Evidence 2: Momentum (RSI + ROC) ───────────────────────────────────
    momentum_data: dict = {
        "rsi_14": rsi,
        "roc_20d_pct": roc_20,
        "roc_50d_pct": roc_50,
        "overbought": rsi is not None and rsi > 70,
        "oversold": rsi is not None and rsi < 30,
    }

    rsi_label = ""
    if rsi is not None:
        if rsi > 70:
            rsi_label = "OVERBOUGHT"
        elif rsi < 30:
            rsi_label = "OVERSOLD"
        else:
            rsi_label = "NEUTRAL"

    mom_title = f"{ticker} momentum — RSI(14)={rsi:.1f} ({rsi_label})" if rsi else f"{ticker} momentum — RSI unavailable"
    if roc_20 is not None:
        mom_title += f" | 20d ROC {roc_20:+.1f}%"

    evidence.append(Evidence(
        id=f"technical_{ticker}_momentum",
        source=DataSource.TECHNICAL,
        type=EvidenceType.MARKET_DATA,
        ticker=ticker,
        fetched_at=now,
        content_hash=str(hash(str(momentum_data))),
        title=mom_title,
        data=momentum_data,
    ))

    return EvidencePacket(
        ticker=ticker, cycle_id=cycle_id, evidence=evidence,
        fetched_at=now, source_count=1,
    )
