"""Cortex route — system health monitoring page (Source.md §18)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pmacs.web.templating import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer
from pmacs.web.routes.settings import _load_registry, _get_inference_state

router = APIRouter()


class KillSwitchRequest(BaseModel):
    """Request body for kill switch actions."""
    reason: str = ""
    # When True, this engagement is a §20.12 wiring-test event fired from
    # /settings — surfaced on /cortex for visibility but MUST NOT fire the
    # critical alert modal or "KILL SWITCH ENGAGED" toast (operator UX
    # bug Jun 30: the test button looked indistinguishable from a real
    # auto-trigger). See settings.html forceKillSwitchTest().
    is_test: bool = False


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
            current_mode = data_layer.get_current_mode(db)
        finally:
            db.close()

        return templates.TemplateResponse(
            request=request,
            name="cortex.html",
            context={
                "page": "cortex",
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
                "error": data_layer.build_error_context("cortex", exc),
            },
        )


# ---------------------------------------------------------------------------
# API endpoints (Source.md §18 interactive panel actions)
# ---------------------------------------------------------------------------


@router.get("/api/health/detail")
async def health_detail():
    """Lightweight system-health summary for the header health strip.

    Returns the current mode, the active inference backend + whether its API
    key is present, and the last cycle timestamp. Every field is best-effort:
    on any failure the response degrades to a safe default so the strip never
    hard-errors. Designed to be polled cheaply (~30s) by the dashboard chrome.
    """
    cfg = get_config()
    detail: dict = {
        "status": "ok",
        "mode": "INSTALLING",
        "inference": {"backend": "unknown", "local": True, "key_present": False},
        "last_cycle_at": None,
    }
    try:
        try:
            inf = _get_inference_state()
            api_key_ref = inf.get("api_key_ref", "")
            detail["inference"] = {
                "backend": inf.get("active", "unknown"),
                "local": not bool(api_key_ref),
                "key_present": bool(inf.get("has_api_key")),
            }
        except Exception:
            pass
        db = data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            detail["mode"] = data_layer.get_current_mode(db, default="INSTALLING")
            row = db.execute(
                "SELECT opened_at FROM cycles ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row and row[0]:
                detail["last_cycle_at"] = row[0]
        finally:
            db.close()
    except Exception:
        detail["status"] = "degraded"
    return JSONResponse(detail)


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

    Engagement does NOT require operator confirmation — any trigger can engage (safer to over-trigger).

    The ``is_test`` flag marks a §20.12 wiring-test engagement so the
    frontend can suppress the critical-alert modal (the test button looked
    indistinguishable from a real auto-trigger).
    """
    cfg = get_config()
    try:
        from pmacs.cortex.kill_switch import engage
        engage(
            reason=req.reason or "Manual engagement via Cortex page",
            trigger="MANUAL",
            db_path=cfg.sqlite_path,
            audit_path=cfg.audit_path,
            is_test=req.is_test,
        )
        return JSONResponse({"ok": True, "state": "ENGAGED", "is_test": req.is_test})
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("Kill switch engage failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": "Failed to engage kill switch"}, status_code=500)


@router.post("/api/cortex/kill-switch/disengage")
async def kill_switch_disengage(req: KillSwitchRequest):
    """Disengage the kill switch (Source.md §18.5).

    Only the operator can disengage — requires an explicit operator action.
    The ``is_test`` flag propagates so the frontend suppresses alerts for
    §20.12 wiring tests.
    """
    cfg = get_config()
    try:
        from pmacs.cortex.kill_switch import disengage
        success = disengage(
            reason=req.reason or "Manual disengagement via Cortex page",
            db_path=cfg.sqlite_path,
            audit_path=cfg.audit_path,
            is_test=req.is_test,
        )
        if success:
            return JSONResponse({"ok": True, "state": "ARMED", "is_test": req.is_test})
        return JSONResponse(
            {"ok": False, "error": "Disengage failed"},
            status_code=500,
        )
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("Kill switch disengage failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": "Failed to disengage kill switch"}, status_code=500)
