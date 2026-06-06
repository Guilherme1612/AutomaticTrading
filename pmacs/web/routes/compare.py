"""Cycle compare route — side-by-side comparison of two cycles (Source.md §15.9)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from pmacs.web.templating import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer

router = APIRouter()


@router.get("/compare")
async def compare_page(request: Request):
    """Render cycle compare page with cycle selector."""
    cfg = get_config()

    try:
        db = data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            cycles = data_layer.get_recent_decisions(db, limit=50)
        finally:
            db.close()

        return templates.TemplateResponse(
            request=request,
            name="compare.html",
            context={
                "page": "compare",
                "cycles": cycles,
            },
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="compare.html",
            context={
                "page": "compare",
                "error": data_layer.build_error_context("compare", exc),
            },
        )


@router.get("/compare/data")
async def compare_data(
    request: Request,
    cycle_a: str = "",
    cycle_b: str = "",
):
    """API endpoint returning side-by-side comparison data for two cycles.

    Returns JSON with per-ticker diff of evidence, personas, Crucible, verdict.
    """
    if not cycle_a or not cycle_b:
        return JSONResponse({"ok": False, "error": "Both cycle_a and cycle_b required"}, status_code=400)

    cfg = get_config()

    try:
        db = data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            data_a = _load_cycle_data(db, cycle_a)
            data_b = _load_cycle_data(db, cycle_b)

            # Compute diff
            tickers_a = {h["ticker"]: h for h in data_a["holdings"]}
            tickers_b = {h["ticker"]: h for h in data_b["holdings"]}
            all_tickers = sorted(set(tickers_a.keys()) | set(tickers_b.keys()))

            diffs = []
            for ticker in all_tickers:
                h_a = tickers_a.get(ticker)
                h_b = tickers_b.get(ticker)
                diffs.append({
                    "ticker": ticker,
                    "in_a": h_a is not None,
                    "in_b": h_b is not None,
                    "verdict_a": h_a.get("verdict") if h_a else None,
                    "verdict_b": h_b.get("verdict") if h_b else None,
                    "conviction_a": h_a.get("conviction_score") if h_a else None,
                    "conviction_b": h_b.get("conviction_score") if h_b else None,
                    "verdict_changed": (
                        h_a.get("verdict") != h_b.get("verdict")
                        if h_a and h_b else True
                    ),
                })

            return JSONResponse({
                "ok": True,
                "cycle_a": {"id": cycle_a, "meta": data_a["meta"]},
                "cycle_b": {"id": cycle_b, "meta": data_b["meta"]},
                "tickers": diffs,
                "ticker_count": len(diffs),
                "changed_count": sum(1 for d in diffs if d["verdict_changed"]),
            })
        finally:
            db.close()
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("Compare failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": "Failed to compare cycles"}, status_code=500)


def _load_cycle_data(db, cycle_id: str) -> dict:
    """Load holdings and metadata for a single cycle."""
    meta = data_layer.get_agent_cycle_data(db, cycle_id)

    try:
        rows = db.execute(
            """SELECT ticker, verdict, conviction_score, entry_price_usd,
                      state, direction, quantity
               FROM holdings
               WHERE cycle_id = ?""",
            (cycle_id,),
        ).fetchall()
        holdings = [
            {
                "ticker": r[0],
                "verdict": r[1],
                "conviction_score": r[2],
                "entry_price_usd": r[3],
                "state": r[4],
                "direction": r[5],
                "quantity": r[6],
            }
            for r in rows
        ]
    except Exception:
        holdings = []

    return {"meta": meta, "holdings": holdings}
