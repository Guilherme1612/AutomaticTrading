"""pytest fixtures for SMKT synthetic data (Architecture.md §19.4).

Session-scoped fixtures wrapping tests/fixtures/smkt.py factory functions.
"""

from __future__ import annotations

import pytest

from pmacs.schemas.agents import PersonaName
from pmacs.schemas.arbitration import ArbitrationDecision

from .smkt import (
    SMKT_TICKER,
    make_smkt_arbitrated,
    make_smkt_catalyst,
    make_smkt_directional,
    make_smkt_earnings,
    make_smkt_evidence_packet,
    make_smkt_insider,
    make_smkt_ohlcv,
)


@pytest.fixture(scope="session")
def smkt_ticker() -> str:
    """SMKT ticker constant."""
    return SMKT_TICKER


@pytest.fixture(scope="session")
def smkt_ohlcv():
    """20 days of deterministic OHLCV bars for SMKT."""
    return make_smkt_ohlcv()


@pytest.fixture(scope="session")
def smkt_earnings():
    """Fake earnings data for SMKT."""
    return make_smkt_earnings()


@pytest.fixture(scope="session")
def smkt_insider():
    """Fake Form 4 insider transactions for SMKT."""
    return make_smkt_insider()


@pytest.fixture(scope="session")
def smkt_evidence():
    """Synthetic EvidencePacket with multi-source SMKT evidence."""
    return make_smkt_evidence_packet()


@pytest.fixture(scope="session")
def smkt_directional():
    """Default bullish DirectionalProbability for SMKT (gatekeeper persona)."""
    return make_smkt_directional(PersonaName.GATEKEEPER)


@pytest.fixture(scope="session")
def smkt_arbitrated():
    """Default bullish Arbitrated result for SMKT."""
    return make_smkt_arbitrated(
        p_up=0.55,
        p_flat=0.30,
        p_down=0.15,
        matured_sources_used=3,
        decision=ArbitrationDecision.PROCEED,
    )


@pytest.fixture(scope="session")
def smkt_catalyst():
    """Pending earnings catalyst for SMKT."""
    return make_smkt_catalyst()
