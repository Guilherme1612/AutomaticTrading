"""Wizard step: configure broker API (Alpaca paper trading).

Configures Alpaca paper trading API credentials for PAPER mode.
"""
from __future__ import annotations

from pmacs.installer.wizard import Wizard


def run(
    wizard: Wizard,
    *,
    api_key: str = "",
    api_secret: str = "",
    paper: bool = True,
) -> dict:
    """Configure Alpaca broker API.

    Args:
        wizard: The wizard instance.
        api_key: Alpaca API key.
        api_secret: Alpaca API secret.
        paper: Use paper trading endpoint (default True).

    Returns:
        Dict with broker configuration.
    """
    base_url = (
        "https://paper-api.alpaca.markets"
        if paper
        else "https://api.alpaca.markets"
    )

    config: dict = {
        "broker": "alpaca",
        "paper": paper,
        "base_url": base_url,
        "api_key_set": bool(api_key),
        "api_secret_set": bool(api_secret),
    }

    config["all_ok"] = bool(api_key) and bool(api_secret)

    return config
