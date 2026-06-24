"""Universe route — ticker management page (Source.md §17)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pmacs.web.templating import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer

router = APIRouter()

# Static company name lookup — DB has no name column (Source.md §17)
_COMPANY_NAMES: dict[str, str] = {
    "PLTR": "Palantir Technologies",
    "NET": "Cloudflare Inc.",
    "MELI": "MercadoLibre Inc.",
    "CELH": "Celsius Holdings",
    "INMD": "InMode Ltd.",
    "GOOGL": "Alphabet Inc.",
    "META": "Meta Platforms",
    "CRWD": "CrowdStrike Holdings",
    "PANW": "Palo Alto Networks",
    "MSFT": "Microsoft Corp.",
    "NVDA": "NVIDIA Corp.",
    "KOD": "Kodiak Robotics",
    "NBIS": "Nebius Group NV",
    "HIMS": "Hims & Hers Health",
    "ONDS": "Ondas Holdings",
    "TEM": "Tempus AI Inc.",
    "ZETA": "Zeta Global",
    "NU": "Nu Holdings",
    "OUST": "Ouster Inc.",
    "AMZN": "Amazon.com Inc.",
}

# Static sector overrides — DB sector column may have stale data from Finnhub metadata
_COMPANY_SECTORS: dict[str, str] = {
    "NBIS": "Technology",
    "PLTR": "Technology",
    "NET": "Technology",
    "CRWD": "Technology",
    "PANW": "Technology",
    "MSFT": "Technology",
    "NVDA": "Technology",
    "ZETA": "Technology",
    "TEM": "Healthcare",
    "HIMS": "Healthcare",
    "INMD": "Healthcare",
    "MELI": "Consumer Discretionary",
    "CELH": "Consumer Staples",
    "KOD": "Industrials",
    "ONDS": "Technology",
    "NU": "Financials",
    "OUST": "Technology",
    "GOOGL": "Technology",
    "META": "Technology",
    "AMZN": "Technology",
}

# Static subsector lookup — provides meaningful subsector when DB value is empty
_COMPANY_SUBSECTORS: dict[str, str] = {
    "NBIS": "AI Infrastructure",
    "PLTR": "Enterprise AI / Analytics",
    "NET": "Cloud / CDN",
    "CRWD": "Cybersecurity",
    "PANW": "Cybersecurity",
    "MSFT": "Cloud / Enterprise",
    "NVDA": "Semiconductors / AI",
    "ZETA": "MarTech / AdTech",
    "TEM": "AI Diagnostics",
    "HIMS": "Telehealth / DTC",
    "INMD": "Medical Devices",
    "MELI": "E-Commerce / FinTech",
    "CELH": "Beverages / Energy",
    "KOD": "Autonomous Vehicles",
    "ONDS": "Wireless / Drones",
    "NU": "Digital Banking",
    "OUST": "LiDAR / Sensors",
    "GOOGL": "Search / Cloud / AI",
    "META": "Social / Metaverse / AI",
    "AMZN": "E-Commerce / Cloud",
}


class TickerActionRequest(BaseModel):
    """Request body for operator-confirmed universe actions."""
    ticker: str = ""
    tickers: list[str] = []
    subsector: str = ""


# ---------------------------------------------------------------------------
# Page render
# ---------------------------------------------------------------------------


@router.get("/universe")
async def universe_page(request: Request):
    """Render the universe ticker management page."""
    cfg = get_config()

    try:
        db = data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            ticker_rows = data_layer.get_universe_list(db)
            holdings = data_layer.get_active_holdings(db)
            # Query last analysis timestamp per ticker from decisions table
            try:
                rows = db.execute(
                    "SELECT ticker, MAX(decided_at) as last_analyzed FROM decisions GROUP BY ticker"
                ).fetchall()
                last_analyzed_map: dict[str, str] = {r[0]: r[1] for r in rows}
            except Exception:
                last_analyzed_map = {}

            # Determine which tickers have participated in at least one cycle.
            # A ticker is considered cycled if it appears in decisions, queue, or holdings.
            try:
                cycled_rows = db.execute(
                    """
                    SELECT DISTINCT ticker FROM (
                        SELECT ticker FROM decisions
                        UNION
                        SELECT ticker FROM queue
                        UNION
                        SELECT ticker FROM holdings WHERE cycle_id_opened IS NOT NULL
                    )
                    """
                ).fetchall()
                cycled_tickers: set[str] = {r[0] for r in cycled_rows}
            except Exception:
                cycled_tickers = set()
        finally:
            db.close()

        active_tickers = {h["ticker"] for h in holdings}

        # Map to template-expected field names (Source.md §17 per-ticker row)
        tickers = [
            {
                "symbol": t["ticker"],
                "name": _COMPANY_NAMES.get(t["ticker"], t["ticker"]),
                "sector": _COMPANY_SECTORS.get(t["ticker"]) or t.get("sector") or "--",
                "subsector": t.get("subsector") or _COMPANY_SUBSECTORS.get(t["ticker"], ""),
                "catalyst_type": t.get("catalyst_type") or "",
                "status": _compute_status(t, t["ticker"] in active_tickers),
                "has_position": t["ticker"] in active_tickers,
                "is_pinned": t.get("pinned_priority") is not None,
                "has_been_cycled": t["ticker"] in cycled_tickers,
                "last_cycle": "--",
                "last_analyzed": last_analyzed_map.get(t["ticker"]),
            }
            for t in ticker_rows
        ]

        return templates.TemplateResponse(
            request=request,
            name="universe.html",
            context={
                "page": "universe",
                "tickers": tickers,
                "groups": ["All", "Watchlist", "Portfolio", "Sectors"],
            },
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="universe.html",
            context={
                "page": "universe",
                "error": data_layer.build_error_context("universe", exc),
            },
        )


def _compute_status(t: dict[str, Any], has_position: bool) -> str:
    """Derive display status from ticker data."""
    if t.get("halted"):
        return "Halted"
    if has_position:
        return "Active position"
    if t.get("pinned_priority") is not None:
        return "Pinned"
    return "Active"


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


def _get_api_key(service: str, account: str) -> str | None:
    """Try to retrieve an API key, return None on failure."""
    try:
        from pmacs.storage.keychain import get_api_key
        return get_api_key(service, account)
    except Exception:
        return None


@router.get("/api/universe/search")
async def universe_search(q: str = ""):
    """Search tickers by prefix. Polygon first, then Finnhub fallback."""
    import json as _json
    import urllib.request

    q = q.strip().upper()
    if not q or len(q) < 1:
        return JSONResponse({"results": []})

    results: list[dict[str, str]] = []
    existing: set[str] = set()

    # 1. Try Polygon ticker search (prefix match on symbol)
    polygon_key = _get_api_key("pmacs.data.polygon", "api_key")
    if polygon_key:
        try:
            url = (
                f"https://api.polygon.io/v3/reference/tickers"
                f"?ticker.gte={urllib.request.quote(q)}&ticker.lt={urllib.request.quote(q[:-1] + chr(ord(q[-1]) + 1) if q else 'A')}"
                f"&limit=15&active=true&market=stocks&apikey={polygon_key}"
            )
            with urllib.request.urlopen(url, timeout=4) as resp:
                data = _json.loads(resp.read().decode())
            for item in data.get("results", []):
                ticker = item.get("ticker", "")
                if ticker and ticker not in existing:
                    results.append({
                        "ticker": ticker,
                        "name": item.get("name", ""),
                        "source": "polygon",
                    })
                    existing.add(ticker)
        except Exception:
            pass

    # 2. Fallback: Finnhub symbol search (filter to US exchanges, prefix match only)
    if len(results) < 3:
        finnhub_key = _get_api_key("pmacs.data.finnhub", "api_key")
        if finnhub_key:
            try:
                url = f"https://finnhub.io/api/v1/search?q={urllib.request.quote(q)}&token={finnhub_key}"
                with urllib.request.urlopen(url, timeout=4) as resp:
                    data = _json.loads(resp.read().decode())
                _us_exchanges = {"", "US", "NYSE", "NASDAQ", "AMEX", "ARCA", "BATS", "OTC"}
                for item in data.get("result", []):
                    sym = item.get("symbol", "")
                    # Only include if ticker starts with query and looks like a US listing
                    if (sym and sym.startswith(q) and sym not in existing
                            and ("." not in sym) and ("-" not in sym)):
                        results.append({
                            "ticker": sym,
                            "name": item.get("description", ""),
                            "source": "finnhub",
                        })
                        existing.add(sym)
                    if len(results) >= 15:
                        break
            except Exception:
                pass

    return JSONResponse({"results": results[:15]})


def _fetch_sector_from_finnhub(ticker: str) -> tuple[str, str]:
    """Fetch sector and subsector from Finnhub /stock/profile2. Returns (sector, subsector)."""
    import json as _json
    import urllib.request

    try:
        api_key = _get_api_key("pmacs.data.finnhub", "api_key")
        if not api_key:
            return "", ""
        url = f"https://finnhub.io/api/v1/stock/profile2?symbol={urllib.request.quote(ticker)}&token={api_key}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            profile = _json.loads(resp.read().decode())
        sector = profile.get("finnhubIndustry") or profile.get("gics_sector") or ""
        subsector = profile.get("gics_group") or ""
        return sector, subsector
    except Exception:
        return "", ""


@router.post("/api/universe/add")
async def universe_add(req: TickerActionRequest):
    """Add a ticker to the universe (Source.md §17.4)."""
    if not req.ticker:
        return JSONResponse({"ok": False, "error": "ticker is required"}, status_code=400)

    ticker = req.ticker.upper()
    cfg = get_config()
    try:
        # Fetch sector/subsector from Finnhub before writing
        sector, subsector = _fetch_sector_from_finnhub(ticker)

        db = data_layer.get_readwrite_db(cfg.sqlite_path)
        try:
            from pmacs.data.universe import UniverseEntry, add_ticker, init_universe_table
            init_universe_table(db)
            add_ticker(db, UniverseEntry(ticker=ticker, sector=sector or None, subsector=subsector or None))
        finally:
            db.close()
        return JSONResponse({"ok": True, "ticker": ticker, "sector": sector, "subsector": subsector})
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("Universe add failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": "Failed to add ticker"}, status_code=500)


@router.post("/api/universe/remove")
async def universe_remove(req: TickerActionRequest):
    """Remove a ticker from the universe (Source.md §17.4)."""
    if not req.ticker:
        return JSONResponse({"ok": False, "error": "ticker is required"}, status_code=400)

    cfg = get_config()
    try:
        db = data_layer.get_readwrite_db(cfg.sqlite_path)
        try:
            from pmacs.data.universe import remove_ticker
            removed = remove_ticker(db, req.ticker.upper())
        finally:
            db.close()
        if not removed:
            return JSONResponse({"ok": False, "error": f"{req.ticker} not in universe"}, status_code=404)
        return JSONResponse({"ok": True, "ticker": req.ticker.upper()})
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("Universe remove failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": "Failed to remove ticker"}, status_code=500)


@router.post("/api/universe/bulk-tag")
async def universe_bulk_tag(req: TickerActionRequest):
    """Tag selected tickers with a sub-sector (operator-confirmed, Source.md §17.6)."""
    if not req.tickers or not req.subsector:
        return JSONResponse({"ok": False, "error": "tickers and subsector required"}, status_code=400)

    cfg = get_config()
    try:
        db = data_layer.get_readwrite_db(cfg.sqlite_path)
        try:
            for ticker in req.tickers:
                db.execute(
                    "UPDATE universe SET subsector = ? WHERE ticker = ?",
                    (req.subsector, ticker.upper()),
                )
            db.commit()
        finally:
            db.close()
        return JSONResponse({"ok": True, "updated": len(req.tickers)})
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("Universe bulk-tag failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": "Failed to tag tickers"}, status_code=500)


@router.post("/api/universe/bulk-remove")
async def universe_bulk_remove(req: TickerActionRequest):
    """Remove selected tickers from universe (operator-confirmed, Source.md §17.6)."""
    if not req.tickers:
        return JSONResponse({"ok": False, "error": "tickers required"}, status_code=400)

    cfg = get_config()
    try:
        db = data_layer.get_readwrite_db(cfg.sqlite_path)
        try:
            from pmacs.data.universe import remove_ticker
            removed = sum(1 for t in req.tickers if remove_ticker(db, t.upper()))
        finally:
            db.close()
        return JSONResponse({"ok": True, "removed": removed})
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("Universe bulk-remove failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": "Failed to remove tickers"}, status_code=500)


@router.post("/api/universe/index-overlay")
async def universe_index_overlay(request: Request):
    """Toggle Nasdaq-100 index overlay (Source.md §17.5)."""
    body = await request.json()
    # No operator confirmation required for overlay toggle — read-only visual change
    return JSONResponse({"ok": True, "enabled": body.get("enabled", False)})
