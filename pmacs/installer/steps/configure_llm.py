"""Wizard step: configure LLM backend.

Configures the GGUF model path for local llama-server inference.
"""
from __future__ import annotations

from pathlib import Path

from pmacs.installer.wizard import Wizard


def run(wizard: Wizard, model_path: str | None = None) -> dict:
    """Configure LLM backend.

    Args:
        wizard: The wizard instance.
        model_path: Path to GGUF model file.

    Returns:
        Dict with LLM configuration.
    """
    config: dict = {
        "llm_backend": "llama-server",
        "llm_port": 8080,
    }

    if model_path is not None:
        model = Path(model_path)
        config["model_path"] = str(model)
        config["model_exists"] = model.exists()
        config["model_size_mb"] = (
            round(model.stat().st_size / (1024 * 1024), 1)
            if model.exists()
            else 0
        )
    else:
        config["model_path"] = ""
        config["model_exists"] = False

    config["all_ok"] = bool(config.get("model_exists", False))
    return config
