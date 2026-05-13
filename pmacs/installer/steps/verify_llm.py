"""Wizard step: verify LLM inference backend.

POSTs a tiny test prompt to llama-server on :8080 to confirm
the inference backend is running and producing output.

Spec ref: Source.md §12.1 Step 2, Architecture.md §3
"""
from __future__ import annotations

import httpx


async def run(config: dict) -> dict:
    """Verify LLM inference backend is operational.

    Sends a minimal completion request to llama-server at :8080.
    If llama-server is not running, returns failure with install instructions.

    Args:
        config: Wizard config dict (may contain llm_port override).

    Returns:
        Dict with:
            ok: bool - whether inference works
            message: str - human-readable status
            model_path: str - path to loaded model (if detected)
    """
    port = config.get("llm_port", 8080)
    base_url = f"http://127.0.0.1:{port}"

    # Step 1: Health check — hit /health first
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            health_resp = await client.get(f"{base_url}/health")
            if health_resp.status_code != 200:
                return {
                    "ok": False,
                    "message": f"llama-server health check failed (HTTP {health_resp.status_code})",
                    "model_path": "",
                }
    except httpx.ConnectError:
        return {
            "ok": False,
            "message": "llama-server not running on 127.0.0.1:8080. Install: brew install llama.cpp",
            "model_path": "",
        }
    except httpx.TimeoutException:
        return {
            "ok": False,
            "message": "llama-server connection timed out on 127.0.0.1:8080",
            "model_path": "",
        }

    # Step 2: Send a tiny test prompt
    model_path = ""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            completion_resp = await client.post(
                f"{base_url}/completion",
                json={
                    "prompt": "Test: respond with OK",
                    "n_predict": 5,
                    "temperature": 0.1,
                },
            )
            if completion_resp.status_code != 200:
                return {
                    "ok": False,
                    "message": f"Completion request failed (HTTP {completion_resp.status_code})",
                    "model_path": model_path,
                }

            body = completion_resp.json()
            content = body.get("content", "")
            model_path = body.get("model", "")

            if not content.strip():
                return {
                    "ok": False,
                    "message": "llama-server returned empty completion",
                    "model_path": model_path,
                }

    except httpx.TimeoutException:
        return {
            "ok": False,
            "message": "Completion request timed out (30s)",
            "model_path": model_path,
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Completion request error: {exc}",
            "model_path": model_path,
        }

    return {
        "ok": True,
        "message": "llama-server responding normally",
        "model_path": model_path,
    }
