"""Universe route — ticker management page."""

from fastapi import APIRouter, Request

from pmacs.web.app import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer

router = APIRouter()


@router.get("/universe")
async def universe_page(request: Request):
    """Render the universe ticker management page."""
    cfg = get_config()

    db = data_layer.get_readonly_db(cfg.sqlite_path)
    try:
        ticker_rows = data_layer.get_universe_list(db)
    finally:
        db.close()

    # Map to template-expected field names
    tickers = [
        {
            "symbol": t["ticker"],
            "name": t["ticker"],  # Universe has ticker, not company name
            "sector": t.get("sector") or "--",
            "status": "Active",
            "last_cycle": "--",
        }
        for t in ticker_rows
    ]

    return templates.TemplateResponse(
        request=request,
        name="universe.html",
        context={
            "page": "universe",
            "mode": "SHADOW + PAPER",
            "tickers": tickers,
            "groups": ["All", "Watchlist", "Portfolio", "Sectors"],
        },
    )
