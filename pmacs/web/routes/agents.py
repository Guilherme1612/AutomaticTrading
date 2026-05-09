"""Agents route — persona analysis page with D3 Sankey visualization."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

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

    db = data_layer.get_readonly_db(cfg.sqlite_path)
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


@router.get("/agents/sankey-data")
async def agents_sankey_data(request: Request):
    """Return JSON data for the D3 Sankey visualization on the Agents page.

    Returns: evidence_sources, personas (with weights/probabilities),
    arbitration_result, crucible_result, weights, flows, stages.
    """
    cfg = get_config()

    db = data_layer.get_readonly_db(cfg.sqlite_path)
    try:
        queue = data_layer.get_queue_status(db)
        decisions = data_layer.get_recent_decisions(db, limit=1)
        current_ticker = queue[0]["ticker"] if queue else None

        # Try to load cycle persona data from DB
        if decisions:
            cycle_id = decisions[0].get("cycle_id", "")
            cycle_data = data_layer.get_agent_cycle_data(db, cycle_id)
        else:
            cycle_data = {}
    finally:
        db.close()

    # Build evidence sources (static pipeline stages)
    evidence_sources = [
        {"id": "sec_filings", "name": "SEC Filings", "type": "fundamental"},
        {"id": "price_action", "name": "Price Action", "type": "technical"},
        {"id": "catalyst_feed", "name": "Catalyst Feed", "type": "event"},
        {"id": "insider_filings", "name": "Insider Filings", "type": "insider"},
        {"id": "short_data", "name": "Short Data", "type": "sentiment"},
        {"id": "macro_data", "name": "Macro Data", "type": "macro"},
    ]

    # Build persona outputs with weights
    persona_outputs = []
    flows = []
    for p in PERSONAS:
        weight = 1.0 / len(PERSONAS)  # Equal weight default
        persona_outputs.append({
            "id": p["id"],
            "name": p["name"],
            "role": p["role"],
            "weight": round(weight, 3),
            "p_up": None,
            "p_flat": None,
            "p_down": None,
            "status": "idle",
        })

    # Build flows: evidence → persona relevance
    evidence_persona_map = {
        "sec_filings": ["gatekeeper", "growth_hunter", "moat_analyst", "forensics"],
        "price_action": ["gatekeeper", "catalyst_summarizer", "short_interest"],
        "catalyst_feed": ["catalyst_summarizer", "growth_hunter"],
        "insider_filings": ["insider_activity"],
        "short_data": ["short_interest"],
        "macro_data": ["macro_regime"],
    }
    for ev_id, persona_ids in evidence_persona_map.items():
        for pid in persona_ids:
            flows.append({
                "source": ev_id,
                "target": pid,
                "value": 1,
                "label": ev_id.replace("_", " ") + " -> " + pid.replace("_", " "),
            })

    # Pipeline stages (process view)
    stages = [
        {"id": "evidence", "label": "Evidence", "status": "pending"},
        {"id": "personas", "label": "Personas", "status": "pending"},
        {"id": "arbitration", "label": "Arbitration", "status": "pending"},
        {"id": "crucible", "label": "Crucible", "status": "pending"},
        {"id": "sizing", "label": "Sizing", "status": "pending"},
        {"id": "risk_gate", "label": "Risk Gate", "status": "pending"},
        {"id": "verdict", "label": "Verdict", "status": "pending"},
    ]

    return JSONResponse(content={
        "ticker": current_ticker,
        "evidence_sources": evidence_sources,
        "personas": persona_outputs,
        "arbitration_result": None,
        "crucible_result": None,
        "weights": {},
        "flows": flows,
        "stages": stages,
    })
