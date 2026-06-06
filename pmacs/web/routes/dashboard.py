"""Dashboard route — portfolio overview page."""

import shutil

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from pmacs.web.templating import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer

router = APIRouter()


def _check_backend_type() -> str:
    """Check if the system is configured for cloud or local inference."""
    try:
        from pmacs.config import data_dir
        import sqlite3
        db = data_dir() / "pmacs.db"
        if not db.exists():
            return "local"
        conn = sqlite3.connect(str(db))
        row = conn.execute(
            "SELECT value FROM wizard_state WHERE key = ?", ("backend_type",)
        ).fetchone()
        conn.close()
        return row[0] if row else "local"
    except Exception:
        return "local"


def _get_cost_state_for_dashboard(cfg) -> dict:
    """Get lightweight cost state for the dashboard cost widget."""
    daily_cap = 2.00
    monthly_cap = 30.00
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]
    from pathlib import Path
    risk_path = Path(cfg.config_dir) / "risk.toml"
    if risk_path.exists():
        try:
            with open(risk_path, "rb") as f:
                risk_cfg = tomllib.load(f)
            daily_cap = risk_cfg.get("billing", {}).get("daily_cap_usd", daily_cap)
            monthly_cap = risk_cfg.get("billing", {}).get("monthly_cap_usd", monthly_cap)
        except Exception:
            pass

    today_cost = 0.0
    month_cost = 0.0
    last_cycle_cost = 0.0
    avg_cycle_cost = 0.0
    try:
        db = data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            row = db.execute(
                "SELECT COALESCE(SUM(body_cost_usd), 0) FROM api_usage "
                "WHERE date(created_at) = date('now')"
            ).fetchone()
            today_cost = float(row[0]) if row else 0.0
            row = db.execute(
                "SELECT COALESCE(SUM(body_cost_usd), 0) FROM api_usage "
                "WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')"
            ).fetchone()
            month_cost = float(row[0]) if row else 0.0
            row = db.execute(
                "SELECT body_cost_usd FROM api_usage ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            last_cycle_cost = float(row[0]) if row else 0.0
            row = db.execute(
                "SELECT AVG(body_cost_usd) FROM api_usage "
                "WHERE created_at >= datetime('now', '-7 days')"
            ).fetchone()
            avg_cycle_cost = float(row[0]) if row and row[0] else 0.0
        finally:
            db.close()
    except Exception:
        pass

    return {
        "today_cost": today_cost,
        "month_cost": month_cost,
        "daily_cap": daily_cap,
        "monthly_cap": monthly_cap,
        "last_cycle_cost": last_cycle_cost,
        "avg_cycle_cost": avg_cycle_cost,
    }


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
            health = data_layer.get_system_health(cfg.heartbeat_dir, audit_path=cfg.audit_path)
            sparkline_data = data_layer.get_all_sparkline_data(cfg.duckdb_path, window="1W")
            try:
                smoke_row = db.execute(
                    "SELECT COUNT(*) FROM cycles WHERE trigger = 'smoke_test'"
                ).fetchone()
                smoke_done = bool(smoke_row and smoke_row[0] > 0)
            except Exception:
                smoke_done = False
            try:
                cycle_row = db.execute("SELECT COUNT(*) FROM cycles").fetchone()
                cycle_count = int(cycle_row[0]) if cycle_row else 0
            except Exception:
                cycle_count = 0
            try:
                queue_rows = db.execute("SELECT ticker FROM queue ORDER BY priority_band ASC").fetchall()
                queue_tickers = [r[0] for r in queue_rows] if queue_rows else []
            except Exception:
                queue_tickers = []
        finally:
            db.close()

        # Map holdings to template-expected field names
        positions = []
        for h in holdings:
            entry = h.get("entry_price_usd") or 0.0
            current = h.get("current_price_usd") or entry
            size_usd = h.get("position_size_usd") or 0.0
            # Mark-to-market P&L
            pnl = 0.0
            if entry > 0 and current != entry and size_usd > 0:
                shares = size_usd / entry
                pnl = (current - entry) * shares
            stop_price = h.get("stop_price_usd") or h.get("stop_loss_price") or 0.0
            positions.append({
                "ticker": h["ticker"],
                "verdict": h.get("verdict") or "SKIP",
                "conviction": h.get("conviction_score") or 0.0,
                "entry": entry,
                "current": current,
                "size_usd": size_usd,
                "stop_price": stop_price,
                "pnl": pnl,
                "thesis": h.get("thesis_summary") or "",
            })

        # Compute portfolio value: initial_capital + unrealized P&L
        config = data_layer.get_settings(cfg.config_dir)
        risk_cfg = config.get("risk", {})
        initial_capital = float(
            risk_cfg.get("paper_capital", risk_cfg.get("initial_capital", 5000.0))
        )
        # Unrealized P&L: when current_price diverges from entry, mark to market
        unrealized_pnl = 0.0
        for h in holdings:
            entry = h.get("entry_price_usd") or 0.0
            current = h.get("current_price_usd") or entry  # fallback to cost
            size = h.get("position_size_usd") or 0
            if entry > 0 and current != entry:
                shares = size / entry
                unrealized_pnl += (current - entry) * shares
        # Use cash ledger if available, otherwise estimate from holdings
        try:
            from pmacs.engines.cash_ledger import CashLedger
            from pmacs.storage.sqlite import default_db_path

            ledger = CashLedger(db_path=default_db_path())
            snapshot = ledger.get_snapshot()
            portfolio_value = snapshot["total_value_usd"]
        except Exception:
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
                    "inference_backend": _check_backend_type(),
                    "last_cycle": decisions[0]["opened_at"] if decisions else "--",
                },
                "mutation_summary": {
                    "active": False,
                    "candidates": 0,
                    "cycles_since_activation": cycle_count,
                },
                "pre_first_cycle": len(decisions) == 0 and len(holdings) == 0 and not smoke_done,
                "cycle_count": cycle_count,
                "queue_tickers": queue_tickers,
                "cost_state": _get_cost_state_for_dashboard(cfg),
                "last_cycle": decisions[0]["opened_at"] if decisions else "--",
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
        window: Time window — 1D, 1W, 1M, 3M, YTD, ALL.

    Returns:
        JSON array of {t, v} objects.
    """
    cfg = get_config()
    data = data_layer.get_sparkline_data(cfg.duckdb_path, metric=metric, window=window)
    return JSONResponse([{"t": ts, "v": val} for ts, val in data])
