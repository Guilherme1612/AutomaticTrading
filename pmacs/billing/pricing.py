"""Pricing table CRUD + live fetch from OpenRouter.

PRD §3, §9.1: Pricing fetched dynamically from OpenRouter /api/v1/models,
cached in SQLite pricing_table. Refreshed on startup, every 24h, and on cache miss.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import httpx

from pmacs.logsys import log_debug
from pmacs.schemas.billing import PricingRecord

logger = logging.getLogger(__name__)

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_CACHE_STALE_HOURS = 24


def fetch_pricing_from_openrouter(model_id: str) -> PricingRecord | None:
    """Fetch live pricing from OpenRouter's public models endpoint.

    Args:
        model_id: Full model ID (e.g. 'deepseek/deepseek-v4-flash').

    Returns:
        PricingRecord or None if model not found or fetch fails.
    """
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(_OPENROUTER_MODELS_URL)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, Exception) as exc:
        log_debug(
            "PRICING_FETCH_FAILED",
            payload={"model_id": model_id, "error": str(exc)},
            level="WARN",
            error_code="PRICING_FETCH_FAILED",
            msg=f"Failed to fetch pricing from OpenRouter: {exc}",
        )
        return None

    models = data.get("data", [])
    for model in models:
        if model.get("id") == model_id:
            pricing = model.get("pricing", {})
            return PricingRecord(
                model_id=model_id,
                input_price_per_token=float(pricing.get("prompt", "0") or "0") / 1_000_000,
                output_price_per_token=float(pricing.get("completion", "0") or "0") / 1_000_000,
                cached_input_price_per_token=(
                    float(pricing.get("cache_read", "0") or "0") / 1_000_000
                    if pricing.get("cache_read") else None
                ),
                per_request_fee=0.0,
                fetched_at=datetime.now(timezone.utc).isoformat(),
                source="openrouter",
            )

    log_debug(
        "PRICING_MODEL_NOT_FOUND",
        payload={"model_id": model_id},
        level="WARN",
        error_code="PRICING_MODEL_NOT_FOUND",
        msg=f"Model '{model_id}' not found in OpenRouter models list",
    )
    return None


def get_pricing(sqlite_conn, model_id: str) -> PricingRecord | None:
    """Get pricing for a model — cache first, fetch on miss or stale.

    Args:
        sqlite_conn: SQLite connection with pricing_table.
        model_id: Model ID to look up.

    Returns:
        PricingRecord or None if unavailable.
    """
    # Try cache
    row = sqlite_conn.execute(
        "SELECT model_id, input_price_per_token, output_price_per_token, "
        "cached_input_price_per_token, per_request_fee, fetched_at, source "
        "FROM pricing_table WHERE model_id = ?",
        [model_id],
    ).fetchone()

    if row is not None:
        fetched_at = row[5]
        try:
            fetched_dt = datetime.fromisoformat(fetched_at)
            age = datetime.now(timezone.utc) - fetched_dt
            if age < timedelta(hours=_CACHE_STALE_HOURS):
                return PricingRecord(
                    model_id=row[0],
                    input_price_per_token=row[1],
                    output_price_per_token=row[2],
                    cached_input_price_per_token=row[3],
                    per_request_fee=row[4],
                    fetched_at=row[5],
                    source=row[6],
                )
        except (ValueError, TypeError):
            pass  # Stale or unparseable — re-fetch

    # Fetch and cache
    pricing = fetch_pricing_from_openrouter(model_id)
    if pricing is not None:
        _upsert_pricing(sqlite_conn, pricing)
    elif row is not None:
        # Fetch failed but we have stale cache — use it with warning
        log_debug(
            "PRICING_USING_STALE_CACHE",
            payload={"model_id": model_id},
            level="WARN",
            error_code="PRICING_FETCH_FAILED",
            msg=f"Using stale pricing cache for {model_id}",
        )
        return PricingRecord(
            model_id=row[0],
            input_price_per_token=row[1],
            output_price_per_token=row[2],
            cached_input_price_per_token=row[3],
            per_request_fee=row[4],
            fetched_at=row[5],
            source=row[6],
        )

    return pricing


def refresh_pricing_table(sqlite_conn, model_id: str | None = None) -> None:
    """Refresh pricing table — re-fetch pricing for a model or all cached models.

    Called on startup and on schedule (every 24h).
    """
    if model_id:
        models_to_refresh = [model_id]
    else:
        rows = sqlite_conn.execute("SELECT model_id FROM pricing_table").fetchall()
        models_to_refresh = [r[0] for r in rows]

    for mid in models_to_refresh:
        pricing = fetch_pricing_from_openrouter(mid)
        if pricing is not None:
            _upsert_pricing(sqlite_conn, pricing)

    log_debug(
        "PRICING_TABLE_REFRESHED",
        payload={"models_refreshed": models_to_refresh},
        level="INFO",
        msg=f"Pricing table refreshed ({len(models_to_refresh)} models)",
    )


def _upsert_pricing(sqlite_conn, pricing: PricingRecord) -> None:
    """Insert or update pricing record in SQLite."""
    sqlite_conn.execute(
        """INSERT INTO pricing_table
            (model_id, input_price_per_token, output_price_per_token,
             cached_input_price_per_token, per_request_fee, fetched_at, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(model_id) DO UPDATE SET
            input_price_per_token = excluded.input_price_per_token,
            output_price_per_token = excluded.output_price_per_token,
            cached_input_price_per_token = excluded.cached_input_price_per_token,
            per_request_fee = excluded.per_request_fee,
            fetched_at = excluded.fetched_at,
            source = excluded.source
        """,
        [
            pricing.model_id,
            pricing.input_price_per_token,
            pricing.output_price_per_token,
            pricing.cached_input_price_per_token,
            pricing.per_request_fee,
            pricing.fetched_at,
            pricing.source,
        ],
    )
    sqlite_conn.commit()
