"""Cortex route — system health monitoring page."""

from fastapi import APIRouter, Request

from pmacs.web.app import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer

router = APIRouter()


@router.get("/cortex")
async def cortex_page(request: Request):
    """Render the cortex system health page."""
    cfg = get_config()

    db = data_layer.get_readonly_db(cfg.sqlite_path)
    try:
        cortex_data = data_layer.get_cortex_status(
            db, cfg.heartbeat_dir, cfg.audit_path
        )
    finally:
        db.close()

    return templates.TemplateResponse(
        request=request,
        name="cortex.html",
        context={
            "page": "cortex",
            "mode": "SHADOW + PAPER",
            "audit_chain": cortex_data["audit_chain"],
            "cross_db": cortex_data["cross_db"],
            "processes": cortex_data["processes"],
            "disk_clock_network": cortex_data["disk_clock_network"],
            "kill_switch": cortex_data["kill_switch"],
            "model_integrity": cortex_data["model_integrity"],
        },
    )
