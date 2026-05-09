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

    # Get mutation candidates and recent promotions
    db = data_layer.get_readonly_db(cfg.sqlite_path)
    try:
        mutation_candidates = data_layer.get_mutation_candidates(db)
        recent_mutations = data_layer.get_recent_mutations(db)
    finally:
        db.close()

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
            "mutation_candidates": mutation_candidates,
            "recent_mutations": recent_mutations,
        },
    )
