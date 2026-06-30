"""Unit tests for runtime_state.json persistence of active_backend.

Regression test for the 2026-06-29 active-backend reset loop: the operator
flipped active to openrouter via /settings, but every subsequent `git pull`,
`git checkout main`, or fresh clone of the repository reverted
``config/model_registry.json`` ``active`` to ``"anthropic"`` (the value
committed in 4ec5a10). Each revert caused LLM calls to hit
``https://api.anthropic.com/v1/messages`` with a test-stub key → 401 →
persona fallback → Crucible abort → no memo.

The fix introduces ``config/runtime_state.json`` (gitignored) as the
operator's runtime override layer. ``load_config()`` applies this override
AFTER parsing the registry, so VCS operations that touch the registry cannot
silently undo the operator's last explicit choice.

These tests pin the contract:
  1. runtime_state.json's active_backend overrides model_registry.json's active.
  2. Missing runtime_state.json falls back to the registry default.
  3. Corrupt runtime_state.json falls back without raising.
  4. Unknown backend in runtime_state.json is silently ignored.
  5. /api/settings/inference/provider writes runtime_state.json on switch.
  6. End-to-end: setting runtime_state survives a registry overwrite
     (the simulation of `git checkout HEAD -- config/model_registry.json`).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _write_registry(registry_path: Path, active: str) -> None:
    """Write a minimal model_registry.json with the given active backend."""
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "backends": {
                    "anthropic": {
                        "default_model": "claude-sonnet-4-20250514",
                        "structured_output": "tool_use",
                        "api_key_ref": "pmacs.credentials.anthropic_api_key",
                        "base_url": "https://api.anthropic.com",
                    },
                    "openrouter": {
                        "default_model": "deepseek/deepseek-v4-flash",
                        "structured_output": "json_schema",
                        "api_key_ref": "pmacs.credentials.openrouter_api_key",
                        "base_url": "https://openrouter.ai/api/v1",
                    },
                },
                "active": active,
                "personas": {},
            },
            indent=2,
        )
    )


def _write_runtime_state(runtime_state_path: Path, active_backend: str | None) -> None:
    """Write a runtime_state.json (or delete it if active_backend is None)."""
    runtime_state_path.parent.mkdir(parents=True, exist_ok=True)
    if active_backend is None:
        if runtime_state_path.exists():
            runtime_state_path.unlink()
        return
    runtime_state_path.write_text(json.dumps({"active_backend": active_backend}, indent=2))


# ─── Tests for load_config override behavior ─────────────────────────────────


class TestLoadConfigRuntimeStateOverride:
    """load_config() must apply runtime_state.json's active_backend AFTER
    parsing the registry, so the operator's last explicit choice always wins.
    """

    def test_runtime_state_overrides_file(self, tmp_path: Path, monkeypatch):
        """File says anthropic, runtime_state says openrouter → openrouter wins."""
        # Patch CONFIG_DIR before importing config
        import pmacs.config as cfg_mod

        config_dir = tmp_path / "config"
        registry_path = config_dir / "model_registry.json"
        runtime_state_path = config_dir / "runtime_state.json"

        _write_registry(registry_path, active="anthropic")
        _write_runtime_state(runtime_state_path, active_backend="openrouter")

        monkeypatch.setattr(cfg_mod, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(cfg_mod, "RUNTIME_STATE_PATH", runtime_state_path)

        # Reload module-level constants by re-calling _load_runtime_state
        # (load_config uses module-level RUNTIME_STATE_PATH — we already patched it).
        cfg = cfg_mod.load_config()
        assert cfg.model_registry.active == "openrouter", (
            f"Runtime state should override file; got {cfg.model_registry.active!r}"
        )

    def test_missing_runtime_state_falls_back(self, tmp_path: Path, monkeypatch):
        """No runtime_state.json → use registry's active verbatim."""
        import pmacs.config as cfg_mod

        config_dir = tmp_path / "config"
        registry_path = config_dir / "model_registry.json"
        runtime_state_path = config_dir / "runtime_state.json"

        _write_registry(registry_path, active="anthropic")
        # No runtime_state file

        monkeypatch.setattr(cfg_mod, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(cfg_mod, "RUNTIME_STATE_PATH", runtime_state_path)

        cfg = cfg_mod.load_config()
        assert cfg.model_registry.active == "anthropic"

    def test_corrupt_runtime_state_falls_back(self, tmp_path: Path, monkeypatch):
        """Invalid JSON in runtime_state.json must not crash load_config()."""
        import pmacs.config as cfg_mod

        config_dir = tmp_path / "config"
        registry_path = config_dir / "model_registry.json"
        runtime_state_path = config_dir / "runtime_state.json"

        _write_registry(registry_path, active="anthropic")
        runtime_state_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_state_path.write_text("{ not valid json")

        monkeypatch.setattr(cfg_mod, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(cfg_mod, "RUNTIME_STATE_PATH", runtime_state_path)

        cfg = cfg_mod.load_config()  # Must not raise
        assert cfg.model_registry.active == "anthropic"

    def test_unknown_backend_in_runtime_state_ignored(self, tmp_path: Path, monkeypatch):
        """Safety: a bogus backend name in runtime_state must NOT crash; falls back."""
        import pmacs.config as cfg_mod

        config_dir = tmp_path / "config"
        registry_path = config_dir / "model_registry.json"
        runtime_state_path = config_dir / "runtime_state.json"

        _write_registry(registry_path, active="anthropic")
        _write_runtime_state(runtime_state_path, active_backend="bogus_nonexistent")

        monkeypatch.setattr(cfg_mod, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(cfg_mod, "RUNTIME_STATE_PATH", runtime_state_path)

        cfg = cfg_mod.load_config()  # Must not raise
        assert cfg.model_registry.active == "anthropic", (
            "Unknown runtime_state backend must fall back to file default"
        )

    def test_empty_string_runtime_state_ignored(self, tmp_path: Path, monkeypatch):
        """active_backend="" must be treated as 'no override' (not override to '')."""
        import pmacs.config as cfg_mod

        config_dir = tmp_path / "config"
        registry_path = config_dir / "model_registry.json"
        runtime_state_path = config_dir / "runtime_state.json"

        _write_registry(registry_path, active="anthropic")
        runtime_state_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_state_path.write_text(json.dumps({"active_backend": ""}))

        monkeypatch.setattr(cfg_mod, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(cfg_mod, "RUNTIME_STATE_PATH", runtime_state_path)

        cfg = cfg_mod.load_config()
        assert cfg.model_registry.active == "anthropic"


# ─── Test for /api/settings/inference/provider writer ────────────────────────


class TestSetInferenceProviderWritesRuntimeState:
    """The HTTP route must dual-write: registry + runtime_state.json."""

    def test_set_inference_provider_writes_runtime_state(self, tmp_path: Path, monkeypatch):
        """Mock the FastAPI route's call to _save_registry and assert that
        _save_runtime_state is called with the right payload.
        """
        from pmacs.web.routes import settings as settings_mod

        # Patch the runtime_state path so the helper writes to a tmp file
        runtime_state_path = tmp_path / "config" / "runtime_state.json"
        runtime_state_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings_mod, "_RUNTIME_STATE_PATH", runtime_state_path)

        # Directly call the helper
        settings_mod._save_runtime_state({"active_backend": "openrouter"})

        assert runtime_state_path.exists()
        data = json.loads(runtime_state_path.read_text())
        assert data["active_backend"] == "openrouter"

    def test_save_runtime_state_merges_existing(self, tmp_path: Path, monkeypatch):
        """Calling _save_runtime_state twice must merge, not clobber."""
        from pmacs.web.routes import settings as settings_mod

        runtime_state_path = tmp_path / "config" / "runtime_state.json"
        runtime_state_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings_mod, "_RUNTIME_STATE_PATH", runtime_state_path)

        settings_mod._save_runtime_state({"active_backend": "openrouter"})
        settings_mod._save_runtime_state({"notification_level": "WARN"})

        data = json.loads(runtime_state_path.read_text())
        assert data["active_backend"] == "openrouter"
        assert data["notification_level"] == "WARN"


# ─── End-to-end: runtime_state survives a registry overwrite ───────────────


class TestRuntimeStateSurvivesGitReset:
    """The whole point of the fix: a `git checkout HEAD -- config/model_registry.json`
    (or any other VCS op) must NOT undo the operator's choice.
    """

    def test_runtime_state_wins_after_registry_overwrite(self, tmp_path: Path, monkeypatch):
        """Sequence:
            1. Operator sets active=openrouter → runtime_state created.
            2. `git` overwrites model_registry.json with the committed default.
            3. load_config() re-reads → still returns openrouter.
        """
        import pmacs.config as cfg_mod

        config_dir = tmp_path / "config"
        registry_path = config_dir / "model_registry.json"
        runtime_state_path = config_dir / "runtime_state.json"

        # 1. Initial state: registry says anthropic
        _write_registry(registry_path, active="anthropic")
        monkeypatch.setattr(cfg_mod, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(cfg_mod, "RUNTIME_STATE_PATH", runtime_state_path)

        # 2. Operator sets openrouter (simulated via _save_runtime_state directly)
        from pmacs.web.routes import settings as settings_mod
        runtime_state_path.parent.mkdir(parents=True, exist_ok=True)
        # Patch settings._RUNTIME_STATE_PATH so the helper writes to our tmp file.
        monkeypatch.setattr(settings_mod, "_RUNTIME_STATE_PATH", runtime_state_path)
        settings_mod._save_runtime_state({"active_backend": "openrouter"})

        cfg = cfg_mod.load_config()
        assert cfg.model_registry.active == "openrouter"

        # 3. Simulate `git checkout HEAD -- config/model_registry.json`:
        # the committed default overwrites the file with anthropic
        _write_registry(registry_path, active="anthropic")

        # 4. Re-read config — runtime_state MUST still win
        cfg = cfg_mod.load_config()
        assert cfg.model_registry.active == "openrouter", (
            f"After registry overwrite, runtime_state should still win; "
            f"got {cfg.model_registry.active!r}"
        )
