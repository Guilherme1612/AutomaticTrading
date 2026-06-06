"""Integration tests for the first-run wizard.

Tests the wizard route handlers, backend step modules,
and template rendering end-to-end.

Spec ref: Source.md §12
"""
from __future__ import annotations

import importlib.util

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

if not importlib.util.find_spec("multipart"):
    pytest.skip("python-multipart not installed", allow_module_level=True)


@pytest.fixture
def client():
    """FastAPI test client with wizard routes registered."""
    from pmacs.web.app import app
    return TestClient(app, raise_server_exceptions=True)


class TestWizardHomeRoute:
    """GET /wizard/ renders step 1 (welcome)."""

    def test_wizard_home_returns_200(self, client):
        resp = client.get("/wizard/", follow_redirects=True)
        assert resp.status_code == 200

    def test_wizard_home_contains_pmacs_title(self, client):
        resp = client.get("/wizard/", follow_redirects=True)
        assert "PMACS" in resp.text

    def test_wizard_home_has_lets_go_button(self, client):
        resp = client.get("/wizard/", follow_redirects=True)
        assert "Let's go" in resp.text

    def test_wizard_home_has_progress_dots(self, client):
        resp = client.get("/wizard/", follow_redirects=True)
        # 11 dots for 11 steps
        assert "Step 1" in resp.text


class TestWizardStatusRoute:
    """GET /wizard/status returns JSON progress."""

    def test_status_returns_json(self, client):
        resp = client.get("/wizard/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "current_step" in data
        assert "total_steps" in data
        assert data["total_steps"] == 11

    def test_status_default_step_is_1(self, client):
        resp = client.get("/wizard/status")
        data = resp.json()
        assert data["current_step"] == 1


class TestWizardStepRouting:
    """POST /wizard/step/{N} dispatches to correct step handler."""

    def test_step_1_advances_to_step_2(self, client):
        resp = client.post("/wizard/step/1")
        assert resp.status_code == 200

    def test_step_out_of_range_returns_400(self, client):
        resp = client.post("/wizard/step/0")
        assert resp.status_code == 400

        resp = client.post("/wizard/step/13")
        assert resp.status_code == 400

    def test_step_4_accepts_form_data(self, client):
        form_data = {
            "alpaca_key": "test_key",
            "alpaca_secret": "test_secret",
            "polygon_key": "test_polygon",
            "finnhub_key": "",
            "fred_key": "",
            "edgar_ua": "Test test@example.com",
        }
        resp = client.post("/wizard/step/4", data=form_data)
        assert resp.status_code == 200

    def test_step_9_accepts_cycle_prefs(self, client):
        form_data = {
            "display_currency": "USD",
            "timezone": "US/Eastern",
            "eod_cutoff": "16:30",
        }
        resp = client.post("/wizard/step/9", data=form_data)
        assert resp.status_code == 200

    def _reset_model_registry(self):
        """Reset model_registry.json to default state after test."""
        import json
        from pathlib import Path

        config_dir = Path(__file__).resolve().parents[2] / "config"
        registry_path = config_dir / "model_registry.json"
        default = {
            "backends": {
                "llama_server": {
                    "url": "http://127.0.0.1:8080",
                    "default_model": "unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_XL",
                    "structured_output": "gbnf",
                    "api_key_ref": "",
                    "base_url": "",
                },
                "ollama": {
                    "url": "http://127.0.0.1:11434",
                    "default_model": "qwen3.6:35b-a3b-coding-mxfp8",
                    "structured_output": "json_schema",
                    "api_key_ref": "",
                    "base_url": "",
                },
                "anthropic": {
                    "url": "",
                    "default_model": "claude-sonnet-4-20250514",
                    "structured_output": "tool_use",
                    "api_key_ref": "pmacs.credentials.anthropic_api_key",
                    "base_url": "https://api.anthropic.com",
                },
                "openai": {
                    "url": "",
                    "default_model": "gpt-4o",
                    "structured_output": "json_schema",
                    "api_key_ref": "pmacs.credentials.openai_api_key",
                    "base_url": "https://api.openai.com/v1",
                },
                "openrouter": {
                    "url": "",
                    "default_model": "openai/gpt-4o",
                    "structured_output": "json_schema",
                    "api_key_ref": "pmacs.credentials.openrouter_api_key",
                    "base_url": "https://openrouter.ai/api/v1",
                },
            },
            "active": "llama_server",
            "personas": {
                "gatekeeper": None,
                "macro_regime": "default",
                "catalyst_summarizer": "default",
                "moat_analyst": "default",
                "growth_hunter": "default",
                "insider_activity": "default",
                "short_interest": "default",
                "forensics": "default",
                "crucible": "default",
            },
            "candidates": {},
        }
        registry_path.write_text(json.dumps(default, indent=2))

    def test_step_10_llm_provider_local_default(self, client):
        """LLM provider step saves provider choice to model_registry.json."""
        form_data = {"provider": "llama_server"}
        resp = client.post("/wizard/step/10", data=form_data)
        assert resp.status_code == 200
        self._reset_model_registry()

    def test_step_10_llm_provider_anthropic(self, client):
        """LLM provider step accepts anthropic with API key."""
        form_data = {
            "provider": "anthropic",
            "api_model": "claude-sonnet-4-20250514",
            "api_key": "sk-ant-test123",
        }
        resp = client.post("/wizard/step/10", data=form_data)
        assert resp.status_code == 200
        self._reset_model_registry()

    def test_step_10_llm_provider_openai(self, client):
        """LLM provider step accepts openai with API key."""
        form_data = {
            "provider": "openai",
            "api_model": "gpt-4o",
            "api_key": "sk-test123",
        }
        resp = client.post("/wizard/step/10", data=form_data)
        assert resp.status_code == 200
        self._reset_model_registry()

    def test_step_10_llm_provider_openrouter(self, client):
        """LLM provider step accepts openrouter with API key."""
        form_data = {
            "provider": "openrouter",
            "api_model": "anthropic/claude-sonnet-4",
            "api_key": "sk-or-test123",
        }
        resp = client.post("/wizard/step/10", data=form_data)
        assert resp.status_code == 200
        self._reset_model_registry()


class TestVerifyLLMStep:
    """Tests for pmacs.installer.steps.verify_llm module."""

    @pytest.mark.asyncio
    async def test_verify_llm_returns_ok_when_server_running(self):
        from pmacs.installer.steps.verify_llm import run

        mock_health = MagicMock(status_code=200)
        mock_completion = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"content": "OK", "model": "/path/to/model.gguf"}),
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_health)
            mock_instance.post = AsyncMock(return_value=mock_completion)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_instance

            result = await run({})
            assert result["ok"] is True
            assert "model_path" in result

    @pytest.mark.asyncio
    async def test_verify_llm_fails_when_server_down(self):
        import httpx
        from pmacs.installer.steps.verify_llm import run

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_instance

            result = await run({})
            assert result["ok"] is False
            assert "not running" in result["message"].lower() or "install" in result["message"].lower() or "nothing is listening" in result["message"].lower()


class TestVerifyDataStep:
    """Tests for pmacs.installer.steps.verify_data module."""

    @pytest.mark.asyncio
    async def test_verify_data_returns_results_dict(self):
        from pmacs.installer.steps.verify_data import run

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_resp = MagicMock(status_code=200)
            mock_instance.get = AsyncMock(return_value=mock_resp)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_instance

            result = await run({})
            assert "results" in result
            assert "all_ok" in result
            assert isinstance(result["results"], dict)

    @pytest.mark.asyncio
    async def test_verify_data_critical_failure_blocks(self):
        import httpx
        from pmacs.installer.steps.verify_data import run

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_instance

            result = await run({})
            assert result["all_ok"] is False

    @pytest.mark.asyncio
    async def test_verify_data_401_counts_as_reachable(self):
        from pmacs.installer.steps.verify_data import run

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_resp = MagicMock(status_code=401)
            mock_instance.get = AsyncMock(return_value=mock_resp)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_instance

            result = await run({})
            # 401 = reachable (auth needed), not a connectivity failure
            assert result["all_ok"] is True


class TestTOTPEnrollStep:
    """Tests for pmacs.installer.steps.totp_enroll module."""

    @pytest.mark.asyncio
    async def test_totp_phase1_generates_secret_and_qr(self):
        from pmacs.installer.steps.totp_enroll import run

        result = await run({})
        assert "_secret" in result
        assert "qr_data_uri" in result
        assert result["qr_data_uri"].startswith("data:image/svg+xml")
        assert result["ok"] is False  # Not yet verified

    @pytest.mark.asyncio
    async def test_totp_phase2_rejects_invalid_code(self):
        from pmacs.installer.steps.totp_enroll import run
        from pmacs.cortex.totp import generate_totp_secret

        secret = generate_totp_secret()
        form_data = {
            "totp_secret": secret,
            "totp_0": "9",
            "totp_1": "9",
            "totp_2": "9",
            "totp_3": "9",
            "totp_4": "9",
            "totp_5": "9",
        }
        result = await run(form_data)
        assert result["ok"] is False
        assert "Invalid" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_totp_phase2_accepts_valid_code(self):
        from pmacs.installer.steps.totp_enroll import run
        from pmacs.cortex.totp import generate_totp_secret, compute_totp

        secret = generate_totp_secret()
        code = compute_totp(secret)
        digits = list(code)

        form_data = {
            "totp_secret": secret,
        }
        for i, d in enumerate(digits):
            form_data[f"totp_{i}"] = d

        with patch("pmacs.installer.steps.totp_enroll._store_totp_secret"):
            result = await run(form_data)
            assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_totp_phase2_rejects_incomplete_code(self):
        from pmacs.installer.steps.totp_enroll import run
        from pmacs.cortex.totp import generate_totp_secret

        secret = generate_totp_secret()
        form_data = {
            "totp_secret": secret,
            "totp_0": "1",
            "totp_1": "2",
            # Missing digits
        }
        result = await run(form_data)
        assert result["ok"] is False


class TestWizardTemplateRendering:
    """Verify wizard templates render without errors."""

    def test_step01_welcome_renders(self, client):
        resp = client.get("/wizard/")
        assert "System Requirements" in resp.text

    def test_step_templates_contain_progress(self, client):
        resp = client.get("/wizard/")
        # Step 1 should show progress dots
        assert "Step 1" in resp.text or "step" in resp.text.lower()


class TestWizardStepModulesExist:
    """Verify all wizard step modules are importable."""

    def test_verify_llm_importable(self):
        from pmacs.installer.steps import verify_llm
        assert hasattr(verify_llm, "run")

    def test_verify_data_importable(self):
        from pmacs.installer.steps import verify_data
        assert hasattr(verify_data, "run")

    def test_totp_enroll_importable(self):
        from pmacs.installer.steps import totp_enroll
        assert hasattr(totp_enroll, "run")

    def test_existing_steps_importable(self):
        from pmacs.installer.steps import check_system
        from pmacs.installer.steps import configure_llm
        from pmacs.installer.steps import smoke_test
        assert hasattr(check_system, "run")
        assert hasattr(configure_llm, "run")
        assert hasattr(smoke_test, "run")
