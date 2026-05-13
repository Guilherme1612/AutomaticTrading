"""PMACS Dashboard FastAPI application."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

app = FastAPI(title="PMACS Dashboard")

# Mount static files
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

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
