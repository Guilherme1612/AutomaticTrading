"""Agents route — persona analysis page."""

import sqlite3

from fastapi import APIRouter, Request

from pmacs.web.app import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer

router = APIRouter()

PERSONAS = [
    {"id": "gatekeeper", "name": "Gatekeeper", "role": "Queue screening"},
    {"id": "catalyst_summarizer", "name": "Catalyst Summarizer", "role": "Event classification"},
    {"id": "growth_hunter", "name": "Growth Hunter", "role": "Fundamental growth"},
    {"id": "moat_analyst", "name": "Moat Analyst", "role": "Competitive moat"},
    {"id": "macro_regime", "name": "Macro Regime", "role": "Macro environment"},
    {"id": "insider_activity", "name": "Insider Activity", "role": "Insider signals"},
    {"id": "short_interest", "name": "Short Interest", "role": "Short thesis"},
    {"id": "forensics", "name": "Forensics", "role": "Accounting quality"},
    {"id": "crucible", "name": "Crucible", "role": "Adversarial testing"},
]


@router.get("/agents")
async def agents_page(request: Request):
    """Render the agents analysis page with persona cards."""
    cfg = get_config()

    try:
        db = sqlite3.connect(f"file:{cfg.sqlite_path}?mode=ro", uri=True)
    except Exception:
        db = sqlite3.connect(":memory:")

    try:
        queue = data_layer.get_queue_status(db)
        decisions = data_layer.get_recent_decisions(db, limit=1)
    finally:
        db.close()

    current_ticker = queue[0]["ticker"] if queue else None

    return templates.TemplateResponse(
        request=request,
        name="agents.html",
        context={
            "page": "agents",
            "mode": "SHADOW + PAPER",
            "personas": PERSONAS,
            "queue": queue,
            "current_ticker": current_ticker,
            "cycle_log": decisions,
        },
    )
