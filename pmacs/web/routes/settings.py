"""Settings route — configuration management page."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pmacs.web.app import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer

router = APIRouter()


class NotificationLevelRequest(BaseModel):
    event: str
    level: str


@router.get("/settings")
async def settings_page(request: Request):
    """Render the settings configuration page."""
    cfg = get_config()

    try:
        config = data_layer.get_settings(cfg.config_dir)

        # Get mutation candidates and recent promotions
        db = data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            mutation_candidates = data_layer.get_mutation_candidates(db)
            recent_mutations = data_layer.get_recent_mutations(db)
        finally:
            db.close()

        # Load saved notification levels
        notification_levels = data_layer.get_notification_levels(cfg.sqlite_path)

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
                "notification_levels": notification_levels,
            },
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "page": "settings",
                "error": data_layer.build_error_context("settings", exc),
            },
        )


@router.post("/api/settings/notifications")
async def save_notification_level(req: NotificationLevelRequest):
    """Save a notification level preference for a specific event.

    Accepts JSON {event: string, level: string}.
    Writes to SQLite settings table with key 'notif.{event}'.
    Kill switch and audit chain failure levels are non-disableable.
    """
    # Enforce non-disableable events
    if req.event in ("kill_switch_engaged", "audit_chain_failure"):
        return JSONResponse(
            {"ok": False, "error": f"'{req.event}' notification level cannot be changed"},
            status_code=403,
        )

    valid_levels = {"toast", "toast+sound", "modal", "none"}
    if req.level not in valid_levels:
        return JSONResponse(
            {"ok": False, "error": f"Invalid level. Must be one of: {', '.join(sorted(valid_levels))}"},
            status_code=400,
        )

    cfg = get_config()
    ok = data_layer.save_notification_level(cfg.sqlite_path, req.event, req.level)
    if ok:
        return JSONResponse({"ok": True, "event": req.event, "level": req.level})
    return JSONResponse({"ok": False, "error": "Failed to save"}, status_code=500)


@router.get("/api/settings/notifications")
async def get_notification_levels():
    """Return all saved notification levels as a JSON dict.

    Returns: {event_name: level_string, ...}
    """
    cfg = get_config()
    levels = data_layer.get_notification_levels(cfg.sqlite_path)
    return JSONResponse(levels)
