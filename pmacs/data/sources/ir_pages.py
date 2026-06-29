"""Investor relations pages source (IMPORTANT)."""
from __future__ import annotations
from datetime import datetime, timezone
from pmacs.data.gateway import DataGateway
from pmacs.data.sources._html import strip_html as _strip_html
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket, EvidenceType


def fetch_ir_page(ticker: str, url: str, gateway: DataGateway, cycle_id: str = "") -> EvidencePacket:
    """Fetch and extract content from IR pages.

    Extracts plain text from HTML so agents can read guidance, press releases,
    financial highlights, and other material disclosures on the IR page.
    Returns up to 4000 chars of the most relevant content.
    """
    content = ""
    plain_text = ""
    try:
        response = gateway.fetch("ir_pages", url)
        if response and response.status_code == 200:
            raw_html = response.text[:30000]  # cap raw HTML before stripping
            plain_text = _strip_html(raw_html)[:4000]
            content = plain_text
    except Exception:
        pass
    evidence = [Evidence(
        id=f"ir_{ticker}",
        source=DataSource.IR_PAGES,
        type=EvidenceType.CORPORATE_EVENT,
        ticker=ticker,
        fetched_at=datetime.now(timezone.utc),
        content_hash=str(hash(content)),
        title=f"{ticker} IR page — {len(plain_text)} chars extracted",
        data={"url": url, "content": plain_text, "content_length": len(plain_text)},
    )]
    return EvidencePacket(ticker=ticker, cycle_id=cycle_id, evidence=evidence, fetched_at=datetime.now(timezone.utc), source_count=1)
