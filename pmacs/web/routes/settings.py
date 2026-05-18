"""Settings route — configuration management page."""

import difflib

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
        from pmacs.storage.keychain import get_api_key
        secret = get_api_key("pmacs.security", "totp_secret")
        if not verify_totp(secret, req.totp_code):
            return JSONResponse(
                {"ok": False, "error": "Invalid TOTP code"},
                status_code=403,
            )
    except ImportError as exc:
        # Keychain or TOTP module not available — BLOCK the mutation, do not allow
        return JSONResponse(
            {"ok": False, "error": f"TOTP verification unavailable: {exc}"},
            status_code=503,
        )
    except Exception as exc:
        # TOTP secret not configured — BLOCK, never silently pass
        return JSONResponse(
            {"ok": False, "error": f"TOTP verification failed: {exc}"},
            status_code=403,
        )
    cfg = get_config()
    db = data_layer.get_readwrite_db(cfg.sqlite_path)
    try:
        cursor = db.execute(
            "UPDATE mutation_proposals SET status = 'approved' "
            "WHERE id = ? AND status = 'pending'",
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
            "SELECT id, dimension, target, datetime('now'), 'promoted' "
            "FROM mutation_proposals WHERE id = ?",
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
            "UPDATE mutation_proposals SET status = 'rejected' "
            "WHERE id = ? AND status IN ('pending', 'approved')",
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
        from pmacs.storage.keychain import get_api_key
        secret = get_api_key("pmacs.security", "totp_secret")
        if not verify_totp(secret, req.totp_code):
            return JSONResponse(
                {"ok": False, "error": "Invalid TOTP code"},
                status_code=403,
            )
    except ImportError as exc:
        return JSONResponse(
            {"ok": False, "error": f"TOTP verification unavailable: {exc}"},
            status_code=503,
        )
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": f"TOTP verification failed: {exc}"},
            status_code=403,
        )
    cfg = get_config()
    db = data_layer.get_readwrite_db(cfg.sqlite_path)
    try:
        cursor = db.execute(
            "UPDATE mutation_proposals SET status = 'rolled_back' "
            "WHERE id = ? AND status = 'approved'",
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


@router.get("/api/mutation/{candidate_id}/diff")
async def mutation_diff(candidate_id: str):
    """Generate side-by-side HTML diff for a mutation candidate (Source.md §13.3).

    Returns JSON with diff_html, baseline text, and candidate text.
    """
    cfg = get_config()
    db = data_layer.get_readonly_db(cfg.sqlite_path)
    try:
        row = db.execute(
            "SELECT baseline_value, candidate_value FROM mutation_proposals "
            "WHERE id = ?",
            (candidate_id,),
        ).fetchone()
    finally:
        db.close()

    if not row:
        return JSONResponse(
            {"ok": False, "error": "Candidate not found"},
            status_code=404,
        )

    baseline = row[0] or ""
    candidate = row[1] or ""

    # Generate unified diff (safe — no HTML injection risk)
    baseline_lines = baseline.splitlines(keepends=True)
    candidate_lines = candidate.splitlines(keepends=True)

    unified = difflib.unified_diff(
        baseline_lines, candidate_lines,
        fromfile="baseline", tofile="candidate", lineterm="",
    )
    unified_text = "\n".join(unified)

    # Build a safe side-by-side representation (escaped HTML table)
    import html as html_module
    b_display = [html_module.escape(l.rstrip("\n\r")) for l in baseline_lines]
    c_display = [html_module.escape(l.rstrip("\n\r")) for l in candidate_lines]

    # Pair lines for side-by-side display
    max_len = max(len(b_display), len(c_display))
    b_padded = b_display + [""] * (max_len - len(b_display))
    c_padded = c_display + [""] * (max_len - len(c_display))

    diff_rows = []
    for b_line, c_line in zip(b_padded, c_padded):
        css_class = "diff-eq"
        if b_line != c_line:
            css_class = "diff-changed" if b_line and c_line else ("diff-del" if b_line else "diff-add")
        diff_rows.append({"baseline": b_line, "candidate": c_line, "class": css_class})

    return JSONResponse({
        "ok": True,
        "diff_rows": diff_rows,
        "unified": unified_text,
        "baseline": baseline,
        "candidate": candidate,
    })
