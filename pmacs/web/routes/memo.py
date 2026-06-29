"""Memo route — dedicated investment memo page for a single ticker."""

import json
import logging
from datetime import datetime, timezone

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


def _compute_paper_portfolio_value(db) -> float:
    """Sum of position_size_usd across all ACTIVE/MONITORING holdings.

    Falls back to ``config/risk.toml`` starting_usd ($5,000) when no active
    holdings exist. The 5K paper capital is the source of truth for an empty
    book.
    """
    row = db.execute(
        """SELECT COALESCE(SUM(position_size_usd), 0)
           FROM holdings
           WHERE state IN ('ACTIVE', 'MONITORING')"""
    ).fetchone()
    total = row[0] if row else 0.0
    if total and total > 0:
        return float(total)
    # Fall back to starting capital
    try:
        import tomllib
        with open("config/risk.toml", "rb") as f:
            cfg = tomllib.load(f)
            return float(cfg.get("capital", {}).get("starting_usd", 5000.0))
    except Exception:
        return 5000.0


def _load_risk_config() -> dict:
    """Load the risk.toml values the memo card needs."""
    try:
        import tomllib
        with open("config/risk.toml", "rb") as f:
            cfg = tomllib.load(f)
            return {
                "max_position_pct": float(cfg.get("position", {}).get("max_single_position_pct", 0.20)),
                "default_target_pct": float(cfg.get("pricing", {}).get("default_target_gain_pct", 0.10)),
                "default_stop_pct": float(cfg.get("pricing", {}).get("default_stop_loss_pct", 0.15)),
            }
    except Exception:
        return {
            "max_position_pct": 0.20,
            "default_target_pct": 0.10,
            "default_stop_pct": 0.15,
        }


def _filter_catalysts_to_horizon(catalysts: list[dict], months: int = 12) -> list[dict]:
    """Filter catalysts to future-only within the horizon (default 12 months).

    Catalysts with unparseable dates are kept (they show as 'date TBD' in
    the timeline) but not date-bucketed. Past catalysts are dropped — they
    surface in the sidebar's 'what already happened' drawer.
    """
    if not catalysts:
        return []
    now = datetime.now(timezone.utc)
    horizon_end = now.replace(month=now.month + months if now.month <= 12 - months else 1)
    # Python's replace() doesn't accept month > 12. We compute manually.
    total_months = now.year * 12 + now.month - 1 + months
    horizon_year, horizon_month = divmod(total_months, 12)
    horizon_month += 1  # 1-indexed
    horizon_end = now.replace(year=horizon_year, month=horizon_month)
    out: list[dict] = []
    for c in catalysts:
        ds = c.get("expected_date") or ""
        if not ds or ds in ("TBD", "tbd", ""):
            out.append(c)
            continue
        try:
            d = datetime.fromisoformat(ds.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            out.append(c)
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        if d >= now and d <= horizon_end:
            out.append(c)
    return out


def _freshness_age_human(ts_str: str | None) -> tuple[str, str]:
    """Convert an ISO timestamp to (human_label, color_class).

    Color classes: 'fresh' (green, <1h), 'stale-ok' (amber, <24h),
    'stale-bad' (red, >24h). Returns ('—', 'unknown') for missing input.
    """
    if not ts_str:
        return ("—", "unknown")
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return (ts_str[:10] if ts_str else "—", "unknown")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return (f"{int(hours * 60)}m ago", "fresh")
    if hours < 24:
        return (f"{int(hours)}h ago", "fresh")
    if hours < 24 * 7:
        return (f"{int(hours / 24)}d ago", "stale-ok")
    return (f"{int(hours / 24)}d ago", "stale-bad")


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
            latest_memo = _get_latest_memo_from_table(db, ticker)
            memo_tickers = _get_memo_ticker_list(db)
        finally:
            db.close()

        # Prev/next navigation — always available so uncycled/unmemoed tickers
        # can still hop to the next analyzed ticker.
        try:
            idx = memo_tickers.index(ticker)
        except ValueError:
            idx = -1
        prev_ticker = memo_tickers[idx - 1] if idx > 0 else None
        next_ticker = memo_tickers[idx + 1] if 0 <= idx < len(memo_tickers) - 1 else None
        ticker_position = idx + 1 if idx >= 0 else None
        ticker_total = len(memo_tickers)

        # Memo page renders ONLY the full long-form memo (memos.memo_json).
        # If no cycle has produced a structured memo for this ticker yet, show
        # the explicit empty state — never a thin fallback derived from a short
        # decision.thesis_summary line. This keeps the /memo/{ticker} page
        # long-only by operator directive.
        if not latest_memo:
            return templates.TemplateResponse(
                request=request,
                name="memo.html",
                context={
                    "page": "memo",
                    "ticker": ticker,
                    "not_analyzed": True,
                    "prev_ticker": prev_ticker,
                    "next_ticker": next_ticker,
                    "ticker_position": ticker_position,
                    "ticker_total": ticker_total,
                },
            )

        memo = latest_memo

        # Defer heavier lookups (holdings, decisions, mode) until after the
        # long-only guard — they are only meaningful when a memo exists.
        db = data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            holding = _get_holding_for_ticker(db, ticker)
            decisions = data_layer.get_recent_decisions(db, limit=20)
            ticker_decisions = [d for d in decisions if d["ticker"] == ticker]
            current_mode = data_layer.get_current_mode(db)
        finally:
            db.close()

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

        # Agent signals + crucible result — derived from the persisted memo_json
        # (Part C injects agent_signals + crucible_* deterministically), not the
        # demo path's in-memory globals (deleted in Part E). memo.html prefers
        # these route vars but also falls back to memo.agent_signals/memo.crucible_*.
        agent_results = memo.get("agent_signals") or []
        crucible_result = None
        if memo.get("crucible_severity") is not None:
            crucible_result = {
                "severity": memo.get("crucible_severity", 0.0),
                "thesis_survives": memo.get("crucible_thesis_survives", True),
                "summary": memo.get("crucible_summary", ""),
                "attacks": memo.get("crucible_attacks", []),
            }

        # Compute upside/downside
        fair_value = memo.get("fair_value") or (holding.get("price_target_usd") if holding else None)
        upside_pct = None
        if fair_value and current_price and current_price > 0:
            upside_pct = ((fair_value - current_price) / current_price) * 100

        # Valuation range
        val_range = memo.get("valuation_range", {})

        # Verdict / conviction: holdings win, else the most-recent decision.
        # The memo-then-decisions fallback is intentionally skipped because
        # `memo` is a long-form memo_json (no _verdict field) — by operator
        # directive the memo page renders the long form only.
        verdict = (
            holding.get("verdict") if holding else (
                ticker_decisions[0]["verdict"] if ticker_decisions else "N/A"
            )
        )
        conviction = (
            holding.get("conviction_score", 0) if holding else (
                ticker_decisions[0].get("conviction_score", 0) if ticker_decisions else 0
            )
        )

        # ── Allocator-grade context wiring ──────────────────────────────────
        # The hero card (EV + R:R + Sizing) and the risk/catalyst/timeline
        # sections need five things that the memo's long-form JSON does not
        # carry directly: portfolio value, position caps, target/stop prices,
        # forward valuation expectations, and freshness provenance. All math
        # lives in Python (Five Non-Negotiables §2 — LLMs never math).
        risk_cfg = _load_risk_config()
        db2 = data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            paper_portfolio_value = _compute_paper_portfolio_value(db2)
        finally:
            db2.close()

        # Target / stop prices:
        #   1. memo.fair_value (analyst conviction) → target
        #   2. holding.price_target_usd (active position's anchor) → target
        #   3. risk.toml default_target_gain_pct on current_price → target
        # Stop mirrors the same precedence using default_stop_loss_pct.
        position_target = (
            memo.get("fair_value")
            or (holding.get("price_target_usd") if holding else None)
        )
        position_stop = None
        if current_price and current_price > 0:
            if position_target is None:
                position_target = current_price * (1 + risk_cfg["default_target_pct"])
            position_stop = current_price * (1 - risk_cfg["default_stop_pct"])

        # Forward valuation (Phase 7c) — pull expected/bear/base/bull from
        # the memo's scenario_price block (ForwardValuationEngine output).
        forward_valuation = memo.get("forward_valuation") or {}

        # Position sizing math — pure function in engines/position_sizing.py.
        # Returns SizingResult with R:R, share counts at 1/2/5% risk, and
        # binding-constraint identification. is_available=False when inputs
        # are degenerate (no live price, stop not below current, etc).
        sizing = None
        rr_ratio = None
        try:
            from pmacs.engines.position_sizing import (
                compute_sizing,
                SizingInputs,
            )
            sizing_inputs = SizingInputs(
                target_price=float(position_target) if position_target else None,
                stop_price=float(position_stop) if position_stop else None,
                current_price=float(current_price) if current_price else None,
                portfolio_value=paper_portfolio_value,
                max_position_pct=risk_cfg["max_position_pct"],
            )
            sizing = compute_sizing(sizing_inputs)
            rr_ratio = sizing.rr_ratio if sizing.is_available else None
        except Exception:
            sizing = None
            rr_ratio = None

        # Catalysts filtered to the 12-month horizon (future-only).
        catalyst_calendar = memo.get("catalyst_calendar") or []
        catalysts_12mo = _filter_catalysts_to_horizon(catalyst_calendar, months=12)

        # Freshness per data category — for the hero's freshness strip.
        decided_at = memo.get("_decided_at") or memo.get("decided_at")
        freshness = {
            "memo": _freshness_age_human(decided_at),
            "price": _freshness_age_human(memo.get("price_as_of")),
            "filings": _freshness_age_human(memo.get("filings_as_of")),
            "sentiment": _freshness_age_human(memo.get("sentiment_as_of")),
        }

        # PASS verdict surfaces from memo.verdict (set by memo writer persona
        # when evaluate_pass_signal fires). Falls back to holding verdict,
        # then "N/A". ``conviction`` is a raw float; the verdict pill uses
        # the uppercase verdict string.
        memo_verdict = memo.get("verdict") or ""
        if memo_verdict:
            verdict = memo_verdict
        pass_reason = memo.get("pass_reason") or ""
        verdict_tier_label = memo_verdict or verdict

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
                "verdict": verdict,
                "verdict_tier_label": verdict_tier_label,
                "pass_reason": pass_reason,
                "conviction": conviction,
                "prev_ticker": prev_ticker,
                "next_ticker": next_ticker,
                "ticker_position": ticker_position,
                "ticker_total": ticker_total,
                # Allocator-grade fields
                "forward_valuation": forward_valuation,
                "sizing": sizing,
                "rr_ratio": rr_ratio,
                "risk_max_single_position_pct": risk_cfg["max_position_pct"],
                "paper_portfolio_value": paper_portfolio_value,
                "position_target": position_target,
                "position_stop": position_stop,
                "catalysts_12mo": catalysts_12mo,
                "freshness": freshness,
            },
        )
    except Exception as exc:
        _log.error("Memo page failed for %s: %s", ticker, exc, exc_info=True)
        return templates.TemplateResponse(
            request=request,
            name="memo.html",
            context={
                "page": "memo",
                "ticker": ticker,
                "error": data_layer.build_error_context("memo", exc),
            },
        )
