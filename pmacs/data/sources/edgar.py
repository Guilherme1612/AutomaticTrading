"""SEC EDGAR filings source (CRITICAL).

Fetches two complementary EDGAR endpoints:
  1. /submissions/CIK{cik}.json  — recent filing metadata (10-K, 10-Q, 8-K dates)
  2. /api/xbrl/companyfacts/CIK{cik}.json — XBRL financial facts (revenue, EPS, etc.)

This gives agents actual reported numbers from SEC filings, not just metadata.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pmacs.data.gateway import DataGateway
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket, EvidenceType

# XBRL tags to extract — covers the main income statement + EPS items
# Some companies use different tags; we try multiple aliases.
_REVENUE_TAGS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
]
_EPS_TAGS = [
    "EarningsPerShareDiluted",
    "EarningsPerShareBasic",
]
_NETINCOME_TAGS = [
    "NetIncomeLoss",
    "NetIncome",
    "ProfitLoss",
]
_GROSSPROFIT_TAGS = [
    "GrossProfit",
]
_OPCF_TAGS = [
    "NetCashProvidedByUsedInOperatingActivities",
]
_SBC_TAGS = [
    "StockBasedCompensationExpense",
    "ShareBasedCompensation",
    "StockBasedCompensation",
]
_CAPEX_TAGS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "CapitalExpenditure",
    "PurchaseOfPropertyPlantAndEquipment",
    "PaymentsForCapitalExpenditures",
]
_SHARES_TAGS = [
    "CommonStockSharesOutstanding",
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfSharesOutstandingBasic",
]


def _extract_recent_facts(
    facts_ns: dict[str, Any],
    tags: list[str],
    form_filter: str | None = None,
    max_periods: int = 8,
) -> list[dict]:
    """Extract the most recent reported values for any of the given XBRL tags.

    Returns list of dicts: [{"period": "YYYY-MM-DD", "val": float, "form": "10-Q"}, ...]
    sorted newest-first, limited to max_periods entries.
    """
    for tag in tags:
        if tag not in facts_ns:
            continue
        units = facts_ns[tag].get("units", {})
        # Revenue/income in USD; EPS in USD/shares
        for unit_key, entries in units.items():
            if not isinstance(entries, list):
                continue
            # Keep only annual/quarterly filings, deduplicate by (end, form)
            seen: set[tuple[str, str]] = set()
            rows: list[dict] = []
            for e in entries:
                form = e.get("form", "")
                if form_filter and form != form_filter:
                    continue
                if form not in ("10-K", "10-Q"):
                    continue
                end = e.get("end", "")
                key = (end, form)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "period": end,
                    "val": e.get("val"),
                    "form": form,
                    "unit": unit_key,
                })
            if rows:
                # Sort newest period first
                rows.sort(key=lambda x: x["period"], reverse=True)
                return rows[:max_periods]
    return []


def _compute_yoy_growth(rows: list[dict]) -> str | None:
    """Compute YoY growth between most recent and year-ago period (same form type).

    Finds the period closest to exactly 365 days prior to avoid the QoQ trap where
    same_form[0] would be the immediately preceding quarter, not the year-ago quarter.
    """
    if len(rows) < 2:
        return None
    from datetime import date
    latest = rows[0]
    target_form = latest["form"]
    same_form = [r for r in rows[1:] if r["form"] == target_form]
    if not same_form:
        return None
    # Find the same-form period closest to exactly 1 year (365 days) before latest
    try:
        latest_date = date.fromisoformat(latest["period"])
    except (ValueError, TypeError):
        return None
    best_prior = None
    best_delta = None
    for r in same_form:
        try:
            prior_date = date.fromisoformat(r["period"])
        except (ValueError, TypeError):
            continue
        days_back = (latest_date - prior_date).days
        if days_back <= 0:
            continue
        delta = abs(days_back - 365)
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_prior = r
    # Only accept if within 120 days of target (avoids mixing annual vs quarterly)
    if best_prior is None or best_delta > 120:
        return None
    prior = best_prior
    if prior["val"] and prior["val"] != 0 and latest["val"] is not None:
        growth = (latest["val"] - prior["val"]) / abs(prior["val"])
        return f"{growth:+.1%}"
    return None


def fetch(
    cik: str,
    ticker: str,
    gateway: DataGateway,
    cycle_id: str = "",
) -> EvidencePacket:
    """Fetch SEC EDGAR filing metadata + XBRL financial facts.

    Returns up to 3 Evidence items:
      - edgar_{ticker}_filings   → recent 10-K / 10-Q / 8-K metadata
      - edgar_{ticker}_financials → revenue, EPS, net income from XBRL
      - edgar_{ticker}_cashflow  → operating cash flow from XBRL
    """
    now = datetime.now(timezone.utc)
    padded_cik = cik.zfill(10)
    evidence: list[Evidence] = []

    # ── 1. Filing metadata (submission index) ───────────────────────────────
    try:
        resp = gateway.fetch(
            "edgar",
            f"https://data.sec.gov/submissions/CIK{padded_cik}.json",
            headers={"Accept": "application/json", "User-Agent": "PMACS research@pmacs.local"},
        )
        sub_data = resp.json() if resp and resp.status_code == 200 else {}
    except Exception:
        sub_data = {}

    filings_meta: list[dict] = []
    if sub_data:
        filings = sub_data.get("filings", {}).get("recent", {})
        forms = filings.get("form", [])
        accns = filings.get("accessionNumber", [])
        dates = filings.get("filingDate", filings.get("fileDate", []))
        for i, form in enumerate(forms[:10]):
            if form in ("10-K", "10-Q", "8-K", "S-1", "DEF 14A"):
                accn = accns[i] if i < len(accns) else ""
                date = dates[i] if i < len(dates) else ""
                filings_meta.append({"form": form, "accession": accn, "date": date})

    if filings_meta:
        # Most recent 10-K and 10-Q dates for prompt grounding
        most_recent_10k = next((f["date"] for f in filings_meta if f["form"] == "10-K"), "N/A")
        most_recent_10q = next((f["date"] for f in filings_meta if f["form"] == "10-Q"), "N/A")
        evidence.append(Evidence(
            id=f"edgar_{ticker}_filings",
            source=DataSource.EDGAR,
            type=EvidenceType.SEC_FILING,
            ticker=ticker,
            fetched_at=now,
            content_hash=str(hash(str(filings_meta))),
            title=f"{ticker} SEC filings — 10-K filed {most_recent_10k}, 10-Q filed {most_recent_10q}",
            data={"filings": filings_meta, "most_recent_10k": most_recent_10k, "most_recent_10q": most_recent_10q},
        ))

    # ── 2. XBRL company facts (income statement + EPS) ─────────────────────
    try:
        resp = gateway.fetch(
            "edgar",
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{padded_cik}.json",
            headers={"Accept": "application/json", "User-Agent": "PMACS research@pmacs.local"},
        )
        facts_data = resp.json() if resp and resp.status_code == 200 else {}
    except Exception:
        facts_data = {}

    if facts_data:
        gaap = facts_data.get("facts", {}).get("us-gaap", {})
        dei = facts_data.get("facts", {}).get("dei", {})  # noqa: F841 (for future use)

        financials: dict[str, Any] = {"ticker": ticker, "source": "SEC XBRL", "fetched_at": now.isoformat()}

        # Revenue
        rev_rows = _extract_recent_facts(gaap, _REVENUE_TAGS)
        if rev_rows:
            financials["revenue_periods"] = rev_rows[:6]
            yoy = _compute_yoy_growth(rev_rows)
            if yoy:
                financials["revenue_yoy_growth"] = yoy
            # Most recent quarter revenue
            financials["revenue_most_recent"] = {
                "value_usd": rev_rows[0]["val"],
                "period_end": rev_rows[0]["period"],
                "form": rev_rows[0]["form"],
            }

        # EPS diluted
        eps_rows = _extract_recent_facts(gaap, _EPS_TAGS)
        if eps_rows:
            financials["eps_periods"] = eps_rows[:6]
            yoy = _compute_yoy_growth(eps_rows)
            if yoy:
                financials["eps_yoy_growth"] = yoy
            financials["eps_most_recent"] = {
                "value": eps_rows[0]["val"],
                "period_end": eps_rows[0]["period"],
                "form": eps_rows[0]["form"],
            }

        # Net income
        ni_rows = _extract_recent_facts(gaap, _NETINCOME_TAGS)
        if ni_rows:
            financials["net_income_most_recent"] = {
                "value_usd": ni_rows[0]["val"],
                "period_end": ni_rows[0]["period"],
                "form": ni_rows[0]["form"],
            }
            yoy = _compute_yoy_growth(ni_rows)
            if yoy:
                financials["net_income_yoy_growth"] = yoy

        # Gross profit — only compute margin when GP and revenue share the same period
        gp_rows = _extract_recent_facts(gaap, _GROSSPROFIT_TAGS)
        if gp_rows:
            financials["gross_profit_most_recent"] = {"value_usd": gp_rows[0]["val"], "period_end": gp_rows[0]["period"]}
            # Find matching revenue period for accurate margin calculation
            if rev_rows:
                gp_period = gp_rows[0]["period"]
                matching_rev = next((r for r in rev_rows if r["period"] == gp_period), None)
                if matching_rev:
                    gp = gp_rows[0]["val"]
                    rev = matching_rev["val"]
                    if gp and rev and rev != 0:
                        margin = round(gp / rev * 100, 1)
                        financials["gross_margin_pct"] = f"{margin:.1f}%"

        # Operating cash flow — extract early so CapEx/FCF and cash flow evidence can reuse
        opcf_rows = _extract_recent_facts(gaap, _OPCF_TAGS)

        # Stock-based compensation
        sbc_rows = _extract_recent_facts(gaap, _SBC_TAGS)
        if sbc_rows:
            financials["sbc_most_recent"] = {
                "value_usd": sbc_rows[0]["val"],
                "period_end": sbc_rows[0]["period"],
                "form": sbc_rows[0]["form"],
            }
            yoy = _compute_yoy_growth(sbc_rows)
            if yoy:
                financials["sbc_yoy_growth"] = yoy
            # SBC as % of revenue (dilution metric)
            if rev_rows:
                sbc_period = sbc_rows[0]["period"]
                matching_rev = next((r for r in rev_rows if r["period"] == sbc_period), None)
                if matching_rev and matching_rev["val"] and matching_rev["val"] != 0:
                    sbc_pct = round(sbc_rows[0]["val"] / matching_rev["val"] * 100, 1)
                    financials["sbc_pct_of_revenue"] = f"{sbc_pct:.1f}%"

        # Capital expenditures — reuse opcf_rows already extracted for cash flow evidence
        capex_rows = _extract_recent_facts(gaap, _CAPEX_TAGS)
        if capex_rows:
            financials["capex_most_recent"] = {
                "value_usd": capex_rows[0]["val"],
                "period_end": capex_rows[0]["period"],
                "form": capex_rows[0]["form"],
            }
            # Derive FCF = OpCF - |CapEx| (match by period, handle either sign convention)
            if opcf_rows:
                ocf_by_period = {r["period"]: r["val"] for r in opcf_rows if r.get("val")}
                capex_by_period = {r["period"]: r["val"] for r in capex_rows if r.get("val")}
                fcf_rows = []
                for period in ocf_by_period:
                    if period in capex_by_period:
                        ocf_val = ocf_by_period[period]
                        capex_val = capex_by_period[period]
                        fcf_val = ocf_val - abs(capex_val)
                        fcf_rows.append({"period": period, "val": fcf_val})
                fcf_rows.sort(key=lambda x: x["period"], reverse=True)
                if fcf_rows:
                    financials["free_cash_flow_most_recent"] = {
                        "value_usd": fcf_rows[0]["val"],
                        "period_end": fcf_rows[0]["period"],
                    }
                    if rev_rows:
                        rev_by_period = {r["period"]: r["val"] for r in rev_rows if r.get("val")}
                        fcf_period = fcf_rows[0]["period"]
                        if fcf_period in rev_by_period and rev_by_period[fcf_period] != 0:
                            fcf_margin = round(fcf_rows[0]["val"] / rev_by_period[fcf_period] * 100, 1)
                            financials["fcf_margin_pct"] = f"{fcf_margin:.1f}%"

        # Shares outstanding
        shares_rows = _extract_recent_facts(gaap, _SHARES_TAGS)
        if shares_rows:
            financials["shares_outstanding"] = {
                "value": shares_rows[0]["val"],
                "period_end": shares_rows[0]["period"],
                "form": shares_rows[0]["form"],
            }

        if len(financials) > 3:  # has real data beyond ticker/source/fetched_at
            # Build a human-readable title
            rev_summary = (
                f"revenue {financials.get('revenue_yoy_growth', 'N/A')} YoY"
                if "revenue_yoy_growth" in financials else "revenue N/A"
            )
            eps_summary = (
                f"EPS {financials.get('eps_yoy_growth', 'N/A')} YoY"
                if "eps_yoy_growth" in financials else "EPS N/A"
            )
            evidence.append(Evidence(
                id=f"edgar_{ticker}_financials",
                source=DataSource.EDGAR,
                type=EvidenceType.SEC_FILING,
                ticker=ticker,
                fetched_at=now,
                content_hash=str(hash(str(financials))),
                title=f"{ticker} SEC-reported financials — {rev_summary}, {eps_summary}",
                data=financials,
            ))

        # ── 3. Operating cash flow (reuses opcf_rows extracted above) ────────
        if opcf_rows:
            cf_data: dict[str, Any] = {
                "ticker": ticker,
                "operating_cashflow_periods": opcf_rows[:4],
                "opcf_most_recent": {
                    "value_usd": opcf_rows[0]["val"],
                    "period_end": opcf_rows[0]["period"],
                    "form": opcf_rows[0]["form"],
                },
            }
            yoy = _compute_yoy_growth(opcf_rows)
            if yoy:
                cf_data["opcf_yoy_growth"] = yoy

            # ── Derived ratio: FCF/Net Income (earnings quality) ────────────
            if ni_rows and opcf_rows:
                # Match by period
                ni_by_period = {r["period"]: r["val"] for r in ni_rows if r.get("val")}
                ocf_by_period = {r["period"]: r["val"] for r in opcf_rows if r.get("val")}
                ratios = []
                for period, ocf_val in ocf_by_period.items():
                    ni_val = ni_by_period.get(period)
                    if ni_val and ni_val != 0:
                        ratios.append({"period": period, "fcf_to_ni": round(ocf_val / ni_val, 2)})
                if ratios:
                    cf_data["fcf_to_net_income"] = ratios
                    latest_ratio = ratios[0]["fcf_to_ni"]
                    cf_data["earnings_quality"] = (
                        "HIGH" if latest_ratio >= 1.0
                        else "MODERATE" if latest_ratio >= 0.5
                        else "LOW"
                    )

            # ── Derived ratio: Operating leverage (revenue growth vs opcf growth) ──
            rev_growth_str = financials.get("revenue_yoy_growth")
            if rev_growth_str and yoy:
                try:
                    rev_g = float(rev_growth_str.replace("%", "").replace("+", "")) / 100
                    ocf_g = float(yoy.replace("%", "").replace("+", "")) / 100
                    leverage = ocf_g / rev_g if rev_g != 0 else None
                    if leverage is not None:
                        cf_data["operating_leverage"] = round(leverage, 2)
                        cf_data["operating_leverage_interpretation"] = (
                            "POSITIVE (OCF growing faster than revenue)"
                            if leverage > 1.2
                            else "NEUTRAL"
                            if leverage > 0.8
                            else "NEGATIVE (OCF growing slower than revenue)"
                        )
                except (ValueError, ZeroDivisionError):
                    pass

            evidence.append(Evidence(
                id=f"edgar_{ticker}_cashflow",
                source=DataSource.EDGAR,
                type=EvidenceType.SEC_FILING,
                ticker=ticker,
                fetched_at=now,
                content_hash=str(hash(str(cf_data))),
                title=f"{ticker} operating cash flow — {opcf_rows[0]['val']:,.0f} USD ({opcf_rows[0]['period']})"
                      if opcf_rows[0].get("val") else f"{ticker} operating cash flow",
                data=cf_data,
            ))

    # Fallback: if XBRL fetch completely failed, still emit a filing-index stub
    if not evidence:
        evidence.append(Evidence(
            id=f"edgar_{ticker}_stub",
            source=DataSource.EDGAR,
            type=EvidenceType.SEC_FILING,
            ticker=ticker,
            fetched_at=now,
            content_hash=str(hash(f"{ticker}_stub")),
            title=f"{ticker} — EDGAR data unavailable",
            data={"ticker": ticker, "error": "EDGAR fetch failed or CIK unknown"},
        ))

    return EvidencePacket(
        ticker=ticker,
        cycle_id=cycle_id,
        evidence=evidence,
        fetched_at=now,
        source_count=1,
    )
