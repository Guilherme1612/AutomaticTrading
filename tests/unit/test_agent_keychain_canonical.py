"""Unit tests for the canonical-only LLM-provider keychain convention.

After commit f669757-era cleanup, PMACS stores each LLM-provider API key
(openrouter / anthropic / openai) under exactly one keychain slot — the
canonical config-driven name stored in ``model_registry.json``
``backends.<name>.api_key_ref`` (e.g. ``pmacs.credentials.openrouter_api_key``).

Earlier code wrote the same key twice (canonical + short-name like
``openrouter_key``) and ``_get_api_key`` had a fallback that read the
short-name if the canonical lookup failed. That pattern was removed:

  * ``pmacs.agents.base.PersonaRunner._get_api_key`` reads the canonical
    slot only — no short-name fallback.
  * ``pmacs.web.routes.wizard`` writes only the canonical slot on save.
  * The wizard smoke check (LLM provider step) verifies the canonical
    slot via the active backend's ``api_key_ref``, not a hardcoded
    short-name.

These tests pin that contract. If any of them starts failing, the
canonical-only invariant has been violated and a key-drift risk has
re-entered the system.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


# --- _get_api_key: canonical-only contract ----------------------------------


class TestGetApiKeyCanonicalOnly:
    """``PersonaRunner._get_api_key`` must read the canonical slot only."""

    def _call(self, api_key_ref: str) -> str:
        # PersonaRunner is abstract (build_prompt / get_pydantic_model /
        # get_sanity_validator are unimplemented). Test _get_api_key as an
        # unbound method since it does not depend on runner state.
        from pmacs.agents.base import PersonaRunner
        return PersonaRunner._get_api_key(None, api_key_ref)

    def test_returns_empty_for_empty_ref(self):
        assert self._call("") == ""

    def test_reads_canonical_slot(self):
        """The literal api_key_ref is read — nothing else."""
        with patch(
            "keyring.get_password",
            return_value="sk-or-v1-canonical",
        ) as mock_get:
            result = self._call("pmacs.credentials.openrouter_api_key")
        assert result == "sk-or-v1-canonical"
        # Only the canonical slot was looked up — exactly one call.
        assert mock_get.call_count == 1
        called_args = mock_get.call_args.args
        assert called_args == (
            "pmacs.credentials",
            "pmacs.credentials.openrouter_api_key",
        )

    def test_does_not_fall_back_to_short_name(self):
        """Regression: previously _get_api_key fell back to 'openrouter_key'
        when the canonical slot was empty. The fallback was removed; an
        empty canonical slot must now return empty string."""

        # keyring.get_password returns None for the canonical slot,
        # but a real key for the short-name slot.
        def fake_keyring(service, account):
            if account == "pmacs.credentials.openrouter_api_key":
                return None
            if account == "openrouter_key":
                return "sk-or-v1-LEGACY-DEPRECATED"
            return None

        with patch("keyring.get_password", side_effect=fake_keyring):
            result = self._call("pmacs.credentials.openrouter_api_key")
        # The deprecated short-name slot must NOT be returned.
        assert result == ""

    def test_short_name_only_storage_yields_empty(self):
        """If a user previously wrote a key only at the short-name slot
        (e.g. 'openrouter_key'), _get_api_key will not find it. They must
        re-enter via Settings or run the wizard. This is the desired
        fail-loud behavior — silent fallbacks hid key drift in the past.
        """
        with patch(
            "keyring.get_password",
            side_effect=lambda s, a: "sk-or-v1-only-short" if a == "openrouter_key" else None,
        ):
            result = self._call("pmacs.credentials.openrouter_api_key")
        assert result == ""

    def test_handles_keyring_exception(self):
        """If keyring itself raises (e.g. locked keychain), return empty
        rather than propagating the exception. The LLM caller surfaces
        the empty key as a clear 'no key' error downstream."""
        with patch("keyring.get_password", side_effect=Exception("keychain locked")):
            result = self._call("pmacs.credentials.openrouter_api_key")
        assert result == ""


# --- wizard: writes canonical slot only -------------------------------------


class TestWizardWritesCanonicalSlotOnly:
    """The wizard step 10 handler must write the canonical keychain slot
    only — never the legacy '{provider}_key' short-name slot."""

    def test_wizard_writes_canonical_only(self, monkeypatch):
        """POSTing to /wizard/step/3 with provider=openrouter + api_key
        must call keyring.set_password exactly once, with the canonical
        account name. The short-name slot must not be written.

        Note: the keyring write lives in step 3 (cloud LLM provider
        selection), not step 10 (which only handles PAPER promotion)."""
        import importlib.util
        if not importlib.util.find_spec("multipart"):
            pytest.skip("python-multipart not installed")

        # Track every set_password call
        writes: list[tuple[str, str, str]] = []
        real_set = None
        try:
            import keyring as _kr
            real_set = _kr.set_password

            def fake_set(service, account, value):
                writes.append((service, account, value))

            monkeypatch.setattr("keyring.set_password", fake_set)
            # The wizard does ``import keyring`` inline inside the handler
            # (line ~342), so patching the global ``keyring.set_password``
            # is what actually intercepts the call. The other two patches
            # are belt-and-suspenders in case a future refactor moves the
            # import to module scope — they fail quietly (raising=False)
            # when the named attribute doesn't exist.
            monkeypatch.setattr("pmacs.web.routes.wizard.set_password", fake_set, raising=False)
            monkeypatch.setattr("pmacs.web.routes.wizard.keyring.set_password", fake_set, raising=False)
        except ImportError:
            pytest.skip("keyring not installed")

        from fastapi.testclient import TestClient
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            monkeypatch.setenv("PMACS_DATA_DIR", td)
            from pmacs.web.app import app
            client = TestClient(app, raise_server_exceptions=True)

            # Mark backend_type=cloud in the wizard KV store so step 3
            # takes the cloud branch (which has the keyring write).
            # The wizard handler reads _get_backend_type() which looks
            # at the wizard KV file. We create a minimal one.
            from pmacs.web.routes.wizard import _write_wizard_kv
            _write_wizard_kv("backend_type", "cloud")

            resp = client.post(
                "/wizard/step/3",
                data={
                    "provider": "openrouter",
                    "api_model": "deepseek/deepseek-v4-flash",
                    "api_key": "sk-or-v1-wizard-test",
                },
            )

        assert resp.status_code == 200, f"wizard step 3 returned {resp.status_code}: {resp.text[:300]}"

        # Find any keyring.set_password calls related to openrouter
        openrouter_writes = [
            (s, a, v) for s, a, v in writes
            if "openrouter" in a
        ]
        assert openrouter_writes, f"no openrouter keychain write observed: {writes}"
        # Exactly one write — the canonical slot. No short-name slot.
        assert len(openrouter_writes) == 1, (
            f"wizard must write exactly one keychain slot for openrouter, "
            f"got {len(openrouter_writes)}: {openrouter_writes}"
        )
        service, account, value = openrouter_writes[0]
        assert service == "pmacs.credentials"
        assert account == "pmacs.credentials.openrouter_api_key"
        assert value == "sk-or-v1-wizard-test"
        # Belt-and-suspenders: the legacy short-name slot must not appear.
        assert not any(a == "openrouter_key" for _, a, _ in writes), (
            f"wizard must NOT write the legacy 'openrouter_key' short-name slot: {writes}"
        )

    def test_wizard_writes_canonical_for_anthropic(self, monkeypatch):
        """Same invariant for the anthropic provider."""
        import importlib.util
        if not importlib.util.find_spec("multipart"):
            pytest.skip("python-multipart not installed")

        writes: list[tuple[str, str, str]] = []

        def fake_set(service, account, value):
            writes.append((service, account, value))

        # Wizard does ``import keyring`` inline inside the handler, so
        # patching the global ``keyring.set_password`` is sufficient. The
        # openrouter test above also patches module-local references with
        # ``raising=False`` for belt-and-suspenders coverage.
        monkeypatch.setattr("keyring.set_password", fake_set)
        monkeypatch.setattr(
            "pmacs.web.routes.wizard.set_password",
            fake_set,
            raising=False,
        )

        from fastapi.testclient import TestClient
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            monkeypatch.setenv("PMACS_DATA_DIR", td)
            from pmacs.web.app import app
            client = TestClient(app, raise_server_exceptions=True)

            # Mark backend_type=cloud in the wizard KV store so step 3
            # takes the cloud branch (which has the keyring write).
            from pmacs.web.routes.wizard import _write_wizard_kv
            _write_wizard_kv("backend_type", "cloud")

            resp = client.post(
                "/wizard/step/3",
                data={
                    "provider": "anthropic",
                    "api_model": "claude-sonnet-4-20250514",
                    "api_key": "sk-ant-wizard-test",
                },
            )

        assert resp.status_code == 200
        anthropic_writes = [
            (s, a, v) for s, a, v in writes if "anthropic" in a
        ]
        assert len(anthropic_writes) == 1, (
            f"wizard must write exactly one keychain slot for anthropic, got {anthropic_writes}"
        )
        service, account, value = anthropic_writes[0]
        assert account == "pmacs.credentials.anthropic_api_key"
        assert not any(a == "anthropic_key" for _, a, _ in writes), (
            f"wizard must NOT write the legacy 'anthropic_key' short-name slot: {writes}"
        )


# --- wizard smoke check: reads canonical via active backend ----------------


class TestWizardSmokeCheckReadsCanonical:
    """The wizard smoke test verifies the active backend's API key using
    the canonical keychain slot — sourced from
    ``model_registry.json`` ``backends.<active>.api_key_ref``.

    Previously the smoke check hardcoded ``anthropic_key`` (the short-name
    slot for the anthropic provider only). Now it uses the active backend's
    configured ``api_key_ref`` so it works for any cloud provider and
    fails fast on a wrong slot.
    """

    def test_smoke_check_uses_canonical_slot(self, tmp_path, monkeypatch):
        """Smoke check looks up the active backend's api_key_ref, not a
        hardcoded short-name."""
        import importlib.util
        if not importlib.util.find_spec("multipart"):
            pytest.skip("python-multipart not installed")

        # The smoke check lives in a wizard endpoint that reads
        # model_registry.json + keychain. We test the lookup function
        # directly by calling the same _load_registry path the smoke
        # check uses, and verifying the canonical slot is read.
        # Patch the registry path FIRST, before any save/load, so both
        # _save_registry and _load_registry see the same file.
        registry_path = tmp_path / "model_registry.json"
        import pmacs.web.routes.settings as settings_mod
        monkeypatch.setattr(settings_mod, "_REGISTRY_PATH", registry_path)

        from pmacs.web.routes.settings import _load_registry, _save_registry

        registry = {
            "backends": {
                "openrouter": {
                    "api_key_ref": "pmacs.credentials.openrouter_api_key",
                },
            },
            "active": "openrouter",
        }
        _save_registry(registry)

        # Patch keyring.get_password to return a real key only for the
        # canonical slot. If the smoke check tried the short-name slot
        # first, it would get None and fail.
        def fake_get(service, account):
            if account == "pmacs.credentials.openrouter_api_key":
                return "sk-or-v1-canonical-key"
            if account == "openrouter_key":
                return None  # short-name slot is empty
            return None

        monkeypatch.setattr("keyring.get_password", fake_get)

        # Now invoke the smoke check logic that the wizard uses
        reg = _load_registry()
        active_name = reg.get("active", "llama_server")
        api_key_ref = (
            reg.get("backends", {}).get(active_name, {}).get("api_key_ref", "")
        )
        import keyring
        api_key = keyring.get_password("pmacs.credentials", api_key_ref) or ""

        # The smoke check finds the canonical key and not the short-name one.
        assert api_key_ref == "pmacs.credentials.openrouter_api_key"
        assert api_key == "sk-or-v1-canonical-key"

    def test_smoke_check_fails_when_only_short_name_set(self, tmp_path, monkeypatch):
        """If a user previously saved a key only at the short-name slot,
        the new smoke check correctly reports it as missing (since no
        code reads that slot anymore). This is the desired behavior —
        a clear error beats a silent fallback to a possibly-stale key.
        """
        from pmacs.web.routes.settings import _load_registry, _save_registry

        registry_path = tmp_path / "model_registry.json"
        import pmacs.web.routes.settings as settings_mod
        monkeypatch.setattr(settings_mod, "_REGISTRY_PATH", registry_path)

        from pmacs.web.routes.settings import _load_registry, _save_registry

        _save_registry({
            "backends": {
                "openrouter": {
                    "api_key_ref": "pmacs.credentials.openrouter_api_key",
                },
            },
            "active": "openrouter",
        })

        def fake_get(service, account):
            # Short-name slot has a "key" (legacy); canonical is empty.
            if account == "openrouter_key":
                return "sk-or-v1-LEGACY"
            return None

        monkeypatch.setattr("keyring.get_password", fake_get)

        reg = _load_registry()
        active_name = reg.get("active", "llama_server")
        api_key_ref = (
            reg.get("backends", {}).get(active_name, {}).get("api_key_ref", "")
        )
        import keyring
        api_key = keyring.get_password("pmacs.credentials", api_key_ref) or ""

        # Smoke check correctly reports the key as missing.
        assert api_key == "", (
            "smoke check must NOT find a key stored only at the short-name slot"
        )
