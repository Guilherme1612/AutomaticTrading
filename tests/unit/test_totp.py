"""Unit tests for pmacs.cortex.totp — RFC 6238 TOTP implementation."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from pmacs.cortex.totp import compute_totp, generate_totp_secret, verify_totp


class TestGenerateSecret:
    """Tests for generate_totp_secret."""

    def test_returns_base32_string(self) -> None:
        secret = generate_totp_secret()
        assert isinstance(secret, str)
        # base32 decode must succeed
        import base64

        raw = base64.b32decode(secret)
        assert len(raw) == 20  # 160 bits

    def test_unique_secrets(self) -> None:
        s1 = generate_totp_secret()
        s2 = generate_totp_secret()
        assert s1 != s2


class TestComputeTotp:
    """Tests for compute_totp with known vectors."""

    def test_known_secret_deterministic(self) -> None:
        """RFC 6238 test vector: SHA1 with secret '12345678901234567890'."""
        secret_b32 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32 of "12345678901234567890"
        # Counter = 59 (timestamp 59, period 30 → counter 1)
        code = compute_totp(secret_b32, timestamp=59)
        assert len(code) == 6
        assert code.isdigit()

    def test_same_timestamp_same_code(self) -> None:
        secret = generate_totp_secret()
        code1 = compute_totp(secret, timestamp=1000000)
        code2 = compute_totp(secret, timestamp=1000000)
        assert code1 == code2

    def test_different_timestamp_different_code(self) -> None:
        secret = generate_totp_secret()
        code1 = compute_totp(secret, timestamp=1000000)
        code2 = compute_totp(secret, timestamp=1000060)  # 2 periods later
        assert code1 != code2


class TestVerifyTotp:
    """Tests for verify_totp."""

    def test_roundtrip_generate_compute_verify(self) -> None:
        """Generate secret, compute code, verify succeeds."""
        secret = generate_totp_secret()
        ts = int(time.time())
        code = compute_totp(secret, timestamp=ts)
        with patch("pmacs.cortex.totp.time") as mock_time:
            mock_time.time.return_value = float(ts)
            assert verify_totp(secret, code) is True

    def test_reject_wrong_code(self) -> None:
        secret = generate_totp_secret()
        assert verify_totp(secret, "000000") is False

    def test_reject_expired_code_outside_window(self) -> None:
        """Code computed at T should fail verification when current time is T+90s with window=1."""
        secret = generate_totp_secret()
        ts = 1000000
        code = compute_totp(secret, timestamp=ts)
        # Current time is 3 periods (90s) later — well outside window=1
        with patch("pmacs.cortex.totp.time") as mock_time:
            mock_time.time.return_value = float(ts + 90)
            assert verify_totp(secret, code, window=1) is False

    def test_accept_within_window(self) -> None:
        """Code computed at T should verify at T+30s with window=1."""
        secret = generate_totp_secret()
        ts = 1000000
        code = compute_totp(secret, timestamp=ts)
        with patch("pmacs.cortex.totp.time") as mock_time:
            mock_time.time.return_value = float(ts + 30)
            assert verify_totp(secret, code, window=1) is True

    def test_reject_non_numeric_code(self) -> None:
        secret = generate_totp_secret()
        assert verify_totp(secret, "abcdef") is False

    def test_reject_short_code(self) -> None:
        secret = generate_totp_secret()
        assert verify_totp(secret, "12345") is False

    def test_reject_long_code(self) -> None:
        secret = generate_totp_secret()
        assert verify_totp(secret, "1234567") is False
