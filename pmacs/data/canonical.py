"""Canonical JSON serialization for hash-chaining (Architecture.md §5.1)."""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from enum import Enum


def canonical_json(payload: dict) -> str:
    """Deterministic serialization for hash-chaining.

    Sort keys, compact separators, no NaN/Inf, float rounding to 10 decimals.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_default,
    )


def _default(obj):
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            raise ValueError("NaN/Inf not allowed in canonical JSON")
        return round(obj, 10)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    raise TypeError(f"Unsupported type: {type(obj)}")
