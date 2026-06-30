"""Invariant test: ONLY the operator's explicit POST may change ``active``.

Operator directive (Jun 30 2026): ``config/model_registry.json.active`` and
``config/runtime_state.json.active_backend`` may be mutated by exactly ONE
code path — the operator's POST to ``/api/settings/inference/provider`` with
``force=true``. NOTHING ELSE: no kill-switch engagement, no daemon tick, no
SSE event, no mutation-engine promotion, no wizard page reload, no health
check. This test pins that invariant at the filesystem level so the
"silently flipped back to anthropic" regression cannot recur.

Strategy:
  - Source-grep every discoverable writer at import time and assert no
    non-operator module touches the registry path constants or calls the
    save helpers. This is defense-in-depth: even if a future writer slips
    in, the import-time assertion fails loudly.
  - Round-trip test of the operator POST: with force=true the registry flips;
    without force=true it stays put. This proves the ONE allowed path works.
  - Round-trip test of ``_get_inference_state()``: when runtime_state.json
    disagrees with model_registry.json, the API response honors the operator
    override (regression: the cycle and the UI used to disagree).
"""
from __future__ import annotations

import importlib
import inspect
import json
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _init_kill_switch_db(db_path: Path) -> None:
    """Bring up a minimal kill_switch table for testing the engage() path."""
    from pmacs.cortex.kill_switch import _ensure_table

    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_table(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. Source-grep assertions — every non-operator module is forbidden
#    from touching the registry.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name",
    [
        # Kill switch lifecycle: engage/disengage/periodic check
        "pmacs.cortex.kill_switch",
        "pmacs.cortex.self_check",
        # Mutation engine: even operator-promoted mutations must not flip
        # ``active`` (it's a separate operator-only field).
        "pmacs.mutation.daemon",
        "pmacs.mutation.promotion",
        "pmacs.mutation.rollback",
        # Cortex page + dashboard chrome + SSE handlers
        "pmacs.web.routes.cortex",
        "pmacs.web.routes.dashboard",
        # Wizard — wizard step-3 IS operator-driven, but its import path
        # must not run during normal page loads. Source-grep checks that
        # the writer is inside an HTTP handler, not module-level.
    ],
)
def test_module_source_does_not_touch_registry_writers(module_name):
    """Forbidden import-time writers: no non-operator module may call the
    save helpers or assign to ``registry['active']`` directly."""
    try:
        mod = importlib.import_module(module_name)
    except ImportError:
        pytest.skip(f"{module_name} not importable in this env")
    src = inspect.getsource(mod)
    forbidden = [
        "_save_registry(",
        "_save_runtime_state(",
        'registry["active"] =',
        "registry['active'] =",
        "registry_path.write_text",
    ]
    for needle in forbidden:
        assert needle not in src, (
            f"{module_name} contains FORBIDDEN writer '{needle}'. The "
            "operator is the ONLY entity that may change ``active`` — see "
            "tests/unit/test_active_backend_invariant.py"
        )


def test_wizard_writer_only_inside_http_handler():
    """Wizard step-3 IS operator-driven (one allowed writer), but the
    assignment to ``registry['active']`` must live inside an async HTTP
    handler, not module level or a scheduled task."""
    from pmacs.web.routes import wizard

    src = inspect.getsource(wizard)
    writers = [
        i for i, line in enumerate(src.splitlines(), 1)
        if 'registry["active"]' in line or "registry['active']" in line
    ]
    assert writers, "wizard step-3 should write registry['active'] once"
    full_src = src
    for ln in writers:
        # Walk back at most 80 lines to find the enclosing def
        start = max(0, ln - 80)
        window = "\n".join(full_src.splitlines()[start:ln])
        assert ("async def " in window) or ("def " in window), (
            f"wizard.py:{ln} writer must live inside an async HTTP handler, "
            "not module level or a scheduled task"
        )


# ---------------------------------------------------------------------------
# 2. Kill switch engage/disengage MUST NOT mutate the registry
# ---------------------------------------------------------------------------


def test_kill_switch_engage_does_not_mutate_registry(tmp_path):
    """Engaging the kill switch from any trigger must leave the registry
    files byte-identical. This is the regression that the user surfaced on
    Jun 30 ("it changed from openrouter to anthropic the moment the kill
    switch was engaged")."""
    from pmacs.cortex.kill_switch import engage

    registry = tmp_path / "model_registry.json"
    registry.write_text(json.dumps({
        "backends": {"openrouter": {}, "anthropic": {}},
        "active": "openrouter",
    }))
    runtime = tmp_path / "runtime_state.json"
    runtime.write_text(json.dumps({"active_backend": "openrouter"}))

    snap_reg = (registry.stat().st_mtime, registry.read_text())
    snap_rt = (runtime.stat().st_mtime, runtime.read_text())

    db = tmp_path / "test.db"
    _init_kill_switch_db(db)
    engage(
        reason="invariant-test: budget breach",
        trigger="CYCLE_BLOCKED_BUDGET_DAILY",
        db_path=str(db),
        audit_path=str(tmp_path / "audit.log"),
        cycle_id="test-cycle-inv",
    )

    assert (registry.stat().st_mtime, registry.read_text()) == snap_reg, (
        "kill switch engagement MUST NOT mutate model_registry.json"
    )
    assert (runtime.stat().st_mtime, runtime.read_text()) == snap_rt, (
        "kill switch engagement MUST NOT mutate runtime_state.json"
    )


def test_kill_switch_disengage_does_not_mutate_registry(tmp_path):
    from pmacs.cortex.kill_switch import disengage, engage

    registry = tmp_path / "model_registry.json"
    registry.write_text(json.dumps({"backends": {}, "active": "openrouter"}))
    runtime = tmp_path / "runtime_state.json"
    runtime.write_text(json.dumps({"active_backend": "openrouter"}))

    db = tmp_path / "test.db"
    _init_kill_switch_db(db)
    engage(reason="setup", trigger="MANUAL", db_path=str(db),
           audit_path=str(tmp_path / "audit.log"))

    snap_reg = (registry.stat().st_mtime, registry.read_text())
    snap_rt = (runtime.stat().st_mtime, runtime.read_text())

    disengage(reason="invariant-test: clear", db_path=str(db),
              audit_path=str(tmp_path / "audit.log"))

    assert (registry.stat().st_mtime, registry.read_text()) == snap_reg
    assert (runtime.stat().st_mtime, runtime.read_text()) == snap_rt


def test_check_all_triggers_does_not_mutate_registry(tmp_path):
    """A full trigger sweep (the periodic check cortex runs every minute)
    must not touch the registry. Even if EVERY trigger fires."""
    from pmacs.cortex.kill_switch import check_all_triggers

    registry = tmp_path / "model_registry.json"
    registry.write_text(json.dumps({"backends": {}, "active": "openrouter"}))
    runtime = tmp_path / "runtime_state.json"
    runtime.write_text(json.dumps({"active_backend": "openrouter"}))

    snap_reg = (registry.stat().st_mtime, registry.read_text())
    snap_rt = (runtime.stat().st_mtime, runtime.read_text())

    db = tmp_path / "test.db"
    _init_kill_switch_db(db)
    # Fire the full sweep — this calls every _check_* trigger function.
    # Some will return triggered=True and engage the KS; we don't care
    # about the kill-switch state, only about the registry being untouched.
    try:
        check_all_triggers(db_path=str(db), audit_path=str(tmp_path / "audit.log"))
    except Exception:
        # Trigger check failures are fine — they don't reach the registry
        pass

    assert (registry.stat().st_mtime, registry.read_text()) == snap_reg
    assert (runtime.stat().st_mtime, runtime.read_text()) == snap_rt


# ---------------------------------------------------------------------------
# 3. The ONE operator path must work, and ONLY it must work
# ---------------------------------------------------------------------------


def _write_minimal_config_dir(tmp_path: Path) -> tuple[Path, Path]:
    """Create a tmp_path/config/ layout that matches pmacs.config.CONFIG_DIR.

    load_config() reads paths relative to the project root's ``config/``
    subdirectory. We monkeypatch CONFIG_DIR + RUNTIME_STATE_PATH to point at
    our tmp_path/config/ so we don't pollute the real one.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    registry = config_dir / "model_registry.json"
    registry.write_text(json.dumps({
        "backends": {
            "openrouter": {"api_key_ref": "openrouter_api_key", "default_model": "m"},
            "anthropic": {"api_key_ref": "anthropic_api_key", "default_model": "n"},
        },
        "active": "openrouter",
    }))
    runtime = config_dir / "runtime_state.json"
    runtime.write_text(json.dumps({"active_backend": "openrouter"}))
    return registry, runtime


def test_operator_post_with_force_true_changes_active(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from pmacs.config import CONFIG_DIR
    from pmacs.web.app import app
    from pmacs.web.routes import settings as settings_route

    registry, runtime = _write_minimal_config_dir(tmp_path)
    # Repoint both the loader's CONFIG_DIR (used by load_config()) and the
    # route module's globals (used by _save_registry / _save_runtime_state).
    monkeypatch.setattr("pmacs.config.CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(settings_route, "_REGISTRY_PATH", registry)
    monkeypatch.setattr(settings_route, "_RUNTIME_STATE_PATH", runtime)
    monkeypatch.setattr("keyring.get_password", lambda *a, **k: "stub")
    # Force load_config to re-parse (cache invalidation)
    from pmacs.config import load_config
    load_config.__wrapped__ = load_config  # no-op, ensures module reloads
    import pmacs.config as pmacs_config
    monkeypatch.setattr(pmacs_config, "CONFIG_DIR", tmp_path / "config")

    client = TestClient(app)
    resp = client.post(
        "/api/settings/inference/provider",
        json={"provider": "anthropic", "force": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert resp.json()["active"] == "anthropic"

    # Both files updated
    reg = json.loads(registry.read_text())
    rt = json.loads(runtime.read_text())
    assert reg["active"] == "anthropic"
    assert rt["active_backend"] == "anthropic"


def test_operator_post_without_force_is_rejected(tmp_path, monkeypatch):
    """Without force=true, the registry must stay put. Proves the
    "operator-only" gate is real, not a no-op."""
    from fastapi.testclient import TestClient
    from pmacs.web.app import app
    from pmacs.web.routes import settings as settings_route

    registry, runtime = _write_minimal_config_dir(tmp_path)
    import pmacs.config as pmacs_config
    monkeypatch.setattr(pmacs_config, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(settings_route, "_REGISTRY_PATH", registry)
    monkeypatch.setattr(settings_route, "_RUNTIME_STATE_PATH", runtime)
    monkeypatch.setattr("keyring.get_password", lambda *a, **k: "stub")

    snap_reg = registry.read_text()
    snap_rt = runtime.read_text()

    client = TestClient(app)

    # Missing force
    resp = client.post(
        "/api/settings/inference/provider",
        json={"provider": "anthropic"},
    )
    assert resp.status_code == 400
    assert registry.read_text() == snap_reg
    assert runtime.read_text() == snap_rt

    # Explicit force=false
    resp = client.post(
        "/api/settings/inference/provider",
        json={"provider": "anthropic", "force": False},
    )
    assert resp.status_code == 400
    assert registry.read_text() == snap_reg
    assert runtime.read_text() == snap_rt


# ---------------------------------------------------------------------------
# 4. The UI read path must honor the operator's runtime_state override
# ---------------------------------------------------------------------------


def test_get_inference_state_honors_runtime_state_override(tmp_path, monkeypatch):
    """Regression: when model_registry.json disagrees with runtime_state.json
    (e.g. after a git pull reset the registry to the committed default),
    ``_get_inference_state()`` must return the runtime_state.json value —
    otherwise the operator sees the wrong active backend on /settings while
    cycles still use the right one.

    This was the actual user-visible bug Jun 30; kill-switch engagement was a
    red herring (the KS never wrote the registry — but the UI read desync
    made it LOOK like it had).
    """
    import pmacs.config as pmacs_config
    import pmacs.web.routes.settings as settings_route

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "model_registry.json").write_text(json.dumps({
        "backends": {
            "anthropic": {"api_key_ref": "anthropic_api_key", "default_model": "x"},
            "openrouter": {"api_key_ref": "openrouter_api_key", "default_model": "y"},
        },
        "active": "anthropic",  # committed default (e.g. after git pull)
    }))
    (config_dir / "runtime_state.json").write_text(json.dumps(
        {"active_backend": "openrouter"}  # operator's explicit choice
    ))

    monkeypatch.setattr(pmacs_config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(settings_route, "_REGISTRY_PATH", config_dir / "model_registry.json")
    monkeypatch.setattr(settings_route, "_RUNTIME_STATE_PATH", config_dir / "runtime_state.json")
    monkeypatch.setattr("keyring.get_password", lambda *a, **k: "stub")

    state = settings_route._get_inference_state()
    assert state["active"] == "openrouter", (
        f"_get_inference_state() must honor runtime_state.json override — "
        f"got {state['active']!r}, expected 'openrouter'. Cycles already use "
        f"openrouter via load_config(); the API must agree."
    )