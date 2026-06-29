"""Pipeline route — kanban-style verdict board + P1-P4 priority queue."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pmacs.web.templating import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer

router = APIRouter()


def _band_int_to_label(val: int | None) -> str:
    """Convert numeric priority band to display label."""
    if val == 1:
        return "HIGH"
    if val == 2:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ReorderRequest(BaseModel):
    ticker: str
    from_band: str
    to_band: str


class PinRequest(BaseModel):
    ticker: str
    pinned: bool


class SchemeSaveRequest(BaseModel):
    name: str
    config: dict


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------

@router.get("/pipeline")
async def pipeline_page(request: Request):
    """Render the pipeline kanban page with verdict columns and P1-P4 queue rail."""
    cfg = get_config()

    try:
        db = data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            decisions = data_layer.get_recent_decisions(db, limit=20)
            holdings = data_layer.get_active_holdings(db)
            queue = data_layer.get_queue_status(db)
            banded = data_layer.get_priority_banded_queue(db)

            # FIX-2: compute total/completed/failed cycle counts (mirrors dashboard.py)
            try:
                cycle_row = db.execute("SELECT COUNT(*) FROM cycles").fetchone()
                cycle_count = int(cycle_row[0]) if cycle_row else 0
            except Exception:
                cycle_count = 0
            try:
                dec_row = db.execute("SELECT COUNT(DISTINCT cycle_id) FROM decisions").fetchone()
                decision_cycles = int(dec_row[0]) if dec_row else 0
            except Exception:
                decision_cycles = 0
            total_cycles = max(cycle_count, decision_cycles)
            completed_cycles = cycle_count
            failed_cycles = max(0, total_cycles - completed_cycles)

            # Build a lookup of recent cycle decisions for thesis/timestamp
            recent_thesis: dict[str, dict] = {}
            try:
                rows = db.execute(
                    """SELECT d.ticker, d.verdict, d.conviction_score, d.thesis_summary, d.decided_at,
                              d.priority_band, d.price_usd
                       FROM decisions d
                       ORDER BY d.decided_at DESC
                       LIMIT 200"""
                ).fetchall()
                import json as _json_pipeline
                for r in rows:
                    t = r[0]
                    if t not in recent_thesis:
                        raw_thesis = r[3] or ""
                        # Parse thesis JSON for richer data
                        thesis_data = {}
                        display_thesis = raw_thesis
                        if raw_thesis.startswith("{"):
                            try:
                                _parsed = _json_pipeline.loads(raw_thesis)
                                thesis_data = _parsed
                                display_thesis = (_parsed.get("thesis")
                                                  or _parsed.get("raw_text")
                                                  or _parsed.get("verdict_line")
                                                  or raw_thesis)
                            except Exception:
                                pass
                        recent_thesis[t] = {
                            "verdict": r[1] or "SKIP",
                            "conviction": r[2] or 0.0,
                            "thesis": display_thesis,
                            "timestamp": r[4] or "",
                            "priority": r[5],
                            "price_usd": r[6],
                            "fair_value": thesis_data.get("fair_value"),
                            "valuation_range": thesis_data.get("valuation_range", {}),
                            "agent_signals": thesis_data.get("agent_signals", []),
                            "crucible_severity": thesis_data.get("crucible_severity"),
                            "crucible_survives": thesis_data.get("crucible_thesis_survives"),
                            "financial_snapshot": thesis_data.get("financial_snapshot", {}),
                            "verdict_line": thesis_data.get("verdict_line", ""),
                        }
            except Exception:
                pass
        finally:
            db.close()

        # Bin holdings by verdict for kanban columns
        verdict_cards: dict[str, list] = {"STRONG_BUY": [], "BUY": [], "HOLD": [], "SKIP": []}
        seen_tickers: set[str] = set()
        for h in holdings:
            verdict = h.get("verdict") or "SKIP"
            ticker = h["ticker"]
            seen_tickers.add(ticker)
            extra = recent_thesis.get(ticker, {})
            # Prefer holding's own thesis over latest decision thesis
            holding_thesis = h.get("thesis_summary") or ""
            card = {
                "ticker": ticker,
                "conviction": h.get("conviction_score") or extra.get("conviction") or 0.0,
                "thesis": holding_thesis if holding_thesis else extra.get("thesis", ""),
                "timestamp": extra.get("timestamp", ""),
                "priority": _band_int_to_label(extra.get("priority")),
                "fair_value": extra.get("fair_value"),
                "valuation_range": extra.get("valuation_range", {}),
                "agent_signals": extra.get("agent_signals", []),
                "crucible_severity": extra.get("crucible_severity"),
                "crucible_survives": extra.get("crucible_survives"),
                "financial_snapshot": extra.get("financial_snapshot", {}),
                "verdict_line": extra.get("verdict_line", ""),
                "price_usd": extra.get("price_usd"),
                "is_active": True,  # sourced from get_active_holdings → force-exit eligible
            }
            # Skip no-data cards (0% conviction + no thesis = infrastructure failure)
            if card["conviction"] == 0.0 and not card["thesis"]:
                continue
            if verdict in verdict_cards:
                verdict_cards[verdict].append(card)

        # Also add recent decisions that aren't active holdings
        for ticker, info in recent_thesis.items():
            if ticker in seen_tickers:
                continue
            verdict = info.get("verdict") or "SKIP"
            card = {
                "ticker": ticker,
                "conviction": info.get("conviction", 0.0),
                "thesis": info.get("thesis", ""),
                "timestamp": info.get("timestamp", ""),
                "priority": _band_int_to_label(info.get("priority")),
                "fair_value": info.get("fair_value"),
                "valuation_range": info.get("valuation_range", {}),
                "agent_signals": info.get("agent_signals", []),
                "crucible_severity": info.get("crucible_severity"),
                "crucible_survives": info.get("crucible_survives"),
                "financial_snapshot": info.get("financial_snapshot", {}),
                "verdict_line": info.get("verdict_line", ""),
                "price_usd": info.get("price_usd"),
                "is_active": False,  # recent decision, not a held position
            }
            # Skip no-data cards (0% conviction + no thesis = infrastructure failure)
            if card["conviction"] == 0.0 and not card["thesis"]:
                continue
            if verdict in verdict_cards:
                verdict_cards[verdict].append(card)

        columns = [
            {"verdict": "STRONG_BUY", "color": "green", "cards": verdict_cards["STRONG_BUY"]},
            {"verdict": "BUY", "color": "blue", "cards": verdict_cards["BUY"]},
            {"verdict": "HOLD", "color": "amber", "cards": verdict_cards["HOLD"]},
            {"verdict": "SKIP", "color": "red", "cards": verdict_cards["SKIP"]},
        ]

        # Priority bands for the right rail
        band_labels = {
            "P1": {"label": "P1 — Highest Priority", "color": "red"},
            "P2": {"label": "P2 — Standard", "color": "amber"},
            "P3": {"label": "P3 — Low Priority", "color": "blue"},
            "P4": {"label": "P4 — Background", "color": "zinc"},
        }

        priority_bands = []
        for band_key in ("P1", "P2", "P3", "P4"):
            meta = band_labels[band_key]
            items = banded.get(band_key, [])
            priority_bands.append({
                "band": band_key,
                "label": meta["label"],
                "color": meta["color"],
                "tickers": items,
                "count": len(items),
            })

        return templates.TemplateResponse(
            request=request,
            name="pipeline.html",
            context={
                "page": "pipeline",
                "columns": columns,
                "queue_size": len(queue),
                "cycles_today": len(decisions),
                "total_cycles": total_cycles,
                "completed_cycles": completed_cycles,
                "failed_cycles": failed_cycles,
                "priority_bands": priority_bands,
                "active_tickers": [h["ticker"] for h in holdings],
            },
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="pipeline.html",
            context={
                "page": "pipeline",
                "error": data_layer.build_error_context("pipeline", exc),
            },
        )


# ---------------------------------------------------------------------------
# Queue management API endpoints
# ---------------------------------------------------------------------------

@router.post("/pipeline/queue/reorder")
async def queue_reorder(req: ReorderRequest):
    """Move a ticker from one priority band to another."""
    cfg = get_config()
    db = data_layer.get_readwrite_db(cfg.sqlite_path)
    try:
        ok = data_layer.reorder_queue_item(db, req.ticker, req.from_band, req.to_band)
    finally:
        db.close()

    if ok:
        return JSONResponse({"ok": True, "ticker": req.ticker, "band": req.to_band})
    return JSONResponse({"ok": False, "error": "Item not found or band invalid"}, status_code=404)


@router.post("/pipeline/queue/pin")
async def queue_pin(req: PinRequest):
    """Pin or unpin a ticker in the queue."""
    cfg = get_config()
    db = data_layer.get_readwrite_db(cfg.sqlite_path)
    try:
        ok = data_layer.pin_queue_item(db, req.ticker, req.pinned)
    finally:
        db.close()

    if ok:
        return JSONResponse({"ok": True, "ticker": req.ticker, "pinned": req.pinned})
    return JSONResponse({"ok": False, "error": "Item not found"}, status_code=404)


class RemoveTickerRequest(BaseModel):
    ticker: str


@router.post("/pipeline/queue/remove")
async def queue_remove_ticker(req: RemoveTickerRequest):
    """Remove a ticker from the priority queue entirely."""
    ticker = req.ticker.upper().strip()
    if not ticker:
        return JSONResponse({"ok": False, "error": "ticker required"}, status_code=400)

    cfg = get_config()
    db = data_layer.get_readwrite_db(cfg.sqlite_path)
    try:
        cursor = db.execute("DELETE FROM queue WHERE ticker = ?", (ticker,))
        db.commit()
        deleted = cursor.rowcount
    finally:
        db.close()

    if deleted > 0:
        return JSONResponse({"ok": True, "ticker": ticker})
    return JSONResponse({"ok": False, "error": "Ticker not found in queue"}, status_code=404)


class AddTickerRequest(BaseModel):
    ticker: str
    priority_band: int = 3


@router.post("/pipeline/queue/add")
async def queue_add_ticker(req: AddTickerRequest):
    """Add a ticker to the queue manually."""
    import sqlite3 as _sqlite3
    from datetime import datetime, timezone

    ticker = req.ticker.upper().strip()
    if not ticker or len(ticker) > 10 or not ticker.isalpha():
        return JSONResponse({"ok": False, "error": "Invalid ticker"}, status_code=400)

    cfg = get_config()
    db = data_layer.get_readwrite_db(cfg.sqlite_path)
    try:
        now = datetime.now(timezone.utc).isoformat()
        cycle_id = f"MANUAL-{ticker}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
        db.execute(
            "INSERT OR REPLACE INTO queue (cycle_id, ticker, priority_band, pinned, enqueued_at) "
            "VALUES (?, ?, ?, 0, ?)",
            (cycle_id, ticker, req.priority_band, now),
        )
        db.commit()
    finally:
        db.close()

    return JSONResponse({"ok": True, "ticker": ticker})


# FIX-1: Tickers that failed on CYCLE-20260606T114225 due to keyring/auth issue.
# Enqueue them for operator-triggered re-analysis from the Pipeline page.
_FAILED_TICKERS_TO_RERUN: list[str] = [
    "TEM", "ZETA", "NU", "OUST", "KOD", "INFQ", "SWMR", "ASTS", "RBRK", "NOK"
]


@router.post("/pipeline/queue/rerun-failed")
async def queue_rerun_failed():
    """Bulk-enqueue the tickers that failed due to the 2026-06-06 auth issue.

    Does NOT start a cycle automatically — the operator still clicks Run cycle
    on the Agents page. This just repopulates the queue so the failed tickers
    are included in the next analysis pass.
    """
    import sqlite3 as _sqlite3
    from datetime import datetime, timezone

    cfg = get_config()
    db = data_layer.get_readwrite_db(cfg.sqlite_path)
    enqueued: list[str] = []
    skipped: list[str] = []
    try:
        now = datetime.now(timezone.utc).isoformat()
        for ticker in _FAILED_TICKERS_TO_RERUN:
            ticker = ticker.upper().strip()
            # Skip if already in queue
            row = db.execute(
                "SELECT 1 FROM queue WHERE ticker = ? AND completed_at IS NULL",
                (ticker,),
            ).fetchone()
            if row:
                skipped.append(ticker)
                continue
            cycle_id = f"RERUN-{ticker}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
            db.execute(
                "INSERT OR REPLACE INTO queue (cycle_id, ticker, priority_band, pinned, enqueued_at) "
                "VALUES (?, ?, ?, 0, ?)",
                (cycle_id, ticker, 1, now),  # P1 priority for re-analysis
            )
            enqueued.append(ticker)
        db.commit()
    finally:
        db.close()

    return JSONResponse({
        "ok": True,
        "enqueued": enqueued,
        "skipped": skipped,
        "count": len(enqueued),
    })


@router.post("/pipeline/queue/promote")
async def queue_promote_all():
    """Promote all P1 items to head of next cycle (pin them)."""
    cfg = get_config()
    db = data_layer.get_readwrite_db(cfg.sqlite_path)
    try:
        count = data_layer.promote_all_p1(db)
    finally:
        db.close()

    return JSONResponse({"ok": True, "promoted_count": count})


@router.post("/pipeline/queue/scheme/save")
async def queue_scheme_save(req: SchemeSaveRequest):
    """Save a priority scheme configuration."""
    cfg = get_config()
    ok = data_layer.save_priority_scheme(cfg.sqlite_path, req.name, req.config)
    if ok:
        return JSONResponse({"ok": True, "name": req.name})
    return JSONResponse({"ok": False, "error": "Failed to save scheme"}, status_code=500)


@router.get("/pipeline/queue/scheme/{name}")
async def queue_scheme_load(name: str):
    """Load a saved priority scheme by name, or list all if name is '__list'."""
    cfg = get_config()
    if name == "__list":
        names = data_layer.list_priority_schemes(cfg.sqlite_path)
        return JSONResponse({"ok": True, "names": names})
    scheme = data_layer.load_priority_scheme(cfg.sqlite_path, name)
    if scheme is not None:
        return JSONResponse({"ok": True, "name": name, "config": scheme})
    return JSONResponse({"ok": False, "error": "Scheme not found"}, status_code=404)


# ---------------------------------------------------------------------------
# Cycle control API endpoints
# ---------------------------------------------------------------------------



def _fetch_real_price(ticker: str) -> float | None:
    """Fetch real-time price from Finnhub. Returns None on failure."""
    import json
    import logging
    import urllib.request
    try:
        from pmacs.storage.keychain import get_api_key
        api_key = get_api_key("pmacs.data.finnhub", "api_key")
        if not api_key:
            return None
        url = f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={api_key}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        price = data.get("c", 0)
        return float(price) if price > 0 else None
    except Exception as exc:
        logging.getLogger("pmacs.web").warning("Price fetch failed for %s: %s", ticker, exc)
        return None



class CycleStartRequest(BaseModel):
    trigger: str = "manual"
    tickers: list[str] = []  # if non-empty, only these tickers are cycled


def _launch_orchestrator_cycle(
    cfg, trigger: str, cycle_id: str, tickers: list[str] | None, skip_kill_switch: bool
) -> None:
    """Build a CycleOrchestrator and run run_cycle in a worker thread (Task #8 Part B).

    The single canonical cycle engine. ``run_cycle`` owns the cycles-row insert
    (via initiate_cycle, which uses the provided ``cycle_id``), so the route does
    NOT insert its own row. Returns immediately (the orchestrator is blocking).

    Operator-initiated entry points (SOLO / cycle-start) pass ``skip_kill_switch=True``
    so a research cycle proceeds even when the kill switch is ENGAGED — the flag
    only skips the four is_engaged gates; it never disengages the kill switch
    (Non-Negotiable #5), and budget enforcement still runs per LLM call. The gated
    /api/cycle/orchestrator entry passes ``skip_kill_switch=False``.
    """
    import asyncio
    from pathlib import Path

    from pmacs.nervous.api import _publisher
    from pmacs.nervous.orchestrator import CycleOrchestrator

    lock_path = str(Path(cfg.sqlite_path).parent / "cycle_orchestrator.lock")
    orch = CycleOrchestrator(
        db_path=Path(cfg.sqlite_path),
        audit_path=Path(cfg.audit_path) if getattr(cfg, "audit_path", None) else None,
        sse_publisher=_publisher,
        config={"lock_path": lock_path},
    )
    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        None,
        lambda: orch.run_cycle(
            trigger, tickers=tickers, cycle_id=cycle_id, skip_kill_switch=skip_kill_switch,
        ),
    )


@router.post("/api/cycle/start")
async def cycle_start(req: CycleStartRequest):
    """Manually trigger a new analysis cycle (Source.md §15).

    Operator-confirmed: cycles consume API credits and place paper trades.
    Runs the full spec-canonical pipeline via the orchestrator (the demo path is
    retired in Task #8 Part E). The operator's explicit action bypasses the
    kill-switch gate (Non-Negotiable #5: bypass is per-run opt-in, never an
    auto-disengage; budget enforcement still applies).
    """
    from datetime import datetime, timezone

    cfg = get_config()
    try:
        cycle_id = datetime.now(timezone.utc).strftime("CYCLE-%Y%m%dT%H%M%S")
        # Resolve the ticker subset from the request or the universe table.
        tickers: list[str] | None
        if req.tickers:
            tickers = [t.upper().strip() for t in req.tickers if t.strip()]
        else:
            from pmacs.storage.sqlite import get_connection
            db = get_connection(cfg.sqlite_path)
            try:
                rows = db.execute(
                    "SELECT ticker FROM universe WHERE COALESCE(halted, 0) = 0 "
                    "AND COALESCE(delisted, 0) = 0 "
                    "ORDER BY COALESCE(pinned_priority, 999) ASC, added_at ASC"
                ).fetchall()
            finally:
                db.close()
            tickers = [r[0] for r in rows] if rows else ["AAPL", "MSFT", "GOOGL"]

        _launch_orchestrator_cycle(
            cfg, req.trigger or "manual", cycle_id, tickers, skip_kill_switch=True,
        )
        return JSONResponse({"ok": True, "cycle_id": cycle_id, "message": "Cycle " + cycle_id + " started"})
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("Cycle start failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": "Failed to start cycle"}, status_code=503)


@router.post("/api/cycle/orchestrator")
async def cycle_orchestrator(req: CycleStartRequest):
    """Trigger a full GATED cycle through the spec-canonical orchestrator (Source.md §15).

    This is the one entry point where the operator can run a *kill-switch-gated*
    full cycle: ``skip_kill_switch=False`` (default), so the cycle is blocked if
    the kill switch is engaged. The wave-2 path (Agents.md §11b-§11d, §16.9) runs
    7 personas + bull/bear advocates + cross-persona auditor, applies auditor
    arbitration weight caps, computes reverse-DCF + scenario-weighted expected
    price, and persists a structured memo to the memos table that /memo/{ticker}
    renders. The orchestrator opens and closes its own cycle row and streams SSE.

    Runs synchronously in a worker thread (the orchestrator is blocking); returns
    immediately with the cycle_id. Operator-confirmed: consumes LLM/API credits and
    may place paper trades.
    """
    from datetime import datetime, timezone

    cfg = get_config()
    try:
        cycle_id = datetime.now(timezone.utc).strftime("ORCH-%Y%m%dT%H%M%S")
        tickers = [t.upper().strip() for t in req.tickers if t.strip()] or None
        _launch_orchestrator_cycle(
            cfg, req.trigger or "OPERATOR", cycle_id, tickers, skip_kill_switch=False,
        )
        return JSONResponse({
            "ok": True, "cycle_id": cycle_id,
            "message": "Gated orchestrator cycle started (wave-2 path)",
        })
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("Orchestrator cycle start failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": "Failed to start orchestrator cycle"}, status_code=503)


class SoloRunRequest(BaseModel):
    ticker: str


@router.post("/api/solo/run")
async def solo_run(req: SoloRunRequest):
    """Run a one-time solo analysis for any ticker (research mode).

    No operator confirmation required — read-only data fetching and LLM analysis.
    Paper trades may still be created for BUY/STRONG_BUY verdicts.
    Results stream via SSE in real-time and persist in DB.

    Runs the full spec-canonical pipeline via the orchestrator (Task #8 Part B);
    the demo path is retired in Part E. The operator's explicit action bypasses
    the kill-switch gate (per-run opt-in, never an auto-disengage; Non-Negotiable
    #5). ``run_cycle`` owns the cycles-row insert, so the route does not.
    """
    from datetime import datetime, timezone

    ticker = req.ticker.upper().strip()
    if not ticker or len(ticker) > 10 or not ticker.isalpha():
        return JSONResponse({"ok": False, "error": "Invalid ticker (1-10 letters)"}, status_code=400)

    cfg = get_config()
    cycle_id = f"SOLO-{ticker}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

    try:
        _launch_orchestrator_cycle(
            cfg, "solo_research", cycle_id, [ticker], skip_kill_switch=True,
        )
        return JSONResponse({"ok": True, "cycle_id": cycle_id, "ticker": ticker, "message": f"Solo analysis started for {ticker}"})
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("Solo run failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": "Failed to start solo analysis"}, status_code=503)


@router.post("/api/cycle/smoke-test")
async def cycle_smoke_test():
    """First-use smoke-test cycle — no operator confirmation required (Source.md §12 Step 9).
    Creates a cycle record and verifies the pipeline is functional.
    """
    from datetime import datetime, timezone

    cfg = get_config()
    try:
        from pmacs.storage.sqlite import get_connection

        cycle_id = datetime.now(timezone.utc).strftime("SMOKE-%Y%m%dT%H%M%S")
        db = get_connection(cfg.sqlite_path)
        try:
            db.execute(
                "INSERT INTO cycles (cycle_id, opened_at, state, trigger, mode) VALUES (?, ?, ?, ?, ?)",
                (cycle_id, datetime.now(timezone.utc).isoformat(), "COMPLETED", "smoke_test", "PAPER"),
            )
            db.commit()
        finally:
            db.close()

        return JSONResponse({"ok": True, "cycle_id": cycle_id, "message": "Smoke-test cycle " + cycle_id + " passed", "reload": True})
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("Smoke-test cycle failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": "Smoke-test failed: " + str(exc)}, status_code=503)


class ForceExitRequest(BaseModel):
    ticker: str


@router.post("/api/pipeline/force-exit")
async def force_exit(req: ForceExitRequest):
    """Force-exit an active holding (Source.md §15).

    Transitions the holding to EXIT_THESIS_INVALIDATED and persists
    the state change to SQLite. Operator-confirmed (Non-Negotiable #5).
    """
    cfg = get_config()
    try:
        import sqlite3
        from pmacs.storage.sqlite import get_connection

        db = get_connection(cfg.sqlite_path)
        try:
            # Find active holding for this ticker
            row = db.execute(
                "SELECT id, state FROM holdings WHERE ticker = ? AND state = 'ACTIVE' LIMIT 1",
                (req.ticker,),
            ).fetchone()
            if row is None:
                return JSONResponse({"ok": False, "error": "No active holding found"}, status_code=404)

            holding_id = row[0]
            db.execute(
                "UPDATE holdings SET state = 'EXIT_THESIS_INVALIDATED', abort_reason = 'force_exit:operator' WHERE id = ?",
                (holding_id,),
            )
            db.commit()
        finally:
            db.close()

        from pmacs.logsys import log_debug
        log_debug("FORCE_EXIT", payload={"ticker": req.ticker, "holding_id": holding_id},
                  level="INFO", msg=f"Force exit: {req.ticker} (holding {holding_id})")

        return JSONResponse({"ok": True, "holding_id": holding_id, "ticker": req.ticker})
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("Force exit failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": "Force exit failed"}, status_code=500)
