"""TOTP implementation per RFC 6238. 30s period, 6 digits, SHA-1."""
from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time


def generate_totp_secret() -> str:
    """Generate a new random TOTP secret (base32 encoded)."""
    import secrets

    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def compute_totp(
    secret: str, timestamp: int | None = None, period: int = 30, digits: int = 6
) -> str:
    """Compute TOTP code for given secret and timestamp."""
    if timestamp is None:
        timestamp = int(time.time())

    key = base64.b32decode(secret)
    counter = timestamp // period
    counter_bytes = struct.pack(">Q", counter)

    h = hmac.new(key, counter_bytes, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = struct.unpack(">I", h[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    """Verify TOTP code within ±window periods. Returns True if valid."""
    if len(code) != 6 or not code.isdigit():
        return False

    now = int(time.time())
    period = 30
    for offset in range(-window, window + 1):
        expected = compute_totp(secret, now + offset * period)
        if hmac.compare_digest(code, expected):
            return True
    return False
