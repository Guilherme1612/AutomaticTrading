"""Multi-source corroboration engine -- Tier A/B/C validation (Arch §7.2)."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from pmacs.logsys.debug_log import log_debug
from pmacs.schemas.data import DataSource, Evidence, EvidenceType


class CorroborationTier(str, Enum):
    TIER_A = "TIER_A"  # Primary source -- single sufficient
    TIER_B = "TIER_B"  # Tier 1 press + price consistency
    TIER_C = "TIER_C"  # Lower confidence


class CorroborationResult(BaseModel):
    """Result of multi-source corroboration check."""

    model_config = ConfigDict(frozen=True)

    tier: CorroborationTier
    is_resolved: bool
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence: list[str] = Field(default_factory=list)  # evidence IDs
    price_consistent: bool | None = None
    outlier_flagged: bool = False


# ---------------------------------------------------------------------------
# Source tier classification
# ---------------------------------------------------------------------------

# Tier A sources: primary / official sources
_TIER_A_SOURCES: frozenset[DataSource] = frozenset({
    DataSource.EDGAR,
    DataSource.OPENFDA,
})

# Tier A evidence types: official filings and press releases from company wires
_TIER_A_TYPES: frozenset[EvidenceType] = frozenset({
    EvidenceType.SEC_FILING,
    EvidenceType.PRESS_RELEASE,
})

# Tier 1 press domains for Tier B classification
_TIER_1_PRESS_DOMAINS: frozenset[str] = frozenset({
    "reuters.com",
    "wsj.com",
    "bloomberg.com",
    "ft.com",
    "apnews.com",
    "ap.org",
})

# Tier C sources: secondary / lower confidence
_TIER_C_SOURCES: frozenset[DataSource] = frozenset({
    DataSource.IR_PAGES,
    DataSource.FINRA,
    DataSource.FUNDAMENTALS,
})

# Minimum price movement (%) to count as "consistent with direction"
_PRICE_CONSISTENCY_THRESHOLD_PCT = 2.0

# 3-sigma outlier threshold (%): if price moves opposite by this much relative
# to expected direction, flag as outlier (hold in PENDING for re-corroboration).
# Using a simplified heuristic: 3x the consistency threshold.
_OUTLIER_THRESHOLD_PCT = 6.0


def classify_evidence_tier(evidence: Evidence) -> CorroborationTier:
    """Classify a single evidence item into its corroboration tier.

    Tier A: EDGAR filings, openFDA records, SEC filings, press releases
    Tier B: Tier 1 press (Reuters, WSJ, Bloomberg, FT, AP)
    Tier C: Everything else (IR pages, secondary sources)
    """
    # Tier A: official sources
    if evidence.source in _TIER_A_SOURCES:
        return CorroborationTier.TIER_A

    # Tier A: SEC filing or press release evidence type from any source
    if evidence.type in _TIER_A_TYPES:
        return CorroborationTier.TIER_A

    # Tier B: Tier 1 press coverage
    if evidence.source == DataSource.PRESS:
        url = (evidence.url or "").lower()
        for domain in _TIER_1_PRESS_DOMAINS:
            if domain in url:
                return CorroborationTier.TIER_B
        # PRESS source but not Tier 1 -> Tier C
        return CorroborationTier.TIER_C

    # Tier C: known secondary sources
    if evidence.source in _TIER_C_SOURCES:
        return CorroborationTier.TIER_C

    # Default: Tier C for anything not explicitly Tier A or B
    return CorroborationTier.TIER_C


def _check_price_consistency(
    price_before: float | None,
    price_after: float | None,
    expected_direction: str,
) -> tuple[bool | None, float | None, bool]:
    """Check if price movement is consistent with expected direction.

    Returns:
        (price_consistent, price_change_pct, is_outlier)
        price_consistent: True if move aligns with direction, False if contradicts,
                          None if no price data.
        price_change_pct: Percentage change (positive = up).
        is_outlier: True if 3-sigma contradiction detected.
    """
    if price_before is None or price_after is None or price_before == 0:
        return None, None, False

    change_pct = ((price_after - price_before) / price_before) * 100.0

    if expected_direction == "positive":
        consistent = change_pct >= _PRICE_CONSISTENCY_THRESHOLD_PCT
        is_outlier = change_pct <= -_OUTLIER_THRESHOLD_PCT
    elif expected_direction == "negative":
        consistent = change_pct <= -_PRICE_CONSISTENCY_THRESHOLD_PCT
        is_outlier = change_pct >= _OUTLIER_THRESHOLD_PCT
    else:
        # "neutral" or unknown direction -- no consistency check needed
        consistent = True
        is_outlier = False

    return consistent, change_pct, is_outlier


def corroborate(
    claim: str,
    evidence: list[Evidence],
    price_before: float | None = None,
    price_after: float | None = None,
    expected_direction: str = "positive",
    cycle_id: str = "",
) -> CorroborationResult:
    """Apply multi-source corroboration rules (Architecture.md §7.2).

    Tier A: primary source found -> resolved, high confidence (0.9)
    Tier B: Tier 1 press + price consistent (>2% move in expected direction)
            -> resolved, medium confidence (0.7)
    Tier B + price contradiction (>3sigma): outlier flagged, NOT resolved
    Tier C: secondary sources only -> low confidence (0.3), not resolved alone
    """
    # Classify all evidence
    tier_a_ids: list[str] = []
    tier_b_ids: list[str] = []
    tier_c_ids: list[str] = []

    for ev in evidence:
        tier = classify_evidence_tier(ev)
        if tier == CorroborationTier.TIER_A:
            tier_a_ids.append(ev.id)
        elif tier == CorroborationTier.TIER_B:
            tier_b_ids.append(ev.id)
        else:
            tier_c_ids.append(ev.id)

    # Check price consistency
    price_consistent, price_change_pct, is_outlier = _check_price_consistency(
        price_before, price_after, expected_direction,
    )

    # ---- Tier A: single primary source is sufficient ----
    if tier_a_ids:
        log_debug(
            "CORROBORATION_TIER_A",
            payload={
                "claim": claim[:200],
                "evidence_ids": tier_a_ids,
                "price_change_pct": price_change_pct,
            },
            level="INFO",
            cycle_id=cycle_id or "",
            msg=f"Tier A corroboration: {len(tier_a_ids)} primary source(s) for '{claim[:60]}'",
        )
        return CorroborationResult(
            tier=CorroborationTier.TIER_A,
            is_resolved=True,
            confidence=0.9,
            supporting_evidence=tier_a_ids,
            price_consistent=price_consistent,
            outlier_flagged=False,
        )

    # ---- Tier B: needs price-action consistency ----
    if tier_b_ids:
        # 3-sigma outlier guard: Tier B claim contradicts price action
        if is_outlier:
            log_debug(
                "CORROBORATION_OUTLIER",
                payload={
                    "claim": claim[:200],
                    "evidence_ids": tier_b_ids,
                    "price_change_pct": price_change_pct,
                    "expected_direction": expected_direction,
                },
                level="WARN",
                error_code="CORROBORATION_OUTLIER",
                cycle_id=cycle_id or "",
                msg=f"Tier B outlier: price contradicts claim, holding PENDING for re-corroboration",
            )
            return CorroborationResult(
                tier=CorroborationTier.TIER_B,
                is_resolved=False,
                confidence=0.5,
                supporting_evidence=tier_b_ids,
                price_consistent=False,
                outlier_flagged=True,
            )

        # Tier B + price consistent -> resolved
        if price_consistent is True:
            log_debug(
                "CORROBORATION_TIER_B",
                payload={
                    "claim": claim[:200],
                    "evidence_ids": tier_b_ids,
                    "price_change_pct": price_change_pct,
                },
                level="INFO",
                cycle_id=cycle_id or "",
                msg=f"Tier B corroboration: {len(tier_b_ids)} Tier 1 source(s) + price consistent",
            )
            return CorroborationResult(
                tier=CorroborationTier.TIER_B,
                is_resolved=True,
                confidence=0.7,
                supporting_evidence=tier_b_ids,
                price_consistent=True,
                outlier_flagged=False,
            )

        # Tier B but no price data or price not clearly consistent -> not resolved yet
        log_debug(
            "CORROBORATION_TIER_B_PENDING",
            payload={
                "claim": claim[:200],
                "evidence_ids": tier_b_ids,
                "price_consistent": price_consistent,
                "price_change_pct": price_change_pct,
            },
            level="INFO",
            cycle_id=cycle_id or "",
            msg=f"Tier B found but price not yet consistent, keeping PENDING",
        )
        return CorroborationResult(
            tier=CorroborationTier.TIER_B,
            is_resolved=False,
            confidence=0.5,
            supporting_evidence=tier_b_ids,
            price_consistent=price_consistent,
            outlier_flagged=False,
        )

    # ---- Tier C: not sufficient to resolve alone ----
    if tier_c_ids:
        log_debug(
            "CORROBORATION_TIER_C",
            payload={
                "claim": claim[:200],
                "evidence_ids": tier_c_ids,
            },
            level="INFO",
            cycle_id=cycle_id or "",
            msg=f"Only Tier C evidence found, not sufficient to resolve",
        )
        return CorroborationResult(
            tier=CorroborationTier.TIER_C,
            is_resolved=False,
            confidence=0.3,
            supporting_evidence=tier_c_ids,
            price_consistent=price_consistent,
            outlier_flagged=False,
        )

    # ---- No evidence at all ----
    return CorroborationResult(
        tier=CorroborationTier.TIER_C,
        is_resolved=False,
        confidence=0.0,
        supporting_evidence=[],
        price_consistent=price_consistent,
        outlier_flagged=False,
    )
