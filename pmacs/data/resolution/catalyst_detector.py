"""Catalyst resolution detector -- scans evidence for resolution signals (Arch §7.1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pmacs.logsys.debug_log import log_debug
from pmacs.schemas.catalysts import Catalyst, CatalystStatus, CatalystType
from pmacs.schemas.data import DataSource, Evidence, EvidenceType
from pmacs.schemas.resolution import ResolutionResult


def _resolve_generic(
    catalyst: Catalyst,
    evidence: list[Evidence],
    price_before: float | None,
    price_after: float | None,
    cycle_id: str = "",
) -> ResolutionResult | None:
    """Generic resolver for catalyst types without specialized resolvers.

    Uses corroboration engine directly with keyword-based evidence matching.
    Applies to: PRODUCT_LAUNCH, REGULATORY_RULING, MA_CLOSE,
                 PARTNERSHIP_ANNOUNCEMENT, GUIDANCE_UPDATE.
    """
    # Only resolve PENDING or CONFIRMED catalysts
    if catalyst.status not in (CatalystStatus.PENDING, CatalystStatus.CONFIRMED):
        return None

    # ---- Collect relevant evidence FIRST ----
    # Check evidence before timeout: if we have corroborating evidence, resolve
    # even if past the 48h window. Timeout only applies when NO evidence found.
    type_keywords: dict[CatalystType, list[str]] = {
        CatalystType.PRODUCT_LAUNCH: [
            "launch", "released", "shipping", "available",
            "unveiled", "announced product",
        ],
        CatalystType.REGULATORY_RULING: [
            "ruling", "regulation", "sec", "antitrust", "fcc",
            "ftc", "doj", "settlement", "fine", "penalty",
        ],
        CatalystType.MA_CLOSE: [
            "merger", "acquisition", "deal closed", "transaction",
            "tender offer", "buyout", "acquired",
        ],
        CatalystType.PARTNERSHIP_ANNOUNCEMENT: [
            "partnership", "joint venture", "collaboration",
            "agreement", "loi", "memorandum of understanding",
        ],
        CatalystType.GUIDANCE_UPDATE: [
            "guidance", "outlook", "forecast", "raised outlook",
            "lowered outlook", "forward-looking", "revised guidance",
        ],
    }

    keywords = type_keywords.get(catalyst.type, [])
    relevant_evidence: list[Evidence] = []

    for ev in evidence:
        # Check title and description for type-specific keywords
        text = f"{ev.title or ''} {(ev.data or {}).get('description', '')}".lower()
        # Also check against catalyst description
        catalyst_text = catalyst.description.lower()

        if any(kw in text for kw in keywords):
            relevant_evidence.append(ev)
        elif catalyst.description and any(kw in text for kw in catalyst_text.split()):
            # Fuzzy: evidence mentions something from the catalyst description
            relevant_evidence.append(ev)

    # ---- Timeout check: 48h past expected_date, only if no evidence ----
    if not relevant_evidence and catalyst.expected_date is not None:
        now = datetime.now(timezone.utc)
        expected_resolution = datetime(
            catalyst.expected_date.year,
            catalyst.expected_date.month,
            catalyst.expected_date.day,
            22, 0, 0,
            tzinfo=timezone.utc,
        )
        if now > expected_resolution + timedelta(hours=48):
            log_debug(
                "CATALYST_TIMEOUT_GENERIC",
                payload={
                    "catalyst_id": catalyst.id,
                    "ticker": catalyst.ticker,
                    "catalyst_type": catalyst.type.value,
                    "expected_date": str(catalyst.expected_date),
                },
                level="WARN",
                error_code="CATALYST_TIMEOUT",
                cycle_id=cycle_id,
                msg=f"{catalyst.type.value} catalyst {catalyst.id} timed out",
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
                summary=f"{catalyst.type.value} timed out: 48h past {catalyst.expected_date}",
                data={"timeout": True, "expected_date": str(catalyst.expected_date)},
            )

    if not relevant_evidence:
        return None

    # Determine expected direction from evidence
    expected_direction = _infer_direction_generic(relevant_evidence)

    # Run corroboration
    from pmacs.data.resolution.corroboration import corroborate

    claim = f"{catalyst.type.value} for {catalyst.ticker}"
    corr = corroborate(
        claim=claim,
        evidence=relevant_evidence,
        price_before=price_before,
        price_after=price_after,
        expected_direction=expected_direction,
        cycle_id=cycle_id,
    )

    if not corr.is_resolved:
        return None

    # Determine new status
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
        "CATALYST_RESOLVED_GENERIC",
        payload={
            "catalyst_id": catalyst.id,
            "ticker": catalyst.ticker,
            "catalyst_type": catalyst.type.value,
            "new_status": new_status.value,
            "tier": corr.tier.value,
            "confidence": corr.confidence,
            "price_change_pct": price_change_pct,
        },
        level="INFO",
        cycle_id=cycle_id,
        msg=f"{catalyst.type.value} resolved: {catalyst.ticker} -> {new_status.value}",
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
        summary=f"{catalyst.type.value} {expected_direction}: {corr.tier.value}, confidence={corr.confidence:.1f}",
    )


def _infer_direction_generic(evidence_items: list[Evidence]) -> str:
    """Infer direction from generic evidence content."""
    positive_kw = (
        "approved", "approval", "beat", "raised", "surged", "launch",
        "closed deal", "partnership", "collaboration", "positive",
        "better than expected", "record", "growth",
    )
    negative_kw = (
        "rejected", "denied", "miss", "lowered", "declined", "cancelled",
        "abandoned", "fine", "penalty", "negative", "worse than expected",
        "disappointing", "failed",
    )

    for ev in evidence_items:
        text = f"{ev.title or ''} {(ev.data or {}).get('description', '')}".lower()
        if any(kw in text for kw in positive_kw):
            return "positive"
        if any(kw in text for kw in negative_kw):
            return "negative"
    return "neutral"


def detect_resolutions(
    ticker: str,
    pending_catalysts: list[Catalyst],
    evidence: list[Evidence],
    price_before: float | None = None,
    price_after: float | None = None,
    cycle_id: str = "",
) -> list[ResolutionResult]:
    """Check each pending catalyst against available evidence (Arch §7.1).

    Routes each catalyst to its type-specific resolver, collects resolution
    results, and returns catalysts that have resolved.

    Args:
        ticker: Ticker symbol to check.
        pending_catalysts: Catalysts in PENDING/CONFIRMED status for this ticker.
        evidence: Evidence items from the data layer.
        price_before: Price before expected resolution window.
        price_after: Price after expected resolution window.
        cycle_id: Current cycle ID for logging.

    Returns:
        List of ResolutionResult for catalysts that resolved.
    """
    if not pending_catalysts:
        return []

    results: list[ResolutionResult] = []

    # Filter evidence to this ticker
    ticker_evidence = [e for e in evidence if e.ticker == ticker]

    for catalyst in pending_catalysts:
        if catalyst.ticker != ticker:
            continue

        try:
            result = _resolve_catalyst(
                catalyst, ticker_evidence, price_before, price_after, cycle_id,
            )
            if result is not None:
                results.append(result)
        except Exception as exc:
            # Never crash the cycle if resolution fails for one catalyst
            log_debug(
                "CATALYST_RESOLUTION_ERROR",
                payload={
                    "catalyst_id": catalyst.id,
                    "ticker": catalyst.ticker,
                    "catalyst_type": catalyst.type.value,
                    "error": str(exc),
                },
                level="WARN",
                error_code="CATALYST_RESOLUTION_ERROR",
                cycle_id=cycle_id,
                msg=f"Catalyst resolution failed for {catalyst.id}: {exc}",
            )

    return results


def _resolve_catalyst(
    catalyst: Catalyst,
    evidence: list[Evidence],
    price_before: float | None,
    price_after: float | None,
    cycle_id: str,
) -> ResolutionResult | None:
    """Route catalyst to the appropriate type-specific resolver."""
    if catalyst.type == CatalystType.EARNINGS_RELEASE:
        from pmacs.data.resolution.earnings_resolver import resolve_earnings
        return resolve_earnings(catalyst, evidence, price_before, price_after, cycle_id)

    if catalyst.type == CatalystType.FDA_DECISION:
        from pmacs.data.resolution.fda_resolver import resolve_fda
        return resolve_fda(catalyst, evidence, price_before, price_after, cycle_id)

    # All other types use the generic resolver
    return _resolve_generic(
        catalyst, evidence, price_before, price_after, cycle_id,
    )
