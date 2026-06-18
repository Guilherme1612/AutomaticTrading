"""FINRA short interest data source (IMPORTANT).

Attempts to fetch short interest via yfinance (shortPercentOfFloat, shortRatio).
Falls back to INSUFFICIENT_DATA if yfinance has no short data, so the short_interest
agent uses [KNOWLEDGE] estimates.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pmacs.data.gateway import DataGateway
from pmacs.logsys import log_debug
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket, EvidenceType


def _average_daily_volume(ticker: str) -> float | None:
    """Return 1-month average daily volume via yfinance history.

    Returns None if yfinance is unavailable or history is empty.
    """
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1mo")
        if hist is None or hist.empty:
            return None
        return float(hist["Volume"].mean())
    except Exception:
        return None


def fetch_short_interest(ticker: str, gateway: DataGateway, cycle_id: str = "") -> EvidencePacket:
    """Fetch short interest data via yfinance.

    Uses yfinance .info dict which provides shortPercentOfFloat and shortRatio
    sourced from exchange-reported data. No FINRA authentication required.
    """
    now = datetime.now(timezone.utc)

    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        info = stock.info or {}

        short_pct = info.get("shortPercentOfFloat")
        short_ratio = info.get("shortRatio")
        shares_short = info.get("sharesShort")
        shares_short_prior = info.get("sharesShortPriorMonth")
        short_pct_outstanding = info.get("shortPercentOfOutstanding")

        # Fallback: try key_statistics if main info dict has no short data
        if short_pct is None and shares_short is None:
            try:
                ks = stock.get_key_statistics()
                if ks:
                    short_pct = ks.get("shortPercentOfFloat")
                    short_ratio = ks.get("shortRatio")
                    shares_short = ks.get("sharesShort")
                    shares_short_prior = ks.get("sharesShortPriorMonth")
            except Exception:
                pass

        if short_pct is not None or shares_short is not None:
            data: dict = {}
            if short_pct is not None:
                data["short_pct_float"] = round(float(short_pct) * 100, 2)
            if short_ratio is not None:
                data["short_ratio"] = round(float(short_ratio), 2)
                # days_to_cover is the same metric as short_ratio (shares short / avg daily volume)
                data["days_to_cover"] = data["short_ratio"]
            if shares_short is not None:
                data["shares_short"] = int(shares_short)
            if shares_short_prior is not None:
                data["shares_short_prior_month"] = int(shares_short_prior)
                if shares_short and shares_short_prior and int(shares_short_prior) != 0:
                    data["short_change_pct"] = round(
                        (int(shares_short) / int(shares_short_prior) - 1) * 100, 1,
                    )
            if short_pct_outstanding is not None:
                data["short_pct_outstanding"] = round(float(short_pct_outstanding) * 100, 2)

            # If we have shares short but no ratio, compute days_to_cover from volume history
            if "days_to_cover" not in data and shares_short is not None:
                avg_volume = _average_daily_volume(ticker)
                if avg_volume and avg_volume > 0:
                    data["days_to_cover"] = round(int(shares_short) / avg_volume, 2)
                    data["days_to_cover_source"] = "shares_short / 1mo_avg_volume"

            # Classify short interest level
            pct = data.get("short_pct_float", 0)
            if pct > 20:
                data["short_sentiment"] = "HIGH_SHORT_INTEREST"
            elif pct > 10:
                data["short_sentiment"] = "ELEVATED"
            elif pct > 0:
                data["short_sentiment"] = "NORMAL"
            else:
                data["short_sentiment"] = "UNKNOWN"

            title = f"{ticker} short interest: {pct:.1f}% of float" if pct else f"{ticker} short interest data"
            evidence = [Evidence(
                id=f"finra_{ticker}_short",
                source=DataSource.FINRA,
                type=EvidenceType.ANALYST_DATA,
                ticker=ticker,
                fetched_at=now,
                content_hash=str(hash(str(data))),
                title=title,
                data=data,
            )]
            return EvidencePacket(
                ticker=ticker, cycle_id=cycle_id, evidence=evidence,
                fetched_at=now, source_count=1,
            )

    except ImportError:
        log_debug(
            "FINRA_YFINANCE_UNAVAILABLE",
            payload={"ticker": ticker},
            level="INFO",
            cycle_id=cycle_id,
            msg="yfinance not installed, FINRA short interest unavailable",
        )
    except Exception as exc:
        log_debug(
            "FINRA_FETCH_FAILED",
            payload={"ticker": ticker, "error": str(exc)[:200]},
            level="INFO",
            cycle_id=cycle_id,
            msg=f"FINRA short interest fetch failed for {ticker}: {exc}",
        )

    # Fallback: insufficient data
    evidence = [Evidence(
        id=f"finra_{ticker}_short",
        source=DataSource.FINRA,
        type=EvidenceType.ANALYST_DATA,
        ticker=ticker,
        fetched_at=now,
        content_hash=f"finra_{ticker}_insufficient",
        data={
            "status": "INSUFFICIENT_DATA",
            "reason": "yfinance short data unavailable — use [KNOWLEDGE] estimates",
            "short_pct_float": None,
            "days_to_cover": None,
            "short_change_pct": None,
        },
    )]
    return EvidencePacket(
        ticker=ticker, cycle_id=cycle_id, evidence=evidence,
        fetched_at=now, source_count=1,
    )
