"""Memo route — dedicated investment memo page for a single ticker."""

import json
import logging

from fastapi import APIRouter, Request

from pmacs.web.config import get_config
from pmacs.web import data as data_layer

router = APIRouter()
_log = logging.getLogger("pmacs.web.memo")


def _parse_memo_json(thesis_summary: str | None) -> dict:
    """Parse thesis_summary as JSON, falling back to raw text."""
    if not thesis_summary:
        return {}
    try:
        data = json.loads(thesis_summary)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    # Legacy plain-text memo
    return {"raw_text": thesis_summary, "thesis": thesis_summary}


def _ensure_price_target_column(cfg):
    """Ensure price_target_usd column exists in holdings table."""
    try:
        rw_db = data_layer.get_readwrite_db(cfg.sqlite_path)
        try:
            rw_db.execute("ALTER TABLE holdings ADD COLUMN price_target_usd REAL")
            rw_db.commit()
        except Exception:
            pass
        finally:
            rw_db.close()
    except Exception:
        pass


def _get_latest_memo_from_table(db, ticker: str) -> dict | None:
    """Query the memos table for the most recent structured memo for a ticker."""
    row = db.execute(
        """SELECT memo_json, verdict, conviction_score, decided_at
           FROM memos
           WHERE ticker = ?
           ORDER BY decided_at DESC LIMIT 1""",
        (ticker,),
    ).fetchone()
    if not row:
        return None
    memo_json, verdict, conviction_score, decided_at = row
    parsed = _parse_memo_json(memo_json)
    parsed["_verdict"] = verdict
    parsed["_conviction_score"] = conviction_score
    parsed["_decided_at"] = decided_at
    return parsed


def _get_memo_ticker_list(db) -> list[str]:
    """Return tickers with memos ordered by most-recently-analyzed first."""
    rows = db.execute(
        """SELECT ticker FROM memos
           GROUP BY ticker
           ORDER BY MAX(decided_at) DESC"""
    ).fetchall()
    return [r[0] for r in rows]


def _get_holding_for_ticker(db, ticker: str) -> dict | None:
    """Get holding data for a specific ticker (active or most recent)."""

    # Try active first
    row = db.execute(
        """SELECT id, ticker, state, entry_price_usd, position_size_usd,
                  sector, verdict, conviction_score, thesis_summary,
                  current_price_usd, COALESCE(price_target_usd, 0),
                  entry_date, cycle_id_opened
           FROM holdings
           WHERE ticker = ? AND state = 'ACTIVE'
           ORDER BY entry_date DESC LIMIT 1""",
        (ticker,),
    ).fetchone()
    if row:
        return {
            "id": row[0], "ticker": row[1], "state": row[2],
            "entry_price_usd": row[3], "position_size_usd": row[4],
            "sector": row[5], "verdict": row[6], "conviction_score": row[7],
            "thesis_summary": row[8], "current_price_usd": row[9],
            "price_target_usd": row[10] or None,
            "entry_date": row[11], "cycle_id_opened": row[12],
        }
    # Fall back to most recent holding (any state)
    row = db.execute(
        """SELECT id, ticker, state, entry_price_usd, position_size_usd,
                  sector, verdict, conviction_score, thesis_summary,
                  current_price_usd, COALESCE(price_target_usd, 0),
                  entry_date, cycle_id_opened
           FROM holdings
           WHERE ticker = ?
           ORDER BY entry_date DESC LIMIT 1""",
        (ticker,),
    ).fetchone()
    if row:
        return {
            "id": row[0], "ticker": row[1], "state": row[2],
            "entry_price_usd": row[3], "position_size_usd": row[4],
            "sector": row[5], "verdict": row[6], "conviction_score": row[7],
            "thesis_summary": row[8], "current_price_usd": row[9],
            "price_target_usd": row[10] or None,
            "entry_date": row[11], "cycle_id_opened": row[12],
        }
    return None


@router.get("/memo/{ticker}")
async def memo_page(request: Request, ticker: str):
    """Render dedicated investment memo page for a ticker."""
    from pmacs.web.templating import templates
    ticker = ticker.upper().strip()
    cfg = get_config()

    try:
        _ensure_price_target_column(cfg)
        db = data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            holding = _get_holding_for_ticker(db, ticker)
            latest_memo = _get_latest_memo_from_table(db, ticker)
            decisions = data_layer.get_recent_decisions(db, limit=20)
            ticker_decisions = [d for d in decisions if d["ticker"] == ticker]
            memo_tickers = _get_memo_ticker_list(db)
            current_mode = data_layer.get_current_mode(db)
        finally:
            db.close()

        # Prev/next navigation
        try:
            idx = memo_tickers.index(ticker)
        except ValueError:
            idx = -1
        prev_ticker = memo_tickers[idx - 1] if idx > 0 else None
        next_ticker = memo_tickers[idx + 1] if 0 <= idx < len(memo_tickers) - 1 else None
        ticker_position = idx + 1 if idx >= 0 else None
        ticker_total = len(memo_tickers)

        if not holding and not ticker_decisions and not latest_memo:
            return templates.TemplateResponse(
                request=request,
                name="memo.html",
                context={
                    "page": "memo",
                    "mode": current_mode,
                    "ticker": ticker,
                    "error": data_layer.build_error_context(
                        "memo", Exception(f"No data found for {ticker}")
                    ),
                },
            )

        # Use the dedicated memos table (full structured JSON) as primary source.
        # Fall back to thesis_summary from holdings/decisions only if no memo exists.
        if latest_memo:
            memo = latest_memo
        else:
            source = holding or ticker_decisions[0]
            memo = _parse_memo_json(source.get("thesis_summary"))

        # Get current price — prefer memo's stored price (captured at decision time),
        # then holdings, then live fetch
        current_price = memo.get("current_price") if memo else None
        if not current_price and holding:
            current_price = holding.get("current_price_usd")

        # Try to get live price from Finnhub as last resort
        if not current_price:
            try:
                from pmacs.web.routes.pipeline import _fetch_real_price
                current_price = _fetch_real_price(ticker)
            except Exception:
                pass

        # Get agent results from in-memory store if available
        agent_results = []
        crucible_result = None
        try:
            from pmacs.web.routes.pipeline import (
                _last_cycle_agent_results,
                _last_cycle_crucible_results,
            )
            if ticker in _last_cycle_agent_results:
                agent_results = _last_cycle_agent_results[ticker]
            if ticker in _last_cycle_crucible_results:
                crucible_result = _last_cycle_crucible_results[ticker]
        except ImportError:
            pass

        # Compute upside/downside
        fair_value = memo.get("fair_value") or (holding.get("price_target_usd") if holding else None)
        upside_pct = None
        if fair_value and current_price and current_price > 0:
            upside_pct = ((fair_value - current_price) / current_price) * 100

        # Valuation range
        val_range = memo.get("valuation_range", {})

        return templates.TemplateResponse(
            request=request,
            name="memo.html",
            context={
                "page": "memo",
                "ticker": ticker,
                "holding": holding,
                "memo": memo,
                "current_price": current_price,
                "fair_value": fair_value,
                "upside_pct": upside_pct,
                "valuation_range": val_range,
                "agent_results": agent_results,
                "crucible_result": crucible_result,
                "ticker_decisions": ticker_decisions[:5],
                "verdict": holding.get("verdict") if holding else (latest_memo.get("_verdict") if latest_memo else (ticker_decisions[0]["verdict"] if ticker_decisions else "N/A")),
                "conviction": holding.get("conviction_score", 0) if holding else (latest_memo.get("_conviction_score", 0) if latest_memo else (ticker_decisions[0].get("conviction_score", 0) if ticker_decisions else 0)),
                "prev_ticker": prev_ticker,
                "next_ticker": next_ticker,
                "ticker_position": ticker_position,
                "ticker_total": ticker_total,
            },
        )
    except Exception as exc:
        _log.error("Memo page failed for %s: %s", ticker, exc, exc_info=True)
        return templates.TemplateResponse(
            request=request,
            name="memo.html",
            context={
                "page": "memo",
                "mode": "SHADOW + PAPER",
                "ticker": ticker,
                "error": data_layer.build_error_context("memo", exc),
            },
        )
