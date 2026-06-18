"""FDA decision catalyst resolver (Arch §7.1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pmacs.data.resolution.corroboration import corroborate
from pmacs.logsys.debug_log import log_debug
from pmacs.schemas.catalysts import Catalyst, CatalystStatus
from pmacs.schemas.data import DataSource, Evidence, EvidenceType
from pmacs.schemas.resolution import ResolutionResult


def _find_fda_records(evidence: list[Evidence]) -> list[Evidence]:
    """Find openFDA decision records (Tier A)."""
    results: list[Evidence] = []
    for ev in evidence:
        if ev.source == DataSource.OPENFDA:
            data = ev.data or {}
            # Check for FDA decision indicators
            ev_type = (data.get("type", "") or "").lower()
            description = (ev.title or "").lower()
            text = f"{ev_type} {description}"
            if any(kw in text for kw in (
                "approval", "approved", "complete response letter",
                " rejection", "denied", " Advisory Committee",
                "PDUFA", "action date", "designam",
            )):
                results.append(ev)
    return results


def _find_fda_press(evidence: list[Evidence]) -> list[Evidence]:
    """Find Tier 1 press coverage of FDA decisions (Tier B)."""
    results: list[Evidence] = []
    for ev in evidence:
        if ev.source == DataSource.PRESS and ev.type in (
            EvidenceType.NEWS,
            EvidenceType.REGULATORY,
        ):
            title = (ev.title or "").lower()
            description = (ev.data or {}).get("description", "").lower()
            text = f"{title} {description}"
            if any(kw in text for kw in (
                "fda", "food and drug", "drug approval", "drug rejected",
                "clinical trial", "pdufa", " advisory committee",
            )):
                results.append(ev)
    return results


def _infer_direction(evidence_items: list[Evidence]) -> str:
    """Infer expected direction from FDA evidence content.

    Returns "positive", "negative", or "neutral".
    """
    for ev in evidence_items:
        text = f"{ev.title or ''} {(ev.data or {}).get('description', '')}".lower()
        # Positive signals (approval)
        if any(kw in text for kw in (
            "approved", "approves", "approval", "green light", "cleared",
            "breakthrough therapy", "fast track",
        )):
            return "positive"
        # Negative signals (rejection, CRL)
        if any(kw in text for kw in (
            "rejected", "denied", "complete response letter",
            "crl", "not approved", "refused",
        )):
            return "negative"
    return "neutral"


def resolve_fda(
    catalyst: Catalyst,
    evidence: list[Evidence],
    price_before: float | None = None,
    price_after: float | None = None,
    cycle_id: str = "",
) -> ResolutionResult | None:
    """Check if an FDA decision catalyst has resolved.

    Checks openFDA evidence for decision records (Tier A).
    Checks press for FDA announcement coverage (Tier B).
    Price action check for drug approval/rejection impact.
    """
    # Only resolve PENDING or CONFIRMED catalysts
    if catalyst.status not in (CatalystStatus.PENDING, CatalystStatus.CONFIRMED):
        return None

    # ---- Collect relevant evidence FIRST ----
    # Check evidence before timeout: if we have corroborating evidence, resolve
    # even if past the 48h window. Timeout only applies when NO evidence found.
    fda_records = _find_fda_records(evidence)
    fda_press = _find_fda_press(evidence)
    all_fda_evidence = fda_records + fda_press

    # ---- Timeout check: 48h past expected_date, only if no evidence ----
    if not all_fda_evidence and catalyst.expected_date is not None:
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
                "CATALYST_TIMEOUT_FDA",
                payload={
                    "catalyst_id": catalyst.id,
                    "ticker": catalyst.ticker,
                    "expected_date": str(catalyst.expected_date),
                },
                level="WARN",
                error_code="CATALYST_TIMEOUT",
                cycle_id=cycle_id,
                msg=f"FDA catalyst {catalyst.id} timed out (48h past expected date)",
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
                summary=f"FDA catalyst timed out: 48h past expected date {catalyst.expected_date}",
                data={"timeout": True, "expected_date": str(catalyst.expected_date)},
            )

    if not all_fda_evidence:
        return None

    # Determine expected direction from evidence
    expected_direction = _infer_direction(all_fda_evidence)

    # Run corroboration
    claim = f"FDA decision for {catalyst.ticker}"
    corr = corroborate(
        claim=claim,
        evidence=all_fda_evidence,
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
        "FDA_RESOLVED",
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
        msg=f"FDA resolved: {catalyst.ticker} -> {new_status.value} via {corr.tier.value}",
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
        summary=f"FDA {expected_direction}: {corr.tier.value} corroboration, confidence={corr.confidence:.1f}",
    )
