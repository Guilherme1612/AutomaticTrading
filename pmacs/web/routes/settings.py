"""Settings route — configuration management page."""

from fastapi import APIRouter, Request

from pmacs.web.app import templates

router = APIRouter()


@router.get("/settings")
async def settings_page(request: Request):
    """Render the settings configuration page."""
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "page": "settings",
            "mode": "SHADOW + PAPER",
            "sections": [
                "General",
                "Brokers",
                "Inference",
                "Universe",
                "Risk",
                "Crucible",
                "Mutation Engine",
                "Agent Personas",
                "Queue",
                "Audit & Debug",
                "Operator",
            ],
            "config": {},
        },
    )
