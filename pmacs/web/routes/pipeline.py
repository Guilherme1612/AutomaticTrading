"""Pipeline route — kanban-style verdict board."""

import sqlite3

from fastapi import APIRouter, Request

from pmacs.web.app import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer

router = APIRouter()


@router.get("/pipeline")
async def pipeline_page(request: Request):
    """Render the pipeline kanban page with verdict columns."""
    cfg = get_config()

    try:
        db = sqlite3.connect(f"file:{cfg.sqlite_path}?mode=ro", uri=True)
    except Exception:
        db = sqlite3.connect(":memory:")

    try:
        decisions = data_layer.get_recent_decisions(db, limit=20)
        holdings = data_layer.get_active_holdings(db)
        queue = data_layer.get_queue_status(db)
    finally:
        db.close()

    # Bin holdings by verdict for kanban columns
    verdict_cards = {"STRONG_BUY": [], "BUY": [], "HOLD": [], "SKIP": []}
    for h in holdings:
        verdict = h.get("verdict") or "SKIP"
        card = {
            "ticker": h["ticker"],
            "conviction": h.get("conviction_score") or 0.0,
        }
        if verdict in verdict_cards:
            verdict_cards[verdict].append(card)

    columns = [
        {"verdict": "STRONG_BUY", "color": "green", "cards": verdict_cards["STRONG_BUY"]},
        {"verdict": "BUY", "color": "blue", "cards": verdict_cards["BUY"]},
        {"verdict": "HOLD", "color": "amber", "cards": verdict_cards["HOLD"]},
        {"verdict": "SKIP", "color": "red", "cards": verdict_cards["SKIP"]},
    ]

    return templates.TemplateResponse(
        request=request,
        name="pipeline.html",
        context={
            "page": "pipeline",
            "mode": "SHADOW + PAPER",
            "columns": columns,
            "queue_size": len(queue),
            "cycles_today": len(decisions),
        },
    )
