"""FX tests — Phase 2 exit test #2."""

from datetime import date, datetime, timezone

from pmacs.schemas.currency import FxRate, usd_to_eur, eur_to_usd


def _make_rate(usd_per_eur: float = 1.08) -> FxRate:
    return FxRate(
        usd_per_eur=usd_per_eur,
        business_date=date(2024, 1, 15),
        fetched_at=datetime.now(timezone.utc),
    )


class TestFx:
    def test_round_trip(self):
        """usd_to_eur(eur_to_usd(100, snap), snap) ≈ 100"""
        rate = _make_rate(1.08)
        original = 100.0
        converted = eur_to_usd(usd_to_eur(original, rate), rate)
        assert abs(converted - original) < 1e-6

    def test_usd_to_eur(self):
        rate = _make_rate(1.08)
        result = usd_to_eur(108.0, rate)
        assert abs(result - 100.0) < 1e-6

    def test_eur_to_usd(self):
        rate = _make_rate(1.08)
        result = eur_to_usd(100.0, rate)
        assert abs(result - 108.0) < 1e-6

    def test_eur_per_usd_property(self):
        rate = _make_rate(1.08)
        assert abs(rate.eur_per_usd - 1 / 1.08) < 1e-6

    def test_zero_amount(self):
        rate = _make_rate(1.08)
        assert usd_to_eur(0.0, rate) == 0.0
        assert eur_to_usd(0.0, rate) == 0.0
