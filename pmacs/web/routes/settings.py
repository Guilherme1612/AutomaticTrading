"""Settings route — configuration management page."""

from fastapi import APIRouter, Request

from pmacs.web.app import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer

router = APIRouter()


@router.get("/settings")
async def settings_page(request: Request):
    """Render the settings configuration page."""
    cfg = get_config()
    config = data_layer.get_settings(cfg.config_dir)

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
            "config": config,
        },
    )
