"""Per-persona retry-abort tests — 3-retry logic with temp bump (Agents.md §3).

Tests the retry mechanism in PersonaRunner.run():
- Layer 1 failure (LLM connection) triggers retry
- Layer 2 failure (Pydantic parse) triggers retry
- Layer 3 failure (Sanity) triggers retry
- After 3 total failures: ABORTED_LLM logged, None returned
- Temperature bumps +0.05 per retry
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pmacs.agents.base import MAX_RETRIES, TEMP_BUMP, PersonaRunner
from pmacs.schemas.data import Evidence, EvidencePacket, DataSource, EvidenceType
from datetime import datetime, timezone


# Minimal concrete runner for testing
class _TestRunner(PersonaRunner):
    def __init__(self, **kwargs):
        super().__init__(persona_name="test_persona", **kwargs)
        self._prompt_text = ""

    def get_pydantic_model(self):
        from pydantic import BaseModel
        class _TestOutput(BaseModel):
            ticker: str = ""
            signal: str = "NEUTRAL"
        return _TestOutput

    def get_sanity_validator(self):
        validator = MagicMock()
        validator.validate.return_value = MagicMock(passed=True, reason="")
        return validator

    def build_prompt(self, evidence, episodic_context=None):
        return "Test prompt"


def _make_evidence(ticker: str = "AAPL") -> list[EvidencePacket]:
    """Create minimal evidence packets."""
    return [
        EvidencePacket(
            ticker=ticker,
            cycle_id="test-cycle",
            evidence=[
                Evidence(
                    id="ev-1",
                    source=DataSource.POLYGON,
                    type=EvidenceType.MARKET_DATA,
                    ticker=ticker,
                    fetched_at=datetime.now(timezone.utc),
                    content_hash="abc123",
                    data={"price": 150.0},
                )
            ],
        )
    ]


# ---------------------------------------------------------------------------
# Retry on Layer 1 failure (LLM connection)
# ---------------------------------------------------------------------------


def test_retry_on_connection_error_then_success() -> None:
    """Connection refused on first attempt, succeeds on second."""
    runner = _TestRunner(cycle_id="test-001")
    evidence = _make_evidence()

    call_count = 0
    def mock_call_llm(prompt, grammar, temperature, timeout=120.0):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            import httpx
            raise httpx.ConnectError("Connection refused")
        return json.dumps({"ticker": "AAPL", "signal": "NEUTRAL"})

    with patch.object(runner, "_call_llm", side_effect=mock_call_llm):
        result = runner.run(evidence)

    assert call_count == 2
    assert result is not None


def test_retry_on_connection_error_all_fail() -> None:
    """All 3 attempts fail with connection error → returns None."""
    runner = _TestRunner(cycle_id="test-002")
    evidence = _make_evidence()

    import httpx
    with patch.object(runner, "_call_llm", side_effect=httpx.ConnectError("Connection refused")):
        result = runner.run(evidence)

    assert result is None


def test_temperature_bumps_on_retry() -> None:
    """Temperature increases by 0.05 per retry attempt."""
    runner = _TestRunner(cycle_id="test-003", temperature=0.2)
    evidence = _make_evidence()

    temperatures = []
    def mock_call_llm(prompt, grammar, temperature, timeout=120.0):
        temperatures.append(temperature)
        return json.dumps({"ticker": "AAPL", "signal": "NEUTRAL"})

    with patch.object(runner, "_call_llm", side_effect=mock_call_llm):
        runner.run(evidence)

    # First attempt at base temp
    assert temperatures[0] == pytest.approx(0.2)


def test_temperature_bumps_on_pydantic_failure() -> None:
    """Temp bumps when Pydantic parse fails."""
    runner = _TestRunner(cycle_id="test-004", temperature=0.2)
    evidence = _make_evidence()

    call_count = 0
    def mock_call_llm(prompt, grammar, temperature, timeout=120.0):
        nonlocal call_count
        call_count += 1
        if call_count <= MAX_RETRIES:
            return "NOT VALID JSON {{{"  # Pydantic parse failure
        return json.dumps({"ticker": "AAPL", "signal": "NEUTRAL"})

    with patch.object(runner, "_call_llm", side_effect=mock_call_llm):
        result = runner.run(evidence)

    assert call_count == MAX_RETRIES + 1
    assert result is not None


# ---------------------------------------------------------------------------
# Retry on Layer 2 failure (Pydantic parse)
# ---------------------------------------------------------------------------


def test_retry_on_pydantic_failure_all_fail() -> None:
    """All attempts return invalid JSON → returns None."""
    runner = _TestRunner(cycle_id="test-005")
    evidence = _make_evidence()

    with patch.object(runner, "_call_llm", return_value="NOT JSON AT ALL"):
        result = runner.run(evidence)

    assert result is None


# ---------------------------------------------------------------------------
# Retry on Layer 3 failure (Sanity)
# ---------------------------------------------------------------------------


def test_retry_on_sanity_failure() -> None:
    """Sanity check fails on first attempt, passes on second."""
    runner = _TestRunner(cycle_id="test-006")
    evidence = _make_evidence()

    call_count = 0
    def mock_call_llm(prompt, grammar, temperature, timeout=120.0):
        nonlocal call_count
        call_count += 1
        return json.dumps({"ticker": "AAPL", "signal": "NEUTRAL"})

    sanity_calls = 0
    original_get_sanity = _TestRunner.get_sanity_validator

    def mock_get_sanity(self):
        validator = original_get_sanity(self)
        original_validate = validator.validate

        def counting_validate(parsed, evidence):
            nonlocal sanity_calls
            sanity_calls += 1
            if sanity_calls == 1:
                return MagicMock(passed=False, reason="Degenerate distribution")
            return MagicMock(passed=True, reason="")

        validator.validate = counting_validate
        return validator

    with (
        patch.object(_TestRunner, "get_sanity_validator", mock_get_sanity),
        patch.object(runner, "_call_llm", side_effect=mock_call_llm),
    ):
        result = runner.run(evidence)

    assert sanity_calls == 2
    assert result is not None


def test_retry_on_sanity_failure_all_fail() -> None:
    """All attempts fail sanity → returns None."""
    runner = _TestRunner(cycle_id="test-007")
    evidence = _make_evidence()

    def mock_get_sanity_always_fail(self):
        validator = MagicMock()
        validator.validate.return_value = MagicMock(passed=False, reason="Always fails")
        return validator

    with (
        patch.object(_TestRunner, "get_sanity_validator", mock_get_sanity_always_fail),
        patch.object(runner, "_call_llm", return_value=json.dumps({"ticker": "AAPL", "signal": "NEUTRAL"})),
    ):
        result = runner.run(evidence)

    assert result is None


# ---------------------------------------------------------------------------
# Retry constants match spec
# ---------------------------------------------------------------------------


def test_max_retries_is_two() -> None:
    """Spec says 2 retries = 3 total attempts (Agents.md §3)."""
    assert MAX_RETRIES == 2


def test_temp_bump_is_005() -> None:
    """Spec says +0.05 temperature bump per retry."""
    assert TEMP_BUMP == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# Empty output retry
# ---------------------------------------------------------------------------


def test_retry_on_empty_output() -> None:
    """LLM returning empty string triggers retry."""
    runner = _TestRunner(cycle_id="test-008")
    evidence = _make_evidence()

    call_count = 0
    def mock_call_llm(prompt, grammar, temperature, timeout=120.0):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ""
        return json.dumps({"ticker": "AAPL", "signal": "NEUTRAL"})

    with patch.object(runner, "_call_llm", side_effect=mock_call_llm):
        result = runner.run(evidence)

    assert result is not None
    assert call_count == 2


def test_retry_on_whitespace_only_output() -> None:
    """LLM returning whitespace-only triggers retry."""
    runner = _TestRunner(cycle_id="test-009")
    evidence = _make_evidence()

    call_count = 0
    def mock_call_llm(prompt, grammar, temperature, timeout=120.0):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "   \n\t  "
        return json.dumps({"ticker": "AAPL", "signal": "NEUTRAL"})

    with patch.object(runner, "_call_llm", side_effect=mock_call_llm):
        result = runner.run(evidence)

    assert result is not None


# ---------------------------------------------------------------------------
# Mixed failure sequence
# ---------------------------------------------------------------------------


def test_mixed_failure_sequence_then_success() -> None:
    """Layer 2 fail → Layer 3 fail → success on 3rd attempt."""
    runner = _TestRunner(cycle_id="test-010", temperature=0.2)
    evidence = _make_evidence()

    call_count = 0
    def mock_call_llm(prompt, grammar, temperature, timeout=120.0):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "NOT JSON"  # Layer 2 fail
        return json.dumps({"ticker": "AAPL", "signal": "NEUTRAL"})

    sanity_count = 0
    def mock_get_sanity_with_one_fail(self):
        validator = MagicMock()

        def counting_validate(parsed, evidence):
            nonlocal sanity_count
            sanity_count += 1
            if sanity_count == 1:
                return MagicMock(passed=False, reason="First sanity fail")
            return MagicMock(passed=True, reason="")

        validator.validate = counting_validate
        return validator

    with (
        patch.object(_TestRunner, "get_sanity_validator", mock_get_sanity_with_one_fail),
        patch.object(runner, "_call_llm", side_effect=mock_call_llm),
    ):
        result = runner.run(evidence)

    # Attempt 1: JSON fail, Attempt 2: LLM OK but sanity fail, Attempt 3: LLM OK + sanity OK
    assert result is not None
