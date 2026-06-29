"""Earnings catalyst resolver -- detects earnings release outcomes (Arch §7.1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pmacs.data.resolution.corroboration import corroborate
from pmacs.logsys.debug_log import log_debug
from pmacs.schemas.catalysts import Catalyst, CatalystStatus
from pmacs.schemas.data import DataSource, Evidence, EvidenceType
from pmacs.schemas.resolution import ResolutionResult


def _find_earnings_filings(evidence: list[Evidence]) -> list[Evidence]:
    """Find SEC 8-K filings with earnings results (Tier A)."""
    results: list[Evidence] = []
    for ev in evidence:
        if ev.source == DataSource.EDGAR and ev.type == EvidenceType.SEC_FILING:
            data = ev.data or {}
            form_type = data.get("form_type", "").upper()
            # 8-K filings with earnings results
            if form_type in ("8-K", "10-Q", "10-K"):
                # Check if the filing mentions earnings
                items = data.get("items", [])
                description = (ev.title or "").lower()
                if (
                    "earnings" in description
                    or "results of operations" in description
                    or any("earnings" in str(i).lower() for i in items)
                    or any("results of operations" in str(i).lower() for i in items)
                ):
                    results.append(ev)
    return results


def _find_earnings_press(evidence: list[Evidence]) -> list[Evidence]:
    """Find Tier 1 press coverage of earnings results (Tier B)."""
    results: list[Evidence] = []
    for ev in evidence:
        if ev.source == DataSource.PRESS and ev.type in (
            EvidenceType.NEWS,
            EvidenceType.EARNINGS,
        ):
            title = (ev.title or "").lower()
            description = (ev.data or {}).get("description", "").lower()
            text = f"{title} {description}"
            if any(kw in text for kw in ("earnings", "quarterly results", "eps", "revenue miss", "beat expectations")):
                results.append(ev)
    return results


def _infer_direction(evidence_items: list[Evidence]) -> str:
    """Infer expected direction from earnings evidence content.

    Returns "positive", "negative", or "neutral".
    """
    for ev in evidence_items:
        text = f"{ev.title or ''} {(ev.data or {}).get('description', '')}".lower()
        # Positive signals
        if any(kw in text for kw in (
            "beat", "above expectations", "raised guidance", "record revenue",
            "surged", "better than expected",
        )):
            return "positive"
        # Negative signals
        if any(kw in text for kw in (
            "miss", "below expectations", "lowered guidance", "declined",
            "worse than expected", "disappointing",
        )):
            return "negative"
    return "neutral"


def resolve_earnings(
    catalyst: Catalyst,
    evidence: list[Evidence],
    price_before: float | None = None,
    price_after: float | None = None,
    cycle_id: str = "",
) -> ResolutionResult | None:
    """Check if an earnings catalyst has resolved.

    Checks for:
    - SEC 8-K filing with earnings results (Tier A)
    - Tier 1 press coverage of earnings (Tier B)
    - Price action consistent with earnings surprise
    - Timeout: 48h past expected_date with no resolution
    """
    # Only resolve PENDING or CONFIRMED catalysts
    if catalyst.status not in (CatalystStatus.PENDING, CatalystStatus.CONFIRMED):
        return None

    # ---- Collect relevant evidence FIRST ----
    # Check evidence before timeout: if we have corroborating evidence, resolve
    # even if past the 48h window. Timeout only applies when NO evidence found.
    earnings_filings = _find_earnings_filings(evidence)
    earnings_press = _find_earnings_press(evidence)
    all_earnings_evidence = earnings_filings + earnings_press

    # ---- Timeout check: 48h past expected_date, only if no evidence ----
    if not all_earnings_evidence and catalyst.expected_date is not None:
        now = datetime.now(timezone.utc)
        expected_resolution = datetime(
            catalyst.expected_date.year,
            catalyst.expected_date.month,
            catalyst.expected_date.day,
            22, 0, 0,  # Assume market close + buffer (22:00 UTC = 6pm ET)
            tzinfo=timezone.utc,
        )
        if now > expected_resolution + timedelta(hours=48):
            log_debug(
                "CATALYST_TIMEOUT_EARNINGS",
                payload={
                    "catalyst_id": catalyst.id,
                    "ticker": catalyst.ticker,
                    "expected_date": str(catalyst.expected_date),
                },
                level="WARN",
                error_code="CATALYST_TIMEOUT",
                cycle_id=cycle_id,
                msg=f"Earnings catalyst {catalyst.id} timed out (48h past expected date)",
            )
            return ResolutionResult(
                catalyst_id=catalyst.id,
                ticker=catalyst.ticker,
                catalyst_type=catalyst.type,
                old_status=catalyst.status,
                new_status=CatalystStatus.RESOLVED_NEUTRAL,
                corroboration_tier="TIMEOUT",
                confidence=0.1,
                supporting_evidence_ids=[],
                summary=f"Earnings catalyst timed out: 48h past expected date {catalyst.expected_date}",
                data={"timeout": True, "expected_date": str(catalyst.expected_date)},
            )

    if not all_earnings_evidence:
        return None

    # Determine expected direction from evidence
    expected_direction = _infer_direction(all_earnings_evidence)

    # Run corroboration
    claim = f"Earnings release for {catalyst.ticker}"
    corr = corroborate(
        claim=claim,
        evidence=all_earnings_evidence,
        price_before=price_before,
        price_after=price_after,
        expected_direction=expected_direction,
        cycle_id=cycle_id,
    )

    if not corr.is_resolved:
        return None

    # Determine new status based on direction and corroboration
    price_change_pct = None
    if price_before is not None and price_after is not None and price_before != 0:
        price_change_pct = ((price_after - price_before) / price_before) * 100.0

    if expected_direction == "positive":
        new_status = CatalystStatus.RESOLVED_POSITIVE
    elif expected_direction == "negative":
        new_status = CatalystStatus.RESOLVED_NEGATIVE
    else:
        new_status = CatalystStatus.RESOLVED_NEUTRAL

    log_debug(
        "EARNINGS_RESOLVED",
        payload={
            "catalyst_id": catalyst.id,
            "ticker": catalyst.ticker,
            "new_status": new_status.value,
            "tier": corr.tier.value,
            "confidence": corr.confidence,
            "price_change_pct": price_change_pct,
            "evidence_count": len(corr.supporting_evidence),
        },
        level="INFO",
        cycle_id=cycle_id,
        msg=f"Earnings resolved: {catalyst.ticker} -> {new_status.value} via {corr.tier.value}",
    )

    return ResolutionResult(
        catalyst_id=catalyst.id,
        ticker=catalyst.ticker,
        catalyst_type=catalyst.type,
        old_status=catalyst.status,
        new_status=new_status,
        corroboration_tier=corr.tier.value,
        confidence=corr.confidence,
        supporting_evidence_ids=corr.supporting_evidence,
        price_change_pct=price_change_pct,
        price_consistent=corr.price_consistent,
        summary=f"Earnings {expected_direction}: {corr.tier.value} corroboration, confidence={corr.confidence:.1f}",
    )
