"""PMACS Dashboard FastAPI application."""

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import select_autoescape
from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware

app = FastAPI(title="PMACS Dashboard")


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


# Mount static files
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html", "htm"]),
)

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
