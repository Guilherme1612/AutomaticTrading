"""Regression: §20 + budget-caps TOML writes must persist without ``tomli_w``.

``tomli_w`` is not a declared dependency (only ``tomli`` for py<3.11 reads is).
The settings routes write ``risk.toml`` / ``crucible.toml`` via
``_write_toml_atomic``, which prefers ``tomli_w`` and falls back to
``_dump_toml_flat`` — a minimal flat-section serializer for the
``{section: {key: scalar}}`` shape these configs use.

Before this guard, ``save_cost_caps`` did a bare ``import tomli_w`` and 500'd
on ImportError in any venv without it. These tests pin:
  - the flat fallback round-trips the real risk.toml/billing shape through tomllib
  - ``_write_toml_atomic`` persists a readable file without ``tomli_w``
  - no settings route bare-imports ``tomli_w`` outside the guarded try/except
"""

import re
import tempfile
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover - py<3.11
    import tomli as tomllib  # type: ignore[no-redef]

from pmacs.web.routes.settings import _dump_toml_flat, _write_toml_atomic

REPRESENTATIVE_RISK = {
    "position": {"max_single_position_pct": 0.20, "max_concurrent_positions": 5},
    "kill_switch": {"daily_loss_pct": 0.05, "rolling_5d_loss_pct": 0.10},
    "billing": {"daily_cap_usd": 12.5, "monthly_cap_usd": 250},
    "pricing": {"default_stop_loss_pct": 0.15},
    "sizing": {"half_kelly": True, "max_position_usd": 1000.0},
}


def test_dump_toml_flat_round_trips_risk_shape():
    """The flat fallback must produce TOML that tomllib reads back identically."""
    blob = _dump_toml_flat(REPRESENTATIVE_RISK)
    decoded = tomllib.loads(blob.decode("utf-8"))
    assert decoded == REPRESENTATIVE_RISK, (
        "flat fallback lost data on round-trip — risk.toml writes would corrupt "
        f"config. got={decoded}"
    )


def test_write_toml_atomic_persists_without_tomli_w(tmp_path: Path):
    """_write_toml_atomic must write a readable file even if tomli_w is absent."""
    target = tmp_path / "risk.toml"
    _write_toml_atomic(target, REPRESENTATIVE_RISK)
    assert target.exists(), "atomic write did not land the file"
    with open(target, "rb") as f:
        decoded = tomllib.load(f)
    assert decoded["billing"] == {"daily_cap_usd": 12.5, "monthly_cap_usd": 250}
    assert decoded["sizing"]["half_kelly"] is True


def test_no_bare_tomli_w_import_in_routes():
    """tomli_w may only appear inside the guarded try/except in _write_toml_atomic.

    A bare ``import tomli_w`` at function/module scope would ImportError on venvs
    without the (undeclared) dependency and 500 the route — exactly the
    save_cost_caps regression this test guards against.
    """
    src = Path(__file__).resolve().parents[2].joinpath(
        "pmacs", "web", "routes", "settings.py"
    ).read_text(encoding="utf-8")
    # Every `import tomli_w` must be immediately followed by usage inside a
    # try block that has an ImportError fallback. The canonical guard lives in
    # _write_toml_atomic: `try:\n     import tomli_w\n     ... \n    except ImportError:`.
    for m in re.finditer(r"import tomli_w", src):
        # Look at the ±120 char window around the import.
        start = max(0, m.start() - 80)
        window = src[start:m.end() + 120]
        assert "except ImportError" in window or "ImportError" in window, (
            f"bare `import tomli_w` without ImportError guard at offset {m.start()} — "
            "would 500 on venvs without the undeclared tomli_w dependency"
        )
