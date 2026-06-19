"""Dashboard route — portfolio overview page."""

import shutil

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from pmacs.web.templating import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer
from pmacs.storage.sqlite import connect as _sql_connect

router = APIRouter()


def _check_backend_type() -> str:
    """Check if the system is configured for cloud or local inference.

    Uses model_registry.json as source of truth — the active backend's
    api_key_ref determines whether it's cloud or local.
    """
    try:
        import json
        from pathlib import Path
        registry_path = Path(__file__).resolve().parents[3] / "config" / "model_registry.json"
        if registry_path.exists():
            registry = json.loads(registry_path.read_text())
            active = registry.get("active", "llama_server")
            backend = registry.get("backends", {}).get(active, {})
            return "local" if not backend.get("api_key_ref", "") else "cloud"
        return "local"
    except Exception:
        return "local"


def _get_cost_state_for_dashboard(cfg) -> dict:
    """Get lightweight cost state for the dashboard cost widget.

    Delegates to the shared ``data_layer.get_cost_state`` (DuckDB-backed).
    """
    return data_layer.get_cost_state(cfg)


@router.get("/")
async def dashboard_page(request: Request):
    """Render the main dashboard page with portfolio summary."""
    cfg = get_config()

    try:
        db = data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            holdings = data_layer.get_active_holdings(db)
            decisions = data_layer.get_recent_decisions(db, limit=10)
            current_mode = data_layer.get_current_mode(db)
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
            # Count total decisions across all cycles (includes partial/failed)
            try:
                dec_row = db.execute("SELECT COUNT(DISTINCT cycle_id) FROM decisions").fetchone()
                decision_cycles = int(dec_row[0]) if dec_row else 0
            except Exception:
                decision_cycles = 0
            # Use the larger of cycles table vs distinct decision cycles
            total_cycles = max(cycle_count, decision_cycles)
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
                "mode": current_mode,
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
                "total_cycles": total_cycles,
                "completed_cycles": cycle_count,
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
                "mode": "SHADOW + PAPER",
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
