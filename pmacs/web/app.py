"""PMACS Dashboard FastAPI application."""

import os
import secrets

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware

app = FastAPI(title="PMACS Dashboard")

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
        # Skip CSRF in test mode
        if not _CSRF_ENABLED:
            return await call_next(request)

        # Set CSRF cookie on every response if not present
        response: Response = await call_next(request)

        # For safe methods, ensure a CSRF token cookie exists
        if request.method in _SAFE_METHODS:
            if _CSRF_COOKIE not in request.cookies:
                response.set_cookie(
                    _CSRF_COOKIE,
                    secrets.token_hex(32),
                    httponly=False,  # JS must read it for header submission
                    samesite="strict",
                    secure=False,  # loopback only, no TLS
                    path="/",
                )
        else:
            # For unsafe methods, validate double-submit
            cookie_token = request.cookies.get(_CSRF_COOKIE)
            header_token = request.headers.get(_CSRF_HEADER)
            if not cookie_token or not header_token or not secrets.compare_digest(cookie_token, header_token):
                return JSONResponse(
                    {"ok": False, "error": "CSRF validation failed"},
                    status_code=403,
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


# Mount static files
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

_jinja_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=select_autoescape(["html", "htm"]),
)
templates = Jinja2Templates(env=_jinja_env)

# Import and include routes (after app/templates are defined to avoid circular imports)
from pmacs.web.routes import (  # noqa: E402
    agents,
    cortex,
    dashboard,
    debug,
    pipeline,
    settings,
    universe,
    wizard,
)

app.include_router(dashboard.router)
app.include_router(agents.router)
app.include_router(pipeline.router)
app.include_router(universe.router)
app.include_router(cortex.router)
app.include_router(debug.router)
app.include_router(settings.router)
app.include_router(wizard.router)
