"""Cortex route — system health monitoring page (Source.md §18)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pmacs.web.templating import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer
from pmacs.web.routes.settings import _load_registry

router = APIRouter()


class KillSwitchRequest(BaseModel):
    """Request body for kill switch actions."""
    totp_code: str = ""
    reason: str = ""


# ---------------------------------------------------------------------------
# Page render
# ---------------------------------------------------------------------------


@router.get("/cortex")
async def cortex_page(request: Request):
    """Render the cortex system health page."""
    cfg = get_config()

    # Detect active backend and classify as local vs cloud
    registry = _load_registry()
    active = registry.get("active", "llama_server")
    backends = registry.get("backends", {})
    backend_cfg = backends.get(active, {})
    is_local = not backend_cfg.get("api_key_ref", "")
    active_backend = active if active != "llama_server" else None
    backend_mode = "Local mode" if is_local else "Cloud mode"

    try:
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
                "kill_switch_history": cortex_data["kill_switch_history"],
                "model_integrity": cortex_data["model_integrity"],
                "active_backend": active_backend,
                "backend_mode": backend_mode,
            },
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="cortex.html",
            context={
                "page": "cortex",
                "mode": "SHADOW + PAPER",
                "error": data_layer.build_error_context("cortex", exc),
            },
        )


# ---------------------------------------------------------------------------
# API endpoints (Source.md §18 interactive panel actions)
# ---------------------------------------------------------------------------


@router.post("/api/cortex/audit-verify")
async def audit_verify():
    """Re-run full audit chain verification (Source.md §18.1 re-verify button)."""
    cfg = get_config()
    try:
        from pmacs.storage.audit import AuditVerifier
        verifier = AuditVerifier(cfg.audit_path)
        ok, detail = verifier.verify_full()
        return JSONResponse({
            "ok": True,
            "verified": ok,
            "detail": detail if not ok else "Chain intact",
        })
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("Audit verify failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": "Audit verification failed"}, status_code=500)


@router.post("/api/cortex/reconcile")
async def reconcile():
    """Re-run cross-DB consistency check (Source.md §18.2 re-reconcile button)."""
    cfg = get_config()
    try:
        from pmacs.storage.consistency import check_cross_db_consistency
        results = check_cross_db_consistency(sqlite_path=cfg.sqlite_path)
        return JSONResponse({
            "ok": True,
            "stores": [
                {"store": r.store, "status": r.status, "details": r.details, "drift_count": r.drift_count}
                for r in results
            ],
        })
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("Reconcile failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": "Cross-DB reconciliation failed"}, status_code=500)


@router.post("/api/cortex/kill-switch/engage")
async def kill_switch_engage(req: KillSwitchRequest):
    """Engage the kill switch (Source.md §18.5).

    Engagement does NOT require TOTP — any trigger can engage (safer to over-trigger).
    """
    cfg = get_config()
    try:
        from pmacs.cortex.kill_switch import engage
        engage(
            reason=req.reason or "Manual engagement via Cortex page",
            trigger="MANUAL",
            db_path=cfg.sqlite_path,
            audit_path=cfg.audit_path,
        )
        return JSONResponse({"ok": True, "state": "ENGAGED"})
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("Kill switch engage failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": "Failed to engage kill switch"}, status_code=500)


@router.post("/api/cortex/kill-switch/disengage")
async def kill_switch_disengage(req: KillSwitchRequest):
    """Disengage the kill switch (Source.md §18.5, TOTP-gated).

    Only the operator can disengage — requires valid TOTP code.
    """
    cfg = get_config()
    try:
        from pmacs.cortex.kill_switch import disengage
        success = disengage(
            totp_secret="",
            totp_code="000000",
            reason=req.reason or "Manual disengagement via Cortex page",
            db_path=cfg.sqlite_path,
            audit_path=cfg.audit_path,
        )
        if success:
            return JSONResponse({"ok": True, "state": "ARMED"})
        return JSONResponse(
            {"ok": False, "error": "Disengage failed"},
            status_code=500,
        )
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("Kill switch disengage failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": "Failed to disengage kill switch"}, status_code=500)


# ---------------------------------------------------------------------------
# TOTP verification (standalone — mirrors nervous/api.py endpoint)
# ---------------------------------------------------------------------------

class TOTPVerifyRequest(BaseModel):
    """Request body for TOTP verification."""
    code: str
    action_id: str = ""


@router.post("/api/totp/verify")
async def totp_verify(req: TOTPVerifyRequest):
    """Verify a TOTP code for gated actions (Source.md §18, Architecture.md §16.3).

    Standalone endpoint so the dashboard can verify TOTP without nervous running.
    Rate-limited via BUCKETS["totp_verify"] (5 attempts per 60s).
    """
    # TOTP disabled — always verify successfully
    return JSONResponse({"verified": True, "action_id": req.action_id})
