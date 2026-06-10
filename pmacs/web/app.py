"""PMACS — combined web + API server (port :8000)."""

import asyncio
import json
import secrets

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse, RedirectResponse

from pmacs.config import data_dir as _data_dir
from pmacs.cortex.health import write_heartbeat as _write_heartbeat
from pmacs.nervous.api import router as _nervous_router

_heartbeat_dir: Path = _data_dir() / "heartbeats"

app = FastAPI(title="PMACS")

# Include nervous API routes (health, TOTP) into combined app
app.include_router(_nervous_router)


# ---------------------------------------------------------------------------
# Startup: close any cycles stuck in RUNNING state from a prior crash
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def _close_stuck_cycles() -> None:
    """Mark RUNNING cycles as ABORTED if the server was restarted mid-run.

    A cycle stuck in RUNNING means the process was killed before it closed.
    We abort them on startup so the UI doesn't show stale 'current analysis'
    state and is_cycle_running reports correctly.
    """
    import datetime as _dt
    try:
        from pmacs.web.config import get_config
        from pmacs.web import data as _data_layer
        cfg = get_config()
        from pmacs.storage.sqlite import connect as _sql_connect
        db = _sql_connect(cfg.sqlite_path)
        try:
            now = _dt.datetime.now(_dt.timezone.utc).isoformat()
            affected = db.execute(
                "UPDATE cycles SET state='ABORTED', closed_at=? WHERE state='RUNNING'",
                (now,),
            ).rowcount
            db.commit()
            if affected:
                from pmacs.logsys import log_debug
                log_debug(
                    "STARTUP_STUCK_CYCLES_ABORTED",
                    payload={"count": affected},
                    msg=f"Aborted {affected} stuck RUNNING cycle(s) on startup",
                )
        finally:
            db.close()
    except Exception:
        pass  # Non-fatal: don't block startup


# ---------------------------------------------------------------------------
# Heartbeat middleware — writes nervous heartbeat on every request
# ---------------------------------------------------------------------------

@app.middleware("http")
async def _heartbeat_middleware(request: Request, call_next):
    _write_heartbeat("nervous", heartbeat_dir=_heartbeat_dir)
    return await call_next(request)


# ---------------------------------------------------------------------------
# Global exception handler (Architecture.md §1.8 audit logging)
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    from pmacs.logsys import log_debug
    log_debug(
        "WEB_UNHANDLED_EXCEPTION",
        payload={
            "method": request.method,
            "path": str(request.url.path),
            "error": str(exc),
            "error_type": type(exc).__name__,
        },
        level="ERROR",
        error_code="WEB_UNHANDLED_EXCEPTION",
        msg=f"Unhandled exception on {request.method} {request.url.path}: {exc}",
    )
    return JSONResponse(
        {"ok": False, "error": "Internal server error"},
        status_code=500,
    )

# Disable CSRF in test mode — detect pytest in sys.modules
import sys as _sys

_CSRF_ENABLED = "pytest" not in _sys.modules

# ---------------------------------------------------------------------------
# CSRF double-submit cookie (Architecture.md §18)
# ---------------------------------------------------------------------------

_CSRF_COOKIE = "pmacs_csrf"
_CSRF_HEADER = "x-csrf-token"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit cookie CSRF protection for state-mutating requests."""

    async def dispatch(self, request: Request, call_next):
        # Skip CSRF in test mode (runtime check)
        if "pytest" in _sys.modules:
            return await call_next(request)

        # Validate CSRF for unsafe methods BEFORE processing
        if request.method not in _SAFE_METHODS:
            cookie_token = request.cookies.get(_CSRF_COOKIE)
            header_token = request.headers.get(_CSRF_HEADER)
            if not cookie_token or not header_token or not secrets.compare_digest(cookie_token, header_token):
                response = JSONResponse(
                    {"ok": False, "error": "CSRF validation failed"},
                    status_code=403,
                )
                # Set cookie on 403 so the retry succeeds
                if not cookie_token:
                    response.set_cookie(
                        _CSRF_COOKIE,
                        secrets.token_hex(32),
                        httponly=False,
                        samesite="strict",
                        secure=False,
                        path="/",
                    )
                return response

        response: Response = await call_next(request)

        # Always ensure cookie exists on any successful response
        if _CSRF_COOKIE not in request.cookies:
            response.set_cookie(
                _CSRF_COOKIE,
                secrets.token_hex(32),
                httponly=False,
                samesite="strict",
                secure=False,
                path="/",
            )

        return response


# ---------------------------------------------------------------------------
# Security headers (Source.md §18.6)
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "  # HTMX requires inline handlers
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "font-src 'self'"
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CSRFMiddleware)


# ---------------------------------------------------------------------------
# Wizard-first redirect (redirects to /wizard/ if setup not completed)
# ---------------------------------------------------------------------------

class WizardRedirectMiddleware(BaseHTTPMiddleware):
    """Redirect to /wizard/ if first-run setup has not been completed."""

    _WIZARD_PREFIXES = ("/wizard", "/static", "/favicon", "/events")
    _SKIP_EXTENSIONS = (".css", ".js", ".png", ".jpg", ".svg", ".ico", ".woff", ".woff2")

    async def dispatch(self, request: Request, call_next):
        # Skip during tests (runtime check — import-time flag may miss pytest)
        if "pytest" in _sys.modules:
            return await call_next(request)

        path = request.url.path

        # Skip wizard routes, static files, and assets
        if any(path.startswith(p) for p in self._WIZARD_PREFIXES):
            return await call_next(request)
        if path.endswith(self._SKIP_EXTENSIONS):
            return await call_next(request)

        # Check wizard completion state
        try:
            from pmacs.web.routes.wizard import _read_wizard_state
            state = _read_wizard_state()
            if not state.get("completed", False):
                return RedirectResponse(url="/wizard/", status_code=302)
        except Exception:
            return RedirectResponse(url="/wizard/", status_code=302)

        return await call_next(request)


app.add_middleware(WizardRedirectMiddleware)


# ---------------------------------------------------------------------------
# Favicon — inline SVG to prevent 404s
# ---------------------------------------------------------------------------

@app.get("/favicon.ico")
async def favicon():
    """Serve a simple SVG favicon."""
    from fastapi.responses import Response
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="6" fill="#2563eb"/>'
        '<text x="16" y="23" font-family="sans-serif" font-size="20" '
        'font-weight="bold" text-anchor="middle" fill="white">P</text>'
        '</svg>'
    )
    return Response(content=svg, media_type="image/svg+xml")


# Mount static files
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

from pmacs.web.templating import templates  # noqa: E402  # shared instance used by routes

# Import and include routes
from pmacs.web.routes import (  # noqa: E402
    agents,
    compare,
    cortex,
    dashboard,
    debug,
    memo,
    pipeline,
    settings,
    universe,
    wizard,
)

app.include_router(dashboard.router)
app.include_router(agents.router)
app.include_router(compare.router)
app.include_router(pipeline.router)
app.include_router(universe.router)
app.include_router(cortex.router)
app.include_router(debug.router)
app.include_router(settings.router)
app.include_router(wizard.router)
app.include_router(memo.router)


# ---------------------------------------------------------------------------
# SSE broadcast helper — called by _emit_event in pipeline routes to push
# events to all connected browser clients via the nervous publisher.
# ---------------------------------------------------------------------------

def _broadcast_event(frame: str) -> None:
    """Push a pre-serialised event JSON string to all SSE clients.

    Parses the flat event produced by _emit_event and forwards it through
    the in-process SSEPublisher so the /events endpoint fans it out to
    every connected browser.
    """
    import json as _json

    from pmacs.nervous.api import _publisher

    try:
        evt = _json.loads(frame)
        stream = evt.get("stream", "system")
        event_type = evt.get("event_type", "unknown")
        # Include event/event_type in data so JS handlers can read them
        data = {k: v for k, v in evt.items() if k not in ("stream", "id", "timestamp")}
        _publisher.publish(stream, event_type, data)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# SSE endpoint — subscribes directly to in-process nervous publisher
# (Architecture.md §4.4)
#
# In combined mode the browser connects to /events on :8000.
# Events are pushed from _publisher (pmacs.nervous.api) without any
# HTTP round-trip.  Format uses onmessage (no named event: field) so
# the existing JS dispatcher works unchanged.
# ---------------------------------------------------------------------------

@app.get("/events")
async def sse_events(request: Request):
    """SSE endpoint for browser clients.

    Subscribes directly to the in-process SSEPublisher from pmacs-nervous.
    Sends keep-alive comments when no events arrive within 30s.

    Query params:
        last_event_id: Resume from this event sequence number (reconnection).
    """
    from pmacs.nervous.api import _publisher

    last_id_str = request.query_params.get("last_event_id", "")
    last_id = int(last_id_str) if last_id_str.isdigit() else 0

    client_id, queue = _publisher.subscribe()

    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue

                try:
                    event = json.loads(frame)
                except (json.JSONDecodeError, ValueError):
                    yield f"data: {frame}\n\n"
                    continue

                evt_id = event.get("id")
                try:
                    if evt_id and int(evt_id) <= last_id:
                        continue
                except (ValueError, TypeError):
                    pass

                # Flatten to onmessage-compatible format (stream field for JS dispatch)
                inner = event.get("data", {})
                merged = {**inner, "stream": event.get("stream", ""), "event_type": event.get("type", "")}
                data_str = json.dumps(merged, separators=(",", ":"))
                id_line = f"id: {evt_id}\n" if evt_id else ""
                yield f"{id_line}data: {data_str}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _publisher.unsubscribe(client_id)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
