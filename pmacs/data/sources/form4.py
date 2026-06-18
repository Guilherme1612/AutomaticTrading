"""SEC Form 4 insider filings source (IMPORTANT).

Fetches insider transaction data from EDGAR submissions API and parses the
primary Form 4 XML documents to extract actual transactions: open-market
purchases (P), open-market sales (S), option exercises / routine compensation
(M, F, A, X, C, etc.), and derivative transactions.

Provides the insider_activity agent with deterministic signals:
  - CLUSTER_BUY: 3+ distinct insiders buying in open market within 30 days
  - CEO_BUY: CEO/CFO/Chairman/President open-market purchase
  - LARGE_BUY: single purchase >= $500K
  - LARGE_SELL: single sale >= $1M
  - CLUSTER_SELL: 3+ distinct insiders selling in open market within 30 days
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from pmacs.data.gateway import DataGateway
from pmacs.logsys import log_debug
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket, EvidenceType


# Dollar thresholds for signal classification
_LARGE_BUY_USD = 500_000
_LARGE_SELL_USD = 1_000_000
_CLUSTER_WINDOW_DAYS = 30
_MAX_XML_FETCHES = 5
_MAX_TRANSACTION_EVIDENCE = 10


@dataclass
class InsiderTransaction:
    """One extracted Form 4 transaction."""

    reporting_owner_cik: str = ""
    reporting_owner_name: str = ""
    officer_title: str = ""
    is_director: bool = False
    is_officer: bool = False
    is_ten_percent_owner: bool = False
    is_other: bool = False

    security_title: str = ""
    transaction_date: str = ""
    transaction_code: str = ""
    transaction_type: str = "OTHER"  # PURCHASE | SALE | OPTION_EXERCISE | ROUTINE | OTHER
    acquired_disposed: str = ""  # A | D
    shares: float = 0.0
    price_per_share: float | None = None
    dollar_value: float | None = None
    is_derivative: bool = False
    underlying_security_title: str = ""
    underlying_shares: float = 0.0
    exercise_price: float | None = None


# ---------------------------------------------------------------------------
# XML helpers (namespace-agnostic)
# ---------------------------------------------------------------------------


def _strip_ns(tag: str) -> str:
    """Return local tag name stripping any namespace prefix."""
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _text(el: ET.Element | None, default: str = "") -> str:
    """Return stripped text of first child <value> or the element itself."""
    if el is None:
        return default
    value = el.find("{*}value")
    if value is not None and value.text:
        return value.text.strip()
    if el.text:
        return el.text.strip()
    return default


def _float_text(el: ET.Element | None, default: float | None = None) -> float | None:
    text = _text(el, "")
    if not text:
        return default
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return default


def _bool_text(el: ET.Element | None) -> bool:
    text = _text(el, "false").lower()
    return text in ("true", "1", "yes")


def _find_all(root: ET.Element, tag: str) -> list[ET.Element]:
    """Find all descendants by local tag name, ignoring namespace."""
    return [el for el in root.iter() if _strip_ns(el.tag) == tag]


def _find_one(root: ET.Element, tag: str) -> ET.Element | None:
    for el in root.iter():
        if _strip_ns(el.tag) == tag:
            return el
    return None


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------


def _parse_transaction_table(root: ET.Element, table_tag: str, is_derivative: bool) -> list[InsiderTransaction]:
    """Parse either nonDerivativeTable or derivativeTable."""
    table = _find_one(root, table_tag)
    if table is None:
        return []

    txn_tags = "nonDerivativeTransaction" if not is_derivative else "derivativeTransaction"
    holding_tags = "nonDerivativeHolding" if not is_derivative else "derivativeHolding"

    transactions: list[InsiderTransaction] = []
    for txn_el in _find_all(table, txn_tags):
        txn = InsiderTransaction(is_derivative=is_derivative)

        # Security title
        txn.security_title = _text(_find_one(txn_el, "securityTitle"))

        # Transaction date
        txn.transaction_date = _text(_find_one(txn_el, "transactionDate"))

        # Coding
        coding = _find_one(txn_el, "transactionCoding")
        if coding is not None:
            txn.transaction_code = _text(_find_one(coding, "transactionCode")).upper()

        # Amounts
        amounts = _find_one(txn_el, "transactionAmounts")
        if amounts is not None:
            txn.shares = _float_text(_find_one(amounts, "transactionShares"), 0.0) or 0.0
            txn.price_per_share = _float_text(_find_one(amounts, "transactionPricePerShare"))
            txn.acquired_disposed = _text(_find_one(amounts, "transactionAcquiredDisposedCode")).upper()

        # Post-transaction ownership (informational)
        post = _find_one(txn_el, "postTransactionAmounts")
        if post is not None:
            _ = _float_text(_find_one(post, "sharesOwnedFollowingTransaction"))  # noqa: F841

        # Derivative specifics
        if is_derivative:
            txn.exercise_price = _float_text(_find_one(txn_el, "conversionOrExercisePrice"))
            underlying = _find_one(txn_el, "underlyingSecurity")
            if underlying is not None:
                txn.underlying_security_title = _text(_find_one(underlying, "underlyingSecurityTitle"))
                txn.underlying_shares = _float_text(_find_one(underlying, "underlyingSecurityShares"), 0.0) or 0.0

        transactions.append(txn)

    return transactions


def _parse_form4_xml(xml_text: str) -> dict[str, Any]:
    """Parse a single Form 4 XML document.

    Returns {"period_of_report": str, "issuer_symbol": str, "reporting_owner": dict,
              "transactions": list[InsiderTransaction]}.
    """
    root = ET.fromstring(xml_text)

    period_of_report = _text(_find_one(root, "periodOfReport"))
    issuer_symbol = ""
    issuer = _find_one(root, "issuer")
    if issuer is not None:
        issuer_symbol = _text(_find_one(issuer, "issuerTradingSymbol"))

    owner_info: dict[str, Any] = {
        "cik": "",
        "name": "",
        "officer_title": "",
        "is_director": False,
        "is_officer": False,
        "is_ten_percent_owner": False,
        "is_other": False,
    }
    reporting_owner = _find_one(root, "reportingOwner")
    if reporting_owner is not None:
        owner_id = _find_one(reporting_owner, "reportingOwnerId")
        if owner_id is not None:
            owner_info["cik"] = _text(_find_one(owner_id, "rptOwnerCik"))
            owner_info["name"] = _text(_find_one(owner_id, "rptOwnerName"))
        rel = _find_one(reporting_owner, "reportingOwnerRelationship")
        if rel is not None:
            owner_info["is_director"] = _bool_text(_find_one(rel, "isDirector"))
            owner_info["is_officer"] = _bool_text(_find_one(rel, "isOfficer"))
            owner_info["is_ten_percent_owner"] = _bool_text(_find_one(rel, "isTenPercentOwner"))
            owner_info["is_other"] = _bool_text(_find_one(rel, "isOther"))
            owner_info["officer_title"] = _text(_find_one(rel, "officerTitle"))

    non_derivative = _parse_transaction_table(root, "nonDerivativeTable", is_derivative=False)
    derivative = _parse_transaction_table(root, "derivativeTable", is_derivative=True)
    transactions = non_derivative + derivative

    for txn in transactions:
        txn.reporting_owner_cik = owner_info["cik"]
        txn.reporting_owner_name = owner_info["name"]
        txn.officer_title = owner_info["officer_title"]
        txn.is_director = owner_info["is_director"]
        txn.is_officer = owner_info["is_officer"]
        txn.is_ten_percent_owner = owner_info["is_ten_percent_owner"]
        txn.is_other = owner_info["is_other"]
        if txn.shares > 0 and txn.price_per_share is not None and txn.price_per_share > 0:
            txn.dollar_value = txn.shares * txn.price_per_share

    return {
        "period_of_report": period_of_report,
        "issuer_symbol": issuer_symbol,
        "reporting_owner": owner_info,
        "transactions": transactions,
    }


# ---------------------------------------------------------------------------
# Transaction classification / signal computation
# ---------------------------------------------------------------------------


def _classify_transaction(code: str, acquired_disposed: str) -> str:
    """Map SEC transaction code to a simplified transaction type."""
    code = code.upper()
    if code == "P":
        return "PURCHASE"
    if code == "S":
        return "SALE"
    if code in ("M", "X", "C", "A"):
        return "OPTION_EXERCISE"
    if code in ("F", "I", "J", "G", "H"):
        return "ROUTINE"
    return "OTHER"


def _is_executive(title: str) -> bool:
    """Return True if officer title indicates CEO/CFO/Chairman/President."""
    if not title:
        return False
    t = title.lower()
    return any(
        kw in t
        for kw in (
            "chief executive", "ceo", "president", "chairman",
            "chief financial", "cfo", "chief operating", "coo",
        )
    )


def _compute_signals(transactions: list[InsiderTransaction]) -> dict[str, Any]:
    """Compute deterministic insider-activity signals from parsed transactions."""
    for txn in transactions:
        txn.transaction_type = _classify_transaction(txn.transaction_code, txn.acquired_disposed)

    purchases = [t for t in transactions if t.transaction_type == "PURCHASE"]
    sales = [t for t in transactions if t.transaction_type == "SALE"]
    routine = [t for t in transactions if t.transaction_type in ("ROUTINE", "OPTION_EXERCISE")]

    signals: set[str] = set()

    # CEO / executive buy
    exec_buys = [t for t in purchases if _is_executive(t.officer_title)]
    if exec_buys:
        signals.add("CEO_BUY")

    # Large buy / sell
    large_buys = [t for t in purchases if t.dollar_value and t.dollar_value >= _LARGE_BUY_USD]
    if large_buys:
        signals.add("LARGE_BUY")
    large_sells = [t for t in sales if t.dollar_value and t.dollar_value >= _LARGE_SELL_USD]
    if large_sells:
        signals.add("LARGE_SELL")

    # Cluster buys / sales within trailing window
    now = datetime.now(timezone.utc).date()
    cutoff = now - timedelta(days=_CLUSTER_WINDOW_DAYS)

    def _within_window(t: InsiderTransaction) -> bool:
        try:
            return bool(t.transaction_date) and datetime.strptime(t.transaction_date, "%Y-%m-%d").date() >= cutoff
        except ValueError:
            return False

    recent_purchases = [t for t in purchases if _within_window(t)]
    recent_sales = [t for t in sales if _within_window(t)]
    distinct_buyers = {t.reporting_owner_cik or t.reporting_owner_name for t in recent_purchases}
    distinct_sellers = {t.reporting_owner_cik or t.reporting_owner_name for t in recent_sales}

    if len(distinct_buyers) >= 3:
        signals.add("CLUSTER_BUY")
    if len(distinct_sellers) >= 3:
        signals.add("CLUSTER_SELL")

    routine_only = bool(transactions) and not purchases and not sales
    if routine_only:
        signals.add("ROUTINE_ONLY")

    # Net buy / sell count
    net_buys = len(purchases)
    net_sells = len(sales)

    return {
        "signals": sorted(signals),
        "purchase_count": len(purchases),
        "sale_count": len(sales),
        "routine_count": len(routine),
        "total_transactions": len(transactions),
        "large_buy_count": len(large_buys),
        "large_sell_count": len(large_sells),
        "distinct_buyers_30d": len(distinct_buyers),
        "distinct_sellers_30d": len(distinct_sellers),
        "net_buys": net_buys,
        "net_sells": net_sells,
    }


def _transaction_as_dict(t: InsiderTransaction) -> dict[str, Any]:
    return {
        "reporting_owner_cik": t.reporting_owner_cik,
        "reporting_owner_name": t.reporting_owner_name,
        "officer_title": t.officer_title,
        "is_director": t.is_director,
        "is_officer": t.is_officer,
        "is_ten_percent_owner": t.is_ten_percent_owner,
        "security_title": t.security_title,
        "transaction_date": t.transaction_date,
        "transaction_code": t.transaction_code,
        "transaction_type": t.transaction_type,
        "acquired_disposed": t.acquired_disposed,
        "shares": t.shares,
        "price_per_share": t.price_per_share,
        "dollar_value": t.dollar_value,
        "is_derivative": t.is_derivative,
        "underlying_security_title": t.underlying_security_title,
        "underlying_shares": t.underlying_shares,
        "exercise_price": t.exercise_price,
    }


# ---------------------------------------------------------------------------
# Main fetcher
# ---------------------------------------------------------------------------


def fetch_insider_filings(cik: str, ticker: str, gateway: DataGateway, cycle_id: str = "") -> EvidencePacket:
    """Fetch Form 4 insider filings from EDGAR submissions API and parse XML.

    Returns an EvidencePacket with a summary Evidence item plus up to 10
    individual transaction Evidence items.
    """
    now = datetime.now(timezone.utc)
    padded_cik = cik.zfill(10)
    numeric_cik = cik.lstrip("0") or "0"

    evidence: list[Evidence] = []
    try:
        response = gateway.fetch("form4", f"https://data.sec.gov/submissions/CIK{padded_cik}.json", headers={
            "Accept": "application/json",
            "User-Agent": "PMACS/1.0 (research@pmacs.local)",
        })
        data = response.json()

        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        primary_docs = recent.get("primaryDocument", [])
        accessions = recent.get("accessionNumber", [])

        form4_indices = [i for i, f in enumerate(forms) if f in ("4", "4/A")]

        # Fetch up to _MAX_XML_FETCHES most recent Form 4 XML documents
        all_transactions: list[InsiderTransaction] = []
        fetched_filings: list[dict[str, Any]] = []
        for idx in form4_indices[:_MAX_XML_FETCHES]:
            accession = accessions[idx] if idx < len(accessions) else ""
            primary_doc = primary_docs[idx] if idx < len(primary_docs) else ""
            filing_date = dates[idx] if idx < len(dates) else ""
            if not accession or not primary_doc:
                continue

            accession_no_dash = accession.replace("-", "")
            xml_url = (
                f"https://www.sec.gov/Archives/edgar/data/{numeric_cik}/"
                f"{accession_no_dash}/{primary_doc}"
            )
            try:
                xml_resp = gateway.fetch("form4", xml_url, headers={
                    "Accept": "application/xml",
                    "User-Agent": "PMACS/1.0 (research@pmacs.local)",
                })
                parsed = _parse_form4_xml(xml_resp.text)
                all_transactions.extend(parsed["transactions"])
                fetched_filings.append({
                    "accession": accession,
                    "filing_date": filing_date,
                    "primary_document": primary_doc,
                    "issuer_symbol": parsed.get("issuer_symbol", ""),
                    "period_of_report": parsed.get("period_of_report", ""),
                    "transaction_count": len(parsed["transactions"]),
                })
            except Exception as exc:
                log_debug(
                    "FORM4_XML_PARSE_FAILED",
                    payload={"ticker": ticker, "accession": accession, "error": str(exc)[:200]},
                    level="INFO",
                    cycle_id=cycle_id,
                    msg=f"Failed to parse Form 4 XML for {ticker}/{accession}: {exc}",
                )
                continue

        signals = _compute_signals(all_transactions)

        # Summary evidence
        summary_data: dict[str, Any] = {
            "form4_count": len(form4_indices),
            "xml_fetched": len(fetched_filings),
            "total_transactions": signals["total_transactions"],
            "purchase_count": signals["purchase_count"],
            "sale_count": signals["sale_count"],
            "routine_count": signals["routine_count"],
            "signals": signals["signals"],
            "large_buy_count": signals["large_buy_count"],
            "large_sell_count": signals["large_sell_count"],
            "distinct_buyers_30d": signals["distinct_buyers_30d"],
            "distinct_sellers_30d": signals["distinct_sellers_30d"],
            "net_buys": signals["net_buys"],
            "net_sells": signals["net_sells"],
            "fetched_filings": fetched_filings,
        }

        if not all_transactions:
            summary_data["status"] = "NO_TRANSACTIONS"
            summary_data["note"] = (
                "Form 4 filings found but no parseable transactions. "
                "May be initial ownership reports or XML parse limitations."
            )

        evidence.append(Evidence(
            id=f"form4_{ticker}",
            source=DataSource.FORM4,
            type=EvidenceType.INSIDER_FILING,
            ticker=ticker,
            fetched_at=now,
            content_hash=str(hash(str(summary_data))),
            title=f"{ticker} insider activity — {signals['total_transactions']} transactions, signals: {signals['signals']}",
            data=summary_data,
        ))

        # Individual transaction evidence (capped)
        for i, txn in enumerate(all_transactions[:_MAX_TRANSACTION_EVIDENCE]):
            txn_dict = _transaction_as_dict(txn)
            evidence.append(Evidence(
                id=f"form4_{ticker}_txn_{i}",
                source=DataSource.FORM4,
                type=EvidenceType.INSIDER_FILING,
                ticker=ticker,
                fetched_at=now,
                content_hash=str(hash(str(txn_dict))),
                title=(
                    f"{ticker} insider {txn.transaction_type} — "
                    f"{txn.reporting_owner_name or txn.reporting_owner_cik}, "
                    f"{txn.shares:g} shares @ ${txn.price_per_share}"
                ),
                data=txn_dict,
            ))

    except Exception as exc:
        log_debug(
            "FORM4_SUBMISSIONS_FAILED",
            payload={"ticker": ticker, "cik": cik, "error": str(exc)[:200]},
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Failed to fetch EDGAR submissions for {ticker}: {exc}",
        )
        evidence = [Evidence(
            id=f"form4_{ticker}",
            source=DataSource.FORM4,
            type=EvidenceType.INSIDER_FILING,
            ticker=ticker,
            fetched_at=now,
            content_hash=f"form4_{ticker}_error",
            data={
                "form4_count": 0,
                "status": "FETCH_ERROR",
                "note": "Failed to fetch EDGAR submissions data.",
            },
        )]

    return EvidencePacket(
        ticker=ticker, cycle_id=cycle_id, evidence=evidence,
        fetched_at=now, source_count=1,
    )
