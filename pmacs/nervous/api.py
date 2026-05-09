"""FastAPI application for pmacs-nervous — SSE, health, session auth (Architecture.md §4.4).

Runs on :8000, loopback only. SSE endpoint with stream filtering,
Last-Event-ID reconnection, session auth via HttpOnly cookie.
Write heartbeat for nervous process on every request.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from pmacs.cortex.health import write_heartbeat
from pmacs.cortex.totp import verify_totp
from pmacs.nervous.auth import SessionManager
from pmacs.nervous.rate_limit import BUCKETS
from pmacs.nervous.sse_publisher import SSEPublisher

# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

VALID_STREAMS = frozenset({
    "cycle", "agent", "decision", "trade", "mutation", "system",
})

_publisher: SSEPublisher = SSEPublisher()
_session_mgr: SessionManager = SessionManager()
_heartbeat_dir: Path = Path("/var/db/pmacs/heartbeat")

app = FastAPI(title="pmacs-nervous", version="0.1.0")


def configure(
    publisher: SSEPublisher | None = None,
    session_manager: SessionManager | None = None,
    heartbeat_dir: Path | None = None,
) -> None:
    """Configure the API with custom instances (for testing).

    Must be called before the app starts serving requests.
    """
    global _publisher, _session_mgr, _heartbeat_dir
    if publisher is not None:
        _publisher = publisher
    if session_manager is not None:
        _session_mgr = session_manager
    if heartbeat_dir is not None:
        _heartbeat_dir = heartbeat_dir


# ---------------------------------------------------------------------------
# Middleware: heartbeat on every request
# ---------------------------------------------------------------------------

@app.middleware("http")
async def heartbeat_middleware(request: Request, call_next):
    write_heartbeat("nervous", heartbeat_dir=_heartbeat_dir)
    response = await call_next(request)
    return response


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# TOTP verification endpoint (Architecture.md §13, §4.4)
# ---------------------------------------------------------------------------

_totp_secret: str = ""


def set_totp_secret(secret: str) -> None:
    """Set the TOTP secret for verification (called at startup)."""
    global _totp_secret
    _totp_secret = secret


class TOTPVerifyRequest(BaseModel):
    """Request body for TOTP verification."""
    model_config = {"from_attributes": True}

    totp_code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    action_id: str = Field(min_length=1)


class TOTPVerifyResponse(BaseModel):
    """Response body for TOTP verification."""
    model_config = {"from_attributes": True}

    verified: bool
    action_id: str
    error: str = ""


@app.post("/api/totp/verify", response_model=TOTPVerifyResponse)
async def totp_verify(body: TOTPVerifyRequest, request: Request) -> TOTPVerifyResponse:
    """Verify a TOTP code for a given action.

    Rate limited to 5 attempts per minute.
    Audit logged on every attempt.
    """
    # Rate limiting (Architecture.md §16.3)
    if not BUCKETS["totp_verify"].acquire():
        return TOTPVerifyResponse(
            verified=False,
            action_id=body.action_id,
            error="Rate limited: too many attempts",
        )

    # Verify TOTP code
    if not _totp_secret:
        return TOTPVerifyResponse(
            verified=False,
            action_id=body.action_id,
            error="TOTP not configured",
        )

    success = verify_totp(_totp_secret, body.totp_code)

    # Audit log (Architecture.md §5.1) — cycle_id is "totp" for non-cycle actions
    try:
        from pmacs.storage.audit import AuditWriter
        audit_path = Path("logs/audit.log")
        writer = AuditWriter(audit_path)
        writer.append(
            "totp_verify_attempt",
            {"action_id": body.action_id, "success": success},
            cycle_id="totp",
        )
        writer.close()
    except Exception:
        pass  # Audit failure must not block the endpoint

    if success:
        return TOTPVerifyResponse(verified=True, action_id=body.action_id)
    return TOTPVerifyResponse(
        verified=False,
        action_id=body.action_id,
        error="Invalid TOTP code",
    )


# ---------------------------------------------------------------------------
# SSE endpoint
# ---------------------------------------------------------------------------

@app.get("/events")
async def events(
    request: Request,
    stream: str | None = None,
) -> StreamingResponse:
    """SSE endpoint with optional stream filtering and reconnection.

    Query params:
        stream: Comma-separated stream filter (e.g. 'cycle,trade').
                If absent, all streams are sent.

    Headers:
        Last-Event-ID: Resume from this event ID.

    Cookie:
        pmacs_session: Session token (set on first connection).
    """
    # Session handling
    session_token = request.cookies.get("pmacs_session")
    if not session_token or not _session_mgr.verify_session(session_token):
        info = _session_mgr.create_session()
        session_token = info.token

    # Parse stream filter
    streams: set[str] | None = None
    if stream:
        streams = {s.strip() for s in stream.split(",") if s.strip() in VALID_STREAMS}
        if not streams:
            streams = None

    # Last-Event-ID for reconnection
    last_id_str = request.headers.get("Last-Event-ID")
    last_id = int(last_id_str) if last_id_str else 0

    # Subscribe to publisher
    client_id, queue = _publisher.subscribe()

    async def event_generator():
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield ": keepalive\n\n"
                    continue

                # Parse and filter
                try:
                    event = json.loads(frame)
                except (json.JSONDecodeError, ValueError):
                    yield f"data: {frame}\n\n"
                    continue

                # Filter by stream
                if streams is not None and event.get("stream") not in streams:
                    continue

                # Filter by Last-Event-ID (skip already-seen events)
                evt_id = event.get("id", "0")
                try:
                    if int(evt_id) <= last_id:
                        continue
                except (ValueError, TypeError):
                    pass

                # Build SSE frame
                data_str = json.dumps(event.get("data", {}), separators=(",", ":"))
                lines = [
                    f"id: {evt_id}",
                    f"event: {event.get('type', 'message')}",
                    f"data: {data_str}",
                ]
                yield "\n".join(lines) + "\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _publisher.unsubscribe(client_id)

    response = StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    response.set_cookie(
        key="pmacs_session",
        value=session_token,
        httponly=True,
        samesite="strict",
        max_age=86400,  # 24h
    )
    return response
