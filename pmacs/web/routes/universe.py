"""Universe route — ticker management page."""

from fastapi import APIRouter, Request

from pmacs.web.app import templates

router = APIRouter()


@router.get("/universe")
async def universe_page(request: Request):
    """Render the universe ticker management page."""
    return templates.TemplateResponse(
        request=request,
        name="universe.html",
        context={
            "page": "universe",
            "mode": "SHADOW + PAPER",
            "tickers": [],
            "groups": ["All", "Watchlist", "Portfolio", "Sectors"],
        },
    )
