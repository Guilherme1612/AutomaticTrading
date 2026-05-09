"""TOTP verify endpoint tests — POST /api/totp/verify (Architecture.md §13, §4.4)."""

import json
import time

import pytest
from fastapi.testclient import TestClient
from pathlib import Path

from pmacs.cortex.totp import compute_totp, generate_totp_secret
from pmacs.nervous.api import app, configure, set_totp_secret
from pmacs.nervous.auth import SessionManager
from pmacs.nervous.rate_limit import BUCKETS, TokenBucket
from pmacs.nervous.sse_publisher import SSEPublisher


@pytest.fixture(autouse=True)
def _setup(tmp_path):
    """Configure app with fresh instances for each test."""
    publisher = SSEPublisher()
    session_mgr = SessionManager()
    hb_dir = tmp_path / "heartbeat"
    configure(publisher=publisher, session_manager=session_mgr, heartbeat_dir=hb_dir)
    secret = generate_totp_secret()
    set_totp_secret(secret)
    # Reset rate limiter for each test
    BUCKETS["totp_verify"] = TokenBucket(rate=5, period=60.0)
    yield


class TestTOTPVerify:
    """Tests for POST /api/totp/verify."""

    def test_valid_totp_returns_verified(self):
        """Valid TOTP code returns verified=true."""
        from pmacs.nervous import api as api_mod

        code = compute_totp(api_mod._totp_secret)
        with TestClient(app) as client:
            response = client.post(
                "/api/totp/verify",
                json={"totp_code": code, "action_id": "test-action"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["verified"] is True
            assert data["action_id"] == "test-action"

    def test_invalid_totp_returns_unverified(self):
        """Invalid TOTP code returns verified=false with error."""
        with TestClient(app) as client:
            response = client.post(
                "/api/totp/verify",
                json={"totp_code": "000000", "action_id": "test-action"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["verified"] is False
            assert "Invalid TOTP" in data["error"]

    def test_invalid_code_format_rejected(self):
        """Non-numeric or wrong-length codes are rejected by Pydantic validation."""
        with TestClient(app) as client:
            response = client.post(
                "/api/totp/verify",
                json={"totp_code": "abc", "action_id": "test-action"},
            )
            assert response.status_code == 422  # Validation error

    def test_empty_action_id_rejected(self):
        """Empty action_id is rejected by Pydantic validation."""
        from pmacs.nervous import api as api_mod

        code = compute_totp(api_mod._totp_secret)
        with TestClient(app) as client:
            response = client.post(
                "/api/totp/verify",
                json={"totp_code": code, "action_id": ""},
            )
            assert response.status_code == 422

    def test_rate_limiting_after_5_attempts(self):
        """After 5 attempts in 1 minute, subsequent attempts are rate limited."""
        with TestClient(app) as client:
            for i in range(5):
                response = client.post(
                    "/api/totp/verify",
                    json={"totp_code": "000000", "action_id": f"attempt-{i}"},
                )
                assert response.status_code == 200

            # 6th attempt should be rate limited
            response = client.post(
                "/api/totp/verify",
                json={"totp_code": "000000", "action_id": "attempt-6"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["verified"] is False
            assert "Rate limited" in data["error"]

    def test_no_secret_returns_error(self):
        """When TOTP secret is not configured, returns error."""
        set_totp_secret("")
        with TestClient(app) as client:
            response = client.post(
                "/api/totp/verify",
                json={"totp_code": "123456", "action_id": "test-action"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["verified"] is False
            assert "not configured" in data["error"]


class TestRateLimiter:
    """Tests for the TokenBucket rate limiter."""

    def test_allows_up_to_rate(self):
        """Token bucket allows up to rate requests."""
        bucket = TokenBucket(rate=3, period=60.0)
        assert bucket.acquire() is True
        assert bucket.acquire() is True
        assert bucket.acquire() is True
        assert bucket.acquire() is False

    def test_refills_over_time(self):
        """Tokens refill as time passes."""
        bucket = TokenBucket(rate=2, period=1.0)
        assert bucket.acquire() is True
        assert bucket.acquire() is True
        assert bucket.acquire() is False
        # Wait for refill
        time.sleep(0.6)
        assert bucket.acquire() is True
