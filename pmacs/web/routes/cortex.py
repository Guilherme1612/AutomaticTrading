"""Cortex route — system health monitoring page."""

from fastapi import APIRouter, Request

from pmacs.web.app import templates

router = APIRouter()


@router.get("/cortex")
async def cortex_page(request: Request):
    """Render the cortex system health page."""
    return templates.TemplateResponse(
        request=request,
        name="cortex.html",
        context={
            "page": "cortex",
            "mode": "SHADOW + PAPER",
            "audit_chain": {"status": "verified", "last_hash": "--", "entries": 0},
            "cross_db": {"sqlite": "ok", "kuzudb": "ok", "qdrant": "ok", "duckdb": "ok"},
            "processes": [
                {"name": "pmacs-inference", "port": 8080, "status": "unknown"},
                {"name": "pmacs-cortex", "port": None, "status": "unknown"},
                {"name": "pmacs-cortex-self-check", "port": None, "status": "unknown"},
                {"name": "pmacs-execution", "port": None, "status": "unknown"},
                {"name": "pmacs-nervous", "port": 8000, "status": "unknown"},
                {"name": "pmacs-stoploss", "port": None, "status": "unknown"},
                {"name": "pmacs-mutation", "port": None, "status": "unknown"},
                {"name": "pmacs-dashboard", "port": 8001, "status": "running"},
            ],
            "disk_clock_network": {
                "disk_free_gb": 50,
                "clock_skew_ms": 0,
                "network_ok": True,
            },
            "kill_switch": {"engaged": False, "totp_required": True},
            "model_integrity": {"hash_verified": False, "model_path": "--"},
        },
    )
