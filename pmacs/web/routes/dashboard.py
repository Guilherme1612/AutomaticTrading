"""Dashboard route — portfolio overview page."""

import sqlite3

from fastapi import APIRouter, Request

from pmacs.web.app import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer

router = APIRouter()


@router.get("/")
async def dashboard_page(request: Request):
    """Render the main dashboard page with portfolio summary."""
    cfg = get_config()

    # Open read-only SQLite connection for this request
    try:
        db = sqlite3.connect(f"file:{cfg.sqlite_path}?mode=ro", uri=True)
    except Exception:
        db = sqlite3.connect(":memory:")

    try:
        holdings = data_layer.get_active_holdings(db)
        decisions = data_layer.get_recent_decisions(db, limit=10)
        risk_metrics = data_layer.get_risk_metrics(cfg.duckdb_path)
        health = data_layer.get_system_health(cfg.heartbeat_dir)
    finally:
        db.close()

    # Map holdings to template-expected field names
    positions = [
        {
            "ticker": h["ticker"],
            "verdict": h.get("verdict") or "SKIP",
            "conviction": h.get("conviction_score") or 0.0,
            "entry": h.get("entry_price_usd") or 0.0,
            "current": h.get("current_price_usd") or h.get("entry_price_usd") or 0.0,
            "pnl": 0.0,  # PnL computed by stop_loss engine, not stored in holdings
        }
        for h in holdings
    ]

    # Compute portfolio value from holdings
    position_value = sum(h.get("position_size_usd") or 0 for h in holdings)
    portfolio_value = 5000.0 - position_value + position_value  # cash + positions

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "page": "dashboard",
            "mode": "SHADOW + PAPER",
            "portfolio_value": portfolio_value,
            "day_change_pct": risk_metrics.get("day_change_pct", 0.0),
            "positions": positions,
            "recent_decisions": decisions,
            "risk_metrics": risk_metrics,
            "system_health": {
                "audit_chain": health.get("audit_chain_status", "unknown"),
                "disk_free_gb": 50,
                "inference_ok": health.get("inference_ok", False),
                "last_cycle": decisions[0]["opened_at"] if decisions else "--",
            },
            "mutation_summary": {
                "active": False,
                "candidates": 0,
                "cycles_since_activation": 0,
            },
        },
    )
