"""Clock / NTP drift monitor (Architecture.md §13.1 trigger #7).

Compares system clock against time.google.com via NTP-like check.
If network unavailable (pf-blocked inference), skips silently.
>60s drift triggers kill switch.
"""
from __future__ import annotations

import socket
import struct
import time

from pmacs.logsys import log_debug

_NTP_PORT = 123
_NTP_HOST = "time.google.com"
_DRIFT_THRESHOLD_S = 60.0
_NTP_TIMEOUT_S = 5.0


def _get_ntp_time(host: str = _NTP_HOST, timeout: float = _NTP_TIMEOUT_S) -> float | None:
    """Query an NTP server for current time.

    Args:
        host: NTP server hostname.
        timeout: Socket timeout in seconds.

    Returns:
        NTP time as Unix timestamp, or None if query failed.
    """
    # NTP packet: 48 bytes, first byte = 0x1B (LI=0, VN=3, Mode=3=client)
    ntp_data = b"\x1b" + 47 * b"\0"

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(ntp_data, (host, _NTP_PORT))
            response, _ = sock.recvfrom(1024)

        if len(response) < 48:
            return None

        # Transmit timestamp is in bytes 40-47
        # First 4 bytes = seconds since NTP epoch (Jan 1 1900)
        # Next 4 bytes = fractional seconds
        tx_seconds = struct.unpack("!I", response[40:44])[0]
        tx_fraction = struct.unpack("!I", response[44:48])[0]

        # NTP epoch offset: 70 years + 17 leap days = 2208988800 seconds
        ntp_epoch_offset = 2208988800
        ntp_time = tx_seconds + tx_fraction / (2**32) - ntp_epoch_offset
        return ntp_time

    except (socket.timeout, socket.gaierror, OSError, ConnectionRefusedError):
        return None


def check_ntp_drift(
    host: str = _NTP_HOST,
    threshold: float = _DRIFT_THRESHOLD_S,
) -> tuple[bool, float | None]:
    """Check system clock drift against NTP server.

    Args:
        host: NTP server hostname.
        threshold: Drift threshold in seconds (default 60).

    Returns:
        Tuple of (is_triggered, drift_seconds).
        drift_seconds is None if NTP check was skipped/failed.
    """
    ntp_time = _get_ntp_time(host)

    if ntp_time is None:
        # No network or NTP unavailable — skip silently
        log_debug(
            "NTP_CHECK_SKIPPED",
            payload={"host": host},
            level="DEBUG",
            msg="NTP check skipped: no response from server",
        )
        return (False, None)

    system_time = time.time()
    drift = abs(system_time - ntp_time)
    is_triggered = drift > threshold

    if is_triggered:
        log_debug(
            "CLOCK_DRIFT_DETECTED",
            payload={"drift_s": round(drift, 1), "threshold_s": threshold},
            level="WARN",
            error_code="CLOCK_DRIFT_DETECTED",
            msg=f"Clock drift {drift:.1f}s exceeds threshold {threshold}s",
        )

    return (is_triggered, drift)
