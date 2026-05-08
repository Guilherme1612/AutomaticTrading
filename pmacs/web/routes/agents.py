"""Agents route — persona analysis page."""

from fastapi import APIRouter, Request

from pmacs.web.app import templates

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
    return templates.TemplateResponse(
        request=request,
        name="agents.html",
        context={
            "page": "agents",
            "mode": "SHADOW + PAPER",
            "personas": PERSONAS,
            "queue": [],
            "current_ticker": None,
            "cycle_log": [],
        },
    )
