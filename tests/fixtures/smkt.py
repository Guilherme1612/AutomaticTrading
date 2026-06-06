"""SMKT synthetic test fixtures (Architecture.md §19.4).

Deterministic ticker `SMKT` with fake OHLCV, earnings, insider transactions.
Used by wizard smoke-test, pre-merge CI, and post-deploy validation.

All data is deterministic — no randomness, no external calls.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone, timedelta
from typing import Any

from pmacs.schemas.agents import DirectionalProbability, PersonaName
from pmacs.schemas.arbitration import Arbitrated, ArbitrationDecision
from pmacs.schemas.catalysts import Catalyst, CatalystType, CatalystStatus
from pmacs.schemas.data import DataSource, Evidence, EvidenceType, EvidencePacket

SMKT_TICKER: str = "SMKT"
SMKT_CYCLE_ID: str = "smoke-test-001"

# Deterministic base date for all fixture timestamps
_BASE_DATE = datetime(2025, 1, 6, 14, 30, 0, tzinfo=timezone.utc)  # Monday


# ---------------------------------------------------------------------------
# OHLCV
# ---------------------------------------------------------------------------

def make_smkt_ohlcv(days: int = 20) -> list[dict[str, Any]]:
    """Deterministic OHLCV bars for SMKT.

    20 trading days, prices seeded around $50 with predictable volatility.
    ATR ~$2.0 (4% of price) — suitable for pricing engine tests.
    """
    bars: list[dict[str, Any]] = []
    # Fixed open/close sequence producing consistent ATR
    closes = [
        50.00, 50.50, 51.20, 50.80, 51.50,  # week 1: mild uptrend
        52.00, 51.50, 52.30, 52.80, 53.10,  # week 2: continuation
        52.70, 53.40, 53.80, 54.20, 53.90,  # week 3: consolidation
        54.50, 55.00, 54.80, 55.30, 55.60,  # week 4: resume
    ]
    for i, close in enumerate(closes[:days]):
        day = _BASE_DATE + timedelta(days=i)
        high = close + 0.80
        low = close - 0.70
        open_ = close - 0.30
        volume = 1_000_000 + i * 50_000
        bars.append({
            "date": day.strftime("%Y-%m-%d"),
            "open": round(open_, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "volume": volume,
        })
    return bars


# ---------------------------------------------------------------------------
# Earnings
# ---------------------------------------------------------------------------

def make_smkt_earnings() -> dict[str, Any]:
    """Fake earnings data for SMKT.

    Two quarters of deterministic results — used by catalyst detection
    and earnings resolver tests.
    """
    return {
        "ticker": SMKT_TICKER,
        "quarters": [
            {
                "fiscal_date": "2024-09-30",
                "revenue_usd": 1_250_000_000,
                "eps": 1.42,
                "eps_estimate": 1.35,
                "beat": True,
            },
            {
                "fiscal_date": "2024-12-31",
                "revenue_usd": 1_380_000_000,
                "eps": 1.58,
                "eps_estimate": 1.50,
                "beat": True,
            },
        ],
        "next_report_date": "2025-04-15",
    }


# ---------------------------------------------------------------------------
# Insider transactions
# ---------------------------------------------------------------------------

def make_smkt_insider() -> list[dict[str, Any]]:
    """Fake Form 4 insider transactions for SMKT.

    Three officers, mix of purchase and sale — used by insider_activity
    persona and forensics tests.
    """
    return [
        {
            "filing_date": "2025-01-03",
            "officer_name": "Jane Smith",
            "title": "CEO",
            "transaction_type": "Purchase",
            "shares": 5_000,
            "price_usd": 49.80,
            "remaining_holding": 120_000,
        },
        {
            "filing_date": "2025-01-06",
            "officer_name": "Bob Chen",
            "title": "CFO",
            "transaction_type": "Purchase",
            "shares": 2_000,
            "price_usd": 50.10,
            "remaining_holding": 45_000,
        },
        {
            "filing_date": "2025-01-08",
            "officer_name": "Alice Park",
            "title": "CTO",
            "transaction_type": "Sale",
            "shares": 10_000,
            "price_usd": 51.50,
            "remaining_holding": 80_000,
        },
    ]


# ---------------------------------------------------------------------------
# Evidence packet
# ---------------------------------------------------------------------------

def _make_evidence(
    eid: str,
    source: DataSource,
    etype: EvidenceType,
    data: dict[str, Any],
) -> Evidence:
    content_hash = hashlib.sha256(str(data).encode()).hexdigest()[:16]
    return Evidence(
        id=eid,
        source=source,
        type=etype,
        ticker=SMKT_TICKER,
        fetched_at=_BASE_DATE,
        content_hash=content_hash,
        data=data,
    )


def make_smkt_evidence_packet() -> EvidencePacket:
    """Synthetic EvidencePacket covering multiple sources for SMKT.

    Includes financial statements, market data, SEC filings, and insider data.
    """
    evidence = [
        _make_evidence(
            "ev-smkt-001",
            DataSource.POLYGON,
            EvidenceType.MARKET_DATA,
            {"close": 50.50, "volume": 1_050_000, "atr_pct": 0.04},
        ),
        _make_evidence(
            "ev-smkt-002",
            DataSource.EDGAR,
            EvidenceType.SEC_FILING,
            {"form_type": "10-K", "revenue": 1_250_000_000, "net_income": 180_000_000},
        ),
        _make_evidence(
            "ev-smkt-003",
            DataSource.FINNHUB,
            EvidenceType.EARNINGS,
            {"eps_actual": 1.42, "eps_estimate": 1.35, "beat": True},
        ),
        _make_evidence(
            "ev-smkt-004",
            DataSource.FORM4,
            EvidenceType.INSIDER_FILING,
            {"officer": "Jane Smith", "type": "Purchase", "shares": 5_000},
        ),
        _make_evidence(
            "ev-smkt-005",
            DataSource.FUNDAMENTALS,
            EvidenceType.FINANCIAL_STATEMENT,
            {"pe_ratio": 18.5, "debt_to_equity": 0.45, "roe": 0.22},
        ),
    ]
    return EvidencePacket(
        ticker=SMKT_TICKER,
        cycle_id=SMKT_CYCLE_ID,
        evidence=evidence,
        source_count=len({e.source for e in evidence}),
        has_stale_data=False,
    )


# ---------------------------------------------------------------------------
# Directional probability (replaces inline _make_directional pattern)
# ---------------------------------------------------------------------------

def make_smkt_directional(
    persona: PersonaName,
    p_up: float = 0.55,
    p_flat: float = 0.30,
    p_down: float = 0.15,
    confidence: float = 0.7,
    cycle_id: str = SMKT_CYCLE_ID,
) -> DirectionalProbability:
    """Create a synthetic DirectionalProbability for SMKT."""
    return DirectionalProbability(
        persona=persona,
        ticker=SMKT_TICKER,
        p_up=p_up,
        p_flat=p_flat,
        p_down=p_down,
        confidence=confidence,
        reasoning=f"Synthetic {persona.value} signal",
        cycle_id=cycle_id,
    )


# ---------------------------------------------------------------------------
# Arbitrated (replaces inline _make_arbitrated pattern)
# ---------------------------------------------------------------------------

def make_smkt_arbitrated(
    p_up: float = 0.55,
    p_flat: float = 0.30,
    p_down: float = 0.15,
    matured_sources_used: int = 3,
    decision: ArbitrationDecision = ArbitrationDecision.PROCEED,
    cycle_id: str = SMKT_CYCLE_ID,
) -> Arbitrated:
    """Create a synthetic Arbitrated result for SMKT."""
    return Arbitrated(
        ticker=SMKT_TICKER,
        cycle_id=cycle_id,
        p_up=p_up,
        p_flat=p_flat,
        p_down=p_down,
        matured_sources_used=matured_sources_used,
        decision=decision,
    )


# ---------------------------------------------------------------------------
# Catalyst
# ---------------------------------------------------------------------------

def make_smkt_catalyst(
    ctype: CatalystType = CatalystType.EARNINGS_RELEASE,
    status: CatalystStatus = CatalystStatus.PENDING,
    expected_date: date | None = None,
) -> Catalyst:
    """Create a synthetic Catalyst for SMKT."""
    if expected_date is None:
        expected_date = date(2025, 4, 15)
    return Catalyst(
        id="cat-smkt-001",
        ticker=SMKT_TICKER,
        type=ctype,
        status=status,
        expected_date=expected_date,
        description=f"Synthetic {ctype.value} event",
        confidence=0.6,
        detected_at=_BASE_DATE,
    )
