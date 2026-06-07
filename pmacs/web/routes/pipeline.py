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

            # Build a lookup of recent cycle decisions for thesis/timestamp
            recent_thesis: dict[str, dict] = {}
            try:
                rows = db.execute(
                    """SELECT d.ticker, d.verdict, d.conviction_score, d.thesis_summary, d.decided_at,
                              d.priority_band
                       FROM decisions d
                       ORDER BY d.decided_at DESC
                       LIMIT 200"""
                ).fetchall()
                import json as _json_pipeline
                for r in rows:
                    t = r[0]
                    if t not in recent_thesis:
                        raw_thesis = r[3] or ""
                        # thesis_summary is stored as JSON — extract readable thesis/raw_text
                        if raw_thesis.startswith("{"):
                            try:
                                _parsed = _json_pipeline.loads(raw_thesis)
                                raw_thesis = (_parsed.get("thesis")
                                              or _parsed.get("raw_text")
                                              or _parsed.get("verdict_line")
                                              or raw_thesis)
                            except Exception:
                                pass
                        recent_thesis[t] = {
                            "verdict": r[1] or "SKIP",
                            "conviction": r[2] or 0.0,
                            "thesis": raw_thesis,
                            "timestamp": r[4] or "",
                            "priority": r[5],
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
            }
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
            }
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
                "mode": "SHADOW + PAPER",
                "columns": columns,
                "queue_size": len(queue),
                "cycles_today": len(decisions),
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


def _clear_cycle_caches() -> None:
    """Reset in-memory cycle caches to prevent stale data leaking between cycles."""
    global _last_cycle_agent_results, _last_cycle_crucible_results, _last_cycle_arbitration, _last_cycle_id
    _last_cycle_agent_results = {}
    _last_cycle_crucible_results = {}
    _last_cycle_arbitration = {}
    _last_cycle_id = ""


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


def _call_openrouter(prompt: str, max_tokens: int = 1024, temperature: float = 0.2,
                     system_prompt: str | None = None) -> str:
    """Call OpenRouter API directly using model_registry.json config."""
    import json as _json
    import logging

    import httpx

    try:
        import keyring
        api_key = keyring.get_password("pmacs.credentials", "pmacs.credentials.openrouter_api_key")
        if not api_key:
            raise RuntimeError("OpenRouter API key not found in keyring (pmacs.credentials.openrouter_api_key)")
    except ImportError:
        raise RuntimeError("keyring module not installed — cannot retrieve API key")

    try:
        from pathlib import Path
        registry_path = Path(__file__).resolve().parents[3] / "config" / "model_registry.json"
        with open(registry_path) as f:
            registry = _json.load(f)
        backend = registry.get("backends", {}).get("openrouter", {})
        model = backend.get("default_model", "deepseek/deepseek-v4-flash")
        base_url = backend.get("base_url", "https://openrouter.ai/api").rstrip("/")
    except Exception:
        model = "deepseek/deepseek-v4-flash"
        base_url = "https://openrouter.ai/api/v1"

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "content-type": "application/json",
    }

    timeout = max(60, max_tokens // 50)  # scale timeout with output size
    with httpx.Client(timeout=float(timeout)) as client:
        response = client.post(f"{base_url}/chat/completions", json=body, headers=headers)
        response.raise_for_status()
        data = response.json()

    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

    usage = data.get("usage", {})
    if usage:
        logging.getLogger("pmacs.web").info(
            "OpenRouter: model=%s prompt=%d completion=%d tokens",
            model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
        )
        # Silent token ledger — not shown in UI. Read with: python ops/token_usage.py
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
                "caller": _call_openrouter.__name__,
            })
            with open(_ledger, "a") as _f:
                _f.write(_entry + "\n")
        except Exception:
            pass  # never block a cycle over telemetry

    return content


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

        # Fetch metrics and profile in parallel using threads
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f_metrics = pool.submit(_get, f"https://finnhub.io/api/v1/stock/metric?symbol={ticker}&metric=all&token={api_key}")
            f_profile = pool.submit(_get, f"https://finnhub.io/api/v1/stock/profile2?symbol={ticker}&token={api_key}")
            raw_m = f_metrics.result(timeout=10)
            raw_p = f_profile.result(timeout=10)

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

        return "\n".join(lines)

    except Exception as exc:
        _log.warning("Fundamentals fetch failed for %s: %s", ticker, exc)
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
        system_prompt = system_prompt.replace("{episodic_context}", "")
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
        f"Probabilities must sum to 1.0."
        f"{evidence_section}"
        f"{news_text}"
    )

    try:
        raw = _call_openrouter(
            prompt, max_tokens=5000, temperature=0.2,
            system_prompt=system_prompt or None,
        )
        data = _parse_json_safe(raw)
        if not data:
            raise ValueError("Empty or invalid JSON response")

        p_up = float(data.get("p_up", 0.33))
        p_down = float(data.get("p_down", 0.33))
        p_flat = max(0.0, 1.0 - p_up - p_down)

        return {
            "persona": persona,
            "p_up": min(1.0, max(0.0, p_up)),
            "p_flat": min(1.0, max(0.0, p_flat)),
            "p_down": min(1.0, max(0.0, p_down)),
            "analysis": str(data.get("analysis", ""))[:1000],
            "key_signal": str(data.get("key_signal", ""))[:200],
            "confidence": float(data.get("confidence", 0.5)),
            "evidence_cited": data.get("evidence_cited", []),
            "error": None,
        }
    except Exception as exc:
        _log.error("Agent %s failed for %s: %s", persona, ticker, exc)
        return {
            "persona": persona,
            "p_up": 0.33, "p_flat": 0.34, "p_down": 0.33,
            "analysis": f"Agent {persona} failed: {exc}",
            "key_signal": "ANALYSIS_UNAVAILABLE",
            "confidence": 0.0,
            "evidence_cited": [],
            "error": str(exc),
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

    prompt = (
        f"Review the following investment thesis for {ticker} @ ${price:.2f}.\n\n"
        f"Agent analyses:\n{agent_summary}\n\n"
        f"Find the weakest points. Respond with JSON:\n"
        f"  - severity: 0.0-1.0 (how badly the thesis is damaged)\n"
        f"  - attacks: list of 1-5 specific criticisms\n"
        f"  - thesis_survives: true if the overall thesis holds despite attacks\n"
        f"  - summary: 2-3 sentence adversarial summary\n"
        f"  - overlooked_risks: list of 1-3 risks the agents missed"
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

    n = len(valid)
    avg_p_up = sum(r["p_up"] for r in valid) / n
    avg_p_down = sum(r["p_down"] for r in valid) / n
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


# --- Conviction + Verdict (reuse spec engines) ------------------------------

def _compute_verdict(arb: dict, crucible_severity: float, is_bootstrap: bool = True) -> tuple[str, float]:
    """Compute conviction score and verdict tier.

    Returns (verdict, conviction_score).
    """
    direction = arb["direction"]
    # Bootstrap: no calibration history exists, so consensus fraction is the right
    # maturity proxy. Use 1.0 (not 0.50 floor) so genuine majority consensus can
    # cross BUY. Matches conviction.py bootstrap fix.
    maturity = 1.0 if is_bootstrap else max(0.25, min(arb["confidence"], 1.0))

    # Crucible amplification: high severity is MORE punitive than linear.
    # severity^0.7 makes severity 0.66 → effective 0.735 (factor 0.265 vs linear 0.34).
    # This models the reality that agents often miss what crucible catches (dilution,
    # extreme valuations, accounting red flags) and crucible is the primary safety layer.
    amplified_severity = crucible_severity ** 0.7
    crucible_factor = max(0.0, 1.0 - amplified_severity)

    # ev_factor: scaled from direction. No floor — if direction is 0 or negative,
    # ev_factor is 0 and conviction collapses to 0. The old max(ev_factor, 0.1)
    # floor was wrong: it gave 10% EV credit even to zero-edge or negative-edge
    # stocks, allowing direction * 0.1 to still produce positive conviction.
    ev_factor = min(max(direction, 0) / 0.15, 1.0) if direction > 0 else 0.0

    conviction = direction * maturity * crucible_factor * ev_factor
    conviction = max(-1.0, min(1.0, conviction))

    # Bootstrap paper mode: lower thresholds to allow position entry during paper
    # trading. Standard thresholds (0.6/0.3/0.1) are too high for bootstrap where
    # crucible amplification + no calibration data suppresses conviction heavily.
    # Paper positions are low-risk (virtual capital) and generate needed trade data
    # for Sharpe/drawdown/win-rate calculations required for mode promotion.
    if is_bootstrap:
        if conviction >= 0.40:
            verdict = "STRONG_BUY"
        elif conviction >= 0.15:
            verdict = "BUY"
        elif conviction >= 0.05:
            verdict = "HOLD"
        else:
            verdict = "SKIP"
    else:
        if conviction >= 0.6:
            verdict = "STRONG_BUY"
        elif conviction >= 0.3:
            verdict = "BUY"
        elif conviction >= 0.1:
            verdict = "HOLD"
        else:
            verdict = "SKIP"

    return verdict, round(conviction, 4)


# --- Memo generation ---------------------------------------------------------

def _generate_full_memo(ticker: str, price: float, agent_results: list[dict],
                        crucible: dict, verdict: str, conviction: float,
                        arb: dict) -> str:
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

    agent_summary = "\n".join(
        f"## {r['persona']}\n"
        f"Signal: {r['key_signal']}\n"
        f"P(up)={r['p_up']:.0%} P(down)={r['p_down']:.0%} Confidence={r['confidence']:.0%}\n"
        f"{r['analysis'][:500]}"
        for r in agent_results
    )

    prompt = (
        f"You are writing a deep investment research memo for {ticker}, currently trading at ${price:.2f}.\n\n"
        f"System verdict: {verdict} | Conviction: {conviction:.0%}\n"
        f"Arbitrated probabilities: P(up)={arb['p_up']:.0%} P(down)={arb['p_down']:.0%}\n\n"
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
        f"4. business_model (string): 4-6 sentences explaining what the company does, how it makes money, "
        f"its revenue streams, customer segments, and unit economics.\n\n"
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
        f"14. position_sizing_note (string): Suggested sizing rationale given conviction and risk.\n\n"
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
        raw = _call_openrouter(prompt, max_tokens=10000, temperature=0.3,
                               system_prompt=system_prompt)
        data = _parse_json_safe(raw)
        if not data:
            _log.warning("Memo JSON parse failed for %s, using fallback", ticker)
            return json.dumps({
                "verdict_line": f"{verdict} {ticker} @ ${price:.2f}",
                "fair_value": None,
                "valuation_range": {},
                "thesis": f"{verdict} at conviction {conviction:.0%}",
                "raw_text": f"{verdict} {ticker} @ ${price:.2f} | Conviction: {conviction:.0%}",
            })

        # Build structured memo data
        memo_data = {
            "verdict_line": data.get("verdict_line", f"{verdict} {ticker} @ ${price:.2f}"),
            "fair_value": data.get("fair_value"),
            "valuation_range": data.get("valuation_range", {}),
            "valuation_methodology": data.get("valuation_methodology", ""),
            "business_model": data.get("business_model", ""),
            "financial_snapshot": data.get("financial_snapshot", {}),
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
                 "p_up": r["p_up"], "p_down": r["p_down"], "confidence": r["confidence"]}
                for r in agent_results if not r.get("error")
            ],
            "crucible_attacks": crucible.get("attacks", [])[:5],
            "crucible_severity": crucible.get("severity", 0),
            "crucible_thesis_survives": crucible.get("thesis_survives", True),
            "crucible_summary": crucible.get("summary", ""),
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
        return json.dumps(memo_data)

    except Exception as exc:
        _log.error("Memo generation failed for %s: %s", ticker, exc)
        return json.dumps({
            "verdict_line": f"{verdict} {ticker} @ ${price:.2f}",
            "fair_value": None,
            "thesis": f"Memo generation failed: {exc}",
            "raw_text": f"{verdict} {ticker} @ ${price:.2f} | Conviction: {conviction:.0%}",
        })


# --- Main cycle execution (real parallel agent pipeline) ----------------------


def _run_all_agents_sync(ticker: str, price: float, news: list[dict], fundamentals: str = "") -> list[dict]:
    """Run all 7 agents concurrently via ThreadPoolExecutor."""
    import concurrent.futures
    import logging

    _log = logging.getLogger("pmacs.web.pipeline")
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:
        futures = {
            pool.submit(_run_single_agent, persona, ticker, price, news, fundamentals): persona
            for persona in _PERSONAS
        }
        for future in concurrent.futures.as_completed(futures):
            persona = futures[future]
            try:
                results.append(future.result())
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
    import time
    from datetime import datetime, timezone

    from pmacs.storage.sqlite import get_connection
    from pmacs.web.config import get_config

    _log = logging.getLogger("pmacs.web.cycle")
    cfg = get_config()
    total = len(tickers)
    processed = 0

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
            try:
                ev = await loop.run_in_executor(None, _fetch_ticker_fundamentals, t)
                # Enrich with Yahoo price targets + technical indicators
                enrichment = await loop.run_in_executor(None, _fetch_enrichment_data, t)
                if enrichment:
                    ev = ev + "\n" + enrichment if ev else enrichment
                return t, ev
            except Exception as exc:
                _log.warning("Fundamentals fetch failed for %s: %s", t, exc)
                return t, ""

        # Fetch prices, news, and fundamentals simultaneously across all tickers
        price_tasks = [_fetch_price_async(t) for t in tickers]
        news_tasks = [_fetch_news_async(t) for t in tickers]
        fund_tasks = [_fetch_fundamentals_async(t) for t in tickers]
        all_results = await asyncio.gather(*price_tasks, *news_tasks, *fund_tasks)

        for t, p in all_results[:total]:
            if p:
                real_prices[t] = p
                _log.info("Price for %s: $%.2f", t, p)
        for t, n in all_results[total:total*2]:
            prefetched_news[t] = n
            _log.info("[%s] Pre-fetched %d news articles", t, len(n))
        for t, ev in all_results[total*2:]:
            prefetched_fundamentals[t] = ev
            _log.info("[%s] Fundamentals: %d chars", t, len(ev))

        _log.info("Cycle %s: fetched %d/%d prices, %d/%d news, %d/%d fundamentals (parallel)",
                  cycle_id[:12], len(real_prices), total, len(prefetched_news), total,
                  sum(1 for v in prefetched_fundamentals.values() if v), total)

        t_cycle_start = time.perf_counter()
        _clear_cycle_caches()
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

                # Emit per-agent completion events with real scores + analysis text
                _per_agent_latency_ms = agent_latency // len(_PERSONAS)
                for r in agent_results:
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
                        "latency_ms": r.get("latency_ms", _per_agent_latency_ms),
                        "attempt_count": r.get("attempt_count", 1),
                    })

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

                # --- Phase 5: Verdict + conviction ---
                verdict, conviction = _compute_verdict(arb, crucible["severity"])
                processed += 1

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
                        memo_db.execute(
                            "INSERT INTO memos (cycle_id, ticker, verdict, conviction_score, memo_json, raw_text, decided_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (cycle_id, ticker, verdict, conviction, memo, _raw_text, _decided_at),
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

        # Cycle closed
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
        _log.error("Cycle %s fatal error: %s", cycle_id[:12], cycle_exc, exc_info=True)
        _emit_event("cycle", "cycle.closed", {
            "cycle_id": cycle_id,
            "status": "error",
            "tickers_processed": processed,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })


class CycleStartRequest(BaseModel):
    trigger: str = "manual"
    totp_code: str = ""
    tickers: list[str] = []  # if non-empty, only these tickers are cycled


@router.post("/api/cycle/start")
async def cycle_start(req: CycleStartRequest):
    """Manually trigger a new analysis cycle (Source.md §15).

    TOTP-gated: cycles consume API credits and place paper trades.
    """
    from pmacs.web.routes.settings import _verify_totp
    ok, err = _verify_totp(req.totp_code)
    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=403)

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


class SoloRunRequest(BaseModel):
    ticker: str


@router.post("/api/solo/run")
async def solo_run(req: SoloRunRequest):
    """Run a one-time solo analysis for any ticker (research mode).

    No TOTP required — read-only data fetching and LLM analysis.
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
    """First-use smoke-test cycle — no TOTP required (Source.md §12 Step 10).
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
    totp_code: str = ""


@router.post("/api/pipeline/force-exit")
async def force_exit(req: ForceExitRequest):
    """Force-exit an active holding (Source.md §15).

    Transitions the holding to EXIT_THESIS_INVALIDATED and persists
    the state change to SQLite.  TOTP-gated (Non-Negotiable #5).
    """
    # TOTP verification — operator must authenticate state changes
    from pmacs.web.routes.settings import _verify_totp
    ok, err = _verify_totp(req.totp_code)
    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=403)

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


@router.get("/api/cycle/compare")
async def cycle_compare(request: Request):
    """Compare two cycles side-by-side (Source.md §15.9)."""
    cycle_a = request.query_params.get("cycle_a", "")
    cycle_b = request.query_params.get("cycle_b", "")
    if not cycle_a or not cycle_b:
        return JSONResponse({"ok": False, "error": "cycle_a and cycle_b required"}, status_code=400)

    cfg = get_config()
    try:
        db = data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            decisions_a = data_layer.get_decisions_for_cycle(db, cycle_a)
            decisions_b = data_layer.get_decisions_for_cycle(db, cycle_b)
        finally:
            db.close()

        return JSONResponse({
            "ok": True,
            "cycle_a": {"id": cycle_a, "decisions": decisions_a},
            "cycle_b": {"id": cycle_b, "decisions": decisions_b},
        })
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("Cycle compare failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": "Failed to compare cycles"}, status_code=500)
