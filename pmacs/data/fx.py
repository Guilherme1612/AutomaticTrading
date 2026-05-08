"""FX handler — ECB EUR/USD with usd_per_eur convention (Architecture.md §16.8).

Staleness handling:
- ECB does not publish on weekends or TARGET holidays.
- A rate from Friday is considered fresh through Sunday.
- `is_rate_stale()` uses ECB business day calendar for staleness checks.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx

from pmacs.schemas.currency import FxRate, FxSnapshot


# ECB reference rate URL
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

# Known ECB/TARGET holidays (approximate — ECB follows Eurosystem calendar).
# These are fixed-date holidays; Easter-based holidays vary by year and are
# computed dynamically in _is_ecb_business_day().
_ECB_FIXED_HOLIDAYS: frozenset[tuple[int, int]] = frozenset({
    (1, 1),    # New Year's Day
    (5, 1),    # Labour Day
    (12, 25),  # Christmas Day
    (12, 26),  # Boxing Day / St Stephen's Day
})


def _easter_date(year: int) -> date:
    """Compute Easter Sunday using Anonymous Gregorian algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = ((h + l_ - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _is_ecb_business_day(d: date) -> bool:
    """Check if a date is an ECB business day.

    Returns False for weekends and known ECB/TARGET holidays.
    """
    # Weekend check
    if d.weekday() >= 5:  # Saturday=5, Sunday=6
        return False

    # Fixed holidays
    if (d.month, d.day) in _ECB_FIXED_HOLIDAYS:
        return False

    # Easter-based holidays (vary by year)
    easter = _easter_date(d.year)
    good_friday = easter.replace(day=easter.day - 2) if easter.day > 2 else None
    if good_friday is None:
        # Handle Easter in early March — compute properly
        from datetime import timedelta
        good_friday = easter - __import__("datetime").timedelta(days=2)
    easter_monday = easter + __import__("datetime").timedelta(days=1)

    if d == good_friday or d == easter_monday:
        return False

    return True


def _last_ecb_business_day(d: date) -> date:
    """Walk backward from d until finding an ECB business day."""
    while not _is_ecb_business_day(d):
        from datetime import timedelta
        d = d - timedelta(days=1)
    return d


def is_rate_stale(rate: FxRate, now: date | None = None) -> bool:
    """Check if an FX rate is stale relative to the ECB publication schedule.

    A rate is stale if its business_date is older than the most recent
    ECB publication date. Weekend/holiday gaps are handled: a Friday rate
    is considered fresh through Sunday.

    Args:
        rate: The FxRate to check.
        now: Current date (defaults to date.today()).

    Returns:
        True if the rate is stale, False if fresh.
    """
    if now is None:
        now = date.today()

    expected_business_date = _last_ecb_business_day(now)

    if rate.business_date < expected_business_date:
        return True  # Rate is older than most recent publication

    return False  # Rate matches expected publication date


def fetch_ecb_rate() -> FxRate:
    """Fetch the latest EUR/USD rate from ECB.

    Returns FxRate with usd_per_eur convention (ECB standard).
    Business date is the ECB publication date.

    Note: ECB does not publish on weekends or TARGET holidays.
    The returned rate's business_date reflects the actual publication date,
    which may be a Friday for weekend queries. Use is_rate_stale() to
    check if a cached rate is still valid.
    """
    try:
        response = httpx.get(ECB_URL, timeout=30.0, follow_redirects=True)
        response.raise_for_status()

        # Parse XML to find USD rate
        import xml.etree.ElementTree as ET
        root = ET.fromstring(response.text)

        # ECB format: <Cube><Cube time="2024-01-15"><Cube currency="USD" rate="1.08"/></Cube></Cube>
        usd_per_eur = None
        business_date = None

        for cube in root.iter():
            if cube.get("currency") == "USD":
                usd_per_eur = float(cube.get("rate"))
            if cube.get("time"):
                parts = cube.get("time").split("-")
                business_date = date(int(parts[0]), int(parts[1]), int(parts[2]))

        if usd_per_eur is None:
            raise ValueError("USD rate not found in ECB response")
        if business_date is None:
            business_date = date.today()

        return FxRate(
            usd_per_eur=usd_per_eur,
            business_date=business_date,
            fetched_at=datetime.now(timezone.utc),
        )
    except Exception as e:
        raise RuntimeError(f"Failed to fetch ECB rate: {e}") from e
