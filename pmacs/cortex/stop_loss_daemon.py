"""Stop-loss daemon process -- pmacs-stoploss.

Runs as a daemon during RTH, checking all active positions every 30 minutes.
For each active holding:
1. Fetch current price.
2. Check stop-loss breach via stop_loss_monitor.
3. Check trailing stop via trailing_stop.
4. If breached: write StopTrigger to SQLite and notify nervous.

This is the pmacs-stoploss process from Architecture.md Section 4.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from pmacs.engines.stop_loss_monitor import check_stop_breach, check_trailing_breach
from pmacs.logsys import log_debug
from pmacs.schemas.stop_loss import StopEventStatus, StopTrigger

CHECK_INTERVAL_S = 1800  # 30 minutes during RTH
RTH_START = "09:30"  # US Eastern
RTH_END = "16:00"  # US Eastern
HEARTBEAT_FILENAME = "pmacs-stoploss.json"


def is_rth() -> bool:
    """Check if currently in Regular Trading Hours (US Eastern).

    Returns:
        True if current time is within RTH on a weekday (Mon-Fri),
        US Eastern time.
    """
    from datetime import datetime

    import pytz

    eastern = pytz.timezone("US/Eastern")
    now = datetime.now(eastern)
    if now.weekday() >= 5:  # weekend
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


def _write_stop_trigger(conn: sqlite3.Connection, result, stop_type_category: str, cycle_id: str) -> None:
    """Write a StopTrigger to SQLite stop_events table."""
    now = datetime.now(timezone.utc).isoformat()
    trigger = StopTrigger(
        id=f"stop-{result.holding_id}-{now}",
        holding_id=result.holding_id,
        ticker=result.ticker,
        stop_type="TRAILING_STOP" if result.stop_type == "TRAILING_STOP" else "FIXED_STOP",
        trigger_price_usd=result.current_price,
        stop_price_usd=result.stop_price,
        current_price_usd=result.current_price,
        gap_down=result.is_gap_down,
        cycle_id=cycle_id,
        detected_at=now,
        status=StopEventStatus.PENDING,
        stop_type_category=stop_type_category,
    )
    conn.execute(
        """INSERT INTO stop_events
           (holding_id, ticker, stop_type, trigger_price_usd, stop_price_usd,
            detected_at, cycle_id, status, stop_type_category, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            trigger.holding_id,
            trigger.ticker,
            trigger.stop_type.value if hasattr(trigger.stop_type, "value") else trigger.stop_type,
            trigger.trigger_price_usd,
            trigger.stop_price_usd,
            trigger.detected_at,
            trigger.cycle_id,
            trigger.status.value,
            trigger.stop_type_category,
            now,
        ),
    )
    conn.commit()


def check_holding(
    holding,
    current_price: float,
    market_state: str = "RTH",
) -> tuple | None:
    """Check a single holding for stop breaches.

    Trailing stop takes priority when armed (both fixed and trailing breached).

    Args:
        holding: Holding model with stop_price_usd, trailing_stop_price_usd, etc.
        current_price: Latest market price.
        market_state: "RTH" or other.

    Returns:
        Tuple of (StopCheckResult, stop_type_category) or None if no breach.
    """
    # Check trailing first -- takes priority when armed
    trailing_result = check_trailing_breach(holding, current_price, market_state)
    if trailing_result is not None:
        return trailing_result, "TRAILING"

    # Check fixed stop
    fixed_result = check_stop_breach(holding, current_price, market_state)
    if fixed_result is not None:
        return fixed_result, "FIXED"

    return None


def _fetch_current_price(ticker: str, gateway=None) -> float | None:
    """Fetch current price for a ticker.

    Tries Finnhub first (via data gateway), falls back to Alpaca data API,
    then to a SQLite-stored last-known price.
    """
    # Strategy 1: Finnhub via data gateway
    if gateway is not None:
        try:
            from pmacs.data.sources.finnhub import fetch_quote
            from pmacs.storage.keychain import read_key
            api_key = read_key("pmacs.finnhub.api_key") or ""
            if api_key:
                packet = fetch_quote(ticker, gateway, api_key)
                if packet.evidence and packet.evidence[0].data:
                    data = packet.evidence[0].data
                    price = data.get("c")
                    if price and price > 0:
                        return float(price)
        except Exception:
            pass  # Fall through to next strategy

    # Strategy 2: Alpaca data API (for paper trading)
    try:
        from pmacs.storage.keychain import read_key
        api_key = read_key("pmacs.alpaca.paper_key")
        secret = read_key("pmacs.alpaca.paper_secret")
        if api_key and secret:
            import httpx
            resp = httpx.get(
                f"https://data.alpaca.markets/v2/stocks/{ticker}/quotes/latest",
                headers={
                    "APCA-API-KEY-ID": api_key,
                    "APCA-API-SECRET-KEY": secret,
                },
                params={"feed": "iex"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                body = resp.json()
                quote = body.get("quote", {})
                price = quote.get("ap") or quote.get("bp")
                if price and price > 0:
                    return float(price)
    except Exception:
        pass  # Fall through to last-known price

    return None


def _write_heartbeat(heartbeat_dir: Path, cycle_id: str) -> None:
    """Write process heartbeat file for cortex daemon health monitoring."""
    heartbeat_path = heartbeat_dir / HEARTBEAT_FILENAME
    heartbeat_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "process": "pmacs-stoploss",
        "ts": datetime.now(timezone.utc).isoformat(),
        "cycle_id": cycle_id,
        "status": "alive",
    }
    heartbeat_path.write_text(json.dumps(payload))


def run_stop_loss_loop(
    db_path: Path,
    heartbeat_dir: Path,
    check_interval: int = CHECK_INTERVAL_S,
    gateway=None,
) -> None:
    """Main loop for pmacs-stoploss process.

    During RTH, checks all active positions for stop breaches and
    trailing stop triggers. Writes heartbeat to heartbeat_dir each cycle.

    Args:
        db_path: Path to the SQLite database.
        heartbeat_dir: Directory for process heartbeat files.
        check_interval: Seconds between checks (default 1800 = 30 min).
        gateway: Optional DataGateway for price fetching.
    """
    cycle_counter = 0

    while True:
        if is_rth():
            cycle_counter += 1
            cycle_id = f"sl-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{cycle_counter}"
            breached_count = 0

            try:
                conn = sqlite3.connect(str(db_path))
                try:
                    rows = conn.execute(
                        "SELECT id, ticker, state, stop_price_usd, trailing_stop_price_usd "
                        "FROM holdings WHERE state = 'ACTIVE'"
                    ).fetchall()

                    for row in rows:
                        holding_id, ticker, state, stop_price, trailing_price = row

                        # Fetch current price
                        current_price = _fetch_current_price(ticker, gateway)

                        if current_price is None:
                            # Last resort: use last-known price from SQLite
                            try:
                                lk_row = conn.execute(
                                    "SELECT last_price_usd FROM holdings WHERE id = ?",
                                    (holding_id,),
                                ).fetchone()
                                if lk_row and lk_row[0]:
                                    current_price = float(lk_row[0])
                            except Exception:
                                pass

                        if current_price is None:
                            log_debug(
                                "STOP_LOSS_NO_PRICE",
                                payload={"ticker": ticker, "holding_id": holding_id},
                                level="WARN",
                                error_code="PRICE_UNAVAILABLE",
                                cycle_id=cycle_id,
                                msg=f"Cannot fetch price for {ticker}, skipping stop check",
                            )
                            continue

                        # Build a minimal holding-like object for checks
                        class _HoldingProxy:
                            pass
                        h = _HoldingProxy()
                        h.id = holding_id
                        h.ticker = ticker
                        h.stop_price_usd = stop_price
                        h.trailing_stop_price_usd = trailing_price
                        h.trailing_stop_armed = trailing_price is not None

                        result = check_holding(h, current_price)
                        if result is not None:
                            stop_result, stop_type_category = result
                            _write_stop_trigger(conn, stop_result, stop_type_category, cycle_id)
                            breached_count += 1
                            log_debug(
                                "STOP_BREACH_DETECTED",
                                payload={
                                    "holding_id": holding_id,
                                    "ticker": ticker,
                                    "stop_type": stop_type_category,
                                    "current_price": current_price,
                                    "stop_price": stop_result.stop_price,
                                },
                                level="WARN",
                                error_code="STOP_BREACH_DETECTED",
                                cycle_id=cycle_id,
                                msg=f"Stop breach: {ticker} {stop_type_category} at ${current_price:.2f}",
                            )

                    # Write heartbeat each cycle
                    _write_heartbeat(heartbeat_dir, cycle_id)

                    log_debug(
                        "STOP_LOSS_CYCLE_COMPLETE",
                        payload={
                            "holdings_checked": len(rows),
                            "breaches": breached_count,
                        },
                        level="INFO",
                        cycle_id=cycle_id,
                        msg=f"Stop-loss cycle: {len(rows)} holdings checked, {breached_count} breaches",
                    )
                finally:
                    conn.close()

            except Exception as exc:
                log_debug(
                    "STOP_LOSS_DAEMON_ERROR",
                    payload={"error": str(exc)},
                    level="WARN",
                    error_code="SQLITE_WRITE_FAILED",
                    cycle_id=cycle_id,
                    msg=f"Stop-loss daemon cycle error: {exc}",
                )
        time.sleep(check_interval)
