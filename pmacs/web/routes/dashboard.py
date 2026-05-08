"""Dashboard route — portfolio overview page."""

from fastapi import APIRouter, Request

from pmacs.web.app import templates

router = APIRouter()


@router.get("/")
async def dashboard_page(request: Request):
    """Render the main dashboard page with portfolio summary."""
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "page": "dashboard",
            "mode": "SHADOW + PAPER",
            "portfolio_value": 5000.00,
            "day_change_pct": 0.0,
            "positions": [],
            "recent_decisions": [],
            "risk_metrics": {
                "max_drawdown_pct": 0.0,
                "sharpe": 0.0,
                "win_rate_pct": 0.0,
                "open_positions": 0,
                "capital_used_pct": 0.0,
            },
            "system_health": {
                "audit_chain": "verified",
                "disk_free_gb": 50,
                "inference_ok": True,
                "last_cycle": "--",
            },
            "mutation_summary": {
                "active": False,
                "candidates": 0,
                "cycles_since_activation": 0,
            },
        },
    )
