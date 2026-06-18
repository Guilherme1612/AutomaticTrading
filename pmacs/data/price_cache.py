"""Price cache -- latest price per ticker with staleness budget (Architecture.md §6.1).

Caches the latest close price per ticker. Uses Polygon as primary source,
Alpaca data as fallback. Thread-safe via threading.Lock.

Spec ref: Architecture.md §6.1, §9.4
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from pmacs.data.gateway import DataGateway
from pmacs.logsys import log_debug


class PriceCache:
    """Caches latest prices with configurable staleness tolerance.

    Args:
        gateway: DataGateway instance for rate-limited HTTP fetching.
        max_age_seconds: Maximum age of cached price before re-fetch (default 300s).
    """

    def __init__(self, gateway: DataGateway, max_age_seconds: int = 300) -> None:
        self._gateway = gateway
        self._max_age = max_age_seconds
        self._cache: dict[str, tuple[float, float]] = {}  # ticker -> (price, timestamp)
        self._lock = threading.Lock()
        self._polygon_key: str | None = None
        self._alpaca_key: str | None = None
        self._finnhub_key: str | None = None
        self._keys_loaded = False

    def _load_keys(self) -> None:
        """Lazily load API keys from macOS Keychain (once).

        Uses the same dotted naming convention as evidence_router.py
        so keys stored via the wizard are found correctly.
        """
        if self._keys_loaded:
            return
        try:
            from pmacs.storage.keychain import read_key

            self._polygon_key = read_key("pmacs.polygon.api_key")
            self._alpaca_key = read_key("pmacs.alpaca.paper_key")
            self._finnhub_key = read_key("pmacs.finnhub.api_key")
        except Exception:
            pass
        self._keys_loaded = True

    def get_price(self, ticker: str, cycle_id: str = "") -> float | None:
        """Get cached price or fetch fresh. Returns latest close price or None.

        Args:
            ticker: Stock ticker symbol.
            cycle_id: Cycle ID for debug logging.

        Returns:
            Latest close price as float, or None if both sources fail.
        """
        with self._lock:
            self._load_keys()

            cached = self._cache.get(ticker)
            if cached is not None:
                price, ts = cached
                age = time.monotonic() - ts
                if age < self._max_age:
                    log_debug(
                        "PRICE_CACHE_HIT",
                        payload={
                            "ticker": ticker,
                            "price": price,
                            "age_seconds": round(age, 1),
                            "cycle_id": cycle_id,
                        },
                        level="DEBUG",
                        cycle_id=cycle_id,
                        msg=f"Price cache hit for {ticker}: ${price:.2f} (age {age:.0f}s)",
                    )
                    return price

            # Cache miss or stale -- fetch fresh (3-source strategy matching evidence_router)
            price = self._fetch_polygon_price(ticker, cycle_id)

            if price is None:
                price = self._fetch_finnhub_price(ticker, cycle_id)

            if price is None:
                price = self._fetch_alpaca_price(ticker, cycle_id)

            if price is not None:
                self._cache[ticker] = (price, time.monotonic())
                log_debug(
                    "PRICE_FETCHED",
                    payload={
                        "ticker": ticker,
                        "price": price,
                        "cycle_id": cycle_id,
                    },
                    level="INFO",
                    cycle_id=cycle_id,
                    msg=f"Fetched price for {ticker}: ${price:.2f}",
                )
            else:
                log_debug(
                    "PRICE_UNAVAILABLE",
                    payload={"ticker": ticker, "cycle_id": cycle_id},
                    level="WARN",
                    error_code="DATA_UNAVAILABLE",
                    cycle_id=cycle_id,
                    msg=f"All price sources failed for {ticker}",
                )

            return price

    def _fetch_polygon_price(self, ticker: str, cycle_id: str) -> float | None:
        """Fetch latest close from Polygon daily bars.

        Args:
            ticker: Stock ticker symbol.
            cycle_id: Cycle ID for debug logging.

        Returns:
            Latest close price, or None on failure.
        """
        if not self._polygon_key:
            return None
        try:
            from pmacs.data.sources.polygon import fetch_daily_bars

            packet = fetch_daily_bars(ticker, self._gateway, self._polygon_key, cycle_id)
            if packet.evidence:
                # Evidence is sorted chronologically; last item has latest close
                for ev in reversed(packet.evidence):
                    close = ev.data.get("close")
                    if close is not None and float(close) > 0:
                        return float(close)
        except Exception as exc:
            log_debug(
                "PRICE_POLYGON_FAILED",
                payload={"ticker": ticker, "error": str(exc), "cycle_id": cycle_id},
                level="WARN",
                error_code="DATA_SOURCE_FAILED",
                cycle_id=cycle_id,
                msg=f"Polygon price fetch failed for {ticker}: {exc}",
            )
        return None

    def _fetch_finnhub_price(self, ticker: str, cycle_id: str) -> float | None:
        """Fetch latest price from Finnhub quote.

        Args:
            ticker: Stock ticker symbol.
            cycle_id: Cycle ID for debug logging.

        Returns:
            Latest price, or None on failure.
        """
        if not self._finnhub_key:
            return None
        try:
            from pmacs.data.sources.finnhub import fetch_quote

            packet = fetch_quote(ticker, self._gateway, self._finnhub_key, cycle_id)
            if packet.evidence and packet.evidence[0].data:
                price = packet.evidence[0].data.get("c")
                if price is not None and float(price) > 0:
                    return float(price)
        except Exception as exc:
            log_debug(
                "PRICE_FINNHUB_FAILED",
                payload={"ticker": ticker, "error": str(exc), "cycle_id": cycle_id},
                level="WARN",
                error_code="DATA_SOURCE_FAILED",
                cycle_id=cycle_id,
                msg=f"Finnhub price fetch failed for {ticker}: {exc}",
            )
        return None

    def _fetch_alpaca_price(self, ticker: str, cycle_id: str) -> float | None:
        """Fetch latest close from Alpaca data bars.

        Args:
            ticker: Stock ticker symbol.
            cycle_id: Cycle ID for debug logging.

        Returns:
            Latest close price, or None on failure.
        """
        if not self._alpaca_key:
            return None
        try:
            from pmacs.data.sources.alpaca_data import fetch_bars

            packet = fetch_bars(ticker, self._gateway, self._alpaca_key, cycle_id)
            if packet.evidence:
                # Alpaca bars: last bar has latest close
                for ev in reversed(packet.evidence):
                    close = ev.data.get("c")
                    if close is None:
                        close = ev.data.get("close")
                    if close is not None and float(close) > 0:
                        return float(close)
        except Exception as exc:
            log_debug(
                "PRICE_ALPACA_FAILED",
                payload={"ticker": ticker, "error": str(exc), "cycle_id": cycle_id},
                level="WARN",
                error_code="DATA_SOURCE_FAILED",
                cycle_id=cycle_id,
                msg=f"Alpaca price fetch failed for {ticker}: {exc}",
            )
        return None
