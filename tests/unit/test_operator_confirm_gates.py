"""CI lint: every §6 operator-confirmed *destructive* POST must route through the
typed-confirm gate (Source.md §13.2, §6 decision-rights matrix).

The operator-confirmation friction layer is `confirmAction({confirmSymbol: ...})`
in `pmacs/web/static/app.js` — it shows a modal requiring the operator to type a
symbol before the POST fires. A destructive route invoked via `confirmAction`
*without* `confirmSymbol` silently bypasses the gate (it POSTs directly). This
test makes that a failing check, enforced in CI like the Architecture.md §16
anti-patterns.

Gated routes (irreversible / Non-Negotiable):
  - /api/pipeline/force-exit          (§15/§16.4, Non-Negotiable #5)
  - /api/mutation/promote             (§6 operator-confirmed)
  - /api/mutation/reject              (§6 operator-confirmed)
  - /api/mutation/rollback            (§6 operator-confirmed)
  - /api/cortex/kill-switch/disengage  (Non-Negotiable #5: only operator lifts it)
  - /api/settings/risk                (§20.6 operator-confirmed — writes risk.toml)
  - /api/settings/crucible            (§20.7 operator-confirmed — writes crucible.toml)
  - /api/settings/brokers             (§20.3 operator-confirmed — catastrophe-net stop)
  - /api/settings/operator            (§20.12 operator-confirmed — per-trade approval)

Reversible settings tweaks (e.g. budget caps) are intentionally NOT gated.
"""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "pmacs" / "web"

# Each entry: (route, [files to search for its confirmAction invocation]).
GATED_ROUTES = {
    "/api/pipeline/force-exit": ["static/app.js"],
    "/api/mutation/promote": ["templates/settings.html"],
    "/api/mutation/reject": ["templates/settings.html"],
    "/api/mutation/rollback": ["templates/settings.html"],
    "/api/cortex/kill-switch/disengage": ["templates/cortex.html"],
    "/api/settings/risk": ["templates/settings.html"],
    "/api/settings/crucible": ["templates/settings.html"],
    "/api/settings/brokers": ["templates/settings.html"],
    "/api/settings/operator": ["templates/settings.html"],
}

_BLOCK = re.compile(r"confirmAction\(\{(.*?)\n\s*\}\);", re.S)


def _blocks(path: Path):
    src = path.read_text()
    return [(m.group(1), m.start()) for m in _BLOCK.finditer(src)]


def _assert_route_gated(route, files):
    """The confirmAction block whose callbackUrl == route must contain confirmSymbol."""
    found_block = False
    for rel in files:
        blocks = _blocks(WEB / rel)
        for body, _ in blocks:
            cb = re.search(r"callbackUrl:\s*['\"]([^'\"]+)['\"]", body)
            if not cb or cb.group(1) != route:
                continue
            found_block = True
            assert "confirmSymbol" in body, (
                f"confirmAction for {route} in {rel} lacks confirmSymbol — "
                f"the typed-confirm gate (§13.2) is bypassed; it POSTs directly."
            )
    assert found_block, (
        f"No confirmAction({{callbackUrl: '{route}'}}) block found in {files}. "
        f"Every §6 destructive POST must route through confirmAction."
    )


def test_force_exit_is_typed_confirm_gated():
    _assert_route_gated("/api/pipeline/force-exit", GATED_ROUTES["/api/pipeline/force-exit"])


def test_mutation_promote_is_typed_confirm_gated():
    _assert_route_gated("/api/mutation/promote", GATED_ROUTES["/api/mutation/promote"])


def test_mutation_reject_is_typed_confirm_gated():
    _assert_route_gated("/api/mutation/reject", GATED_ROUTES["/api/mutation/reject"])


def test_mutation_rollback_is_typed_confirm_gated():
    _assert_route_gated("/api/mutation/rollback", GATED_ROUTES["/api/mutation/rollback"])


def test_kill_switch_disengage_is_typed_confirm_gated():
    _assert_route_gated(
        "/api/cortex/kill-switch/disengage",
        GATED_ROUTES["/api/cortex/kill-switch/disengage"],
    )


def test_settings_risk_is_typed_confirm_gated():
    _assert_route_gated("/api/settings/risk", GATED_ROUTES["/api/settings/risk"])


def test_settings_crucible_is_typed_confirm_gated():
    _assert_route_gated("/api/settings/crucible", GATED_ROUTES["/api/settings/crucible"])


def test_settings_brokers_is_typed_confirm_gated():
    _assert_route_gated("/api/settings/brokers", GATED_ROUTES["/api/settings/brokers"])


def test_settings_operator_is_typed_confirm_gated():
    _assert_route_gated("/api/settings/operator", GATED_ROUTES["/api/settings/operator"])


def test_force_exit_button_present_on_pipeline_cards():
    """Source.md §16.4: active-position pipeline cards expose a Force-exit button."""
    src = (WEB / "templates/pipeline.html").read_text()
    assert "forceExit(" in src, "pipeline.html has no forceExit() button"
    assert "is_active" in src, "pipeline cards don't gate force-exit on is_active"


def test_force_exit_button_present_on_dashboard_positions():
    """Source.md §16.4: dashboard active-positions table exposes a Force-exit button."""
    src = (WEB / "templates/dashboard/_positions.html").read_text()
    assert "forceExit(" in src, "dashboard positions partial has no forceExit() button"


def test_typed_confirm_modal_exists_in_base():
    """Source.md §13.2: the typed-confirm modal primitive is present in base.html."""
    src = (WEB / "templates/base.html").read_text()
    assert 'id="confirm-modal"' in src, "base.html missing #confirm-modal primitive"
    assert 'id="confirm-modal-input"' in src
    assert 'id="confirm-modal-confirm"' in src
