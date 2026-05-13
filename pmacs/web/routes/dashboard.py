"""Dashboard route — portfolio overview page."""

import shutil

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from pmacs.web.app import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer

router = APIRouter()


@router.get("/")
async def dashboard_page(request: Request):
    """Render the main dashboard page with portfolio summary."""
    cfg = get_config()

    try:
        db = data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            holdings = data_layer.get_active_holdings(db)
            decisions = data_layer.get_recent_decisions(db, limit=10)
            risk_metrics = data_layer.get_risk_metrics(cfg.duckdb_path)
            health = data_layer.get_system_health(cfg.heartbeat_dir)
            sparkline_data = data_layer.get_all_sparkline_data(cfg.duckdb_path, window="1W")
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

        # Compute portfolio value: initial_capital + unrealized P&L
        config = data_layer.get_settings(cfg.config_dir)
        risk_cfg = config.get("risk", {})
        initial_capital = float(
            risk_cfg.get("paper_capital", risk_cfg.get("initial_capital", 5000.0))
        )
        position_cost = sum(h.get("position_size_usd") or 0 for h in holdings)
        # Unrealized P&L: when current_price diverges from entry, mark to market
        unrealized_pnl = 0.0
        for h in holdings:
            entry = h.get("entry_price_usd") or 0.0
            current = h.get("current_price_usd") or entry  # fallback to cost
            size = h.get("position_size_usd") or 0
            if entry > 0 and current != entry:
                shares = size / entry
                unrealized_pnl += (current - entry) * shares
        # TODO: integrate with cash_ledger once Architecture.md §9 CashLedger is built
        portfolio_value = initial_capital + unrealized_pnl

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
                "sparkline_data": sparkline_data,
                "system_health": {
                    "audit_chain": health.get("audit_chain_status", "unknown"),
                    "disk_free_gb": round(shutil.disk_usage("/").free / (1024**3), 1),
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
    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "page": "dashboard",
                "error": data_layer.build_error_context("dashboard", exc),
            },
        )


@router.get("/api/dashboard/sparkline")
async def sparkline_api(request: Request, metric: str = "sharpe", window: str = "1W"):
    """Return sparkline time-series data as JSON for dynamic refresh.

    Query params:
        metric: One of the rolling_metrics metric_name values.
        window: Time window — 1D, 1W, 1M, 3M, ALL.

    Returns:
        JSON array of {t, v} objects.
    """
    cfg = get_config()
    data = data_layer.get_sparkline_data(cfg.duckdb_path, metric=metric, window=window)
    return JSONResponse([{"t": ts, "v": val} for ts, val in data])
