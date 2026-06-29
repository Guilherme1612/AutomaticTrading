"""Press releases source (IMPORTANT).

Fetches company news from Finnhub, prioritises high-signal items,
and extracts structured catalyst events for agent consumption.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pmacs.data.gateway import DataGateway
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket, EvidenceType

# Keywords indicating high-signal news worth prioritising
_HIGH_SIGNAL_KEYWORDS = (
    "acqui", "merger", "partner", "deal", "agreement", "contract",
    "guidance", "raise", "lower", "beat", "miss", "revenue", "earning",
    "invest", "fund", "billion", "million", "launch", "expan",
    "hyperscaler", "compute", "gpu", "datacenter", "data center",
    "microsoft", "amazon", "google", "meta", "aws", "azure",
    "record", "quarter", "annual", "forecast", "outlook", "target",
    # Additional high-signal keywords for deals, team, and strategic events
    "backlog", "order book", "committed", "capacity", "reservation",
    "fda", "approval", "phase", "clinical", "trial",
    "buyback", "repurchase", "dividend", "split",
    "ipo", "spin-off", "spinoff", "restructur",
    "sec", "sec.gov", "8-k", "material", "definitive",
    "offshor", "tariff", "regulation", "regulatory",
    "founder", "ceo", "cfo", "executive", "management",
    "patent", "intellectual", "proprietary",
    "customer", "tenant", "utilization", "deploy",
    "nebius", "yandex", "coreweave",
    # Growth and forward-looking metrics
    "gw ", "gigawatt", "megawatt", "power capacity", "pipeline",
    "backlog", "bookings", "annual recurring", "arr", "mrr",
    "guide", "raised guidance", "lowered guidance", "outperform",
)

# Catalyst categorization rules: (category, keywords)
_CATALYST_CATEGORIES = [
    ("M&A", ("acqui", "merger", "buyout", "takeover", "buying", "acquired")),
    ("PARTNERSHIP", ("partner", "deal", "agreement", "contract", "joint venture")),
    ("GUIDANCE", ("guidance", "raise", "lower", "forecast", "outlook", "target", "reaffirm", "guide")),
    ("EARNINGS", ("earnings", "quarterly", "beat", "miss", "surprise")),
    ("PRODUCT_LAUNCH", ("launch", "release", "announce", "unveil", "shipp")),
    ("REGULATORY", ("fda", "approval", "phase", "clinical", "sec", "regulat")),
    ("FINANCING", ("fund", "invest", "capital", "debt", "equity", "offering", "convertible")),
    ("BUYBACK", ("buyback", "repurchase", "dividend")),
    ("HYPERSCALER_DEAL", ("hyperscaler", "microsoft", "amazon", "google", "meta", "aws", "azure", "coreweave")),
    ("CAPACITY_EXPANSION", ("datacenter", "data center", "capacity", "gpu", "compute", "expand", "gigawatt", "megawatt", " gw ")),
    ("MANAGEMENT", ("ceo", "cfo", "cto", "founder", "executive", "appoint", "resign", "hire")),
    ("BACKLOG_BOOKINGS", ("backlog", "bookings", "annual recurring", "arr ", "mrr", "pipeline", "reserved")),
]


def _categorize_item(item: dict) -> str:
    """Assign a catalyst category to a news item based on keyword matching."""
    text = (item.get("headline", "") + " " + item.get("summary", "")).lower()
    for category, keywords in _CATALYST_CATEGORIES:
        if any(kw in text for kw in keywords):
            return category
    return "GENERAL"


def _extract_catalyst_events(
    items: list[dict],
    ticker: str,
    now: datetime,
) -> Evidence | None:
    """Build a structured catalyst timeline from scored news items.

    Groups items by category, dates them, and produces a ranked timeline
    of catalyst events with dates and brief descriptions.
    """
    if not items:
        return None

    events: list[dict] = []
    for item in items:
        category = _categorize_item(item)
        if category == "GENERAL":
            continue  # Only include categorized catalysts

        headline = item.get("headline", "")
        published = ""
        dt = item.get("datetime")
        if dt:
            try:
                published = datetime.fromtimestamp(dt, tz=timezone.utc).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                pass

        events.append({
            "date": published,
            "category": category,
            "headline": headline,
            "signal_score": _score_item(item),
        })

    if not events:
        return None

    # Sort by date (newest first), then by signal score
    events.sort(key=lambda x: (x.get("date", ""), x.get("signal_score", 0)), reverse=True)

    # Summary counts by category
    category_counts: dict[str, int] = {}
    for e in events:
        category_counts[e["category"]] = category_counts.get(e["category"], 0) + 1

    cat_summary = ", ".join(f"{cat}: {cnt}" for cat, cnt in sorted(category_counts.items(), key=lambda x: -x[1]))

    catalyst_data = {
        "total_catalysts": len(events),
        "category_summary": cat_summary,
        "events": events[:15],  # Cap at 15 most significant
    }

    return Evidence(
        id=f"press_{ticker}_catalyst_timeline",
        source=DataSource.PRESS,
        type=EvidenceType.PRESS_RELEASE,
        ticker=ticker,
        fetched_at=now,
        content_hash=str(hash(str(catalyst_data))),
        title=f"{ticker} catalyst timeline — {len(events)} events ({cat_summary})",
        data=catalyst_data,
    )


def _score_item(item: dict) -> int:
    """Return priority score for a news item (higher = more relevant)."""
    text = (item.get("headline", "") + " " + item.get("summary", "")).lower()
    return sum(1 for kw in _HIGH_SIGNAL_KEYWORDS if kw in text)


def fetch_press_releases(ticker: str, gateway: DataGateway, cycle_id: str = "") -> EvidencePacket:
    """Fetch company news via Finnhub, prioritising high-signal items.

    Fetches the last 365 days (catches strategic deals, hyperscaler commitments,
    guidance updates, and M&A that may have occurred up to a year ago but are still
    thesis-relevant). Returns up to 20 items ranked by signal keyword density.
    """
    url = "https://finnhub.io/api/v1/company-news"
    evidence = []
    try:
        from datetime import date, timedelta
        today = date.today()
        one_year_ago = today - timedelta(days=365)
        response = gateway.fetch(
            "press", url,
            params={"symbol": ticker, "from": str(one_year_ago), "to": str(today)},
        )
        raw_items = response.json() if response and response.status_code == 200 else []
        if not isinstance(raw_items, list):
            raw_items = []

        # Sort by signal score (desc) then recency (desc) — keep top 20
        scored = sorted(raw_items, key=lambda x: (_score_item(x), x.get("datetime", 0)), reverse=True)
        top_items = scored[:20]

        for item in top_items:
            # Build a structured data dict — agents see clean fields, not raw API noise
            structured = {
                "headline": item.get("headline", ""),
                "summary": (item.get("summary", "") or "")[:400],
                "category": item.get("category", ""),
                "source_name": item.get("source", ""),
                "url": item.get("url", ""),
                "published_utc": datetime.fromtimestamp(
                    item.get("datetime", 0), tz=timezone.utc
                ).strftime("%Y-%m-%d") if item.get("datetime") else "",
                "signal_score": _score_item(item),
            }
            evidence.append(Evidence(
                id=f"press_{ticker}_{item.get('id', hash(item.get('headline','')))}",
                source=DataSource.PRESS,
                type=EvidenceType.PRESS_RELEASE,
                ticker=ticker,
                fetched_at=datetime.now(timezone.utc),
                content_hash=str(hash(str(structured))),
                title=structured["headline"][:120] if structured["headline"] else f"{ticker} news",
                data=structured,
            ))

        # ── Extract structured catalyst timeline ────────────────────────
        catalyst_ev = _extract_catalyst_events(top_items, ticker, datetime.now(timezone.utc))
        if catalyst_ev:
            evidence.append(catalyst_ev)

    except Exception:
        pass
    return EvidencePacket(
        ticker=ticker, cycle_id=cycle_id, evidence=evidence,
        fetched_at=datetime.now(timezone.utc), source_count=1,
    )
