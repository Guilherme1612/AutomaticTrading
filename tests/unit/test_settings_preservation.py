"""Regression: switching the active backend must never clobber per-backend config.

Operator directive (see memory `feedback_preserve_backend_config`): when the
operator switches the active LLM backend — or writes a per-backend field — the
previously-configured ``default_model`` and api key for *every* backend must
survive. The operator found their OpenRouter model silently reset to the
``openai/gpt-4o`` placeholder after a switch, because
``set_inference_model`` used to overwrite ``default_model`` unconditionally
(even on an empty model string).

These tests pin the fix (Jun 23):
  - ``set_inference_model`` with an empty model is a no-op: it does NOT write,
    does NOT clobber the existing ``default_model``, and returns the effective
    (preserved) model.
  - ``set_inference_model`` with a non-empty model DOES persist the new model.
  - ``set_inference_provider`` (the active-switch path) ONLY touches ``active`` —
    it leaves every backend's ``default_model`` and ``api_key_ref`` intact.

The routes are async and read/write the real ``config/model_registry.json``
via ``_load_registry`` / ``_save_registry``; we monkeypatch both to an
in-memory fixture so the test never touches disk or the operator's real config.
API keys live in the keychain (not the registry), so we assert on ``api_key_ref``
preservation rather than the secret itself.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from pmacs.web.routes import settings as settings_module
from pmacs.web.routes.settings import (
    InferenceModelRequest,
    InferenceProviderRequest,
    set_inference_model,
    set_inference_provider,
)


def _fixture_registry() -> dict:
    """A representative registry with two backends carrying distinct models."""
    return {
        "active": "llama_server",
        "backends": {
            "llama_server": {
                "default_model": "qwen3.6-35b-a3b-q5",
                "structured_output": "gbnf",
                "base_url": "http://127.0.0.1:8080",
            },
            "openrouter": {
                "default_model": "deepseek/deepseek-v4-flash",
                "structured_output": "json_schema",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key_ref": "openrouter_api_key",
            },
        },
    }


@pytest.fixture
def isolated_registry(monkeypatch):
    """Route ``_load_registry``/``_save_registry`` to an in-memory dict."""
    state: dict = {"reg": _fixture_registry()}

    def _load() -> dict:
        # Return a shallow-enough copy that nested writes are observable on
        # the live dict (the routes mutate backends in place then save).
        return state["reg"]

    def _save(reg: dict) -> None:
        state["reg"] = reg

    monkeypatch.setattr(settings_module, "_load_registry", _load)
    monkeypatch.setattr(settings_module, "_save_registry", _save)
    return state


def _body(resp) -> dict:
    return json.loads(resp.body)


# --- set_inference_model: the empty-model preservation guard -----------------


def test_empty_model_does_not_clobber_existing_model(isolated_registry):
    """An empty model string means 'no change' — the existing model survives."""
    state = isolated_registry
    before = state["reg"]["backends"]["openrouter"]["default_model"]
    assert before == "deepseek/deepseek-v4-flash"

    resp = asyncio.run(
        set_inference_model(InferenceModelRequest(provider="openrouter", model=""))
    )

    assert resp.status_code == 200
    body = _body(resp)
    assert body["ok"] is True
    assert body["model"] == "deepseek/deepseek-v4-flash"  # effective == preserved
    # The registry on disk is untouched.
    assert state["reg"]["backends"]["openrouter"]["default_model"] == before


def test_whitespace_only_model_does_not_clobber(isolated_registry):
    """A whitespace-only model must be treated identically to empty."""
    before = isolated_registry["reg"]["backends"]["openrouter"]["default_model"]
    resp = asyncio.run(
        set_inference_model(InferenceModelRequest(provider="openrouter", model="   "))
    )
    assert resp.status_code == 200
    assert _body(resp)["model"] == before
    assert isolated_registry["reg"]["backends"]["openrouter"]["default_model"] == before


def test_nonempty_model_persists(isolated_registry):
    """A real model string DOES overwrite — that is the intended write path."""
    state = isolated_registry
    resp = asyncio.run(
        set_inference_model(
            InferenceModelRequest(provider="openrouter", model="openai/gpt-4o")
        )
    )
    assert resp.status_code == 200
    assert _body(resp)["model"] == "openai/gpt-4o"
    assert state["reg"]["backends"]["openrouter"]["default_model"] == "openai/gpt-4o"


def test_other_backends_untouched_when_one_model_written(isolated_registry):
    """Writing openrouter's model must not disturb llama_server's model."""
    state = isolated_registry
    asyncio.run(
        set_inference_model(
            InferenceModelRequest(provider="openrouter", model="openai/gpt-4o")
        )
    )
    assert (
        state["reg"]["backends"]["llama_server"]["default_model"]
        == "qwen3.6-35b-a3b-q5"
    )


def test_empty_model_on_unknown_provider_returns_400(isolated_registry):
    resp = asyncio.run(
        set_inference_model(InferenceModelRequest(provider="nope", model=""))
    )
    assert resp.status_code == 400


# --- set_inference_provider: the active-switch path preserves per-backend config


def test_switching_active_only_changes_active(isolated_registry):
    """Switching the active backend must touch ONLY ``active`` — every backend's
    model and api_key_ref must survive the switch verbatim."""
    state = isolated_registry
    resp = asyncio.run(
        set_inference_provider(InferenceProviderRequest(provider="openrouter"))
    )
    assert resp.status_code == 200
    assert _body(resp) == {"ok": True, "active": "openrouter"}
    assert state["reg"]["active"] == "openrouter"
    # Per-backend config preserved across the switch.
    assert (
        state["reg"]["backends"]["openrouter"]["default_model"]
        == "deepseek/deepseek-v4-flash"
    )
    assert state["reg"]["backends"]["openrouter"]["api_key_ref"] == "openrouter_api_key"
    assert (
        state["reg"]["backends"]["llama_server"]["default_model"]
        == "qwen3.6-35b-a3b-q5"
    )


def test_switching_active_back_and_forth_preserves_both_models(isolated_registry):
    """The operator's actual complaint: switch to API, switch back to local, and
    both per-backend models must still be the ones they configured."""
    state = isolated_registry
    asyncio.run(set_inference_provider(InferenceProviderRequest(provider="openrouter")))
    asyncio.run(set_inference_provider(InferenceProviderRequest(provider="llama_server")))
    assert state["reg"]["active"] == "llama_server"
    assert (
        state["reg"]["backends"]["llama_server"]["default_model"]
        == "qwen3.6-35b-a3b-q5"
    )
    assert (
        state["reg"]["backends"]["openrouter"]["default_model"]
        == "deepseek/deepseek-v4-flash"
    )


def test_switching_to_unknown_provider_returns_400(isolated_registry):
    resp = asyncio.run(
        set_inference_provider(InferenceProviderRequest(provider="nope"))
    )
    assert resp.status_code == 400
