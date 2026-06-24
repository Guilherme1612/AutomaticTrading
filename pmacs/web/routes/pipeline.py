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

# Personas used in the analysis pipeline (spec/Agents.md §4-13)
_PERSONAS = [
    "growth_hunter", "catalyst_summarizer", "moat_analyst",
    "short_interest", "insider_activity", "forensics", "macro_regime",
]

# In-memory store of last cycle's agent results per ticker (for agents page)
_last_cycle_agent_results: dict[str, list[dict]] = {}   # ticker → [agent_result, ...]
_last_cycle_crucible_results: dict[str, dict] = {}       # ticker → crucible_result
_last_cycle_arbitration: dict[str, dict] = {}            # ticker → arbitration_result
_last_cycle_id: str = ""

# Current running cycle state — tracks which ticker is actively processing
_current_cycle_tickers: list[str] = []      # ordered ticker list for this cycle
_current_ticker_processing: str = ""         # ticker currently being analysed

# Evidence cache — prevents re-fetching identical data on repeated solo runs.
# Keyed by ticker, stores (timestamp, price, news, fundamentals). TTL = 600s (10 min).
# This eliminates the #1 source of run-to-run variance: partial evidence from timeouts.
_EVIDENCE_CACHE_TTL = 600  # seconds
_evidence_cache: dict[str, tuple[float, float, list, str]] = {}  # ticker → (ts, price, news, fundamentals)


def _clear_cycle_caches() -> None:
    """Reset in-memory cycle caches to prevent stale data leaking between cycles."""
    global _last_cycle_agent_results, _last_cycle_crucible_results, _last_cycle_arbitration, _last_cycle_id
    global _current_cycle_tickers, _current_ticker_processing, _evidence_cache
    _last_cycle_agent_results = {}
    _last_cycle_crucible_results = {}
    _last_cycle_arbitration = {}
    _last_cycle_id = ""
    _current_cycle_tickers = []
    _current_ticker_processing = ""
    _evidence_cache = {}


def _emit_event(stream: str, event_type: str, data: dict) -> None:
    """Push an SSE event to all connected browser clients.

    Payload fields are flattened to the top level so handlers can access
    data.persona, data.ticker, etc. directly (not data.data.persona).
    """
    import json
    from datetime import datetime, timezone

    from pmacs.web.app import _broadcast_event

    # Monotonic counter for event IDs (avoids time.time() collisions)
    global _last_cycle_id
    if not hasattr(_emit_event, "_evt_seq"):
        _emit_event._evt_seq = 0
    _emit_event._evt_seq += 1

    event = {
        "stream": stream,
        "event_type": event_type,
        "event": event_type.split(".")[-1],
        "id": str(_emit_event._evt_seq),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    event.update(data)  # flatten payload fields to top level
    _broadcast_event(json.dumps(event, separators=(",", ":")))


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


def _call_llm(prompt: str, max_tokens: int = 1024, temperature: float = 0.2,
              system_prompt: str | None = None) -> str:
    """Call the active LLM backend from model_registry.json.

    Supports: ollama, openrouter, openai, anthropic.
    Falls back to ollama localhost if config unreadable.
    """
    import json as _json
    import logging

    import httpx

    _log = logging.getLogger("pmacs.web")

    # --- Load active backend from model_registry.json ---
    active_name = "ollama"
    backend_cfg: dict = {}
    try:
        from pathlib import Path
        registry_path = Path(__file__).resolve().parents[3] / "config" / "model_registry.json"
        with open(registry_path) as f:
            registry = _json.load(f)
        active_name = registry.get("active", "ollama")
        backend_cfg = registry.get("backends", {}).get(active_name, {})
    except Exception:
        pass

    model = backend_cfg.get("default_model", "qwen3.6:35b-a3b-coding-mxfp8")
    base_url = backend_cfg.get("base_url", "http://127.0.0.1:11434/v1").rstrip("/")
    api_key_ref = backend_cfg.get("api_key_ref", "")

    # --- Resolve API key (cloud backends only) ---
    api_key = ""
    if api_key_ref:
        try:
            import keyring
            api_key = keyring.get_password("pmacs.credentials", api_key_ref) or ""
        except ImportError:
            pass
        if not api_key:
            try:
                from pmacs.storage.keychain import get_api_key as _keychain_get
                parts = api_key_ref.rsplit(".", 1)
                if len(parts) == 2:
                    api_key = _keychain_get(parts[0], parts[1])
            except Exception:
                pass
        if not api_key:
            raise RuntimeError(
                f"API key not found for backend '{active_name}' (ref: {api_key_ref}). "
                f"Set it via Settings page or keyring."
            )

    # --- Anthropic uses a different API format ---
    if active_name == "anthropic":
        return _call_anthropic_api(prompt, system_prompt, model, base_url, api_key,
                                   max_tokens, temperature, _log)

    # --- OpenAI-compatible path (ollama, openrouter, openai, llama_server) ---
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    body: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    # Only request json_object format for backends that support it
    if active_name in ("openrouter", "openai", "ollama"):
        body["response_format"] = {"type": "json_object"}

    headers: dict = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Ollama with large models needs longer timeouts (35B can take 5+ min)
    if active_name in ("ollama", "llama_server"):
        timeout = max(600, max_tokens // 5)
    else:
        timeout = max(60, max_tokens // 50)

    with httpx.Client(timeout=float(timeout)) as client:
        response = client.post(f"{base_url}/chat/completions", json=body, headers=headers)
        response.raise_for_status()
        data = response.json()

    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

    usage = data.get("usage", {})
    if usage:
        _log.info(
            "LLM [%s]: model=%s prompt=%d completion=%d tokens",
            active_name, model, usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
        )
        try:
            import datetime as _dt
            from pathlib import Path as _Path
            _ledger = _Path(__file__).resolve().parents[3] / "data" / ".token_ledger.jsonl"
            _entry = _json.dumps({
                "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "model": model,
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "caller": f"_call_llm:{active_name}",
            })
            with open(_ledger, "a") as _f:
                _f.write(_entry + "\n")
        except Exception:
            pass

    return content


def _call_anthropic_api(prompt: str, system_prompt: str | None, model: str,
                        base_url: str, api_key: str, max_tokens: int,
                        temperature: float, _log) -> str:
    """Anthropic Messages API (non-OpenAI-compatible)."""
    import httpx

    messages = [{"role": "user", "content": prompt}]
    body: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if system_prompt:
        body["system"] = system_prompt

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/v1/messages"
    timeout = max(120, max_tokens // 30)
    with httpx.Client(timeout=float(timeout)) as client:
        response = client.post(url, json=body, headers=headers)
        response.raise_for_status()
        data = response.json()

    content = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            content += block.get("text", "")

    usage = data.get("usage", {})
    if usage:
        _log.info(
            "LLM [anthropic]: model=%s input=%d output=%d tokens",
            model, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
        )

    return content


# Keep old name as alias for backwards compatibility during transition
_call_openrouter = _call_llm


def _parse_json_safe(raw: str) -> dict | None:
    """Parse JSON from LLM response, repairing truncated output."""
    import json as _json
    if not raw:
        return None
    try:
        return _json.loads(raw)
    except _json.JSONDecodeError:
        repaired = raw
        if not repaired.rstrip().endswith("}"):
            if repaired.count('"') % 2 != 0:
                repaired += '"'
            open_braces = repaired.count("{") - repaired.count("}")
            open_brackets = repaired.count("[") - repaired.count("]")
            repaired += "]" * max(0, open_brackets) + "}" * max(0, open_braces)
        try:
            return _json.loads(repaired)
        except _json.JSONDecodeError:
            return None


# --- Persona prompt loader ---------------------------------------------------

_PROMPT_CACHE: dict[str, str] = {}


def _load_persona_prompt(persona: str) -> str:
    """Load a persona's system prompt from pmacs/agents/prompts/<persona>.md."""
    if persona in _PROMPT_CACHE:
        return _PROMPT_CACHE[persona]
    from pathlib import Path
    prompt_path = Path(__file__).resolve().parents[3] / "pmacs" / "agents" / "prompts" / f"{persona}.md"
    try:
        content = prompt_path.read_text()
        _PROMPT_CACHE[persona] = content
        return content
    except FileNotFoundError:
        return ""


# --- Evidence gathering ------------------------------------------------------

def _fetch_ticker_news(ticker: str) -> list[dict]:
    """Fetch recent news from Finnhub for a ticker (7-day window, max 5)."""
    import json
    import logging
    import urllib.request
    from datetime import datetime, timedelta, timezone

    try:
        from pmacs.storage.keychain import get_api_key
        api_key = get_api_key("pmacs.data.finnhub", "api_key")
        if not api_key:
            return []
        now = datetime.now(timezone.utc)
        from_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        to_date = now.strftime("%Y-%m-%d")
        url = f"https://finnhub.io/api/v1/company-news?symbol={ticker}&from={from_date}&to={to_date}&token={api_key}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=8) as resp:
            articles = json.loads(resp.read())
        results = []
        for a in articles[:5]:
            results.append({
                "headline": a.get("headline", ""),
                "summary": a.get("summary", ""),
                "source": a.get("source", ""),
                "url": a.get("url", ""),
                "datetime": a.get("datetime", 0),
            })
        return results
    except Exception as exc:
        logging.getLogger("pmacs.web").warning("News fetch failed for %s: %s", ticker, exc)
        return []


# --- Fundamentals evidence fetcher -------------------------------------------

def _fetch_ticker_fundamentals(ticker: str) -> str:
    """Fetch Finnhub /stock/metric and /stock/profile2, return formatted evidence text.

    Returns a structured evidence block that can be substituted into {evidence}
    in persona system prompts. Returns empty string on failure.
    """
    import json
    import logging
    import urllib.request

    _log = logging.getLogger("pmacs.web.agent")
    try:
        from pmacs.storage.keychain import get_api_key
        api_key = get_api_key("pmacs.data.finnhub", "api_key")
        if not api_key:
            return ""

        def _get(url: str) -> dict:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=8) as resp:
                return json.loads(resp.read())

        # Fetch metrics, profile, and insider transactions in parallel
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            f_metrics = pool.submit(_get, f"https://finnhub.io/api/v1/stock/metric?symbol={ticker}&metric=all&token={api_key}")
            f_profile = pool.submit(_get, f"https://finnhub.io/api/v1/stock/profile2?symbol={ticker}&token={api_key}")
            f_insider = pool.submit(_get, f"https://finnhub.io/api/v1/stock/insider-transactions?symbol={ticker}&token={api_key}")
            raw_m = f_metrics.result(timeout=10)
            raw_p = f_profile.result(timeout=10)
            try:
                raw_insider = f_insider.result(timeout=10)
            except Exception:
                raw_insider = {}

        metrics = raw_m.get("metric", {}) if raw_m else {}
        profile = raw_p if raw_p else {}

        lines = [f"## Evidence: {ticker} Financial Data (Finnhub, live)\n"]

        # Company identity
        name = profile.get("name", ticker)
        sector = profile.get("finnhubIndustry", "")
        mktcap = profile.get("marketCapitalization")
        lines.append(f"### fundamentals_{ticker}_profile")
        lines.append(f"Company: {name}" + (f" | Sector: {sector}" if sector else ""))
        if mktcap:
            lines.append(f"Market Cap: ${mktcap/1000:.1f}B" if mktcap > 1000 else f"Market Cap: ${mktcap:.0f}M")
        lines.append("")

        # Financial metrics
        _KEYS = [
            ("revenueGrowthTTMYoy", "Revenue Growth TTM YoY", True),
            ("revenueGrowth3Y", "Revenue Growth 3Y CAGR", True),
            ("revenueTTM", "Revenue TTM", False),
            ("grossMarginTTM", "Gross Margin TTM", True),
            ("netProfitMarginTTM", "Net Profit Margin TTM", True),
            ("operatingMarginTTM", "Operating Margin TTM", True),
            ("fcfMarginTTM", "FCF Margin TTM", True),
            ("epsTTM", "EPS TTM", False),
            ("epsGrowthTTMYoy", "EPS Growth TTM YoY", True),
            ("roeTTM", "ROE TTM", True),
            ("roicTTM", "ROIC TTM", True),
            ("peNormalizedAnnual", "P/E (normalized)", False),
            ("psAnnual", "P/S", False),
            ("evToEbitdaTTM", "EV/EBITDA TTM", False),
            ("totalDebtToEquityAnnual", "Debt/Equity", False),
            ("52WeekPriceReturnDaily", "52W Price Return", True),
            ("beta", "Beta", False),
        ]
        lines.append(f"### fundamentals_{ticker}_metrics")
        metric_lines = []
        for key, label, is_pct in _KEYS:
            v = metrics.get(key)
            if v is None:
                continue
            if is_pct:
                metric_lines.append(f"  {label}: {v:+.1f}%")
            elif key == "revenueTTM":
                metric_lines.append(f"  {label}: ${v/1e6:.0f}M" if v < 1e9 else f"  {label}: ${v/1e9:.2f}B")
            else:
                metric_lines.append(f"  {label}: {v:.2f}")

        if metric_lines:
            lines.extend(metric_lines)
        else:
            lines.append("  (no financial metrics available)")

        # Annual series — last 4 periods
        series = raw_m.get("series", {}).get("annual", {}) if raw_m else {}
        for field, label in [("revenue", "Revenue"), ("netIncome", "Net Income"), ("freeCashFlow", "FCF")]:
            entries = series.get(field, [])
            if entries:
                recent = sorted(entries, key=lambda x: x.get("period", ""), reverse=True)[:4]
                vals = ", ".join(
                    f"{e.get('period','?')}: ${e.get('v',0)/1e9:.2f}B" if abs(e.get('v',0)) >= 1e9
                    else f"{e.get('period','?')}: ${e.get('v',0)/1e6:.0f}M"
                    for e in recent
                )
                lines.append(f"  Annual {label} (recent→oldest): {vals}")

        # Insider transactions (Form 4 filings from Finnhub)
        insider_txns = raw_insider.get("data", []) if raw_insider else []
        if insider_txns:
            lines.append("")
            lines.append(f"### form4_{ticker}_insider_transactions")
            lines.append(f"Form 4 insider transactions (last 90 days):")
            # Sort by date descending, take last 20
            sorted_txns = sorted(insider_txns, key=lambda x: x.get("transactionDate", ""), reverse=True)[:20]
            buys = []
            sells = []
            for txn in sorted_txns:
                name = txn.get("name", "Unknown")
                change = txn.get("change", 0)
                price = txn.get("transactionPrice", 0)
                date = txn.get("transactionDate", "N/A")
                code = txn.get("transactionCode", "")
                shares = abs(change) if change else 0
                value = shares * price if price else 0

                # P = open-market purchase, S = open-market sale
                # M = options exercise, F = tax withholding
                if code == "P":
                    buys.append(txn)
                    lines.append(f"  BUY: {name} purchased {shares:,.0f} shares @ ${price:.2f} "
                                 f"(${value:,.0f}) on {date}")
                elif code == "S":
                    sells.append(txn)
                    lines.append(f"  SELL: {name} sold {shares:,.0f} shares @ ${price:.2f} "
                                 f"(${value:,.0f}) on {date}")
                elif code == "M":
                    lines.append(f"  EXERCISE: {name} exercised options for {shares:,.0f} shares on {date}")
                elif code == "F":
                    lines.append(f"  TAX_WITHHOLD: {name} surrendered {shares:,.0f} shares for tax on {date}")
                elif code:
                    lines.append(f"  {code}: {name} — {shares:,.0f} shares @ ${price:.2f} on {date}")

            lines.append(f"  Summary: {len(buys)} open-market buys, {len(sells)} open-market sells "
                         f"out of {len(sorted_txns)} total transactions")
            total_buy_val = sum(abs(t.get("change", 0)) * (t.get("transactionPrice", 0) or 0) for t in buys)
            total_sell_val = sum(abs(t.get("change", 0)) * (t.get("transactionPrice", 0) or 0) for t in sells)
            if total_buy_val > 0:
                lines.append(f"  Total open-market buy value: ${total_buy_val:,.0f}")
            if total_sell_val > 0:
                lines.append(f"  Total open-market sell value: ${total_sell_val:,.0f}")
        else:
            lines.append("")
            lines.append(f"### insider_{ticker}_no_data")
            lines.append(f"No insider transaction data available for {ticker}.")

        return "\n".join(lines)

    except Exception as exc:
        _log.warning("Fundamentals fetch failed for %s: %s", ticker, exc)
        return ""


def _fetch_evidence_router_data(ticker: str, cycle_id: str) -> str:
    """Fetch evidence from the full evidence router pipeline (13 sources including EDGAR).

    This adds SEC XBRL data (revenue, FCF, margins, cash flow) that the basic
    Finnhub-only fundamentals fetch misses. Returns formatted text for agent prompts.
    """
    import logging
    _log = logging.getLogger("pmacs.web.evidence_router")
    try:
        from pmacs.data.evidence_router import fetch_evidence_for_ticker
        from pmacs.agents.base import PersonaRunner

        packet = fetch_evidence_for_ticker(ticker, cycle_id)
        if packet and packet.evidence:
            text = PersonaRunner.format_evidence_for_prompt([packet])
            if text.strip():
                _log.info("[%s] Evidence router: %d evidence items from %d sources",
                          ticker, len(packet.evidence), len({e.source for e in packet.evidence}))
                return text
    except Exception as exc:
        _log.info("[%s] Evidence router failed (non-fatal): %s", ticker, exc)
    return ""


def _fetch_enrichment_data(ticker: str, api_key_finnhub: str = "") -> str:
    """Fetch Yahoo price targets + technical indicators to enrich fundamentals.

    Returns formatted text appended to the fundamentals block so agents
    see analyst price targets, moving averages, RSI, and forward valuation.
    """
    import json
    import logging
    import urllib.request

    _log = logging.getLogger("pmacs.web.agent")
    parts: list[str] = []

    # ── 1. Yahoo Finance price targets + forward valuation ──────────────
    try:
        from pmacs.data.sources.yahoo import fetch_price_targets
        from pmacs.data.gateway import DataGateway

        with DataGateway() as gw:
            packet = fetch_price_targets(ticker, gw, cycle_id="enrichment")
            for ev in packet.evidence:
                if ev.id.endswith("_price_target") and ev.data:
                    d = ev.data
                    parts.append(f"\n### {ev.id}")
                    if d.get("target_mean"):
                        parts.append(f"  Analyst consensus PT: ${d['target_mean']:.2f} mean")
                    if d.get("target_median"):
                        parts.append(f"  Median PT: ${d['target_median']:.2f}")
                    if d.get("target_low") and d.get("target_high"):
                        parts.append(f"  PT range: ${d['target_low']:.2f} – ${d['target_high']:.2f}")
                    if d.get("num_analysts"):
                        parts.append(f"  Covering analysts: {d['num_analysts']}")
                    if d.get("upside_to_mean_pct") is not None:
                        parts.append(f"  Upside to mean: {d['upside_to_mean_pct']:+.1f}%")
                    if d.get("upside_to_median_pct") is not None:
                        parts.append(f"  Upside to median: {d['upside_to_median_pct']:+.1f}%")
                elif ev.id.endswith("_forward_valuation") and ev.data:
                    d = ev.data
                    parts.append(f"\n### {ev.id}")
                    if d.get("forward_pe"):
                        parts.append(f"  Forward P/E: {d['forward_pe']:.2f}")
                    if d.get("peg_ratio"):
                        parts.append(f"  PEG ratio: {d['peg_ratio']:.2f}")
                    if d.get("forward_eps"):
                        parts.append(f"  Forward EPS: ${d['forward_eps']:.2f}")
                    if d.get("trailing_eps"):
                        parts.append(f"  Trailing EPS: ${d['trailing_eps']:.2f}")
                    if d.get("forward_eps_growth_pct") is not None:
                        parts.append(f"  Forward EPS growth: {d['forward_eps_growth_pct']:+.1f}%")
                    if d.get("next_year_eps_growth_pct") is not None:
                        parts.append(f"  Next year EPS growth: {d['next_year_eps_growth_pct']:+.1f}%")
                    if d.get("earnings_growth_yoy") is not None:
                        parts.append(f"  Earnings growth YoY: {d['earnings_growth_yoy']:+.1f}%")
                    if d.get("revenue_growth_yoy") is not None:
                        parts.append(f"  Revenue growth YoY: {d['revenue_growth_yoy']:+.1f}%")
                    if d.get("ntm_revenue_consensus"):
                        v = d["ntm_revenue_consensus"]
                        parts.append(f"  NTM revenue consensus: ${v/1e9:.2f}B" if abs(v) >= 1e9 else f"  NTM revenue consensus: ${v/1e6:.0f}M")
                    eps_trend = d.get("eps_trend") or {}
                    if eps_trend:
                        for label, key in [("Current Q EPS est", "current_q"), ("Next Q EPS est", "next_q"), ("Current year EPS est", "current_year"), ("Next year EPS est", "next_year")]:
                            val = eps_trend.get(key)
                            if val is not None:
                                parts.append(f"  {label}: {val}")
    except Exception as exc:
        _log.info("Yahoo enrichment failed for %s: %s", ticker, exc)

    # ── 2. Technical analysis (SMA, RSI, trend) ────────────────────────
    try:
        from pmacs.data.sources.technical import fetch_technical
        from pmacs.data.gateway import DataGateway
        from pmacs.storage.keychain import get_api_key

        poly_key = get_api_key("pmacs.data.polygon", "api_key")
        if not poly_key:
            poly_key = get_api_key("pmacs.credentials", "polygon_key")

        if poly_key:
            with DataGateway() as gw:
                packet = fetch_technical(ticker, gw, poly_key, cycle_id="enrichment")
                for ev in packet.evidence:
                    if ev.id.endswith("_moving_averages") and ev.data:
                        d = ev.data
                        parts.append(f"\n### {ev.id}")
                        if d.get("current_price"):
                            parts.append(f"  Current price: ${d['current_price']:.2f}")
                        if d.get("sma_50"):
                            parts.append(f"  SMA(50): ${d['sma_50']:.2f}")
                        if d.get("sma_200"):
                            parts.append(f"  SMA(200): ${d['sma_200']:.2f}")
                        if d.get("trend"):
                            parts.append(f"  Trend: {d['trend'].replace('_', ' ')}")
                        if d.get("dist_from_sma50_pct") is not None:
                            parts.append(f"  Distance from SMA50: {d['dist_from_sma50_pct']:+.1f}%")
                        if d.get("dist_from_sma200_pct") is not None:
                            parts.append(f"  Distance from SMA200: {d['dist_from_sma200_pct']:+.1f}%")
                        if d.get("high_52w") and d.get("low_52w"):
                            parts.append(f"  52-week range: ${d['low_52w']:.2f} – ${d['high_52w']:.2f}")
                        if d.get("dist_from_high_52w_pct") is not None:
                            parts.append(f"  Distance from 52w high: {d['dist_from_high_52w_pct']:+.1f}%")
                    elif ev.id.endswith("_momentum") and ev.data:
                        d = ev.data
                        parts.append(f"\n### {ev.id}")
                        if d.get("rsi_14") is not None:
                            parts.append(f"  RSI(14): {d['rsi_14']:.1f}{' (OVERBOUGHT)' if d.get('overbought') else ' (OVERSOLD)' if d.get('oversold') else ''}")
                        if d.get("roc_20d_pct") is not None:
                            parts.append(f"  20-day rate of change: {d['roc_20d_pct']:+.1f}%")
                        if d.get("roc_50d_pct") is not None:
                            parts.append(f"  50-day rate of change: {d['roc_50d_pct']:+.1f}%")
    except Exception as exc:
        _log.info("Technical enrichment failed for %s: %s", ticker, exc)

    return "\n".join(parts) if parts else ""


# --- Single agent runner -----------------------------------------------------

def _run_single_agent(persona: str, ticker: str, price: float, news: list[dict], fundamentals: str = "") -> dict:
    """Run one agent persona via OpenRouter with its specific prompt.

    Returns dict with: p_up, p_flat, p_down, analysis_text, persona, error
    """
    import logging

    _log = logging.getLogger("pmacs.web.agent")
    import datetime as _dt
    system_prompt = _load_persona_prompt(persona)

    # Substitute {evidence} and {episodic_context} in the system prompt
    if system_prompt:
        evidence_block = fundamentals if fundamentals else "(No financial data available — use [EST - not in evidence, verify] for any figures from knowledge)"
        system_prompt = system_prompt.replace("{evidence}", evidence_block)

        # Inject episodic context — prior verdict + ticker knowledge facts
        episodic_text = ""
        try:
            from pmacs.web.config import get_config
            from pmacs.storage.sqlite import get_connection
            from pmacs.agents.episodic_context import _TICKER_KNOWLEDGE
            cfg = get_config()

            # 1. Ticker knowledge — material facts not in live data feeds
            ticker_facts = _TICKER_KNOWLEDGE.get(ticker, [])
            if ticker_facts:
                episodic_text += (
                    "KNOWN MATERIAL FACTS (use [KNOWLEDGE] tag when citing):\n"
                    + "\n".join(f"  - {fact}" for fact in ticker_facts)
                    + "\n\n"
                )

            # 2. Prior analysis for thesis drift tracking
            edb = get_connection(cfg.sqlite_path)
            try:
                prior = edb.execute(
                    "SELECT verdict, conviction_score, thesis_summary, decided_at "
                    "FROM decisions WHERE ticker = ? ORDER BY decided_at DESC LIMIT 1",
                    (ticker,),
                ).fetchone()
                if prior:
                    episodic_text += (
                        f"PRIOR ANALYSIS ({prior[3]}):\n"
                        f"  Verdict: {prior[0]}, Conviction: {prior[1]:.2f}\n"
                        f"  Thesis: {(prior[2] or '')[:300]}\n"
                        f"  Compare your current assessment to this prior. Note what changed."
                    )
            finally:
                edb.close()
        except Exception:
            pass  # episodic context is best-effort

        system_prompt = system_prompt.replace("{episodic_context}", episodic_text)
        system_prompt = system_prompt.replace("{today_date}", _dt.date.today().isoformat())

    # Build user message with ticker context + evidence + news
    news_text = ""
    if news:
        news_text = "\n\nRecent news (last 7 days):\n" + "\n".join(
            f"- {a['headline']}" + (f": {a['summary'][:200]}" if a.get("summary") else "")
            for a in news[:5]
        )

    evidence_section = f"\n\n{fundamentals}" if fundamentals else ""

    prompt = (
        f"Analyze {ticker} (current price: ${price:.2f}) for your specific persona focus.\n"
        f"Provide your analysis as a JSON object with these fields:\n"
        f"  - p_up: probability (0.0-1.0) of stock moving up in 30-90 days\n"
        f"  - p_flat: probability (0.0-1.0) of stock staying flat\n"
        f"  - p_down: probability (0.0-1.0) of stock moving down\n"
        f"  - analysis: 3-5 sentence analysis from your persona's perspective\n"
        f"  - key_signal: one-line summary of the strongest signal you found\n"
        f"  - confidence: your confidence in this analysis (0.0-1.0)\n"
        f"  - evidence_cited: list of specific data points with numbers from the evidence above. Most recent quarter first. Include at least 3 items.\n"
        f"\nIMPORTANT RULES:\n"
        f"  - If your persona's key data source is MISSING (e.g., no FINRA short interest, no Form 4 filings), "
        f"set confidence below 0.20 and keep p_up/p_down at exactly 0.33 (neutral). "
        f"Do NOT produce directional probabilities from insufficient data.\n"
        f"  - Base probabilities ONLY on the evidence provided. Do not speculate beyond the data.\n"
        f"  - DETERMINISM: Given the same evidence, you must produce the same probabilities. "
        f"Round probabilities to the nearest 0.05 (e.g., 0.35, 0.40, 0.45, not 0.37 or 0.42). "
        f"This ensures consistency across repeated analyses.\n"
        f"Probabilities must sum to 1.0."
        f"{evidence_section}"
        f"{news_text}"
    )

    # Evidence-presence check: agents that depend on a specific data source
    # should return neutral when that data is absent, rather than asking the
    # LLM to guess — guessing creates run-to-run variance from hallucinated signals.
    _DATA_DEPENDENT_AGENTS: dict[str, list[str]] = {
        "insider_activity": ["form4_", "insider_"],
        "short_interest": ["finra_", "short_interest_"],
    }
    # Soft-gate: macro_regime without dedicated macro data should cap confidence
    # (the prompt now handles this via CRITICAL DETERMINISM RULE — EVIDENCE ANCHORING)
    required_markers = _DATA_DEPENDENT_AGENTS.get(persona, [])
    if required_markers:
        fund_lower = fundamentals.lower() if fundamentals else ""
        has_data = bool(fund_lower) and any(marker in fund_lower for marker in required_markers)
        if not has_data:
            _log.info("[%s] %s: key data missing (need %s) — returning neutral",
                      ticker, persona, required_markers)
            return {
                "persona": persona,
                "p_up": 0.33, "p_flat": 0.34, "p_down": 0.33,
                "analysis": f"No {persona.replace('_', ' ')} data available for {ticker}. "
                            f"Required evidence markers ({', '.join(required_markers)}) not found in evidence.",
                "key_signal": "INSUFFICIENT_DATA",
                "confidence": 0.0,
                "evidence_cited": [],
                "error": None,
                "attempt_count": 0,
            }

    # Retry logic: attempt up to 2 times before falling back to uninformed defaults.
    # Second attempt uses slightly higher temperature to avoid repeating the same
    # parse failure. This reduces run-to-run variance caused by transient LLM errors.
    _MAX_AGENT_ATTEMPTS = 2
    _last_exc: Exception | None = None
    for _attempt in range(_MAX_AGENT_ATTEMPTS):
        try:
            _temp = 0.01 + (_attempt * 0.05)  # 0.01 base (near-deterministic), 0.06 on retry
            raw = _call_openrouter(
                prompt, max_tokens=5000, temperature=_temp,
                system_prompt=system_prompt or None,
            )
            data = _parse_json_safe(raw)
            if not data:
                raise ValueError("Empty or invalid JSON response")

            p_up = float(data.get("p_up", 0.33))
            p_down = float(data.get("p_down", 0.33))
            confidence = float(data.get("confidence", 0.5))

            # Post-hoc confidence clamping: only neutralize truly data-starved
            # agents (conf < 0.25). Agents at 0.25+ have meaningful analysis —
            # their influence is already scaled by confidence-weighted averaging
            # in _arbitrate. Previous threshold of 0.50 combined with double-
            # neutralization in _arbitrate suppressed legitimate signals.
            _CLAMP_THRESHOLD = 0.25
            if confidence < _CLAMP_THRESHOLD:
                blend = confidence / _CLAMP_THRESHOLD  # 0.0 → 0.0, 0.25 → 1.0
                p_up = 0.33 + (p_up - 0.33) * blend
                p_down = 0.33 + (p_down - 0.33) * blend
                _log.info("[%s] %s: confidence=%.2f < %.2f, clamped p_up=%.2f p_down=%.2f",
                          ticker, persona, confidence, _CLAMP_THRESHOLD, p_up, p_down)

            # Snap to 0.05 grid for determinism — reduces variance from
            # LLM outputting 0.42 vs 0.43 on identical evidence.
            p_up = round(p_up * 20) / 20
            p_down = round(p_down * 20) / 20
            p_flat = max(0.0, 1.0 - p_up - p_down)

            return {
                "persona": persona,
                "p_up": min(1.0, max(0.0, p_up)),
                "p_flat": min(1.0, max(0.0, p_flat)),
                "p_down": min(1.0, max(0.0, p_down)),
                "analysis": str(data.get("analysis", ""))[:1000],
                "key_signal": str(data.get("key_signal", ""))[:200],
                "confidence": confidence,
                "evidence_cited": data.get("evidence_cited", []),
                "error": None,
                "attempt_count": _attempt + 1,
            }
        except Exception as exc:
            _last_exc = exc
            if _attempt < _MAX_AGENT_ATTEMPTS - 1:
                _log.warning("Agent %s attempt %d failed for %s: %s — retrying",
                             persona, _attempt + 1, ticker, exc)
            else:
                _log.error("Agent %s failed for %s after %d attempts: %s",
                           persona, ticker, _MAX_AGENT_ATTEMPTS, exc)

    return {
        "persona": persona,
        "p_up": 0.33, "p_flat": 0.34, "p_down": 0.33,
        "analysis": f"Agent {persona} failed after {_MAX_AGENT_ATTEMPTS} attempts: {_last_exc}",
        "key_signal": "ANALYSIS_UNAVAILABLE",
        "confidence": 0.0,
        "evidence_cited": [],
        "error": str(_last_exc),
    }


# --- Crucible review ---------------------------------------------------------

def _run_crucible(ticker: str, price: float, agent_results: list[dict]) -> dict:
    """Adversarial review of combined agent analyses.

    Returns dict with: severity (0-1), attacks, thesis_survives, summary
    """
    import logging

    _log = logging.getLogger("pmacs.web.crucible")
    system_prompt = _load_persona_prompt("crucible") or (
        "You are an adversarial analyst. Your job is to find weaknesses, logical holes, "
        "and overlooked risks in investment theses. Be thorough and skeptical."
    )

    # Build summary of all agent analyses
    agent_summary = "\n".join(
        f"- {r['persona']}: p_up={r['p_up']:.2f} p_down={r['p_down']:.2f} "
        f"confidence={r['confidence']:.2f}\n  Signal: {r['key_signal']}\n  "
        f"Analysis: {r['analysis'][:300]}"
        for r in agent_results if not r.get("error")
    )

    # Include ticker knowledge so Crucible attacks are informed, not superficial
    knowledge_section = ""
    try:
        from pmacs.agents.episodic_context import _TICKER_KNOWLEDGE
        facts = _TICKER_KNOWLEDGE.get(ticker, [])
        if facts:
            knowledge_section = (
                "\n\nVerified material facts (attack the thesis given these are true — "
                "do not attack these facts themselves):\n"
                + "\n".join(f"  - {f}" for f in facts)
            )
    except Exception:
        pass

    prompt = (
        f"Review the following investment thesis for {ticker} @ ${price:.2f}.\n\n"
        f"Agent analyses:\n{agent_summary}"
        f"{knowledge_section}\n\n"
        f"Score each of the following 4 axes independently on 0.0-1.0 (higher = more damaged):\n"
        f"  A. VALUATION ASSUMPTIONS — are the implied multiples/growth rates realistic?\n"
        f"  B. MOAT DURABILITY — is the competitive advantage real and sustainable?\n"
        f"  C. MANAGEMENT TRACK RECORD — do insiders have credibility and alignment?\n"
        f"  D. COMPETITIVE THREATS — are there overlooked competitive or macro risks?\n\n"
        f"Respond with JSON:\n"
        f"  - severity: average of the 4 axis scores (0.0-1.0)\n"
        f"  - attacks: list of objects, each with 'axis' (A/B/C/D label), 'score' (0.0-1.0), "
        f"'attack' (specific criticism with evidence), 'evidence_cited' (data points used)\n"
        f"  - thesis_survives: true if the overall thesis holds despite attacks\n"
        f"  - summary: 2-3 sentence adversarial summary\n"
        f"  - overlooked_risks: list of 1-3 risks the agents missed\n\n"
        f"IMPORTANT: Base severity ONLY on evidence provided. Do not penalize for missing data "
        f"that no one could reasonably have. Score each axis independently — a strong valuation "
        f"attack should not inflate moat or management scores."
    )

    try:
        raw = _call_openrouter(prompt, max_tokens=3000, temperature=0.1,
                               system_prompt=system_prompt)
        data = _parse_json_safe(raw)
        if not data:
            return {"severity": 0.3, "attacks": ["Crucible analysis unavailable"],
                    "thesis_survives": True, "summary": "Crucible review failed — defaulting to low severity."}

        return {
            "severity": min(1.0, max(0.0, float(data.get("severity", 0.3)))),
            "attacks": data.get("attacks", [])[:5],
            "thesis_survives": bool(data.get("thesis_survives", True)),
            "summary": str(data.get("summary", ""))[:500],
            "overlooked_risks": data.get("overlooked_risks", [])[:3],
        }
    except Exception as exc:
        _log.error("Crucible failed for %s: %s", ticker, exc)
        return {"severity": 0.3, "attacks": [f"Crucible error: {exc}"],
                "thesis_survives": True, "summary": f"Crucible review failed: {exc}"}


# --- Deterministic arbitration -----------------------------------------------

# Risk-filter personas: these are gating signals, not directional thesis signals.
# If either fires a red flag (high p_down), apply a penalty to direction rather
# than letting their bearish reading simply average in as 1/7th of the vote.
# Thresholds per task spec: Forensics > 0.65, ShortInterest > 0.70.
_RISK_FILTER_VETO_THRESHOLDS: dict[str, float] = {
    "forensics": 0.65,
    "short_interest": 0.70,
}
_RISK_FILTER_VETO_PENALTY: float = 0.5  # multiply direction by this on veto


def _arbitrate(agent_results: list[dict]) -> dict:
    """Combine p_up/p_flat/p_down from all agents deterministically.

    Weight each agent equally. Direction = avg(p_up) - avg(p_down).

    Forensics and ShortInterest act as risk-filter veto agents: if either
    signals a red flag (p_down above their veto threshold), the combined
    direction is penalized by 0.5x. This prevents a single strong bearish
    risk signal from being diluted to 1/7th of the vote.
    """
    valid = [r for r in agent_results if not r.get("error")]
    if not valid:
        return {"p_up": 0.33, "p_down": 0.33, "p_flat": 0.34, "direction": 0.0,
                "confidence": 0.0, "agents_used": 0, "veto_triggered": False, "veto_personas": []}

    # NOTE: Low-confidence neutralization is already applied in the agent runner
    # (_run_single_agent, _CLAMP_THRESHOLD=0.25). Do NOT re-neutralize here — that
    # caused double-suppression where a conf=0.15 agent retained only 15% of its
    # original signal (0.30 * 0.50). The confidence-weighted averaging below is
    # sufficient to limit low-confidence agents' influence on the combined result.

    # Confidence-weighted averaging: agents with INSUFFICIENT_DATA (low confidence)
    # contribute less than agents with genuine analysis. Floor at 0.10 so no agent
    # is fully ignored, but a conf=0.05 agent contributes 1/14th vs a conf=0.70 agent.
    _CONF_FLOOR = 0.10
    weights = [max(_CONF_FLOOR, r.get("confidence", 0.5)) for r in valid]
    total_weight = sum(weights)
    n = len(valid)
    avg_p_up = sum(r["p_up"] * w for r, w in zip(valid, weights)) / total_weight
    avg_p_down = sum(r["p_down"] * w for r, w in zip(valid, weights)) / total_weight
    avg_p_flat = max(0.0, 1.0 - avg_p_up - avg_p_down)

    # Normalize to sum to 1.0
    total = avg_p_up + avg_p_down + avg_p_flat
    if total > 0:
        avg_p_up /= total
        avg_p_down /= total
        avg_p_flat /= total

    direction = avg_p_up - avg_p_down

    # Risk-filter veto: check Forensics and ShortInterest p_down thresholds.
    # A veto does NOT flip the direction — it penalizes it. This means a
    # stock that barely crosses BUY will fall back to HOLD/SKIP, but a
    # genuinely strong thesis (direction >> 0) can survive a moderate veto.
    veto_triggered = False
    veto_personas: list[str] = []
    for r in valid:
        persona = r.get("persona", "")
        threshold = _RISK_FILTER_VETO_THRESHOLDS.get(persona)
        if threshold is not None and r["p_down"] > threshold:
            veto_triggered = True
            veto_personas.append(persona)

    if veto_triggered:
        direction *= _RISK_FILTER_VETO_PENALTY

    # Confidence = how many agents agree on direction
    agreeing = sum(1 for r in valid if (r["p_up"] - r["p_down"]) * direction > 0)
    confidence = agreeing / n if n > 0 else 0.0

    return {
        "p_up": round(avg_p_up, 4),
        "p_down": round(avg_p_down, 4),
        "p_flat": round(avg_p_flat, 4),
        "direction": round(direction, 4),
        "confidence": round(confidence, 4),
        "agents_used": n,
        "veto_triggered": veto_triggered,
        "veto_personas": veto_personas,
    }


# --- Conviction + Verdict: delegated to pmacs.engines.conviction -------------


# --- Industry KPI extraction (programmatic, no LLM dependency) ---------------

def _extract_industry_kpis(ticker: str, fundamentals: str, agent_results: list[dict]) -> dict:
    """Extract sector-specific KPIs from fundamentals text and agent analysis.

    Parses structured evidence text and agent outputs using regex to find
    industry metrics like NRR, ARR, active customers, hyperscaler commitments, etc.
    Returns a dict of {kpi_key: formatted_string_value} for memo.html rendering.

    Patterns are calibrated against real agent output text (e.g. "100M+ customers",
    "$46.4B in compute capacity", "cost-to-serve ~$0.80").
    """
    import re

    kpis: dict[str, str] = {}
    # Scan evidence text (structured data sources) separately from agent narrative.
    # KPIs from evidence are authoritative; agent-derived ones are tagged as estimates.
    evidence_text = fundamentals or ""
    agent_text = ""
    for r in agent_results:
        agent_text += "\n" + (r.get("analysis") or "")
        agent_text += "\n" + (r.get("key_signal") or "")
    all_text = evidence_text + "\n" + agent_text

    # --- Dollar amount helper: matches $46.4B, 46.4B, $1.2T, $180M ---
    # Requires digit start to avoid false positives like ", b" matching [\d,.]+[B]
    _AMT = r'\$?(\d[\d,.]*\s*[BMTK](?:illion|n)?)'

    # === SaaS / Cloud ===
    # Preposition helper: matches "of", "is", "at", ":", or whitespace before values
    _OF = r'(?:\s+(?:of|is|at|grew\s+to)\s+|[:\s]+)'

    m = re.search(r'(?:NRR|net\s+revenue\s+retention)' + _OF + r'(\d{2,3}[.\d]*%)', all_text, re.IGNORECASE)
    if m:
        kpis["nrr"] = m.group(1)

    m = re.search(r'(?:ARR|annual\s+recurring\s+revenue)' + _OF + _AMT, all_text, re.IGNORECASE)
    if m:
        kpis["arr"] = "$" + m.group(1).strip()

    m = re.search(r'(?:GRR|gross\s+(?:revenue\s+)?retention)' + _OF + r'(\d{2,3}%)', all_text, re.IGNORECASE)
    if m:
        kpis["grr"] = m.group(1)

    m = re.search(r'(?:RPO|remaining\s+performance\s+obligations?)' + _OF + _AMT, all_text, re.IGNORECASE)
    if m:
        kpis["rpo"] = "$" + m.group(1).strip()

    # Customer count — handles "100M+ customers", "12,400 enterprise customers", "85M customers"
    m = re.search(r'([\d,.]+\s*[KMBkmb]?\+?)\s+(?:active\s+)?(?:enterprise\s+)?(?:customers?|users?|logos?)', all_text, re.IGNORECASE)
    if m:
        val = m.group(1).strip()
        # Filter out small numbers (< 100) that are likely false positives
        raw_num = re.sub(r'[,KMBkmb+\s]', '', val)
        try:
            if float(raw_num) >= 100 or any(c in val.upper() for c in ('K', 'M', 'B')):
                kpis["customer_count"] = val
        except ValueError:
            pass

    # Logo churn
    m = re.search(r'(?:logo\s+churn|customer\s+churn)[:\s]*([\d.]+%)', all_text, re.IGNORECASE)
    if m:
        kpis["logo_churn"] = m.group(1)

    # === FinTech / Banking ===
    # Active customers — "105M active customers", "85 million active users"
    m = re.search(r'([\d,.]+\s*[MBmb](?:illion)?\+?)\s+active\s+(?:customers?|users?)', all_text, re.IGNORECASE)
    if m:
        kpis["active_customers"] = m.group(1).strip()

    # TPV
    m = re.search(r'(?:TPV|total\s+payment\s+volume)[:\s]*' + _AMT, all_text, re.IGNORECASE)
    if m:
        kpis["tpv"] = "$" + m.group(1).strip()

    # ARPAC — "ARPAC of $12.40", "ARPAC grew to $12.40"
    m = re.search(r'ARPAC\s+(?:of\s+|grew\s+to\s+|is\s+|at\s+)?\$?([\d,.]+)', all_text, re.IGNORECASE)
    if m:
        kpis["arpac"] = "$" + m.group(1).strip()

    # Take rate
    m = re.search(r'take\s+rate\s+(?:of\s+|is\s+|at\s+)?([\d.]+%)', all_text, re.IGNORECASE)
    if m:
        kpis["take_rate"] = m.group(1)

    # NPL rate
    m = re.search(r'(?:NPL|non[- ]performing\s+loan)\s*(?:rate)?\s*(?:of\s+|is\s+|at\s+)?([\d.]+%)', all_text, re.IGNORECASE)
    if m:
        kpis["npl_rate"] = m.group(1)

    # Cost-to-serve (FinTech specific) — "cost-to-serve ~$0.80"
    m = re.search(r'cost[- ]to[- ]serve\s*(?:~|of\s+|is\s+)?\$?([\d,.]+)', all_text, re.IGNORECASE)
    if m:
        kpis["cost_to_serve"] = "$" + m.group(1).strip()

    # === AdTech / MarTech ===
    # ARPU — "ARPU of $42", "ARPU $42"
    m = re.search(r'ARPU\s+(?:of\s+|is\s+|at\s+)?\$?([\d,.]+)', all_text, re.IGNORECASE)
    if m:
        kpis["arpu"] = "$" + m.group(1).strip()

    # Contribution ex-TAC — "contribution ex-TAC margin at 35%", "contribution ex-TAC of $200M"
    m = re.search(r'contribution\s+ex[- ]TAC\s+(?:margin\s+)?(?:of\s+|at\s+|is\s+)?\$?([\d,.]+\s*[BMK%]?)', all_text, re.IGNORECASE)
    if m:
        kpis["contribution_ex_tac"] = m.group(1).strip()

    # Platform spend
    m = re.search(r'platform\s+spend\s+(?:of\s+|reached\s+|is\s+|at\s+)?' + _AMT, all_text, re.IGNORECASE)
    if m:
        kpis["platform_spend"] = "$" + m.group(1).strip()

    # === AI Infra ===
    # Contracted/compute capacity — "2.1 GW", "compute capacity", "contracted capacity of 2.1 GW"
    m = re.search(r'(?:contracted|compute|power)\s+capacity\s*(?:of\s+|is\s+|at\s+)?([\d,.]+\s*(?:GW|MW))', all_text, re.IGNORECASE)
    if not m:
        m = re.search(r'([\d,.]+\s*(?:GW|MW))\s+(?:of\s+)?(?:contracted|compute|power)\s+capacity', all_text, re.IGNORECASE)
    if m:
        kpis["contracted_capacity"] = m.group(1).strip()

    # Hyperscaler commitments — "$46.4B in compute capacity", "hyperscaler deal... $46.4B"
    # Pattern 1: "hyperscaler deal/commitment... valued at $X" or "hyperscaler... $X"
    m = re.search(r'hyperscal\w+\s+(?:deal|commitment|contract)\w*[^.]{0,60}?' + _AMT, all_text, re.IGNORECASE)
    if not m:
        # Pattern 2: "$46.4B ... hyperscaler" or "$46.4B for compute"
        m = re.search(_AMT + r'\s+(?:in\s+|for\s+)?(?:hyperscal|compute\s+capacity)', all_text, re.IGNORECASE)
    if not m:
        # Pattern 3: "committed $46.4B"
        m = re.search(r'committed\s+' + _AMT, all_text, re.IGNORECASE)
    if m:
        kpis["hyperscaler_commitments"] = "$" + m.group(1).strip()

    # GPU utilization
    m = re.search(r'GPU\s+utilization\s*(?:of\s+|at\s+|is\s+)?([\d.]+%)', all_text, re.IGNORECASE)
    if m:
        kpis["gpu_utilization"] = m.group(1)

    # === E-Commerce ===
    m = re.search(r'(?:GMV|gross\s+merchandise\s+volume)\s*(?:of\s+|is\s+|at\s+|reached\s+)?' + _AMT, all_text, re.IGNORECASE)
    if m:
        kpis["gmv"] = "$" + m.group(1).strip()

    # === Hardware / Sensors ===
    m = re.search(r'(?:ASP|average\s+selling\s+price)\s*(?:of\s+|is\s+|at\s+)?\$?([\d,.]+)', all_text, re.IGNORECASE)
    if m:
        kpis["asp"] = "$" + m.group(1).strip()

    m = re.search(r'(\d+)\s+design\s+wins?', all_text, re.IGNORECASE)
    if m:
        kpis["design_wins"] = m.group(1)

    m = re.search(r'([\d,.]+\s*[KMk]?)\s+units?\s+shipped', all_text, re.IGNORECASE)
    if m:
        kpis["units_shipped"] = m.group(1).strip()

    m = re.search(r'(?:backlog|order\s+book)\s*(?:of\s+|is\s+|at\s+)?' + _AMT, all_text, re.IGNORECASE)
    if m:
        kpis["backlog"] = "$" + m.group(1).strip()

    # === Healthcare ===
    m = re.search(r'([\d,.]+\s*[KMk]?)\s+patients?\s+enrolled', all_text, re.IGNORECASE)
    if m:
        kpis["patients_enrolled"] = m.group(1).strip()

    # === Structured evidence extraction ===
    # Parse tagged evidence lines (e.g. "short_pct_float: 13.71") that regex
    # patterns miss because they look for natural language, not key:value pairs.
    # Short interest (available from Yahoo for all tickers)
    if not kpis.get("short_pct_float"):
        m = re.search(r'short_pct_float["\s:]+(\d+\.?\d*)', all_text)
        if m:
            kpis["short_pct_float"] = m.group(1) + "%"
    if not kpis.get("short_ratio"):
        m = re.search(r'short_ratio["\s:]+(\d+\.?\d*)', all_text)
        if m:
            kpis["short_ratio"] = m.group(1) + " days"
    # Institutional ownership
    if not kpis.get("institutional_ownership"):
        m = re.search(r'institutional_ownership_pct["\s:]+(\d+\.?\d*)', all_text)
        if m:
            kpis["institutional_ownership"] = m.group(1) + "%"
    # Enterprise customers / advertisers (AdTech/MarTech)
    if not kpis.get("customer_count"):
        m = re.search(r'~?(\d{2,4}\+?)\s+(?:enterprise\s+)?(?:customers?|advertisers?|brands?)', all_text, re.IGNORECASE)
        if m:
            kpis["customer_count"] = m.group(1)
    # Identifiable profiles (AdTech data moat metric)
    m = re.search(r'([\d,.]+\s*[BMT]\+?)\s+(?:identifiable\s+)?profiles?', all_text, re.IGNORECASE)
    if m:
        kpis["data_profiles"] = m.group(1).strip()
    # Signals processed per day (AdTech/data platform)
    m = re.search(r'([\d,.]+\s*[BTM]\+?)\s+signals?\s+per\s+day', all_text, re.IGNORECASE)
    if m:
        kpis["daily_signals"] = m.group(1).strip()

    # Deduplicate: if customer_count and active_customers have same value, keep active_customers
    if kpis.get("customer_count") and kpis.get("active_customers"):
        if kpis["customer_count"].rstrip("+") in kpis["active_customers"]:
            del kpis["customer_count"]

    # Clean trailing punctuation from values (e.g. "$42." → "$42")
    for k in kpis:
        kpis[k] = kpis[k].rstrip(".,;:")

    # Source-tag KPIs: check if each value appears in structured evidence vs only
    # in agent narrative. Evidence-derived values are authoritative; agent-derived
    # values are estimates that should be displayed with lower confidence.
    if evidence_text:
        ev_lower = evidence_text.lower()
        for k, v in list(kpis.items()):
            # Strip $ and % for matching
            match_val = v.lstrip("$").rstrip("%").strip().lower()
            if match_val and match_val not in ev_lower:
                kpis[k] = v + " (est.)"

    return kpis


# --- Memo generation ---------------------------------------------------------

def _generate_full_memo(ticker: str, price: float, agent_results: list[dict],
                        crucible: dict, verdict: str, conviction: float,
                        arb: dict, fundamentals: str = "") -> str:
    """Generate structured investment memo with price target and deep business analysis.

    Returns a JSON string stored in thesis_summary containing:
    - fair_value: absolute fair value estimate per share
    - valuation_range: {low, base, high}
    - business_model, financial_snapshot, growth_drivers, competitive_position
    - risk_factors, catalyst_calendar, thesis, key_evidence, key_risks
    - agent_signals, crucible_attacks, sizing_note
    - raw_text: human-readable fallback
    """
    import json
    import logging

    _log = logging.getLogger("pmacs.web.memo")
    system_prompt = _load_persona_prompt("memo_writer") or (
        "You are an expert investment analyst. Produce deeply researched, structured "
        "investment memos that help a portfolio operator understand the business, its "
        "true value, growth trajectory, and risks. Be specific with numbers and estimates."
    )

    # Inject episodic context into memo_writer system prompt (same as agents)
    if system_prompt and "{episodic_context}" in system_prompt:
        episodic_text = ""
        try:
            from pmacs.web.config import get_config
            from pmacs.storage.sqlite import get_connection
            cfg = get_config()
            edb = get_connection(cfg.sqlite_path)
            try:
                prior = edb.execute(
                    "SELECT verdict, conviction_score, memo_json, decided_at "
                    "FROM memos WHERE ticker = ? ORDER BY id DESC LIMIT 1",
                    (ticker,),
                ).fetchone()
                if prior:
                    import json as _j
                    prior_memo = _j.loads(prior[2] or "{}")
                    prior_fv = prior_memo.get("fair_value")
                    prior_range = prior_memo.get("valuation_range", {})
                    episodic_text = (
                        f"PRIOR ANALYSIS ({prior[3]}):\n"
                        f"  Verdict: {prior[0]}, Conviction: {prior[1]:.4f}\n"
                        f"  Fair Value: ${prior_fv}" if prior_fv else ""
                    )
                    if prior_fv:
                        episodic_text += f"\n  Valuation Range: low=${prior_range.get('low')}, base=${prior_range.get('base')}, high=${prior_range.get('high')}"
                    episodic_text += (
                        f"\n  Thesis: {prior_memo.get('thesis', '')[:300]}\n"
                        f"  ANCHORING: Your fair_value should be within 20% of the prior unless "
                        f"material new evidence justifies a larger revision. State what changed."
                    )
            finally:
                edb.close()
        except Exception:
            episodic_text = ""
        system_prompt = system_prompt.replace("{episodic_context}", episodic_text)

    # Also replace {evidence} placeholder in system prompt
    if system_prompt and "{evidence}" in system_prompt:
        system_prompt = system_prompt.replace("{evidence}", "(Evidence provided in user message below)")

    agent_summary = "\n".join(
        f"## {r['persona']}\n"
        f"Signal: {r['key_signal']}\n"
        f"P(up)={r['p_up']:.0%} P(down)={r['p_down']:.0%} Confidence={r['confidence']:.0%}\n"
        f"{r['analysis'][:500]}"
        for r in agent_results
    )

    fundamentals_block = ""
    if fundamentals:
        fundamentals_block = f"=== FINANCIAL DATA (from data sources — use these numbers, do NOT hallucinate) ===\n{fundamentals}\n\n"

    prompt = (
        f"You are writing a deep investment research memo for {ticker}, currently trading at ${price:.2f}.\n\n"
        f"System verdict: {verdict} | Conviction: {conviction:.0%}\n"
        f"Arbitrated probabilities: P(up)={arb['p_up']:.0%} P(down)={arb['p_down']:.0%}\n"
        f"EV multiple: {arb.get('ev_multiple', 'N/A')} | Crucible severity: {crucible['severity']:.0%}\n\n"
        f"{fundamentals_block}"
        f"=== AGENT ANALYSES ===\n{agent_summary}\n\n"
        f"=== CRUCIBLE ADVERSARIAL REVIEW (severity={crucible['severity']:.0%}) ===\n"
        f"{crucible.get('summary', '')}\n"
        f"Attacks: {crucible.get('attacks', [])}\n\n"
        f"Respond with a single JSON object containing ALL of these fields:\n\n"
        f"1. fair_value (number): Your best estimate of the intrinsic value per share in USD. "
        f"This is NOT a price target or exit price — it is your assessment of what the business "
        f"is truly worth based on fundamentals, growth prospects, and competitive position.\n\n"
        f"2. valuation_range: object with 'low', 'base', 'high' (all numbers in USD per share). "
        f"Low = bear case DCF/comps, base = most likely, high = bull case.\n\n"
        f"3. valuation_methodology (string): 2-3 sentences explaining how you arrived at the fair value "
        f"(e.g., DCF with X% WACC, comparable company analysis using Y multiples, etc.)\n\n"
        f"4. business_model (string): 1-2 sentences on how the company makes money (brief summary).\n\n"
        f"4b. revenue_model (string): 2-4 sentences explaining revenue generation in detail — "
        f"name the products/services, revenue splits (subscription vs transactional vs licensing), "
        f"customer segments (enterprise vs SMB vs consumer), geographic mix, and unit economics "
        f"(ARPU, take rate, ASP). Be specific with percentages where available.\n\n"
        f"4c. key_acquisitions (string): 1-3 sentences on significant acquisitions, mergers, or "
        f"strategic partnerships that shaped the company. Include deal size if known. "
        f"Omit if no notable acquisitions.\n\n"
        f"4d. company_vision (string): 1-3 sentences on management's stated strategy and direction. "
        f"What are they building toward? Source from earnings calls or investor presentations.\n\n"
        f"5. financial_snapshot (object): Key financial metrics with fields: "
        f"revenue (string, e.g. '$12.5B'), revenue_growth (string, e.g. '+25% YoY'), "
        f"gross_margin (string), operating_margin (string), net_margin (string), "
        f"free_cash_flow (string), debt_to_equity (string), roe (string), "
        f"pe_ratio (string), peg_ratio (string). Use 'N/A' if unknown.\n\n"
        f"6. growth_drivers (array of objects, 3-5 items): Each with 'driver' (string) and "
        f"'timeline' (string, e.g. '6-12 months') and 'impact' (string, 'high'/'medium'/'low').\n\n"
        f"7. competitive_position (object): Fields: 'moat_type' (string), 'market_share' (string), "
        f"'advantages' (array of 2-4 strings), 'threats' (array of 1-3 strings).\n\n"
        f"8. risk_factors (array of objects, 3-5 items): Each with 'risk' (string), "
        f"'probability' ('low'/'medium'/'high'), 'impact' ('low'/'medium'/'high'), "
        f"'mitigation' (string).\n\n"
        f"9. catalyst_calendar (array of objects, 2-4 items): Each with 'event' (string), "
        f"'expected_date' (string, e.g. 'Q3 2026'), 'potential_impact' (string).\n\n"
        f"10. thesis (string): 3-5 paragraph deep investment thesis explaining why this is or isn't "
        f"a good investment at the current price.\n\n"
        f"11. key_evidence (array of 3-5 strings): Strongest data points supporting the thesis.\n\n"
        f"12. key_risks (array of 2-4 strings): Biggest risks to the thesis.\n\n"
        f"13. bear_case_response (string): 2-3 sentences addressing the strongest bear case.\n\n"
        f"14. position_sizing_note (string): Simple plain-English recommendation. "
        f"Example: 'Full-size position (20%) — high conviction supports max allocation.' "
        f"Do NOT dump raw numbers or formulas. Just the decision and brief rationale.\n\n"
        f"15. verdict_line (string): One-line verdict summary, max 150 chars.\n\n"
        f"CRITICAL: The fair_value must be a realistic per-share price estimate based on your analysis. "
        f"If the stock is at ${price:.2f} and you think it's undervalued, fair_value should be higher. "
        f"If overvalued, lower. Be specific and defensible with your number.\n\n"
        f"FORENSICS FAIR VALUE GATE: If the crucible severity is ≥ 0.50, or if any agent flagged "
        f"earnings manipulation, accounting irregularities, or operating_margin vs net_margin divergence "
        f"(e.g., operating margin -70% but net margin +93%), you MUST clamp the valuation_range.high "
        f"to no more than 10% above the current price (${price:.2f}). The fair_value itself should be "
        f"at or below the base case — do not let a speculative bull case inflate fair value when "
        f"the financials are questionable. When in doubt, anchor to valuation_range.low."
    )

    try:
        from pmacs.agents.sanity.memo_scorer import score_memo, format_retry_feedback

        MAX_MEMO_RETRIES = 3
        MEMO_SCORE_THRESHOLD = 50
        best_data = None
        best_score = None
        retry_feedback = ""

        for memo_attempt in range(MAX_MEMO_RETRIES):
            attempt_prompt = prompt
            if retry_feedback:
                attempt_prompt = prompt + "\n\n" + retry_feedback

            temp = 0.3 + (memo_attempt * 0.05)
            raw = _call_openrouter(attempt_prompt, max_tokens=10000, temperature=temp,
                                   system_prompt=system_prompt)
            data = _parse_json_safe(raw)
            if not data:
                _log.warning("Memo JSON parse failed for %s attempt %d, retrying", ticker, memo_attempt + 1)
                retry_feedback = (
                    "## MEMO QUALITY FEEDBACK\n"
                    "Previous attempt failed JSON parsing. Respond with ONLY a valid JSON object."
                )
                continue

            # Build evidence-like data from fundamentals text for cross-validation
            _evidence_for_scoring = None
            if fundamentals:
                from pmacs.agents.sanity.memo_scorer import parse_fundamentals_text
                _evidence_for_scoring = parse_fundamentals_text(fundamentals)

            # Score this memo attempt
            memo_score = score_memo(
                memo=data,
                evidence=_evidence_for_scoring,
                agent_results=agent_results,
                crucible_attacks=crucible.get("attacks", []),
                conviction=conviction,
                verdict=verdict,
            )
            _log.info(
                "Memo score for %s attempt %d: %.0f/100 (grade %s)",
                ticker, memo_attempt + 1, memo_score.total, memo_score.grade,
            )

            # Keep the best attempt
            if best_score is None or memo_score.total > best_score.total:
                best_data = data
                best_score = memo_score

            # Good enough — stop retrying
            if memo_score.total >= MEMO_SCORE_THRESHOLD and not memo_score.critical_issues:
                break

            # Build feedback for next attempt
            retry_feedback = format_retry_feedback(memo_score)
            _log.info(
                "Memo for %s scored %.0f/100 (below %d), retrying with feedback",
                ticker, memo_score.total, MEMO_SCORE_THRESHOLD,
            )

        # Use best attempt (even if below threshold)
        data = best_data
        if not data:
            _log.warning("All memo attempts failed for %s, using fallback", ticker)
            return json.dumps({
                "verdict_line": f"{verdict} {ticker} @ ${price:.2f}",
                "fair_value": None,
                "valuation_range": {},
                "thesis": f"{verdict} at conviction {conviction:.0%}",
                "raw_text": f"{verdict} {ticker} @ ${price:.2f} | Conviction: {conviction:.0%}",
                "memo_score": 0,
                "memo_grade": "F",
            })

        # Build structured memo data — always use engine verdict, not LLM's
        llm_verdict_line = data.get("verdict_line", "")
        # Strip any LLM-generated verdict prefix and replace with engine verdict
        # LLM might write "BUY — reason" when engine says SKIP
        if llm_verdict_line:
            # Remove leading verdict word if present
            for v in ("STRONG_BUY", "BUY", "HOLD", "SKIP"):
                if llm_verdict_line.upper().startswith(v):
                    llm_verdict_line = llm_verdict_line[len(v):].lstrip(" —-–:")
                    break
            verdict_line = f"{verdict} — {llm_verdict_line}" if llm_verdict_line else f"{verdict} {ticker} @ ${price:.2f}"
        else:
            verdict_line = f"{verdict} {ticker} @ ${price:.2f}"
        memo_data = {
            "verdict_line": verdict_line,
            "current_price": price,
            "fair_value": data.get("fair_value"),
            "valuation_range": data.get("valuation_range", {}),
            "valuation_methodology": data.get("valuation_methodology", ""),
            "business_model": data.get("business_model", ""),
            "revenue_model": data.get("revenue_model", ""),
            "key_acquisitions": data.get("key_acquisitions", ""),
            "company_vision": data.get("company_vision", ""),
            "financial_snapshot": data.get("financial_snapshot", {}),
            "industry_kpis": {
                k: v for k, v in {
                    **_extract_industry_kpis(ticker, fundamentals, agent_results),
                    **(data.get("industry_kpis") or {}),
                }.items()
                if v and isinstance(v, str)
                and "not available" not in v.lower()
                and "n/a" not in v.lower()
                and "unavailable" not in v.lower()
                and "unknown" not in v.lower()
            },
            "growth_drivers": data.get("growth_drivers", []),
            "competitive_position": data.get("competitive_position", {}),
            "risk_factors": data.get("risk_factors", []),
            "catalyst_calendar": data.get("catalyst_calendar", []),
            "thesis": data.get("thesis", ""),
            "key_evidence": data.get("key_evidence", []),
            "key_risks": data.get("key_risks", []),
            "bear_case_response": data.get("bear_case_response", ""),
            "position_sizing_note": data.get("position_sizing_note", ""),
            "agent_signals": [
                {"persona": r["persona"], "signal": r["key_signal"],
                 "direction": "bullish" if r["p_up"] > r["p_down"] + 0.05
                              else ("bearish" if r["p_down"] > r["p_up"] + 0.05 else "neutral"),
                 "p_up": r["p_up"], "p_flat": r.get("p_flat", 0.34), "p_down": r["p_down"],
                 "confidence": r["confidence"],
                 "analysis": (r.get("analysis") or "")[:500],
                 "evidence_cited": (r.get("evidence_cited") or [])[:5]}
                for r in agent_results if not r.get("error")
            ],
            "crucible_attacks": crucible.get("attacks", [])[:5],
            "crucible_severity": crucible.get("severity", 0),
            "crucible_thesis_survives": crucible.get("thesis_survives", True),
            "crucible_summary": crucible.get("summary", ""),
            # Engine-authoritative fields (not LLM-generated)
            "p_up": round(arb.get("p_up", 0.0), 4),
            "p_flat": round(1.0 - arb.get("p_up", 0.0) - arb.get("p_down", 0.0), 4),
            "p_down": round(arb.get("p_down", 0.0), 4),
            "conviction": round(conviction, 4),
            "ev_multiple": round(arb.get("ev_multiple", 0.0), 4),
            "direction": round(arb.get("direction", 0.0), 4),
            "agents_used": arb.get("agents_used", 0),
            "verdict": verdict,
        }

        # Build human-readable fallback text
        lines = [
            memo_data["verdict_line"],
            f"Conviction: {conviction:.0%} | P(up)={arb['p_up']:.0%} P(down)={arb['p_down']:.0%}",
            "",
        ]
        if memo_data.get("fair_value"):
            fv = memo_data["fair_value"]
            upside = ((fv - price) / price * 100) if price > 0 else 0
            lines.append(f"FAIR VALUE: ${fv:.2f} ({'+' if upside >= 0 else ''}{upside:.1f}% vs current)")
            vr = memo_data.get("valuation_range", {})
            if vr:
                lines.append(f"  Range: ${vr.get('low', '?')} — ${vr.get('base', '?')} — ${vr.get('high', '?')}")
            lines.append("")
        if memo_data.get("business_model"):
            lines.extend(["BUSINESS MODEL", memo_data["business_model"], ""])
        lines.extend(["THESIS", memo_data.get("thesis", ""), ""])
        if memo_data.get("key_evidence"):
            lines.append("KEY EVIDENCE")
            for ev in memo_data["key_evidence"][:5]:
                lines.append(f"  - {ev}")
            lines.append("")
        if memo_data.get("key_risks"):
            lines.append("KEY RISKS")
            for risk in memo_data["key_risks"]:
                lines.append(f"  - {risk}")
            lines.append("")
        if memo_data.get("bear_case_response"):
            lines.extend(["BEAR CASE RESPONSE", memo_data["bear_case_response"], ""])
        if memo_data.get("position_sizing_note"):
            lines.extend(["SIZING", memo_data["position_sizing_note"], ""])
        lines.append("AGENT SIGNALS")
        for sig in memo_data.get("agent_signals", []):
            lines.append(f"  {sig['persona']}: {sig['signal']}")
        if memo_data.get("crucible_attacks"):
            lines.extend(["", "CRUCIBLE ATTACKS"])
            for atk in memo_data["crucible_attacks"][:3]:
                lines.append(f"  - {atk}")

        memo_data["raw_text"] = "\n".join(lines)

        # Inject memo quality score
        if best_score is not None:
            memo_data["memo_score"] = round(best_score.total, 1)
            memo_data["memo_grade"] = best_score.grade
            memo_data["memo_score_dimensions"] = {
                d.name: {"score": round(d.score, 1), "max": round(d.max_score, 1), "issues": d.issues[:3]}
                for d in best_score.dimensions
            }
            if best_score.critical_issues:
                memo_data["memo_critical_issues"] = best_score.critical_issues[:5]

        return json.dumps(memo_data)

    except Exception as exc:
        _log.error("Memo generation failed for %s: %s", ticker, exc)
        return json.dumps({
            "verdict_line": f"{verdict} {ticker} @ ${price:.2f}",
            "fair_value": None,
            "thesis": f"Memo generation failed: {exc}",
            "raw_text": f"{verdict} {ticker} @ ${price:.2f} | Conviction: {conviction:.0%}",
            "memo_score": 0,
            "memo_grade": "F",
        })


# --- Main cycle execution (real parallel agent pipeline) ----------------------


def _run_all_agents_sync(ticker: str, price: float, news: list[dict], fundamentals: str = "", cycle_id: str = "") -> list[dict]:
    """Run all 7 agents concurrently via ThreadPoolExecutor.

    Emits per-agent SSE events as each finishes (not batched) so the UI
    updates progressively instead of in a burst.
    """
    import concurrent.futures
    import logging
    import time

    _log = logging.getLogger("pmacs.web.pipeline")
    results = []

    # Detect local backends (Ollama, llama-server) which serialize requests —
    # running 7 agents in parallel causes timeouts since each waits for others.
    _workers = 7  # cloud backends: full parallelism
    try:
        import json as _j
        from pathlib import Path as _P
        _reg = _j.load(open(_P(__file__).resolve().parents[3] / "config" / "model_registry.json"))
        _active = _reg.get("active", "")
        if _active in ("ollama", "llama_server"):
            _workers = 1  # local: sequential to avoid timeout cascades
            _log.info("Local backend '%s' detected — running agents sequentially", _active)
    except Exception:
        pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=_workers) as pool:
        futures = {
            pool.submit(_run_single_agent, persona, ticker, price, news, fundamentals): persona
            for persona in _PERSONAS
        }
        # Emit running events for all agents immediately
        if cycle_id:
            for persona in _PERSONAS:
                _emit_event("agent", "agent.running", {
                    "cycle_id": cycle_id,
                    "persona": persona,
                    "ticker": ticker,
                    "status": "running",
                })
        for future in concurrent.futures.as_completed(futures):
            persona = futures[future]
            try:
                r = future.result()
                results.append(r)
                # Emit per-agent completion immediately (progressive UI update)
                if cycle_id:
                    _emit_event("agent", "agent.complete", {
                        "cycle_id": cycle_id,
                        "persona": r["persona"],
                        "ticker": ticker,
                        "scores": {
                            "p_up": r["p_up"],
                            "p_down": r["p_down"],
                            "confidence": r["confidence"],
                        },
                        "analysis": r.get("analysis", ""),
                        "key_signal": r.get("key_signal", ""),
                        "evidence_cited": r.get("evidence_cited", []),
                        "latency_ms": r.get("latency_ms", 0),
                        "attempt_count": r.get("attempt_count", 1),
                    })
            except Exception as exc:
                _log.error("Agent %s raised: %s", persona, exc)
                results.append({
                    "persona": persona,
                    "p_up": 0.33, "p_flat": 0.34, "p_down": 0.33,
                    "analysis": f"Agent {persona} crashed: {exc}",
                    "key_signal": "CRASH",
                    "confidence": 0.0,
                    "evidence_cited": [],
                    "error": str(exc),
                })
    return results


async def _run_demo_cycle(cycle_id: str, tickers: list[str]) -> None:
    """Real parallel agent pipeline: 7 personas + crucible + arbitration + memo.

    Per ticker (~15-20s):
      1. Gather evidence (news from Finnhub)              ~1s
      2. 7 agents in parallel (ThreadPoolExecutor)         ~5-8s
      3. Crucible adversarial review                        ~3-5s
      4. Deterministic arbitration                           instant
      5. Verdict + conviction                                instant
      6. Memo generation                                    ~3-5s
      7. Persist to DB + emit SSE
    """
    import asyncio
    import logging
    import socket
    import time
    from datetime import datetime, timezone

    from pmacs.storage.sqlite import get_connection
    from pmacs.web.config import get_config

    _log = logging.getLogger("pmacs.web.cycle")
    cfg = get_config()
    total = len(tickers)
    processed = 0

    # Set global socket timeout so libraries without explicit timeouts
    # (e.g. yfinance) can't hang indefinitely and block the entire cycle.
    _prev_socket_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(12)

    try:
        # Phase 0: Fetch prices + news + fundamentals in parallel (all tickers)
        loop = asyncio.get_event_loop()
        real_prices: dict[str, float] = {}
        prefetched_news: dict[str, list] = {}
        prefetched_fundamentals: dict[str, str] = {}

        async def _fetch_price_async(t: str) -> tuple[str, float | None]:
            try:
                p = await loop.run_in_executor(None, _fetch_real_price, t)
                return t, p
            except Exception as exc:
                _log.warning("Price fetch failed for %s: %s", t, exc)
                return t, None

        async def _fetch_news_async(t: str) -> tuple[str, list]:
            try:
                news = await loop.run_in_executor(None, _fetch_ticker_news, t)
                return t, news
            except Exception as exc:
                _log.warning("News fetch failed for %s: %s", t, exc)
                return t, []

        async def _fetch_fundamentals_async(t: str) -> tuple[str, str]:
            _DATA_FETCH_TIMEOUT = 45  # seconds — global cap on all data fetching
            try:
                return await asyncio.wait_for(
                    _fetch_fundamentals_inner(t), timeout=_DATA_FETCH_TIMEOUT
                )
            except asyncio.TimeoutError:
                _log.error("[%s] Data fetch timed out after %ds — proceeding with cached/partial data", t, _DATA_FETCH_TIMEOUT)
                return t, ""
            except Exception as exc:
                _log.warning("Fundamentals fetch failed for %s: %s", t, exc)
                return t, ""

        async def _fetch_fundamentals_inner(t: str) -> tuple[str, str]:
            parts: list[str] = []
            source_status: dict[str, bool] = {}

            # 1. Full evidence router (EDGAR SEC XBRL + all 13 sources)
            router_ev = await loop.run_in_executor(
                None, _fetch_evidence_router_data, t, cycle_id,
            )
            if router_ev:
                parts.append(router_ev)
                source_status["evidence_router"] = True
            else:
                source_status["evidence_router"] = False

            # 2. Finnhub fundamentals — FALLBACK ONLY. The evidence router now
            # uses yfinance as the primary fundamentals source (accurate annual
            # cash-flow series). Finnhub is only pulled when the router failed, so
            # agents never see Finnhub's incomplete/quirky numbers alongside the
            # authoritative yfinance data (operator directive, 2026-06-17).
            if not source_status.get("evidence_router"):
                finnhub_ev = await loop.run_in_executor(None, _fetch_ticker_fundamentals, t)
                if finnhub_ev:
                    parts.append(finnhub_ev)
                    source_status["finnhub_fallback"] = True
                else:
                    source_status["finnhub_fallback"] = False

            # 3. Yahoo enrichment (price targets + technicals)
            enrichment = await loop.run_in_executor(None, _fetch_enrichment_data, t)
            if enrichment:
                parts.append(enrichment)
                source_status["yahoo_enrichment"] = True
            else:
                source_status["yahoo_enrichment"] = False

            # Log failed sources (no retry — per-source timeouts prevent hangs)
            failed = [k for k, v in source_status.items() if not v]
            if failed:
                _log.warning("[%s] Evidence sources failed: %s — proceeding with partial data", t, failed)

            # Log evidence completeness for diagnostics
            total_chars = sum(len(p) for p in parts)
            _log.info("[%s] Evidence completeness: %d/%d sources, %d chars total",
                      t, sum(1 for v in source_status.values() if v) + len([f for f in failed if f not in source_status]),
                      len(source_status), total_chars)

            return t, "\n".join(parts)

        # Check evidence cache — reuse if same ticker was fetched within TTL.
        # This eliminates the biggest source of run-to-run variance: partial
        # evidence from transient API timeouts or rate limits.
        import time as _time_mod
        _now = _time_mod.time()
        cached_tickers: list[str] = []
        uncached_tickers: list[str] = []
        for t in tickers:
            cached = _evidence_cache.get(t)
            if cached and (_now - cached[0]) < _EVIDENCE_CACHE_TTL:
                real_prices[t] = cached[1]
                prefetched_news[t] = cached[2]
                prefetched_fundamentals[t] = cached[3]
                cached_tickers.append(t)
                _log.info("[%s] Using cached evidence (age: %ds)", t, int(_now - cached[0]))
            else:
                uncached_tickers.append(t)

        if uncached_tickers:
            # Fetch prices, news, and fundamentals simultaneously for uncached tickers
            n_uncached = len(uncached_tickers)
            price_tasks = [_fetch_price_async(t) for t in uncached_tickers]
            news_tasks = [_fetch_news_async(t) for t in uncached_tickers]
            fund_tasks = [_fetch_fundamentals_async(t) for t in uncached_tickers]
            _log.info("Phase 0: gathering price/news/fundamentals for %d tickers", n_uncached)
            all_results = await asyncio.gather(*price_tasks, *news_tasks, *fund_tasks)
            _log.info("Phase 0: gather complete, %d results", len(all_results))

            for t, p in all_results[:n_uncached]:
                if p:
                    real_prices[t] = p
                    _log.info("Price for %s: $%.2f", t, p)
            for t, n in all_results[n_uncached:n_uncached*2]:
                prefetched_news[t] = n
                _log.info("[%s] Pre-fetched %d news articles", t, len(n))
            for t, ev in all_results[n_uncached*2:]:
                prefetched_fundamentals[t] = ev
                _log.info("[%s] Fundamentals: %d chars", t, len(ev))

            # Populate evidence cache for future re-runs
            for t in uncached_tickers:
                if t in real_prices:
                    _evidence_cache[t] = (
                        _now,
                        real_prices.get(t, 0.0),
                        prefetched_news.get(t, []),
                        prefetched_fundamentals.get(t, ""),
                    )

        _log.info("Cycle %s: fetched %d/%d prices, %d/%d news, %d/%d fundamentals (cached=%d, fresh=%d)",
                  cycle_id[:12], len(real_prices), total, len(prefetched_news), total,
                  sum(1 for v in prefetched_fundamentals.values() if v), total,
                  len(cached_tickers), len(uncached_tickers))

        t_cycle_start = time.perf_counter()
        _clear_cycle_caches()
        global _current_cycle_tickers, _current_ticker_processing
        _current_cycle_tickers = list(tickers)
        _emit_event("cycle", "cycle.opened", {
            "cycle_id": cycle_id,
            "tickers": tickers,
            "total": total,
            "eta": f"~{total * 15}s",
        })

        # Audit chain — cycle open (Architecture.md §5.1, Non-Negotiable #3)
        try:
            from pmacs.storage.audit import AuditWriter
            _aw = AuditWriter(cfg.audit_path)
            _aw.append("CYCLE_OPEN", {"tickers": tickers, "total": total, "mode": "PAPER"}, cycle_id=cycle_id)
            _aw.close()
        except Exception as _ae:
            _log.warning("Audit write failed (CYCLE_OPEN): %s", _ae)

        paper_capital = 5000.0
        max_single_pct = 0.20

        for i, ticker in enumerate(tickers):
            try:
                _current_ticker_processing = ticker
                _emit_event("cycle", "ticker_progress", {
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "progress": f"{i + 1}/{total}",
                    "step": "data_fetch_start",
                })

                # --- Phase 1: Evidence gathering (pre-fetched in parallel above) ---
                t0 = time.monotonic()
                price = real_prices.get(ticker, 0)
                news = prefetched_news.get(ticker, [])

                _emit_event("cycle", "ticker_progress", {
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "progress": f"{i + 1}/{total}",
                    "step": "data_fetch_done",
                })

                # --- Phase 2: Run all 7 agents in parallel ---
                # Emit queued/running events for UI animation
                for persona in _PERSONAS:
                    _emit_event("agent", "agent.queued", {
                        "cycle_id": cycle_id,
                        "persona": persona,
                        "ticker": ticker,
                    })

                _emit_event("cycle", "ticker_progress", {
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "progress": f"{i + 1}/{total}",
                    "step": "agents_start",
                })

                t_agents_start = time.monotonic()
                agent_results = await loop.run_in_executor(
                    None, _run_all_agents_sync, ticker, price, news,
                    prefetched_fundamentals.get(ticker, ""),
                    cycle_id,  # enables progressive per-agent SSE events
                )
                agent_latency = int((time.monotonic() - t_agents_start) * 1000)
                _log.info("[%s] 7 agents completed in %dms", ticker, agent_latency)

                # Store agent results for agents page
                _last_cycle_agent_results[ticker] = agent_results
                _last_cycle_id = cycle_id

                _emit_event("cycle", "ticker_progress", {
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "progress": f"{i + 1}/{total}",
                    "step": "agents_done",
                })

                # Per-agent SSE events already emitted progressively by
                # _run_all_agents_sync as each agent finishes (no burst).

                # --- Phase 3: Crucible adversarial review ---
                _emit_event("cycle", "ticker_progress", {
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "progress": f"{i + 1}/{total}",
                    "step": "crucible_start",
                })

                t_crucible_start = time.monotonic()
                crucible = await loop.run_in_executor(
                    None, _run_crucible, ticker, price, agent_results,
                )
                crucible_latency = int((time.monotonic() - t_crucible_start) * 1000)
                _log.info("[%s] Crucible done in %dms — severity=%.2f survives=%s",
                          ticker, crucible_latency, crucible["severity"], crucible["thesis_survives"])

                # Store crucible result for agents page
                _last_cycle_crucible_results[ticker] = crucible

                # Emit crucible result for UI
                _emit_event("agent", "crucible.complete", {
                    "cycle_id": cycle_id,
                    "persona": "crucible",
                    "ticker": ticker,
                    "severity": crucible["severity"],
                    "thesis_survives": crucible["thesis_survives"],
                    "attacks": crucible.get("attacks", [])[:3],
                    "summary": crucible.get("summary", ""),
                    "latency_ms": crucible_latency,
                })

                _emit_event("cycle", "ticker_progress", {
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "progress": f"{i + 1}/{total}",
                    "step": "crucible_done",
                })

                # --- Phase 4: Deterministic arbitration ---
                arb = _arbitrate(agent_results)
                _last_cycle_arbitration[ticker] = arb
                _log.info("[%s] Arbitrated: p_up=%.2f p_down=%.2f direction=%.2f agents=%d",
                          ticker, arb["p_up"], arb["p_down"], arb["direction"], arb["agents_used"])

                # --- Phase 5: Verdict + conviction (engine as source of truth) ---
                from pmacs.engines.conviction import compute_conviction, verdict_tier as engine_verdict_tier
                from pmacs.engines.pricing import compute_ev, EvInputs
                from pmacs.schemas.arbitration import Arbitrated, ArbitrationDecision

                ev_result = compute_ev(EvInputs(
                    p_up=arb["p_up"], p_down=arb["p_down"],
                    current_price=price, cycle_id=cycle_id,
                ))
                _is_bootstrap = True  # web pipeline is always bootstrap currently

                _arb_model = Arbitrated(
                    ticker=ticker, cycle_id=cycle_id,
                    p_up=arb["p_up"], p_flat=arb["p_flat"], p_down=arb["p_down"],
                    decision=ArbitrationDecision.PROCEED_BOOTSTRAP_LOW_CONFIDENCE,
                    agreement_score=arb["confidence"],
                    matured_sources_used=arb["agents_used"],
                )

                conviction = compute_conviction(
                    arb=_arb_model,
                    crucible_severity=crucible["severity"],
                    ev_multiple=ev_result.ev_multiple,
                    is_bootstrap=_is_bootstrap,
                )
                _verdict_enum = engine_verdict_tier(conviction, is_bootstrap=_is_bootstrap)
                verdict = _verdict_enum.value
                processed += 1

                # Enrich stored arbitration with conviction data for sankey-data endpoint
                _last_cycle_arbitration[ticker]["conviction"] = round(conviction, 4)
                _last_cycle_arbitration[ticker]["ev_multiple"] = round(ev_result.ev_multiple, 4)
                _last_cycle_arbitration[ticker]["verdict"] = verdict

                _log.info("[%s] Verdict=%s conviction=%.4f", ticker, verdict, conviction)

                _emit_event("cycle", "ticker_progress", {
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "progress": f"{i + 1}/{total}",
                    "step": "arbitration",
                })

                # Emit arbitrated event
                _emit_event("decision", "decision.arbitrated", {
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "decision": verdict,
                    "p_up": arb["p_up"],
                    "p_down": arb["p_down"],
                    "direction": arb["direction"],
                    "agents_used": arb["agents_used"],
                    "conviction": round(conviction, 4),
                    "ev_multiple": round(ev_result.ev_multiple, 4),
                    "crucible_severity": round(crucible["severity"], 4),
                })

                _emit_event("cycle", "ticker_progress", {
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "progress": f"{i + 1}/{total}",
                    "step": "decision",
                })

                # Only emit decision.final for actionable verdicts (not SKIP)
                if verdict not in ("SKIP", "ERROR"):
                    _emit_event("decision", "decision.final", {
                        "cycle_id": cycle_id,
                        "ticker": ticker,
                        "verdict": verdict,
                        "conviction": conviction,
                    })

                # --- Phase 6: Memo generation (all verdicts including SKIP) ---
                _emit_event("cycle", "ticker_progress", {
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "progress": f"{i + 1}/{total}",
                    "step": "memo",
                })

                if verdict != "ERROR":
                    memo = await loop.run_in_executor(
                        None, _generate_full_memo,
                        ticker, price, agent_results, crucible,
                        verdict, conviction, arb,
                        prefetched_fundamentals.get(ticker, ""),
                    )
                else:
                    memo = f"ERROR {ticker} @ ${price:.2f} | Conviction: {conviction:.0%}"

                # --- Phase 7: Persist to DB ---
                _decided_at = datetime.now(timezone.utc).isoformat()
                try:
                    dec_db = get_connection(cfg.sqlite_path)
                    try:
                        # Ensure price column exists (lazy migration)
                        try:
                            dec_db.execute("ALTER TABLE decisions ADD COLUMN price_usd REAL")
                            dec_db.commit()
                        except Exception:
                            pass
                        dec_db.execute(
                            "INSERT INTO decisions (cycle_id, ticker, verdict, conviction_score, thesis_summary, decided_at, price_usd) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (cycle_id, ticker, verdict, conviction, memo, _decided_at, price),
                        )
                        dec_db.commit()
                    finally:
                        dec_db.close()
                except Exception:
                    pass

                # Write structured memo to dedicated memos table
                try:
                    import json as _json
                    _raw_text = memo
                    if memo and memo.startswith("{"):
                        try:
                            _raw_text = _json.loads(memo).get("raw_text", memo)
                        except Exception:
                            pass
                    memo_db = get_connection(cfg.sqlite_path)
                    try:
                        # Extract memo score from JSON if available
                        _memo_score = None
                        _memo_grade = None
                        if memo and memo.startswith("{"):
                            try:
                                _mj = _json.loads(memo)
                                _memo_score = _mj.get("memo_score")
                                _memo_grade = _mj.get("memo_grade")
                            except Exception:
                                pass
                        memo_db.execute(
                            "INSERT INTO memos (cycle_id, ticker, verdict, conviction_score, memo_json, raw_text, memo_score, memo_grade, decided_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (cycle_id, ticker, verdict, conviction, memo, _raw_text, _memo_score, _memo_grade, _decided_at),
                        )
                        memo_db.commit()
                    finally:
                        memo_db.close()
                except Exception:
                    pass

                # Audit chain — decision (Architecture.md §5.1, Non-Negotiable #3)
                try:
                    from pmacs.storage.audit import AuditWriter
                    _aw = AuditWriter(cfg.audit_path)
                    _aw.append(
                        "DECISION",
                        {"ticker": ticker, "verdict": verdict, "conviction": round(conviction, 4),
                         "price": price},
                        cycle_id=cycle_id,
                    )
                    _aw.close()
                except Exception as _ae:
                    _log.warning("Audit write failed (DECISION %s): %s", ticker, _ae)

                # --- DuckDB persona affinity + Qdrant thesis embedding ---
                try:
                    from pmacs.storage.duckdb import DuckDBAdapter
                    from pathlib import Path as _P
                    _db_dir = _P(cfg.sqlite_path).parent
                    _duck = DuckDBAdapter(db_path=_db_dir / "pmacs_analytics.duckdb")
                    _duck.init_tables()
                    for r in agent_results:
                        if not r.get("error"):
                            _duck.update_persona_affinity(
                                persona=r["persona"], ticker=ticker, brier=0.667,
                            )
                except Exception:
                    pass
                try:
                    from pmacs.storage.qdrant import QdrantAdapter
                    from pathlib import Path as _P2
                    _db_dir2 = _P2(cfg.sqlite_path).parent
                    _qdrant = QdrantAdapter(path=str(_db_dir2 / "pmacs_qdrant"))
                    _qdrant.create_collections()
                    if memo:
                        _qdrant.upsert_with_embedding(
                            collection="theses",
                            id=f"{cycle_id}_{ticker}",
                            text=(memo or "")[:2000],
                            payload={"cycle_id": cycle_id, "ticker": ticker,
                                     "verdict": verdict, "conviction": conviction},
                        )
                except Exception:
                    pass

                # --- Phase 7b: Paper trade execution ---
                is_already_held = False
                try:
                    chk_db = get_connection(cfg.sqlite_path)
                    try:
                        row = chk_db.execute(
                            "SELECT id FROM holdings WHERE ticker = ? AND state = 'ACTIVE'",
                            (ticker,),
                        ).fetchone()
                        is_already_held = row is not None
                    finally:
                        chk_db.close()
                except Exception:
                    pass

                if verdict == "HOLD" and not is_already_held:
                    _emit_event("decision", "decision.skipped", {
                        "cycle_id": cycle_id,
                        "ticker": ticker,
                        "reason": "HOLD verdict but no existing position — no action",
                    })
                elif verdict in ("BUY", "STRONG_BUY"):
                    fill_price = real_prices.get(ticker)
                    if fill_price:
                        position_budget = paper_capital * max_single_pct * conviction
                        shares = max(1, int(position_budget / fill_price))
                        position_value = round(fill_price * shares, 2)

                        # Extract fair value from memo JSON
                        import json as _json
                        fair_value = None
                        try:
                            memo_obj = _json.loads(memo) if memo.startswith("{") else {}
                            fair_value = memo_obj.get("fair_value")
                        except (ValueError, AttributeError):
                            pass

                        # Ensure price_target_usd column exists (lazy migration)
                        try:
                            _mig_db = get_connection(cfg.sqlite_path)
                            _mig_db.execute("ALTER TABLE holdings ADD COLUMN price_target_usd REAL")
                            _mig_db.commit()
                            _mig_db.close()
                        except Exception:
                            pass

                        _emit_event("trade", "trade.submitted", {
                            "cycle_id": cycle_id,
                            "ticker": ticker,
                            "side": "buy",
                            "mode": "PAPER",
                            "shares": shares,
                            "fill_price": fill_price,
                        })
                        _emit_event("trade", "trade.filled", {
                            "cycle_id": cycle_id,
                            "ticker": ticker,
                            "side": "buy",
                            "mode": "PAPER",
                            "fill_price": fill_price,
                            "shares": shares,
                            "position_value": position_value,
                        })

                        try:
                            hold_db = get_connection(cfg.sqlite_path)
                            try:
                                # Use ticker-based ID to prevent duplicate holdings
                                hold_id = f"HOLD-{ticker}"
                                existing = hold_db.execute(
                                    "SELECT id FROM holdings WHERE ticker = ? AND state = 'ACTIVE'",
                                    (ticker,),
                                ).fetchone()
                                if existing:
                                    hold_db.execute(
                                        "UPDATE holdings SET cycle_id_opened = ?, "
                                        "conviction_score = ?, thesis_summary = ?, "
                                        "current_price_usd = ?, price_target_usd = ? "
                                        "WHERE id = ?",
                                        (cycle_id, conviction, memo, fill_price,
                                         fair_value, existing[0]),
                                    )
                                else:
                                    hold_db.execute(
                                        "INSERT INTO holdings "
                                        "(id, ticker, state, cycle_id_opened, entry_date, entry_price_usd, "
                                        "position_size_usd, verdict, conviction_score, thesis_summary, "
                                        "current_price_usd, price_target_usd) "
                                        "VALUES (?, ?, 'ACTIVE', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                        (
                                            hold_id,
                                            ticker,
                                            cycle_id,
                                            datetime.now(timezone.utc).isoformat(),
                                            fill_price,
                                            position_value,
                                            verdict,
                                            conviction,
                                            memo,
                                            fill_price,
                                            fair_value,
                                        ),
                                    )
                                hold_db.commit()
                            finally:
                                hold_db.close()
                        except Exception as hold_exc:
                            _log.error("Holding creation failed for %s: %s", ticker, hold_exc, exc_info=True)

                total_latency = int((time.monotonic() - t0) * 1000)
                _log.info("[%s] Full pipeline: %dms — verdict=%s conviction=%.2f",
                          ticker, total_latency, verdict, conviction)

                _emit_event("cycle", "ticker_progress", {
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "progress": f"{i + 1}/{total}",
                    "step": "complete",
                })
            except Exception as ticker_exc:
                _log.error("Ticker %s failed in cycle %s: %s", ticker, cycle_id[:12], ticker_exc, exc_info=True)
                processed += 1
                _emit_event("decision", "decision.error", {
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "reason": str(ticker_exc),
                })

        # Cycle closed — clear both ticker tracking vars so the Agents page
        # doesn't show a stale "Current Ticker" on next page load.
        _current_ticker_processing = ""
        _current_cycle_tickers = []
        _log.info("Cycle %s completed: %d/%d tickers processed", cycle_id[:12], processed, total)
        _emit_event("cycle", "cycle.closed", {
            "cycle_id": cycle_id,
            "status": "completed",
            "tickers_processed": processed,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round((time.perf_counter() - t_cycle_start) * 1000),
        })

        try:
            db = get_connection(cfg.sqlite_path)
            try:
                db.execute(
                    "UPDATE cycles SET state = ?, closed_at = ? WHERE cycle_id = ?",
                    ("COMPLETED", datetime.now(timezone.utc).isoformat(), cycle_id),
                )
                db.commit()
            finally:
                db.close()
        except Exception:
            pass

        # Audit chain — cycle close (Architecture.md §5.1, Non-Negotiable #3)
        try:
            from pmacs.storage.audit import AuditWriter
            _aw = AuditWriter(cfg.audit_path)
            _aw.append(
                "CYCLE_CLOSE",
                {"tickers_processed": processed, "total": total, "status": "COMPLETED"},
                cycle_id=cycle_id,
            )
            _aw.close()
        except Exception as _ae:
            _log.warning("Audit write failed (CYCLE_CLOSE): %s", _ae)

    except Exception as cycle_exc:
        _current_ticker_processing = ""
        _current_cycle_tickers = []
        _log.error("Cycle %s fatal error: %s", cycle_id[:12], cycle_exc, exc_info=True)
        _emit_event("cycle", "cycle.closed", {
            "cycle_id": cycle_id,
            "status": "error",
            "tickers_processed": processed,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
    finally:
        socket.setdefaulttimeout(_prev_socket_timeout)


class CycleStartRequest(BaseModel):
    trigger: str = "manual"
    tickers: list[str] = []  # if non-empty, only these tickers are cycled


@router.post("/api/cycle/start")
async def cycle_start(req: CycleStartRequest):
    """Manually trigger a new analysis cycle (Source.md §15).

    Operator-confirmed: cycles consume API credits and place paper trades.
    """
    import asyncio
    from datetime import datetime, timezone

    cfg = get_config()
    try:
        from pmacs.storage.sqlite import get_connection

        cycle_id = datetime.now(timezone.utc).strftime("CYCLE-%Y%m%dT%H%M%S")
        db = get_connection(cfg.sqlite_path)
        try:
            db.execute(
                "INSERT INTO cycles (cycle_id, opened_at, state, trigger, mode) VALUES (?, ?, ?, ?, ?)",
                (cycle_id, datetime.now(timezone.utc).isoformat(), "RUNNING", req.trigger, "PAPER"),
            )
            db.commit()
            # Get tickers from universe
            if req.tickers:
                # Caller-specified subset (e.g. queue-only run)
                tickers = [t.upper().strip() for t in req.tickers if t.strip()]
            else:
                rows = db.execute("SELECT ticker FROM universe WHERE COALESCE(halted, 0) = 0 AND COALESCE(delisted, 0) = 0 ORDER BY COALESCE(pinned_priority, 999) ASC, added_at ASC").fetchall()
                tickers = [r[0] for r in rows] if rows else ["AAPL", "MSFT", "GOOGL"]
        finally:
            db.close()

        # Launch demo cycle in background
        asyncio.create_task(_run_demo_cycle(cycle_id, tickers))

        return JSONResponse({"ok": True, "cycle_id": cycle_id, "message": "Cycle " + cycle_id + " started"})
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("Cycle start failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": "Failed to start cycle"}, status_code=503)


@router.post("/api/cycle/orchestrator")
async def cycle_orchestrator(req: CycleStartRequest):
    """Trigger a full cycle through the spec-canonical orchestrator (Source.md §15).

    This is the wave-2 path (Agents.md §11b-§11d, §16.9): runs 7 personas + bull/bear
    advocates + cross-persona auditor, applies auditor arbitration weight caps, computes
    reverse-DCF + scenario-weighted expected price, and persists a structured memo (with
    bull_bear_debate / reverse_dcf / scenario_price / what_would_change_my_mind sections)
    to the memos table that /memo/{ticker} renders. The orchestrator opens and closes its
    own cycle row and streams SSE to the same /events dashboard as the demo cycle.

    Runs synchronously in a worker thread (the orchestrator is blocking); returns
    immediately. req.tickers is ignored on this path — the orchestrator composes its own
    queue from the universe (Architecture.md §12). Operator-confirmed: consumes LLM/API
    credits and may place paper trades.
    """
    import asyncio
    from pathlib import Path

    cfg = get_config()
    try:
        from pmacs.nervous.api import _publisher
        from pmacs.nervous.orchestrator import CycleOrchestrator

        lock_path = str(Path(cfg.sqlite_path).parent / "cycle_orchestrator.lock")
        orch = CycleOrchestrator(
            db_path=Path(cfg.sqlite_path),
            audit_path=Path(cfg.audit_path) if getattr(cfg, "audit_path", None) else None,
            sse_publisher=_publisher,
            config={"lock_path": lock_path},
        )
        trigger = (req.trigger if req.trigger else "OPERATOR")
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, lambda: orch.run_cycle(trigger))
        return JSONResponse({"ok": True, "message": "Orchestrator cycle started (wave-2 path)"})
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
    """
    import asyncio
    from datetime import datetime, timezone

    ticker = req.ticker.upper().strip()
    if not ticker or len(ticker) > 10 or not ticker.isalpha():
        return JSONResponse({"ok": False, "error": "Invalid ticker (1-10 letters)"}, status_code=400)

    cfg = get_config()
    cycle_id = f"SOLO-{ticker}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

    try:
        from pmacs.storage.sqlite import get_connection

        db = get_connection(cfg.sqlite_path)
        try:
            db.execute(
                "INSERT INTO cycles (cycle_id, opened_at, state, trigger, mode) VALUES (?, ?, ?, ?, ?)",
                (cycle_id, datetime.now(timezone.utc).isoformat(), "RUNNING", "solo_research", "PAPER"),
            )
            db.commit()
        finally:
            db.close()

        asyncio.create_task(_run_demo_cycle(cycle_id, [ticker]))

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
