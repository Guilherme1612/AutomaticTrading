"""Wizard step: smoke test — verify LLM and data sources.

Runs a quick verification that the configured LLM and data sources work.
"""
from __future__ import annotations

from pmacs.installer.wizard import Wizard


def run(wizard: Wizard) -> dict:
    """Run a quick smoke test of configured services.

    This is a stub — in production, it would:
    1. Start llama-server and verify it responds
    2. Make a test LLM inference call
    3. Test data source API connectivity
    4. Test broker API connectivity (if configured)

    Args:
        wizard: The wizard instance.

    Returns:
        Dict with smoke test results.
    """
    config = wizard.config

    results: dict = {
        "llm_test": {
            "ok": False,
            "message": "Stub: LLM not tested in wizard stub",
        },
        "data_test": {
            "ok": False,
            "message": "Stub: data sources not tested in wizard stub",
        },
    }

    # Check if configuration was collected
    llm_configured = bool(config.get("model_path"))
    broker_configured = config.get("api_key_set", False)

    if llm_configured:
        results["llm_test"]["message"] = "Model path configured (stub: no actual inference test)"

    if broker_configured:
        results["data_test"]["message"] = "Broker API key set (stub: no actual API test)"

    results["all_ok"] = True  # Stub always passes
    return results
