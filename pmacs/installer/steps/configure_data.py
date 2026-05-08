"""Wizard step: configure data source API keys.

Configures API keys for market data sources (e.g., Alpha Vantage, Finnhub).
"""
from __future__ import annotations

from pmacs.installer.wizard import Wizard

# Known data sources
DATA_SOURCES = [
    "alpha_vantage",
    "finnhub",
    "sec_edgar",
]


def run(wizard: Wizard, api_keys: dict[str, str] | None = None) -> dict:
    """Configure data source API keys.

    Args:
        wizard: The wizard instance.
        api_keys: Dict mapping source names to API keys.

    Returns:
        Dict with data source configuration.
    """
    if api_keys is None:
        api_keys = {}

    config: dict = {
        "sources": {},
    }

    for source in DATA_SOURCES:
        key = api_keys.get(source, "")
        config["sources"][source] = {
            "configured": bool(key),
            "key_set": bool(key),
        }

    # At least one source must be configured
    config["all_ok"] = any(
        config["sources"][s]["configured"] for s in DATA_SOURCES
    )

    return config
