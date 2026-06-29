"""Yahoo Finance price target and analyst data source (IMPORTANT).

Provides analyst price targets (mean, high, low, median) from Yahoo Finance
as a free alternative to Finnhub's paywalled /stock/price-target endpoint.

Also provides:
  - Number of analysts covering the stock
  - Forward P/E, PEG from Yahoo
  - EPS trend (current quarter, next quarter, current year, next year)
  - Revenue estimates (current year, next year)
  - Growth rates (earnings, revenue)

Critical for:
  - MemoWriter: fair value anchored to analyst consensus
  - GrowthHunter: forward EPS/revenue growth rates
  - Arbitration: price target vs current price for conviction
"""
from __future__ import annotations

from datetime import datetime, timezone

from pmacs.data.gateway import DataGateway
from pmacs.logsys import log_debug
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket, EvidenceType


def fetch_price_targets(
    ticker: str, gateway: DataGateway, api_key: str = "", cycle_id: str = "",
) -> EvidencePacket:
    """Fetch analyst price targets and forward estimates from Yahoo Finance.

    Uses yfinance library — no API key required.
    """
    now = datetime.now(timezone.utc)
    evidence: list[Evidence] = []

    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)

        # ── 1. Analyst price targets ────────────────────────────────────────
        info = stock.info or {}
        target_mean = info.get("targetMeanPrice")
        target_high = info.get("targetHighPrice")
        target_low = info.get("targetLowPrice")
        target_median = info.get("targetMedianPrice")
        num_analysts = info.get("numberOfAnalystOpinions")
        current_price = info.get("currentPrice") or info.get("regularMarketPrice")

        # ── 0. Comprehensive financial data for cross-referencing ─────────────
        # Yahoo Finance often has more current TTM data than Finnhub free tier.
        # Extract key metrics for verification.
        verification_data: dict = {}
        _YF_FIELDS = {
            "totalRevenue": "ttm_revenue",
            "grossProfits": "ttm_gross_profit",
            "ebitda": "ttm_ebitda",
            "totalCash": "total_cash",
            "totalDebt": "total_debt",
            "freeCashflow": "ttm_fcf",
            "operatingCashflow": "ttm_operating_cf",
            "capitalExpenditures": "ttm_capex",
            "revenueGrowth": "revenue_growth_yoy",
            "earningsGrowth": "earnings_growth_yoy",
            "grossMargins": "gross_margin",
            "operatingMargins": "operating_margin",
            "profitMargins": "net_margin",
            "returnOnEquity": "roe",
            "sharesOutstanding": "shares_outstanding",
            "heldPercentInsiders": "insider_ownership_pct",
            "heldPercentInstitutions": "institutional_ownership_pct",
            "shortRatio": "short_ratio",
            "shortPercentOfFloat": "short_pct_float",
            "beta": "beta",
            "enterpriseToRevenue": "ev_to_revenue",
            "enterpriseToEbitda": "ev_to_ebitda",
            "priceToSalesTrailing12Months": "ps_ratio",
            "trailingPE": "trailing_pe",
            "debtToEquity": "debt_to_equity",
        }
        for yf_key, our_key in _YF_FIELDS.items():
            val = info.get(yf_key)
            if val is not None:
                try:
                    verification_data[our_key] = float(val)
                except (ValueError, TypeError):
                    pass

        # yfinance always returns margins, growth, and ownership as decimals
        # (e.g. 0.57 = 57%, 0.015 = 1.5%). Always multiply by 100.
        # Guard: if value is already > 1.0 it's likely already a percentage
        # (e.g. ROE > 100% for leveraged companies like AAPL is valid).
        _DECIMAL_FIELDS = (
            "gross_margin", "operating_margin", "net_margin", "roe",
            "insider_ownership_pct", "institutional_ownership_pct", "short_pct_float",
            "revenue_growth_yoy", "earnings_growth_yoy",
        )
        for pct_key in _DECIMAL_FIELDS:
            if pct_key in verification_data:
                val = verification_data[pct_key]
                # yfinance decimals: -1.0 to ~5.0 range (even 500% growth = 5.0)
                # Already-percentage values would be > 10.0 (e.g. 57.0 for 57%)
                if abs(val) <= 10.0:
                    verification_data[pct_key] = round(val * 100, 2)

        if verification_data:
            title_parts = []
            if "ttm_revenue" in verification_data:
                title_parts.append(f"Rev ${verification_data['ttm_revenue']/1e9:.1f}B")
            if "revenue_growth_yoy" in verification_data:
                title_parts.append(f"Growth {verification_data['revenue_growth_yoy']:+.1f}%")
            if "gross_margin" in verification_data:
                title_parts.append(f"GM {verification_data['gross_margin']:.1f}%")
            if "ttm_fcf" in verification_data:
                title_parts.append(f"FCF ${verification_data['ttm_fcf']/1e9:.1f}B")

            ver_title = f"{ticker} Yahoo financials (cross-reference) — {', '.join(title_parts)}" if title_parts else f"{ticker} Yahoo financials"
            evidence.append(Evidence(
                id=f"yahoo_{ticker}_financials",
                source=DataSource.YAHOO,
                type=EvidenceType.FINANCIAL_STATEMENT,
                ticker=ticker,
                fetched_at=now,
                content_hash=str(hash(str(verification_data))),
                title=ver_title,
                data=verification_data,
            ))

        if target_mean is not None:
            pt_data: dict = {
                "target_mean": round(float(target_mean), 2),
                "target_high": round(float(target_high), 2) if target_high else None,
                "target_low": round(float(target_low), 2) if target_low else None,
                "target_median": round(float(target_median), 2) if target_median else None,
                "num_analysts": num_analysts,
                "current_price": round(float(current_price), 2) if current_price else None,
            }

            # Compute upside to mean/median
            if current_price and target_mean:
                pt_data["upside_to_mean_pct"] = round(
                    (float(target_mean) / float(current_price) - 1) * 100, 1,
                )
            if current_price and target_median:
                pt_data["upside_to_median_pct"] = round(
                    (float(target_median) / float(current_price) - 1) * 100, 1,
                )

            pt_title = f"{ticker} analyst price target: ${float(target_mean):.2f} mean"
            if target_median:
                pt_title += f", ${float(target_median):.2f} median"
            if target_low and target_high:
                pt_title += f" (${float(target_low):.2f}-${float(target_high):.2f})"
            if num_analysts:
                pt_title += f" — {num_analysts} analysts"
            if pt_data.get("upside_to_mean_pct") is not None:
                pt_title += f" | {pt_data['upside_to_mean_pct']:+.1f}% to mean"

            evidence.append(Evidence(
                id=f"yahoo_{ticker}_price_target",
                source=DataSource.YAHOO,
                type=EvidenceType.ANALYST_DATA,
                ticker=ticker,
                fetched_at=now,
                content_hash=str(hash(str(pt_data))),
                title=pt_title,
                data=pt_data,
            ))

        # ── 2. Forward valuation + growth rates ─────────────────────────────
        fwd_pe = info.get("forwardPE")
        fwd_eps = info.get("forwardEps")
        trailing_eps = info.get("trailingEps")
        peg = info.get("pegRatio")
        fwd_rev_growth = info.get("revenueGrowth", info.get("earningsGrowth"))

        growth_data: dict = {}
        if fwd_pe is not None:
            growth_data["forward_pe"] = round(float(fwd_pe), 2)
        if peg is not None:
            growth_data["peg_ratio"] = round(float(peg), 2)
        if fwd_eps is not None:
            growth_data["forward_eps"] = round(float(fwd_eps), 2)
        if trailing_eps is not None:
            growth_data["trailing_eps"] = round(float(trailing_eps), 2)
        if fwd_eps and trailing_eps and trailing_eps != 0:
            growth_data["forward_eps_growth_pct"] = round(
                (float(fwd_eps) / float(trailing_eps) - 1) * 100, 1,
            )

        # EPS trend from Yahoo
        eps_trend = info.get("epsTrend") or {}
        if eps_trend:
            growth_data["eps_trend"] = {
                "current_q": eps_trend.get("currentQ"),
                "next_q": eps_trend.get("nextQ"),
                "current_year": eps_trend.get("currentYear"),
                "next_year": eps_trend.get("nextYear"),
            }
            # If we have both current and next year EPS, compute growth
            cy = eps_trend.get("currentYear")
            ny = eps_trend.get("nextYear")
            if cy and ny and float(cy) != 0:
                growth_data["next_year_eps_growth_pct"] = round(
                    (float(ny) / float(cy) - 1) * 100, 1,
                )

        # Revenue estimates — include analyst consensus for current year + next year
        rev_est = info.get("revenueEstimates") or {}
        if rev_est:
            avg_rev = rev_est.get("avgRevenue") or rev_est.get("mean")
            if avg_rev:
                growth_data["ntm_revenue_consensus"] = float(avg_rev)
            # Detailed analyst revenue estimates
            for key, label in [
                ("avgRevenue", "current_year_revenue_avg"),
                ("lowRevenue", "current_year_revenue_low"),
                ("highRevenue", "current_year_revenue_high"),
                ("numberOfAnalysts", "revenue_analyst_count"),
                ("growth", "current_year_revenue_growth_pct"),
            ]:
                val = rev_est.get(key)
                if val is not None:
                    if key == "growth":
                        growth_data[label] = round(float(val) * 100, 1) if abs(float(val)) <= 10 else round(float(val), 1)
                    elif key != "numberOfAnalysts":
                        growth_data[label] = float(val)
                    else:
                        growth_data[label] = int(val)

        # Next year revenue estimates (from financialsData if available)
        fin_data = info.get("financialsData") or {}
        if fin_data:
            ny_rev = fin_data.get("revenueEstimate")
            if ny_rev:
                growth_data["next_year_revenue_consensus"] = float(ny_rev)

        # Forward P/S ratio (current market cap / NTM revenue)
        mcap = info.get("marketCap")
        if mcap and growth_data.get("ntm_revenue_consensus"):
            try:
                growth_data["forward_ps"] = round(float(mcap) / float(growth_data["ntm_revenue_consensus"]), 2)
            except (ZeroDivisionError, ValueError):
                pass

        # Earnings growth
        earnings_growth = info.get("earningsGrowth")
        rev_growth = info.get("revenueGrowth")
        if earnings_growth is not None:
            growth_data["earnings_growth_yoy"] = round(float(earnings_growth) * 100, 1)
        if rev_growth is not None:
            growth_data["revenue_growth_yoy"] = round(float(rev_growth) * 100, 1)

        if growth_data:
            title_parts = []
            if growth_data.get("forward_pe"):
                title_parts.append(f"fwd P/E {growth_data['forward_pe']:.1f}")
            if growth_data.get("forward_eps_growth_pct"):
                title_parts.append(f"EPS growth {growth_data['forward_eps_growth_pct']:+.1f}%")
            if growth_data.get("next_year_eps_growth_pct"):
                title_parts.append(f"NY EPS growth {growth_data['next_year_eps_growth_pct']:+.1f}%")

            growth_title = f"{ticker} forward valuation — {', '.join(title_parts)}" if title_parts else f"{ticker} forward valuation"

            evidence.append(Evidence(
                id=f"yahoo_{ticker}_forward_valuation",
                source=DataSource.YAHOO,
                type=EvidenceType.ANALYST_DATA,
                ticker=ticker,
                fetched_at=now,
                content_hash=str(hash(str(growth_data))),
                title=growth_title,
                data=growth_data,
            ))

    except ImportError:
        log_debug(
            "YFINANCE_UNAVAILABLE",
            payload={"ticker": ticker},
            level="INFO",
            cycle_id=cycle_id,
            msg="yfinance not installed, skipping Yahoo Finance price targets",
        )
    except Exception as exc:
        log_debug(
            "YAHOO_FETCH_FAILED",
            payload={"ticker": ticker, "error": str(exc)[:200]},
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Yahoo Finance fetch failed for {ticker}: {exc}",
        )

    return EvidencePacket(
        ticker=ticker, cycle_id=cycle_id, evidence=evidence,
        fetched_at=now, source_count=1,
    )
