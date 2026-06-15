"""Agents route — persona analysis page with Communication Layer visualization."""

import sqlite3

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from pmacs.web.templating import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer

router = APIRouter()

PERSONAS = [
    {"id": "catalyst_summarizer", "name": "Catalyst Summarizer", "role": "Event classification"},
    {"id": "growth_hunter", "name": "Growth Hunter", "role": "Fundamental growth"},
    {"id": "moat_analyst", "name": "Moat Analyst", "role": "Competitive moat"},
    {"id": "macro_regime", "name": "Macro Regime", "role": "Macro environment"},
    {"id": "insider_activity", "name": "Insider Activity", "role": "Insider signals"},
    {"id": "short_interest", "name": "Short Interest", "role": "Short thesis"},
    {"id": "forensics", "name": "Forensics", "role": "Accounting quality"},
    {"id": "crucible", "name": "Crucible", "role": "Adversarial testing"},
    {"id": "gatekeeper", "name": "Gatekeeper", "role": "Final gate check"},
]


@router.get("/agents")
async def agents_page(request: Request):
    """Render the agents analysis page with persona cards."""
    cfg = get_config()

    try:
        db = data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            queue = data_layer.get_queue_status(db)
            decisions = data_layer.get_recent_decisions(db, limit=10)
            holdings = data_layer.get_active_holdings(db)
            try:
                running_row = db.execute(
                    "SELECT cycle_id FROM cycles WHERE state = 'RUNNING' ORDER BY opened_at DESC LIMIT 1"
                ).fetchone()
            except sqlite3.OperationalError:
                running_row = None
        finally:
            db.close()

        is_cycle_running = running_row is not None

        # Enrich personas with last cycle's agent results ONLY when a cycle is active
        from pmacs.web.routes.pipeline import (
            _last_cycle_agent_results,
            _last_cycle_crucible_results,
            _current_cycle_tickers,
            _current_ticker_processing,
        )

        # Derive current/next ticker from the live cycle state, not the queue table
        last_analyzed_ticker = None  # shown when idle with prior results
        if is_cycle_running and _current_ticker_processing:
            current_ticker = _current_ticker_processing
            idx = _current_cycle_tickers.index(_current_ticker_processing) if _current_ticker_processing in _current_cycle_tickers else -1
            next_ticker = _current_cycle_tickers[idx + 1] if idx >= 0 and idx + 1 < len(_current_cycle_tickers) else None
        else:
            # No cycle running — show last analyzed ticker for context
            current_ticker = None
            next_ticker = None
            if _last_cycle_agent_results:
                last_analyzed_ticker = list(_last_cycle_agent_results.keys())[-1]
            elif decisions:
                last_analyzed_ticker = decisions[0].get("ticker")

        # Build lookup: persona_id → agent result for the active ticker ONLY.
        # When no cycle is running, show clean idle state — no stale data.
        last_ticker_results = {}
        last_crucible = None

        if is_cycle_running and _last_cycle_agent_results:
            # Only show results when a cycle is actively running
            if _current_ticker_processing and _current_ticker_processing in _last_cycle_agent_results:
                last_key = _current_ticker_processing
            else:
                last_key = list(_last_cycle_agent_results.keys())[-1]
            for r in _last_cycle_agent_results[last_key]:
                last_ticker_results[r["persona"]] = r
            last_crucible = _last_cycle_crucible_results.get(last_key)

        _persona_last_run: dict[str, str] = {}

        enriched_personas = []
        for p in PERSONAS:
            pid = p["id"]
            result = last_ticker_results.get(pid)
            if result and not result.get("error"):
                enriched_personas.append({
                    **p,
                    "status": "complete",
                    "p_up": result['p_up'],
                    "p_flat": max(0.0, 1.0 - result['p_up'] - result['p_down']),
                    "p_down": result['p_down'],
                    "key_signal": result.get("key_signal", ""),
                    "analysis": result.get("analysis", ""),
                    "confidence": result.get("confidence", 0.0),
                    "evidence_cited": result.get("evidence_cited", []),
                    "completed_at": result.get("completed_at") or result.get("decided_at") or _persona_last_run.get(pid),
                })
            elif pid == "crucible" and last_crucible:
                enriched_personas.append({
                    **p,
                    "status": "complete",
                    "key_signal": f"Severity {last_crucible['severity']:.0%} — {'survives' if last_crucible['thesis_survives'] else 'rejected'}",
                    "analysis": last_crucible.get("summary", ""),
                    "completed_at": last_crucible.get("completed_at") or _persona_last_run.get(pid),
                })
            else:
                enriched_personas.append({**p, "status": "idle", "completed_at": _persona_last_run.get(pid)})

        # Build decision summary from recent decisions (decisions table has data;
        # holdings table may be empty if no positions have been opened yet)
        decision_summary = [
            {
                "ticker": d["ticker"],
                "verdict": d.get("verdict") or "SKIP",
                "conviction": d.get("conviction") or 0.0,
                "decided_at": d.get("decided_at") or d.get("opened_at") or "",
            }
            for d in sorted(decisions, key=lambda x: x.get("decided_at") or x.get("opened_at") or "", reverse=True)
        ]

        # Session stats for the stats panel
        verdict_counts = {"STRONG_BUY": 0, "BUY": 0, "HOLD": 0, "SKIP": 0}
        total_conviction = 0.0
        best_ticker = ""
        best_conviction = 0.0
        for d in decisions:
            v = d.get("verdict") or "SKIP"
            if v in verdict_counts:
                verdict_counts[v] += 1
            c = d.get("conviction") or 0.0
            total_conviction += c
            if c > best_conviction:
                best_conviction = c
                best_ticker = d.get("ticker", "")
        session_stats = {
            "total": len(decisions),
            "verdict_counts": verdict_counts,
            "avg_conviction": total_conviction / len(decisions) if decisions else 0,
            "best_ticker": best_ticker,
            "best_conviction": best_conviction,
        }

        return templates.TemplateResponse(
            request=request,
            name="agents.html",
            context={
                "page": "agents",
                "mode": "SHADOW + PAPER",
                "personas": enriched_personas,
                "queue": queue,
                "current_ticker": current_ticker,
                "last_analyzed_ticker": last_analyzed_ticker,
                "next_ticker": next_ticker,
                "is_cycle_running": is_cycle_running,
                "cycle_log": decisions,
                "decision_summary": decision_summary,
                "session_stats": session_stats,
                "last_cycle": decisions[0]["opened_at"] if decisions else "--",
            },
        )
    except Exception as exc:
        import traceback, logging
        logging.getLogger("pmacs.agents").error(
            "agents_page exception: %s\n%s", exc, traceback.format_exc()
        )
        return templates.TemplateResponse(
            request=request,
            name="agents.html",
            context={
                "page": "agents",
                "mode": "SHADOW + PAPER",
                "error": data_layer.build_error_context("agents", exc),
            },
        )


@router.get("/agents/sankey-data")
async def agents_sankey_data(request: Request):
    """Return JSON data for the Communication Layer tabs (Signals, Conviction, Process).

    Query params:
        ticker: Optional. Return data for a specific ticker from the last cycle.
                If omitted, returns the most recent ticker's data.

    Returns: personas (with p_up/p_down per agent), arbitration_result,
    crucible_result, stages (for Process timeline), is_running, available_tickers.
    """
    from pmacs.web.routes.pipeline import (
        _last_cycle_agent_results,
        _last_cycle_crucible_results,
        _last_cycle_arbitration,
        _last_cycle_id,
        _current_cycle_tickers,
        _current_ticker_processing,
    )

    cfg = get_config()
    requested_ticker = request.query_params.get("ticker", "").upper().strip()

    db = data_layer.get_readonly_db(cfg.sqlite_path)
    try:
        running_row = db.execute(
            "SELECT cycle_id FROM cycles WHERE state = 'RUNNING' ORDER BY opened_at DESC LIMIT 1"
        ).fetchone()
        is_running = running_row is not None
    finally:
        db.close()

    # Pick the requested or most recent ticker that has agent results
    last_ticker = None
    last_results: list[dict] = []
    last_crucible: dict = {}
    last_arb: dict = {}
    if _last_cycle_agent_results:
        if requested_ticker and requested_ticker in _last_cycle_agent_results:
            last_ticker = requested_ticker
        else:
            last_ticker = list(_last_cycle_agent_results.keys())[-1]
        last_results = _last_cycle_agent_results[last_ticker]
        last_crucible = _last_cycle_crucible_results.get(last_ticker, {})
        last_arb = _last_cycle_arbitration.get(last_ticker, {})

    has_data = bool(last_results)

    # Evidence sources
    evidence_sources = [
        {"id": "sec_filings", "name": "SEC Filings", "type": "fundamental"},
        {"id": "price_action", "name": "Price Action", "type": "technical"},
        {"id": "catalyst_feed", "name": "Catalyst Feed", "type": "event"},
        {"id": "insider_filings", "name": "Insider Filings", "type": "insider"},
        {"id": "short_data", "name": "Short Data", "type": "sentiment"},
        {"id": "macro_data", "name": "Macro Data", "type": "macro"},
    ]

    # Build result lookup: persona_id → result
    result_by_persona = {r["persona"]: r for r in last_results}

    # Persona outputs with real scores when available
    persona_outputs = []
    total_personas = len(PERSONAS)
    for p in PERSONAS:
        pid = p["id"]
        r = result_by_persona.get(pid)
        if r and not r.get("error"):
            p_up = r.get("p_up", 0.0)
            p_down = r.get("p_down", 0.0)
            p_flat = max(0.0, round(1.0 - p_up - p_down, 3))
            persona_outputs.append({
                "id": pid,
                "name": p["name"],
                "role": p["role"],
                "weight": round(1.0 / total_personas, 3),
                "p_up": round(p_up, 3),
                "p_flat": p_flat,
                "p_down": round(p_down, 3),
                "key_signal": r.get("key_signal", ""),
                "status": "complete",
            })
        else:
            persona_outputs.append({
                "id": pid,
                "name": p["name"],
                "role": p["role"],
                "weight": round(1.0 / total_personas, 3),
                "p_up": None,
                "p_flat": None,
                "p_down": None,
                "status": "running" if is_running else "idle",
            })

    # Flows: evidence → persona
    evidence_persona_map = {
        "sec_filings": ["gatekeeper", "growth_hunter", "moat_analyst", "forensics"],
        "price_action": ["gatekeeper", "catalyst_summarizer", "short_interest"],
        "catalyst_feed": ["catalyst_summarizer", "growth_hunter"],
        "insider_filings": ["insider_activity"],
        "short_data": ["short_interest"],
        "macro_data": ["macro_regime"],
    }
    flows = []
    for ev_id, persona_ids in evidence_persona_map.items():
        for pid in persona_ids:
            flows.append({
                "source": ev_id,
                "target": pid,
                "value": 1,
                "label": ev_id.replace("_", " ") + " → " + pid.replace("_", " "),
            })

    # Arbitration result
    arb_out = None
    if last_arb:
        arb_out = {
            "p_up": round(last_arb.get("p_up", 0.0), 3),
            "p_down": round(last_arb.get("p_down", 0.0), 3),
            "direction": round(last_arb.get("direction", 0.0), 3),
            "agents_used": last_arb.get("agents_used", 0),
            "conviction": round(last_arb.get("conviction", 0.0), 4),
            "ev_multiple": round(last_arb.get("ev_multiple", 0.0), 4),
            "verdict": last_arb.get("verdict", ""),
        }

    # Crucible result
    crucible_out = None
    if last_crucible:
        crucible_out = {
            "severity": round(last_crucible.get("severity", 0.0), 3),
            "thesis_survives": last_crucible.get("thesis_survives", True),
            "summary": last_crucible.get("summary", ""),
        }

    # Stage statuses — derive from what data is present
    def _stage_status(has_result: bool, cycle_running: bool) -> str:
        if has_result:
            return "complete"
        if cycle_running:
            return "running"
        return "pending"

    # Stage IDs must match data-pipeline-step attributes in agents.html
    stages = [
        {"id": "data_fetch", "label": "Data Fetch",
         "status": _stage_status(has_data, is_running)},
        {"id": "agents_running", "label": "Agent Analysis",
         "status": _stage_status(has_data, is_running)},
        {"id": "crucible", "label": "Crucible",
         "status": _stage_status(bool(last_crucible), is_running)},
        {"id": "arbitration", "label": "Arbitration",
         "status": _stage_status(bool(last_arb), is_running)},
        {"id": "decision", "label": "Decision",
         "status": _stage_status(bool(last_arb) and bool(last_crucible), is_running)},
    ]

    return JSONResponse(content={
        "ticker": last_ticker or (_current_ticker_processing if is_running else "") or "",
        "cycle_id": _last_cycle_id or "",
        "is_running": is_running,
        "current_ticker_processing": _current_ticker_processing or "",
        "cycle_tickers": list(_current_cycle_tickers),
        "evidence_sources": evidence_sources,
        "personas": persona_outputs,
        "arbitration_result": arb_out,
        "crucible_result": crucible_out,
        "weights": {},
        "flows": flows,
        "stages": stages,
        "available_tickers": list(_last_cycle_agent_results.keys()) if _last_cycle_agent_results else [],
    })
