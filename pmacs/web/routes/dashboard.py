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


def _gather_dashboard_context(cfg) -> dict:
    """Gather every dashboard data source into one context dict.

    Single source of truth used by BOTH the full dashboard page and the
    per-region partial endpoints (Source.md §14, Architecture.md §4.4 —
    dashboard data via SSE-triggered partials, not DB polling). Raises on
    failure so callers can branch to the error-state render.
    """
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

    return {
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
    }


# Per-region partial templates for SSE-triggered dashboard refresh (§14/§4.4).
# Each include renders its full outer container (with hx attrs) so an
# outerHTML swap leaves the region live for the next event.
_DASHBOARD_PARTIALS = {
    "positions": "dashboard/_positions.html",
    "decisions": "dashboard/_decisions.html",
    "health": "dashboard/_health.html",
    "mutation": "dashboard/_mutation.html",
}


@router.get("/")
async def dashboard_page(request: Request):
    """Render the main dashboard page with portfolio summary.

    On every dashboard view, opportunistically warm the evidence cache for any
    universe tickers that don't yet have a recent row. This makes the FIRST
    click on /ticker/{ticker} instant (it hits the warming-state branch but
    the fetch is already in flight from when the operator opened the
    dashboard), and a soft reload a few seconds later lands on the fully
    populated workspace. Operator directive 2026-06-23: "moving/reload pages
    almost instant" — this is the cache-warmth leg of that directive.
    """
    cfg = get_config()
    try:
        ctx = _gather_dashboard_context(cfg)
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
    ctx["page"] = "dashboard"

    # Opportunistic background warm for cold universe tickers. The fetch is
    # deduped via the ticker_data route's _LAZY_FETCH_IN_FLIGHT set so
    # concurrent dashboard loads don't spawn N duplicate fetches. Errors are
    # logged inside the fetcher; we never propagate them.
    try:
        _opportunistic_universe_warm(cfg)
    except Exception:
        pass

    return templates.TemplateResponse(request=request, name="dashboard.html", context=ctx)


def _opportunistic_universe_warm(cfg) -> None:
    """Kick off background evidence fetches for any cold universe tickers.

    Reads the SQLite universe list, asks ticker_data for the per-ticker
    freshness status, and dispatches a non-blocking fetch for any ticker
    whose evidence is older than the lazy TTL (or absent). Existing
    _maybe_warm_evidence_cache handles dedup + the actual fetch.
    """
    try:
        from pmacs.web.routes import ticker_data as _td
        db = data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            rows = db.execute(
                "SELECT ticker FROM universe WHERE halted = 0 AND delisted = 0"
            ).fetchall()
        finally:
            db.close()
        for r in rows:
            ticker = r[0]
            if not _td._evidence_fresh_enough(ticker):
                _td._maybe_warm_evidence_cache(ticker)
    except Exception:
        # Warming is best-effort. Any failure here is a no-op — the operator
        # still sees the dashboard, and the per-ticker page will dispatch its
        # own fetch on click.
        pass


@router.get("/api/dashboard/partials/{region}")
async def dashboard_partial(request: Request, region: str):
    """Return one dashboard region's HTML for SSE-triggered HTMX refresh
    (Source.md §14.4–§14.7; Architecture.md §4.4). The region's container
    carries its own hx attributes so an outerHTML swap keeps it live.
    """
    name = _DASHBOARD_PARTIALS.get(region)
    if name is None:
        return JSONResponse({"error": f"unknown region: {region}"}, status_code=404)
    cfg = get_config()
    try:
        ctx = _gather_dashboard_context(cfg)
    except Exception as exc:
        # Never 500 a region — render the shared error_state fragment instead.
        ctx = {
            "page": "dashboard",
            "error": data_layer.build_error_context("dashboard", exc),
            # Provide safe-empty shapes so the include's `is defined` guards hold.
            "positions": [],
            "recent_decisions": [],
            "system_health": {
                "audit_chain": "unknown",
                "disk_free_gb": 0,
                "inference_ok": False,
                "inference_backend": "local",
                "last_cycle": "--",
            },
            "mutation_summary": {
                "active": False,
                "candidates": 0,
                "cycles_since_activation": 0,
            },
        }
    ctx["page"] = "dashboard"
    return templates.TemplateResponse(request=request, name=name, context=ctx)


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
