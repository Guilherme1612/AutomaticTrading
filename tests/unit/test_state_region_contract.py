"""CI lint: the shared state-design contract (Source.md §13.4, web review Finding F).

§13.4 mandates ONE wrapper for every data region's non-ready states:
  loading (skeleton + WHAT + ETA + cancel) → ready → empty (page-specific) →
  error (code + what-this-means + what-to-try + copy-for-Claude + spec link).

The shared components live in ``pmacs/web/templates/components/``:
  - ``loading_state.html``  — loading (accepts loading_what / loading_eta_seconds / loading_cancel)
  - ``empty_state.html``     — empty (accepts empty_title / empty_message)
  - ``error_state.html``     — error (code + explanation + actions + copy-for-Claude + spec_ref)
  - ``state_region.html``    — dispatcher on ``region_state``

This lint forbids the ad-hoc patterns that motivated the finding:
  - bare ``Loading...`` / ``Loading…`` strings (no WHAT, no ETA, no skeleton)
  - generic ``No data available.`` (§13.4: never generic "No data")

It does NOT forbid ``animate-spin`` globally — that's a legitimate status-badge
class (base.html HTMX indicator, agents.html "cycle running" dot). The contract
is about bare LOADING STRINGS, not every spinner.
"""

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[2] / "pmacs" / "web" / "templates"

# The shared components themselves carry the canonical strings — exempt them.
EXEMPT = {
    "components/loading_state.html",
    "components/state_region.html",
    "components/empty_state.html",
    "components/error_state.html",
}

# wizard/ has legitimate "Downloading…" model/embedding progress, not data-region
# loaders — exempt the whole wizard subtree from the bare-Loading check.
WIZARD_PREFIX = "wizard/"

# Bare loading strings — the §13.4 violation. (Note: "Syncing…" in base.html is
# the global HTMX chrome indicator, not a data-region loader, and doesn't match.)
BARE_LOADING = re.compile(r"Loading\.\.\.|Loading…")
GENERIC_EMPTY = re.compile(r"No data available\.")


def _iter_templates():
    for p in sorted(TEMPLATES.rglob("*.html")):
        rel = p.relative_to(TEMPLATES).as_posix()
        yield p, rel


def test_no_bare_loading_strings_outside_shared_component():
    """§13.4: every loading surface must say WHAT is loading (+ ETA), not bare
    'Loading…'. Use {% include "components/loading_state.html" %} instead."""
    offenders = []
    for path, rel in _iter_templates():
        if rel in EXEMPT or rel.startswith(WIZARD_PREFIX):
            continue
        src = path.read_text(encoding="utf-8")
        for m in BARE_LOADING.finditer(src):
            # Find the line for a readable failure message.
            line = src[: m.start()].count("\n") + 1
            offenders.append(f"{rel}:{line}: bare '{m.group()}'")
    assert not offenders, (
        "Bare loading strings found outside components/loading_state.html — "
        "use the shared loading_state (Source.md §13.4):\n  " + "\n  ".join(offenders)
    )


def test_no_generic_no_data_outside_shared_component():
    """§13.4: empty states must be page-specific (empty_title/empty_message), never
    the generic 'No data available.'."""
    offenders = []
    for path, rel in _iter_templates():
        if rel in EXEMPT:
            continue
        src = path.read_text(encoding="utf-8")
        for m in GENERIC_EMPTY.finditer(src):
            line = src[: m.start()].count("\n") + 1
            offenders.append(f"{rel}:{line}: generic '{m.group()}'")
    assert not offenders, (
        "Generic 'No data available.' found — use components/empty_state.html "
        "with a page-specific empty_title/empty_message (Source.md §13.4):\n  "
        + "\n  ".join(offenders)
    )


def test_shared_state_components_exist():
    """The contract components must be present and includable (loader root is
    pmacs/web/templates/, so they live under components/)."""
    for name in ("loading_state.html", "empty_state.html", "error_state.html", "state_region.html"):
        assert (TEMPLATES / "components" / name).exists(), f"missing shared component {name}"


def test_loading_state_accepts_what_and_eta():
    """loading_state must render WHAT + ETA (§13.4) — guard against regressing it
    back to a bare spinner."""
    src = (TEMPLATES / "components" / "loading_state.html").read_text(encoding="utf-8")
    assert "loading_what" in src, "loading_state lost its loading_what prop"
    assert "loading_eta_seconds" in src, "loading_state lost its ETA prop"
    assert "Cancel" in src, "loading_state lost its cancel affordance (ETA > 30s)"


def test_error_state_has_copy_for_claude_and_spec_ref():
    """§13.4: every error surface includes Copy-for-Claude + spec link."""
    src = (TEMPLATES / "components" / "error_state.html").read_text(encoding="utf-8")
    assert "copyErrorForClaude" in src, "error_state missing Copy-for-Claude button"
    assert "error_spec_ref" in src, "error_state missing spec_ref link"


def test_state_region_dispatches_three_states():
    """state_region must dispatch loading/empty/error on region_state."""
    src = (TEMPLATES / "components" / "state_region.html").read_text(encoding="utf-8")
    assert 'region_state == "loading"' in src
    assert 'region_state == "empty"' in src
    assert 'region_state == "error"' in src


def test_no_stray_components_dir_duplicates_state_files():
    """The stray pmacs/web/components/ dir (outside the loader root) must not keep
    a stale loading_state.html that diverges from the canonical one."""
    stray = Path(__file__).resolve().parents[2] / "pmacs" / "web" / "components" / "loading_state.html"
    assert not stray.exists(), (
        "st stray loading_state.html in pmacs/web/components/ (outside the loader "
        "root) — the canonical copy is pmacs/web/templates/components/loading_state.html"
    )
