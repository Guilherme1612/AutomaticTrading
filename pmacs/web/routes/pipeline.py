"""Pipeline route — kanban-style verdict board + P1-P4 priority queue."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pmacs.web.app import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer

router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ReorderRequest(BaseModel):
    ticker: str
    from_band: str
    to_band: str


class PinRequest(BaseModel):
    ticker: str
    pinned: bool


class SchemeSaveRequest(BaseModel):
    name: str
    config: dict


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------

@router.get("/pipeline")
async def pipeline_page(request: Request):
    """Render the pipeline kanban page with verdict columns and P1-P4 queue rail."""
    cfg = get_config()

    try:
        db = data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            decisions = data_layer.get_recent_decisions(db, limit=20)
            holdings = data_layer.get_active_holdings(db)
            queue = data_layer.get_queue_status(db)
            banded = data_layer.get_priority_banded_queue(db)
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

        # Priority bands for the right rail
        band_labels = {
            "P1": {"label": "P1 — Highest Priority", "color": "red"},
            "P2": {"label": "P2 — Standard", "color": "amber"},
            "P3": {"label": "P3 — Low Priority", "color": "blue"},
            "P4": {"label": "P4 — Background", "color": "zinc"},
        }

        priority_bands = []
        for band_key in ("P1", "P2", "P3", "P4"):
            meta = band_labels[band_key]
            items = banded.get(band_key, [])
            priority_bands.append({
                "band": band_key,
                "label": meta["label"],
                "color": meta["color"],
                "tickers": items,
                "count": len(items),
            })

        return templates.TemplateResponse(
            request=request,
            name="pipeline.html",
            context={
                "page": "pipeline",
                "mode": "SHADOW + PAPER",
                "columns": columns,
                "queue_size": len(queue),
                "cycles_today": len(decisions),
                "priority_bands": priority_bands,
                "active_tickers": [h["ticker"] for h in holdings],
            },
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="pipeline.html",
            context={
                "page": "pipeline",
                "error": data_layer.build_error_context("pipeline", exc),
            },
        )


# ---------------------------------------------------------------------------
# Queue management API endpoints
# ---------------------------------------------------------------------------

@router.post("/pipeline/queue/reorder")
async def queue_reorder(req: ReorderRequest):
    """Move a ticker from one priority band to another."""
    cfg = get_config()
    db = data_layer.get_readwrite_db(cfg.sqlite_path)
    try:
        ok = data_layer.reorder_queue_item(db, req.ticker, req.from_band, req.to_band)
    finally:
        db.close()

    if ok:
        return JSONResponse({"ok": True, "ticker": req.ticker, "band": req.to_band})
    return JSONResponse({"ok": False, "error": "Item not found or band invalid"}, status_code=404)


@router.post("/pipeline/queue/pin")
async def queue_pin(req: PinRequest):
    """Pin or unpin a ticker in the queue."""
    cfg = get_config()
    db = data_layer.get_readwrite_db(cfg.sqlite_path)
    try:
        ok = data_layer.pin_queue_item(db, req.ticker, req.pinned)
    finally:
        db.close()

    if ok:
        return JSONResponse({"ok": True, "ticker": req.ticker, "pinned": req.pinned})
    return JSONResponse({"ok": False, "error": "Item not found"}, status_code=404)


@router.post("/pipeline/queue/promote")
async def queue_promote_all():
    """Promote all P1 items to head of next cycle (pin them)."""
    cfg = get_config()
    db = data_layer.get_readwrite_db(cfg.sqlite_path)
    try:
        count = data_layer.promote_all_p1(db)
    finally:
        db.close()

    return JSONResponse({"ok": True, "promoted_count": count})


@router.post("/pipeline/queue/scheme/save")
async def queue_scheme_save(req: SchemeSaveRequest):
    """Save a priority scheme configuration."""
    cfg = get_config()
    ok = data_layer.save_priority_scheme(cfg.sqlite_path, req.name, req.config)
    if ok:
        return JSONResponse({"ok": True, "name": req.name})
    return JSONResponse({"ok": False, "error": "Failed to save scheme"}, status_code=500)


@router.get("/pipeline/queue/scheme/{name}")
async def queue_scheme_load(name: str):
    """Load a saved priority scheme by name."""
    cfg = get_config()
    scheme = data_layer.load_priority_scheme(cfg.sqlite_path, name)
    if scheme is not None:
        return JSONResponse({"ok": True, "name": name, "config": scheme})
    return JSONResponse({"ok": False, "error": "Scheme not found"}, status_code=404)
