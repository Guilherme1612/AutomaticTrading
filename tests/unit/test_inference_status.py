"""Unit tests for the inference provider 'Working' status (settings.py).

The Settings page renders a per-provider badge that must reflect a REAL
connectivity test, not just keychain existence — a stale key still 'exists'
in the keychain but returns 401. These tests pin the classification logic that
turns a provider's HTTP response into one of: working | invalid_key |
no_budget | no_key | not_working.
"""
from __future__ import annotations

import asyncio

import pytest

from pmacs.web.routes.settings import _classify_cloud_response, _test_provider


class _Resp:
    """Minimal stand-in for an httpx.Response — only status_code + text used."""

    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


# --- _classify_cloud_response -------------------------------------------------


class TestClassifyCloudResponse:
    def test_200_is_working(self):
        assert _classify_cloud_response(_Resp(200)) == ("working", "connection successful")

    def test_401_is_invalid_key(self):
        status, msg = _classify_cloud_response(_Resp(401, "invalid api key"))
        assert status == "invalid_key"
        assert "401" in msg

    def test_403_with_limit_keyword_is_no_budget(self):
        # OpenRouter's actual out-of-budget response: 403 "Key limit exceeded"
        status, msg = _classify_cloud_response(_Resp(403, "Key limit exceeded (total limit)"))
        assert status == "no_budget"
        assert "budget" in msg

    def test_403_with_quota_keyword_is_no_budget(self):
        status, _ = _classify_cloud_response(_Resp(429, "quota exceeded for the month"))
        assert status == "no_budget"

    def test_403_with_credits_keyword_is_no_budget(self):
        status, _ = _classify_cloud_response(_Resp(402, "insufficient credits"))
        assert status == "no_budget"

    def test_bare_403_is_invalid_key(self):
        # A 403 with no billing keyword — treated as a key/permission problem.
        status, _ = _classify_cloud_response(_Resp(403, "forbidden"))
        assert status == "invalid_key"

    def test_429_no_budget_keyword_is_not_working(self):
        status, _ = _classify_cloud_response(_Resp(429, "slow down"))
        assert status == "not_working"

    def test_other_status_is_not_working(self):
        status, _ = _classify_cloud_response(_Resp(500, "server error"))
        assert status == "not_working"
        assert "500" in _

    def test_message_never_echoes_body(self):
        # The body may contain error detail; only a sanitized message is returned
        # (never the raw body, which could echo sensitive context).
        _, msg = _classify_cloud_response(_Resp(403, "Key limit exceeded — secret-token-leak"))
        assert "secret-token-leak" not in msg


# --- _test_provider (no-network branches) -------------------------------------


class TestTestProviderNoNetwork:
    def _run(self, backend_id, backend):
        return asyncio.run(_test_provider(backend_id, backend))

    def test_cloud_no_key_returns_no_key(self):
        import keyring
        real_get = keyring.get_password
        keyring.get_password = lambda service, ref: None  # type: ignore
        try:
            res = self._run("openrouter", {
                "api_key_ref": "pmacs.credentials.openrouter_api_key",
                "default_model": "deepseek/deepseek-v4-flash",
                "base_url": "https://openrouter.ai/api/v1",
                "structured_output": "json_schema",
            })
        finally:
            keyring.get_password = real_get  # type: ignore
        assert res["status"] == "no_key"

    def test_cloud_no_model_returns_not_working(self):
        # Force a key to exist by monkeypatching keyring so we reach the model check.
        import keyring
        real_get = keyring.get_password
        keyring.get_password = lambda service, ref: "sk-test-fake"  # type: ignore
        try:
            res = self._run("openrouter", {
                "api_key_ref": "pmacs.credentials.openrouter_api_key",
                "default_model": "",
                "base_url": "https://openrouter.ai/api/v1",
                "structured_output": "json_schema",
            })
        finally:
            keyring.get_password = real_get  # type: ignore
        assert res["status"] == "not_working"
        assert "model" in res["message"]

    def test_cloud_unknown_structured_output_returns_not_working(self):
        import keyring
        real_get = keyring.get_password
        keyring.get_password = lambda service, ref: "sk-test-fake"  # type: ignore
        try:
            res = self._run("weird", {
                "api_key_ref": "pmacs.credentials.weird_api_key",
                "default_model": "m",
                "base_url": "https://weird.example/api",
                "structured_output": "csv",  # unsupported
            })
        finally:
            keyring.get_password = real_get  # type: ignore
        assert res["status"] == "not_working"
        assert "output type" in res["message"]

    def test_local_llama_server_not_running(self):
        # No llama-server in the test env -> connection refused -> not_working.
        res = self._run("llama_server", {"api_key_ref": ""})
        assert res["status"] == "not_working"
        assert "running" in res["message"].lower()

    def test_local_ollama_not_running(self):
        # Point at a dead port so this is deterministic regardless of whether
        # a real Ollama happens to be running on :11434 on the operator's machine.
        res = self._run("ollama", {"api_key_ref": "", "url": "http://127.0.0.1:1"})
        assert res["status"] == "not_working"
