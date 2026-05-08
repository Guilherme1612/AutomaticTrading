"""FX tests — Phase 2 exit test #2."""

from datetime import date, datetime, timezone

from pmacs.schemas.currency import FxRate, usd_to_eur, eur_to_usd
from pmacs.data.fx import is_rate_stale


def _make_rate(usd_per_eur: float = 1.08, business_date: date = date(2024, 1, 15)) -> FxRate:
    return FxRate(
        usd_per_eur=usd_per_eur,
        business_date=business_date,
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


class TestEcbStaleness:
    """Test ECB rate staleness logic — weekend/holiday handling."""

    def test_friday_rate_fresh_on_saturday(self):
        """A Friday rate checked on Saturday should be fresh (ECB doesn't publish weekends)."""
        friday = date(2024, 1, 12)  # Friday
        saturday = date(2024, 1, 13)
        rate = _make_rate(business_date=friday)
        assert not is_rate_stale(rate, now=saturday)

    def test_friday_rate_fresh_on_sunday(self):
        """A Friday rate checked on Sunday should be fresh."""
        friday = date(2024, 1, 12)
        sunday = date(2024, 1, 14)
        rate = _make_rate(business_date=friday)
        assert not is_rate_stale(rate, now=sunday)

    def test_monday_rate_stale_on_wednesday(self):
        """A Monday rate checked on Wednesday should be stale (Tuesday publication expected)."""
        monday = date(2024, 1, 8)
        wednesday = date(2024, 1, 10)
        rate = _make_rate(business_date=monday)
        assert is_rate_stale(rate, now=wednesday)

    def test_business_day_is_not_stale(self):
        """A rate from today (business day) should not be stale."""
        # Use a known business day: Wednesday Jan 10, 2024
        wednesday = date(2024, 1, 10)
        rate = _make_rate(business_date=wednesday)
        assert not is_rate_stale(rate, now=wednesday)
