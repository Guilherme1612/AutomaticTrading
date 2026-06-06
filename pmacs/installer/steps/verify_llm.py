"""Wizard step: verify LLM inference backend.

Checks the active backend from model_registry.json and verifies it works:
- llama-server: health check + tiny completion on :8080
- Cloud providers (OpenAI, OpenRouter, Anthropic): API key check + tiny completion

Spec ref: Source.md §12.1 Step 2, Architecture.md §3
"""
from __future__ import annotations

import json as _json
import logging as _logging
import httpx
from pathlib import Path as _Path

_log = _logging.getLogger(__name__)

_REGISTRY_PATH = _Path(__file__).resolve().parents[3] / "config" / "model_registry.json"


def _load_active_backend() -> tuple[str, dict]:
    """Return (active_name, backend_dict) from model_registry.json."""
    if not _REGISTRY_PATH.exists():
        return "llama_server", {}
    registry = _json.loads(_REGISTRY_PATH.read_text())
    active = registry.get("active", "llama_server")
    backend = registry.get("backends", {}).get(active, {})
    return active, backend


async def _verify_llama_server(config: dict) -> dict:
    """Verify local llama-server on :8080.

    Two-stage check:
      1. shutil.which("llama-server") — is it installed?
      2. HTTP health + completion — is it running?
    """
    import shutil

    llama_bin = shutil.which("llama-server")

    if not llama_bin:
        return {
            "ok": False,
            "title": "llama-server not installed",
            "message": "The llama-server binary was not found on your system.",
            "install_title": "Install llama.cpp",
            "install_hint": "brew install llama.cpp\n\nOr build from source:\ngit clone https://github.com/ggerganov/llama.cpp\ncd llama.cpp && make",
            "model_path": "",
        }

    port = config.get("llm_port", 8080)
    base_url = f"http://127.0.0.1:{port}"

    # Health check
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            health_resp = await client.get(f"{base_url}/health")
            if health_resp.status_code != 200:
                return {
                    "ok": False,
                    "title": "llama-server not responding",
                    "message": f"Health check failed (HTTP {health_resp.status_code}).",
                    "install_title": "Start llama-server",
                    "install_hint": f"llama-server -m /path/to/model.gguf --port {port}\n\nBinary: {llama_bin}",
                    "model_path": "",
                }
    except httpx.ConnectError:
        return {
            "ok": False,
            "title": "llama-server installed but not running",
            "message": f"Binary found at {llama_bin}, but nothing is listening on 127.0.0.1:{port}.",
            "install_title": "Start llama-server",
            "install_hint": f"llama-server -m /path/to/model.gguf --port {port}\n\nBinary: {llama_bin}",
            "model_path": "",
        }
    except httpx.TimeoutException:
        return {
            "ok": False,
            "title": "llama-server connection timed out",
            "message": f"Connection to 127.0.0.1:{port} timed out.",
            "install_title": "Check llama-server status",
            "install_hint": f"llama-server may be busy loading a model.\nBinary: {llama_bin}",
            "model_path": "",
        }

    # Test completion
    model_path = ""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{base_url}/completion",
                json={"prompt": "Test: respond with OK", "n_predict": 5, "temperature": 0.1},
            )
            if resp.status_code != 200:
                return {"ok": False, "message": f"Completion failed (HTTP {resp.status_code})", "model_path": model_path}
            body = resp.json()
            content = body.get("content", "")
            model_path = body.get("model", "")
            if not content.strip():
                return {"ok": False, "message": "llama-server returned empty completion", "model_path": model_path}
    except httpx.TimeoutException:
        return {"ok": False, "message": "Completion request timed out (30s)", "model_path": model_path}
    except Exception as exc:
        return {"ok": False, "message": f"Completion error: {exc}", "model_path": model_path}

    return {"ok": True, "message": "llama-server responding normally", "model_path": model_path}


async def _verify_cloud_provider(active: str, backend: dict) -> dict:
    """Verify a cloud LLM provider (OpenAI-compatible or Anthropic)."""
    base_url = backend.get("base_url", "").rstrip("/")
    model = backend.get("default_model", "")
    api_key_ref = backend.get("api_key_ref", "")
    structured_output = backend.get("structured_output", "")

    if not base_url:
        return {"ok": False, "message": f"No base_url configured for {active}", "model_path": ""}

    # Retrieve API key from keychain
    api_key = ""
    if api_key_ref:
        try:
            import keyring
            api_key = keyring.get_password("pmacs.credentials", api_key_ref) or ""
        except Exception as exc:
            _log.warning("Keyring lookup failed for %s: %s", api_key_ref, exc)

    if not api_key:
        return {
            "ok": False,
            "message": f"API key not found for {active}. Save it via Settings or Wizard.",
            "model_path": "",
        }

    # Send test prompt based on structured_output type
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if structured_output == "json_schema":
                # OpenAI-compatible (OpenRouter, OpenAI)
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "Respond with OK"}],
                        "max_tokens": 5,
                        "temperature": 0.1,
                    },
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                )
            elif structured_output == "tool_use":
                # Anthropic
                resp = await client.post(
                    f"{base_url}/v1/messages",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "Respond with OK"}],
                        "max_tokens": 5,
                    },
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                )
            else:
                return {"ok": False, "message": f"Unknown output type for {active}: {structured_output}", "model_path": ""}

            if resp.status_code == 200:
                return {"ok": True, "message": f"{active} connection successful", "model_path": model}
            return {
                "ok": False,
                "message": f"{active} returned HTTP {resp.status_code}: {resp.text[:200]}",
                "model_path": model,
            }
    except httpx.TimeoutException:
        return {"ok": False, "message": f"{active} connection timed out (15s)", "model_path": model}
    except Exception as exc:
        return {"ok": False, "message": f"{active} connection error: {exc}", "model_path": model}


async def run(config: dict) -> dict:
    """Verify LLM inference backend is operational.

    Reads the active backend from model_registry.json and dispatches
    to the appropriate verification method.

    Args:
        config: Wizard config dict (may contain llm_port override).

    Returns:
        Dict with:
            ok: bool - whether inference works
            message: str - human-readable status
            model_path: str - path or model name (if detected)
    """
    active, backend = _load_active_backend()

    if active == "llama_server":
        return await _verify_llama_server(config)

    return await _verify_cloud_provider(active, backend)
