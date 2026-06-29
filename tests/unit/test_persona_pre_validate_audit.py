"""Unit tests for _log_normalization audit-event firing.

The PersonaRunner._log_normalization() method fires a
``PERSONA_OUTPUT_NORMALIZED`` cycle-scoped audit event with the fix list
whenever at least one drift fix is applied. It does NOT fire when:

- The fix list is empty (clean LLM output → no noise).
- ``self.cycle_id`` is unset (system-level normalization, no cycle scope).

We mock the ``log_debug`` call to assert the event payload shape and the
no-fire conditions.
"""

from __future__ import annotations

from typing import Literal
from unittest.mock import patch

import pytest
from pydantic import BaseModel, ConfigDict, Field

from pmacs.agents.base import PersonaRunner


class _Simple(BaseModel):
    model_config = ConfigDict(frozen=False)
    label: Literal["A", "B"]
    evidence_ids: list[str] = Field(min_length=1)


def _runner(cycle_id: str = "test-cycle-001") -> PersonaRunner:
    """Build a minimal PersonaRunner subclass instance with a cycle_id."""

    class _Stub(PersonaRunner):
        def __init__(self):
            super().__init__(persona_name="stub", cycle_id=cycle_id)
            self._ticker = "STUB"

        def get_pydantic_model(self):
            return _Simple

        def get_sanity_validator(self):
            return None

        def build_prompt(self, evidence, episodic_context=None):
            return ""

    return _Stub()


class TestLogNormalizationFiring:
    """_log_normalization fires only when fixes is non-empty AND cycle_id is set."""

    def test_no_fire_when_fixes_empty(self):
        """Clean LLM output → no audit event (no noise)."""
        runner = _runner()
        with patch("pmacs.agents.base.log_debug") as mock_log:
            runner._log_normalization([])
            mock_log.assert_not_called()

    def test_no_fire_when_cycle_id_missing(self):
        """Without cycle_id, normalization is treated as system-level."""
        runner = _runner(cycle_id="")
        fixes = [{"field": "x", "type": "renamed"}]
        with patch("pmacs.agents.base.log_debug") as mock_log:
            runner._log_normalization(fixes)
            mock_log.assert_not_called()

    def test_fires_with_fix_payload(self):
        """When cycle_id is set + fixes non-empty, the audit event fires."""
        runner = _runner()
        fixes = [
            {"field": "label", "type": "enum_normalized", "before": "a", "after": "A"},
            {"field": "evidence_ids", "type": "evidence_injected", "before_len": 0, "after_len": 1},
        ]
        with patch("pmacs.agents.base.log_debug") as mock_log:
            runner._log_normalization(fixes, ticker="MSFT")
            mock_log.assert_called_once()
            call = mock_log.call_args
            # event_type positional
            assert call.args[0] == "PERSONA_OUTPUT_NORMALIZED"
            # payload keyword
            payload = call.kwargs.get("payload") or call.kwargs
            assert payload["persona"] == "stub"
            assert payload["ticker"] == "MSFT"
            assert payload["fix_count"] == 2
            assert payload["fixes"] == fixes
            # cycle-scoped, so cycle_id is required
            assert call.kwargs.get("cycle_id") == "test-cycle-001"
            # INFO level (no error_code required for INFO)
            assert call.kwargs.get("level") == "INFO"


class TestLogNormalizationEndToEnd:
    """After _pre_validate, fixes from helpers flow into the audit event."""

    def test_realistic_macro_drift_fires_audit(self):
        """A realistic deepseek-v4-flash drift on macro_regime yields fixes."""
        runner = _runner()
        # Simulate _normalize_literal_enums output for "flat" → "FLAT"
        parsed_in = {
            "label": "flat",  # lowercase drift
            "evidence_ids": [],  # empty drift
        }
        all_fixes: list[dict] = []
        parsed_out, fixes = PersonaRunner._normalize_literal_enums(parsed_in, _Simple)
        all_fixes.extend(fixes)
        parsed_out, fixes = PersonaRunner._ensure_min_evidence_ids(parsed_out, _Simple)
        all_fixes.extend(fixes)

        # All fixes should be recorded
        assert len(all_fixes) == 2  # 1 enum + 1 evidence_injected
        with patch("pmacs.agents.base.log_debug") as mock_log:
            runner._log_normalization(all_fixes, ticker="MSFT")
            mock_log.assert_called_once()
            payload = mock_log.call_args.kwargs["payload"]
            assert payload["fix_count"] == 2
            assert any(f["type"] == "enum_normalized" for f in payload["fixes"])
            # evidence_ids was empty list (not missing) → "evidence_padded"
            assert any(f["type"] == "evidence_padded" for f in payload["fixes"])
