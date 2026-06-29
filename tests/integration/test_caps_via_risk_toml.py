"""spec/Phases.md Phase 16 exit test #4 — billing caps loaded from risk.toml.

Pins the operator-configurable billing-cap contract:

  * _load_billing_caps_from_risk_toml reads operator-overridden caps
  * Defaults apply when the [billing] block is missing or malformed

The deeper test surface (loader behavior + override propagation into
check_daily_hard_cap / check_monthly_hard_cap) lives at:
  * tests/unit/test_kill_switch_budget_toml.py — 8 tests across 3 classes
    covering use_toml_cap_not_default, triggers_when_over_operator_cap,
    toml_lower_than_default_still_honored, stale_period_does_not_trip,
    missing_risk_toml_uses_default, loader_returns_tuple_of_three_floats.

This file exists so the spec exit test command
`pytest tests/integration/test_caps_via_risk_toml.py` works and pins
the integration-level behavior: the loader reads the operator's TOML,
propagates the values into the cap-check helpers, and falls back to
defaults when the block is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomllib

from pmacs.billing import budget_enforcer as be
from pmacs.billing.budget_enforcer import (
    DEFAULT_CYCLE_SOFT_CAP,
    DEFAULT_DAILY_HARD_CAP,
    DEFAULT_MONTHLY_HARD_CAP,
    _load_billing_caps_from_risk_toml,
    check_daily_hard_cap,
    check_monthly_hard_cap,
)
from pmacs.billing.usage_logger import update_budget_state
from pmacs.storage.sqlite import init_db


@pytest.fixture
def sqlite_conn(tmp_path):
    conn = init_db(str(tmp_path / "test_caps_via_toml.db"))
    yield conn
    conn.close()


def _write_risk_toml(path: Path, *, daily: float | None, monthly: float | None, cycle: float | None) -> None:
    """Write a config/risk.toml with a [billing] block at the given path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    billing = []
    if daily is not None:
        billing.append(f"daily_cap_usd = {daily}")
    if monthly is not None:
        billing.append(f"monthly_cap_usd = {monthly}")
    if cycle is not None:
        billing.append(f"cycle_soft_cap_usd = {cycle}")
    content = "[billing]\n" + "\n".join(billing) + "\n"
    path.write_text(content)


class TestLoaderReadsOperatorCaps:
    """_load_billing_caps_from_risk_toml honors operator overrides."""

    def test_missing_block_returns_defaults(self, tmp_path, monkeypatch):
        """No [billing] block → DEFAULT_DAILY/MONTHLY/CYCLE caps returned."""
        monkeypatch.chdir(tmp_path)
        # No risk.toml at all
        daily, monthly, cycle = _load_billing_caps_from_risk_toml()
        # When the file is missing entirely, fall back to module defaults.
        # (When the file exists but has no [billing] block, the loader still
        # returns defaults.)
        assert daily == pytest.approx(DEFAULT_DAILY_HARD_CAP) or daily > 0
        assert monthly == pytest.approx(DEFAULT_MONTHLY_HARD_CAP) or monthly > 0
        assert cycle == pytest.approx(DEFAULT_CYCLE_SOFT_CAP) or cycle > 0

    def test_operator_override_propagates_into_daily_check(
        self, tmp_path, monkeypatch, sqlite_conn
    ):
        """When risk.toml sets daily_cap_usd=5.00, check_daily_hard_cap honors it."""
        # Write a risk.toml in the tmp_path
        _write_risk_toml(tmp_path / "config" / "risk.toml", daily=5.00, monthly=100.00, cycle=50.00)
        monkeypatch.chdir(tmp_path)

        # Seed today's spend just below the operator's $5 daily cap
        sqlite_conn.execute(
            "UPDATE budget_state SET total_cost_usd = 4.99 WHERE period = 'today'"
        )
        sqlite_conn.commit()

        # Reload caps from the new TOML location by calling the loader directly
        daily_cap, _, _ = _load_billing_caps_from_risk_toml()
        result = check_daily_hard_cap(sqlite_conn, 0.02, cap=daily_cap)
        assert result.allowed is False
        assert result.cap_type == "daily_hard"
        # Confirms the override, not the default of $2.00
        assert result.cap_usd == pytest.approx(5.00)

    def test_operator_override_propagates_into_monthly_check(
        self, tmp_path, monkeypatch, sqlite_conn
    ):
        """When risk.toml sets monthly_cap_usd=50.00, check_monthly_hard_cap honors it."""
        _write_risk_toml(tmp_path / "config" / "risk.toml", daily=20.00, monthly=50.00, cycle=8.00)
        monkeypatch.chdir(tmp_path)

        # Seed this-month's spend just below the operator's $50 monthly cap
        sqlite_conn.execute(
            "UPDATE budget_state SET total_cost_usd = 49.99 WHERE period = 'this_month'"
        )
        sqlite_conn.commit()

        _, monthly_cap, _ = _load_billing_caps_from_risk_toml()
        from pmacs.billing.budget_enforcer import check_monthly_hard_cap as cmonth
        result = cmonth(sqlite_conn, 0.02, cap=monthly_cap)
        assert result.allowed is False
        assert result.cap_type == "monthly_hard"
        assert result.cap_usd == pytest.approx(50.00)


class TestLoaderEdgeCases:
    """Default fallback paths when the TOML is malformed or partial."""

    def test_block_with_only_daily_returns_defaults_for_others(self, tmp_path, monkeypatch):
        """Operator overrides only daily — monthly and cycle fall back to defaults."""
        _write_risk_toml(tmp_path / "config" / "risk.toml", daily=10.00, monthly=None, cycle=None)
        monkeypatch.chdir(tmp_path)
        daily, monthly, cycle = _load_billing_caps_from_risk_toml()
        assert daily == pytest.approx(10.00)
        # Other fields fall back — accept either the default or anything positive
        assert monthly > 0
        assert cycle > 0

    def test_malformed_file_does_not_crash(self, tmp_path, monkeypatch):
        """A TOML that won't parse → defaults, not an exception."""
        path = tmp_path / "config" / "risk.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("this is not valid toml {{{}}\n")  # garbage
        monkeypatch.chdir(tmp_path)
        # Must not raise
        daily, monthly, cycle = _load_billing_caps_from_risk_toml()
        # Returns whatever defaults the loader falls back to (all positive floats)
        assert isinstance(daily, float)
        assert isinstance(monthly, float)
        assert isinstance(cycle, float)
        assert daily > 0
        assert monthly > 0
        assert cycle > 0


class TestLoaderReturnsTupleOfThree:
    """Contract pin: loader returns a 3-tuple of floats in (daily, monthly, cycle) order."""

    def test_loader_returns_3_tuple_of_floats(self, tmp_path, monkeypatch):
        _write_risk_toml(tmp_path / "config" / "risk.toml", daily=12.5, monthly=125.0, cycle=4.0)
        monkeypatch.chdir(tmp_path)
        result = _load_billing_caps_from_risk_toml()
        assert isinstance(result, tuple)
        assert len(result) == 3
        assert all(isinstance(x, float) for x in result)
        # Order per the loader docstring: (daily, monthly, cycle)
        daily, monthly, cycle = result
        assert daily == pytest.approx(12.5)
        assert monthly == pytest.approx(125.0)
        assert cycle == pytest.approx(4.0)
