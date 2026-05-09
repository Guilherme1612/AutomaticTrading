"""Debug route — event stream viewer."""

from fastapi import APIRouter, Request

from pmacs.web.app import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer

router = APIRouter()


@router.get("/debug")
async def debug_page(request: Request):
    """Render the debug event stream page."""
    cfg = get_config()

    # Parse filter params from query string
    level = request.query_params.get("level")
    event = request.query_params.get("event")
    cycle_id = request.query_params.get("cycle_id")

    filters = {}
    if level:
        filters["level"] = level
    if event:
        filters["event"] = event
    if cycle_id:
        filters["cycle_id"] = cycle_id

    raw_events = data_layer.get_debug_events(cfg.debug_log_path, filters or None)

    # Map to template-expected field names
    events = [
        {
            "level": e.get("level", "INFO"),
            "timestamp": e.get("ts", ""),
            "stream": e.get("event", ""),
            "message": e.get("msg", ""),
            "detail": e.get("payload", ""),
        }
        for e in raw_events
    ]

    return templates.TemplateResponse(
        request=request,
        name="debug.html",
        context={
            "page": "debug",
            "mode": "SHADOW + PAPER",
            "events": events,
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
