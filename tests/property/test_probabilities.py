"""Property-based tests for core probabilistic invariants.

Uses Hypothesis to verify:
- DirectionalProbability sum constraint
- FX round-trip identity
- Freshness immutability
- Conviction verdict thresholds
- Arbitrated probability constraints
"""

from datetime import date, datetime, timezone

import pytest
from hypothesis import given, assume, settings
from hypothesis import strategies as st

from pmacs.schemas.agents import DirectionalProbability, PersonaName
from pmacs.schemas.currency import FxRate, usd_to_eur, eur_to_usd
from pmacs.schemas.conviction import ConvictionResult, VerdictTier
from pmacs.schemas.arbitration import Arbitrated, ArbitrationDecision
from pmacs.schemas.data import EvidencePacket
from pmacs.data.staleness import check_freshness
from pmacs.schemas.freshness import CriticalityLevel


# --- Strategies ---

def valid_prob_triple():
    """Generate (p_up, p_flat, p_down) that sum to ~1.0."""
    return st.tuples(
        st.floats(min_value=0.01, max_value=0.98, allow_nan=False, allow_infinity=False),
        st.floats(min_value=0.01, max_value=0.98, allow_nan=False, allow_infinity=False),
    ).flatmap(lambda pair: st.just(
        (pair[0], pair[1], max(0.01, round(1.0 - pair[0] - pair[1], 7)))
    ))


valid_rate = st.floats(min_value=0.5, max_value=2.0, allow_nan=False, allow_infinity=False)
positive_amount = st.floats(min_value=0.01, max_value=1_000_000.0, allow_nan=False, allow_infinity=False)
score = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


class TestDirectionalProbabilityProperty:
    """DirectionalProbability: sum must be ~1.0, no degenerate distributions."""

    @given(p_up=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
           p_flat=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
           p_down=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=200)
    def test_sum_constraint(self, p_up, p_flat, p_down):
        """For any (p_up, p_flat, p_down): if sum ~= 1.0 and non-degenerate, model accepts."""
        total = p_up + p_flat + p_down
        is_degenerate = (
            (p_up == 1.0 and p_flat == 0.0 and p_down == 0.0) or
            (p_down == 1.0 and p_flat == 0.0 and p_up == 0.0)
        )
        is_valid_sum = abs(total - 1.0) <= 1e-6

        if is_valid_sum and not is_degenerate:
            dp = DirectionalProbability(
                persona=PersonaName.MACRO_REGIME,
                ticker="TEST",
                p_up=p_up, p_flat=p_flat, p_down=p_down,
            )
            assert abs(dp.p_up + dp.p_flat + dp.p_down - 1.0) <= 1e-6
        elif not is_valid_sum:
            with pytest.raises(ValueError, match="sum to ~1.0"):
                DirectionalProbability(
                    persona=PersonaName.MACRO_REGIME,
                    ticker="TEST",
                    p_up=p_up, p_flat=p_flat, p_down=p_down,
                )
        # degenerate cases: ValueError("Degenerate")
        elif is_degenerate:
            with pytest.raises(ValueError, match="Degenerate"):
                DirectionalProbability(
                    persona=PersonaName.MACRO_REGIME,
                    ticker="TEST",
                    p_up=p_up, p_flat=p_flat, p_down=p_down,
                )


class TestFxRoundTrip:
    """usd_to_eur(eur_to_usd(amount, rate), rate) == amount within 1e-6."""

    @given(amount=positive_amount, usd_per_eur=valid_rate)
    @settings(max_examples=200)
    def test_round_trip_identity(self, amount, usd_per_eur):
        """FX round-trip: usd_to_eur(eur_to_usd(x, r), r) == x."""
        rate = FxRate(
            usd_per_eur=usd_per_eur,
            business_date=date(2024, 1, 15),
            fetched_at=datetime.now(timezone.utc),
        )
        converted = usd_to_eur(eur_to_usd(amount, rate), rate)
        assert abs(converted - amount) < 1e-6, f"Round-trip failed: {amount} -> {converted}"


class TestFreshnessImmutability:
    """check_freshness() never mutates the input packet."""

    @given(delta_seconds=st.integers(min_value=0, max_value=1_000_000))
    @settings(max_examples=100)
    def test_fetched_at_unchanged(self, delta_seconds):
        """After check_freshness(), packet.fetched_at is identical."""
        from datetime import timedelta
        fetched = datetime.now(timezone.utc) - timedelta(seconds=delta_seconds)
        packet = EvidencePacket(ticker="TEST", cycle_id="c1", fetched_at=fetched)
        original_ts = packet.fetched_at

        result = check_freshness(
            packet=packet,
            source="polygon",
            criticality=CriticalityLevel.CRITICAL,
            max_age_seconds=300,
        )

        # Result is a FreshnessResult, not None
        assert result is not None
        assert hasattr(result, "source")
        # Packet must be unchanged
        assert packet.fetched_at == original_ts


class TestConvictionVerdictThresholds:
    """score_to_verdict: STRONG_BUY >= 0.6, BUY >= 0.3, SKIP < 0.3."""

    @given(s=score)
    @settings(max_examples=200)
    def test_verdict_tiers(self, s):
        """Any score in [0,1] maps to correct tier."""
        verdict = ConvictionResult.score_to_verdict(s)
        if s >= 0.6:
            assert verdict == VerdictTier.STRONG_BUY
        elif s >= 0.3:
            assert verdict == VerdictTier.BUY
        else:
            assert verdict == VerdictTier.SKIP


class TestArbitratedProbabilities:
    """PROCEED: sum ~= 1.0. ABORT: no constraint enforced."""

    @given(p_up=st.floats(min_value=0.01, max_value=0.98, allow_nan=False, allow_infinity=False),
           p_flat=st.floats(min_value=0.01, max_value=0.98, allow_nan=False, allow_infinity=False))
    @settings(max_examples=100)
    def test_proceed_sums_to_one(self, p_up, p_flat):
        """PROCEED decisions: p_up+p_flat+p_down == 1.0."""
        p_down = round(1.0 - p_up - p_flat, 7)
        assume(p_down >= 0.01)
        a = Arbitrated(
            ticker="TEST", cycle_id="c1",
            p_up=p_up, p_flat=p_flat, p_down=p_down,
            decision=ArbitrationDecision.PROCEED,
        )
        assert abs(a.p_up + a.p_flat + a.p_down - 1.0) <= 1e-6

    @given(p_up=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
           p_flat=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
           p_down=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=100)
    def test_abort_no_sum_constraint(self, p_up, p_flat, p_down):
        """ABORT decisions: no sum constraint enforced by the model."""
        assume(p_up >= 0.0 and p_flat >= 0.0 and p_down >= 0.0)
        assume(p_up <= 1.0 and p_flat <= 1.0 and p_down <= 1.0)
        a = Arbitrated(
            ticker="TEST", cycle_id="c1",
            p_up=p_up, p_flat=p_flat, p_down=p_down,
            decision=ArbitrationDecision.ABORT_DISAGREEMENT,
        )
        # Should accept any valid values without raising
        assert a.decision == ArbitrationDecision.ABORT_DISAGREEMENT
