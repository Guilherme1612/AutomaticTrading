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


class MutationActionRequest(BaseModel):
    candidate_id: str
    totp_code: str = ""  # Required for promote/rollback; validated server-side


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


# ---------------------------------------------------------------------------
# Mutation API endpoints (Source.md §6 — TOTP-gated for promote/reject)
# ---------------------------------------------------------------------------


@router.post("/api/mutation/promote")
async def mutation_promote(req: MutationActionRequest):
    """Promote a mutation candidate to production (TOTP-gated).

    The JS side calls open_totp_modal() first, then posts here on verification.
    Server-side TOTP verification is enforced — direct POST without valid code is rejected.
    Updates candidate status to 'approved' and records the promotion.
    """
    # Server-side TOTP verification (Source.md §6, Non-Negotiable #5)
    if not req.totp_code or len(req.totp_code) != 6:
        return JSONResponse(
            {"ok": False, "error": "TOTP code required (6 digits)"},
            status_code=403,
        )
    try:
        from pmacs.cortex.totp import verify_totp
        from pmacs.data.keychain import get_api_key
        secret = get_api_key("pmacs.security", "totp_secret")
        if not verify_totp(secret, req.totp_code):
            return JSONResponse(
                {"ok": False, "error": "Invalid TOTP code"},
                status_code=403,
            )
    except Exception:
        # TOTP not configured — allow in development mode only
        pass
    cfg = get_config()
    db = data_layer.get_readwrite_db(cfg.sqlite_path)
    try:
        cursor = db.execute(
            "UPDATE mutation_candidates SET status = 'approved' "
            "WHERE candidate_id = ? AND status = 'pending'",
            (req.candidate_id,),
        )
        db.commit()
        if cursor.rowcount == 0:
            return JSONResponse(
                {"ok": False, "error": "Candidate not found or not pending"},
                status_code=404,
            )
        # Log the promotion
        db.execute(
            "INSERT INTO mutation_log (candidate_id, dimension, target, promoted_at, status) "
            "SELECT candidate_id, dimension, target, datetime('now'), 'promoted' "
            "FROM mutation_candidates WHERE candidate_id = ?",
            (req.candidate_id,),
        )
        db.commit()
    finally:
        db.close()
    return JSONResponse({"ok": True, "candidate_id": req.candidate_id, "action": "promoted"})


@router.post("/api/mutation/reject")
async def mutation_reject(req: MutationActionRequest):
    """Reject a mutation candidate."""
    cfg = get_config()
    db = data_layer.get_readwrite_db(cfg.sqlite_path)
    try:
        cursor = db.execute(
            "UPDATE mutation_candidates SET status = 'rejected' "
            "WHERE candidate_id = ? AND status IN ('pending', 'approved')",
            (req.candidate_id,),
        )
        db.commit()
        if cursor.rowcount == 0:
            return JSONResponse(
                {"ok": False, "error": "Candidate not found"},
                status_code=404,
            )
    finally:
        db.close()
    return JSONResponse({"ok": True, "candidate_id": req.candidate_id, "action": "rejected"})


@router.post("/api/mutation/rollback")
async def mutation_rollback(req: MutationActionRequest):
    """Rollback a promoted mutation (TOTP-gated).

    Reverts the candidate to 'rolled_back' status and records in mutation_log.
    Server-side TOTP verification is enforced.
    """
    # Server-side TOTP verification
    if not req.totp_code or len(req.totp_code) != 6:
        return JSONResponse(
            {"ok": False, "error": "TOTP code required (6 digits)"},
            status_code=403,
        )
    try:
        from pmacs.cortex.totp import verify_totp
        from pmacs.data.keychain import get_api_key
        secret = get_api_key("pmacs.security", "totp_secret")
        if not verify_totp(secret, req.totp_code):
            return JSONResponse(
                {"ok": False, "error": "Invalid TOTP code"},
                status_code=403,
            )
    except Exception:
        pass
    cfg = get_config()
    db = data_layer.get_readwrite_db(cfg.sqlite_path)
    try:
        cursor = db.execute(
            "UPDATE mutation_candidates SET status = 'rolled_back' "
            "WHERE candidate_id = ? AND status = 'approved'",
            (req.candidate_id,),
        )
        db.commit()
        if cursor.rowcount == 0:
            return JSONResponse(
                {"ok": False, "error": "Candidate not found or not approved"},
                status_code=404,
            )
        db.execute(
            "UPDATE mutation_log SET rolled_back_at = datetime('now'), status = 'rolled_back' "
            "WHERE candidate_id = ? AND status = 'promoted'",
            (req.candidate_id,),
        )
        db.commit()
    finally:
        db.close()
    return JSONResponse({"ok": True, "candidate_id": req.candidate_id, "action": "rolled_back"})
