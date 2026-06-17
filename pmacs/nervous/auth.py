"""Session auth for nervous API — single active session, 24h expiry (Architecture.md §4.4).

Session tokens are 256-bit random hex (secrets.token_hex(32)).
Only one active session at a time — new creation invalidates the old.
A valid session is required for write endpoints (single-operator, loopback-only;
no second-factor gate).
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field


SESSION_DURATION_S: float = 24 * 60 * 60  # 24 hours


@dataclass
class _Session:
    """Internal session record."""

    token: str
    created_at: float
    expires_at: float


@dataclass
class SessionInfo:
    """Public session information."""

    token: str
    expires_at: float


class SessionManager:
    """Manages a single active session for the nervous API.

    - One session at a time (new creation invalidates old).
    - 24h expiry from creation.
    - Thread-safe via GIL (single-threaded FastAPI event loop).
    """

    def __init__(self, session_duration_s: float = SESSION_DURATION_S) -> None:
        self._session: _Session | None = None
        self._duration = session_duration_s

    def create_session(self) -> SessionInfo:
        """Generate a new 256-bit session token. Invalidates any existing session.

        Returns:
            SessionInfo with the new token and expiry timestamp.
        """
        token = secrets.token_hex(32)
        now = time.time()
        self._session = _Session(
            token=token,
            created_at=now,
            expires_at=now + self._duration,
        )
        return SessionInfo(token=token, expires_at=self._session.expires_at)

    def verify_session(self, token: str) -> bool:
        """Check if a session token is valid and not expired.

        Args:
            token: The session token to verify.

        Returns:
            True if valid and not expired.
        """
        if self._session is None:
            return False
        if token != self._session.token:
            return False
        if time.time() > self._session.expires_at:
            return False
        return True

    def invalidate_session(self, token: str) -> None:
        """Invalidate a session if it matches the current one."""
        if self._session is not None and token == self._session.token:
            self._session = None

    def verify_write_access(self, session_token: str) -> bool:
        """Verify session for write endpoints.

        Single-operator, loopback-only system — a valid session is sufficient;
        there is no second-factor gate.

        Returns:
            True if session is valid.
        """
        return self.verify_session(session_token)
