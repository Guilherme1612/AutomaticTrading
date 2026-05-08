"""Debug route — event stream viewer."""

from fastapi import APIRouter, Request

from pmacs.web.app import templates

router = APIRouter()


@router.get("/debug")
async def debug_page(request: Request):
    """Render the debug event stream page."""
    return templates.TemplateResponse(
        request=request,
        name="debug.html",
        context={
            "page": "debug",
            "mode": "SHADOW + PAPER",
            "events": [],
            "filter_chips": [
                "ALL",
                "CYCLE",
                "TRADE",
                "AUDIT",
                "ERROR",
                "WARN",
                "KILL_SWITCH",
                "MUTATION",
            ],
        },
    )
