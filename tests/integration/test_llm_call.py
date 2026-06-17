"""Integration tests for the three-layer LLM validation pipeline (Agents.md §3).

Tests the PersonaRunner retry and validation logic using GrowthHunter as the
concrete persona. Mocks the llama-server HTTP endpoint to control LLM output.

Layers under test:
  Layer 1: HTTP call to llama-server with GBNF grammar
  Layer 2: Pydantic model_validate() parse
  Layer 3: Sanity validator (BaseSanityValidator subclass)

spec_ref: Agents.md §3
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from pmacs.agents.base import MAX_RETRIES, TEMP_BUMP, PersonaRunner
from pmacs.agents.growth_hunter import GrowthHunterRunner
from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult
from pmacs.schemas.agents import PersonaOutput
from pmacs.schemas.data import Evidence, EvidencePacket
from pmacs.schemas.personas import GrowthHunterOutput


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_evidence_packet(
    ticker: str = "AAPL",
    evidence_id: str = "ev-001",
) -> EvidencePacket:
    """Create a minimal evidence packet for testing."""
    ev = Evidence(
        id=evidence_id,
        source="polygon",
        type="market_data",
        ticker=ticker,
        fetched_at=datetime.now(timezone.utc),
        content_hash="abc123",
        data={"price": 150.0},
    )
    return EvidencePacket(
        ticker=ticker,
        cycle_id="test-cycle-001",
        evidence=[ev],
    )


def _valid_growth_hunter_json(ticker: str = "AAPL") -> dict[str, Any]:
    """Return a dict that passes GrowthHunterOutput.model_validate()."""
    return {
        "ticker": ticker,
        "revenue_yoy_pct": 22.5,
        "revenue_acceleration": "ACCELERATING",
        "gross_margin_pct": 55.0,
        "gross_margin_trend": "EXPANDING",
        "tam_penetration_pct": 3.2,
        "growth_durability": "HIGH",
        "growth_durability_reasoning": "Strong recurring revenue with low churn.",
        "key_risk_to_growth": "Market saturation in core segment.",
        "p_up": 0.5,
        "p_flat": 0.3,
        "p_down": 0.2,
        "evidence_ids": ["ev-001"],
    }


def _make_llm_response(content: str) -> httpx.Response:
    """Build a fake httpx.Response mimicking llama-server."""
    request = httpx.Request("POST", "http://127.0.0.1:8080/completion")
    return httpx.Response(
        status_code=200,
        request=request,
        json={"content": content},
    )


@pytest.fixture()
def evidence_packets() -> list[EvidencePacket]:
    return [_make_evidence_packet()]


@pytest.fixture()
def runner() -> GrowthHunterRunner:
    return GrowthHunterRunner(
        cycle_id="test-cycle-001",
        audit_writer=None,
        simulation_mode=False,
    )


# ---------------------------------------------------------------------------
# Test 1: Successful three-layer pass
# ---------------------------------------------------------------------------


class TestSuccessfulThreeLayerPass:
    """Valid LLM output -> valid Pydantic -> valid sanity -> PersonaOutput."""

    @patch("pmacs.agents.base.httpx.Client")
    def test_run_returns_persona_output_on_valid_response(
        self, mock_client_cls, runner, evidence_packets
    ):
        valid_json = json.dumps(_valid_growth_hunter_json())
        mock_client = MagicMock()
        mock_client.post.return_value = _make_llm_response(valid_json)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(runner, "_get_active_backend", return_value=(
            "llama_server", {"url": "http://127.0.0.1:8080", "structured_output": "gbnf"},
        )):
            result = runner.run(evidence_packets)

        assert result is not None
        assert isinstance(result, PersonaOutput)
        assert result.persona.value == "growth_hunter"
        assert result.ticker == "AAPL"
        assert result.retry_count == 0
        assert result.cycle_id == "test-cycle-001"

    @patch("pmacs.agents.base.httpx.Client")
    def test_run_parses_raw_output_stored_on_persona_output(
        self, mock_client_cls, runner, evidence_packets
    ):
        valid_dict = _valid_growth_hunter_json()
        valid_json = json.dumps(valid_dict)
        mock_client = MagicMock()
        mock_client.post.return_value = _make_llm_response(valid_json)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(runner, "_get_active_backend", return_value=(
            "llama_server", {"url": "http://127.0.0.1:8080", "structured_output": "gbnf"},
        )):
            result = runner.run(evidence_packets)

        assert result is not None
        parsed = json.loads(result.raw_output)
        assert parsed["ticker"] == "AAPL"
        assert parsed["growth_durability"] == "HIGH"


# ---------------------------------------------------------------------------
# Test 2: Layer 2 failure — invalid JSON from LLM -> Pydantic ValidationError -> retry
# ---------------------------------------------------------------------------


class TestLayer2PydanticFailure:
    """LLM returns garbage JSON -> Pydantic rejects -> retries -> eventual success."""

    @patch("pmacs.agents.base.httpx.Client")
    def test_invalid_json_retries_then_succeeds(
        self, mock_client_cls, runner, evidence_packets
    ):
        valid_json = json.dumps(_valid_growth_hunter_json())
        invalid_json = "this is not json at all"

        mock_client = MagicMock()
        mock_client.post.side_effect = [
            _make_llm_response(invalid_json),
            _make_llm_response(valid_json),
        ]
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(runner, "_get_active_backend", return_value=(
            "llama_server", {"url": "http://127.0.0.1:8080", "structured_output": "gbnf"},
        )):
            result = runner.run(evidence_packets)

        assert result is not None
        assert result.retry_count == 1
        assert mock_client.post.call_count == 2

    @patch("pmacs.agents.base.httpx.Client")
    def test_missing_required_field_retries(
        self, mock_client_cls, runner, evidence_packets
    ):
        """Pydantic rejects output missing a required field (e.g. ticker)."""
        incomplete = _valid_growth_hunter_json()
        del incomplete["ticker"]
        incomplete_json = json.dumps(incomplete)

        valid_json = json.dumps(_valid_growth_hunter_json())

        mock_client = MagicMock()
        mock_client.post.side_effect = [
            _make_llm_response(incomplete_json),
            _make_llm_response(valid_json),
        ]
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(runner, "_get_active_backend", return_value=(
            "llama_server", {"url": "http://127.0.0.1:8080", "structured_output": "gbnf"},
        )):
            result = runner.run(evidence_packets)

        assert result is not None
        assert result.retry_count == 1

    @patch("pmacs.agents.base.httpx.Client")
    def test_invalid_probability_sum_retries(
        self, mock_client_cls, runner, evidence_packets
    ):
        """Pydantic rejects probabilities that don't sum to 1.0."""
        bad_probs = _valid_growth_hunter_json()
        bad_probs["p_up"] = 0.9
        bad_probs["p_flat"] = 0.9
        bad_probs["p_down"] = 0.9

        valid_json = json.dumps(_valid_growth_hunter_json())

        mock_client = MagicMock()
        mock_client.post.side_effect = [
            _make_llm_response(json.dumps(bad_probs)),
            _make_llm_response(valid_json),
        ]
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(runner, "_get_active_backend", return_value=(
            "llama_server", {"url": "http://127.0.0.1:8080", "structured_output": "gbnf"},
        )):
            result = runner.run(evidence_packets)

        assert result is not None
        assert result.retry_count == 1


# ---------------------------------------------------------------------------
# Test 3: Layer 3 failure — valid Pydantic but sanity check fails -> retry
# ---------------------------------------------------------------------------


class TestLayer3SanityFailure:
    """Pydantic parses OK but sanity validator rejects -> retry."""

    @patch("pmacs.agents.base.httpx.Client")
    def test_empty_growth_durability_reasoning_fails_sanity_then_retries(
        self, mock_client_cls, runner, evidence_packets
    ):
        """GrowthHunterSanity rejects empty growth_durability_reasoning."""
        bad_output = _valid_growth_hunter_json()
        bad_output["growth_durability_reasoning"] = "   "

        valid_json = json.dumps(_valid_growth_hunter_json())

        mock_client = MagicMock()
        mock_client.post.side_effect = [
            _make_llm_response(json.dumps(bad_output)),
            _make_llm_response(valid_json),
        ]
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(runner, "_get_active_backend", return_value=(
            "llama_server", {"url": "http://127.0.0.1:8080", "structured_output": "gbnf"},
        )):
            result = runner.run(evidence_packets)

        assert result is not None
        assert result.retry_count == 1

    @patch("pmacs.agents.base.httpx.Client")
    def test_out_of_range_revenue_fails_sanity(
        self, mock_client_cls, runner, evidence_packets
    ):
        """GrowthHunterSanity rejects revenue_yoy_pct out of [-100, 2000]."""
        bad_output = _valid_growth_hunter_json()
        bad_output["revenue_yoy_pct"] = 9999.0

        valid_json = json.dumps(_valid_growth_hunter_json())

        mock_client = MagicMock()
        mock_client.post.side_effect = [
            _make_llm_response(json.dumps(bad_output)),
            _make_llm_response(valid_json),
        ]
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(runner, "_get_active_backend", return_value=(
            "llama_server", {"url": "http://127.0.0.1:8080", "structured_output": "gbnf"},
        )):
            result = runner.run(evidence_packets)

        assert result is not None
        assert result.retry_count == 1

    @patch("pmacs.agents.base.httpx.Client")
    def test_degenerate_distribution_fails_sanity(
        self, mock_client_cls, runner, evidence_packets
    ):
        """GrowthHunterSanity rejects p_up == p_flat == p_down."""
        bad_output = _valid_growth_hunter_json()
        bad_output["p_up"] = 1.0 / 3
        bad_output["p_flat"] = 1.0 / 3
        bad_output["p_down"] = 1.0 / 3

        valid_json = json.dumps(_valid_growth_hunter_json())

        mock_client = MagicMock()
        mock_client.post.side_effect = [
            _make_llm_response(json.dumps(bad_output)),
            _make_llm_response(valid_json),
        ]
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(runner, "_get_active_backend", return_value=(
            "llama_server", {"url": "http://127.0.0.1:8080", "structured_output": "gbnf"},
        )):
            result = runner.run(evidence_packets)

        assert result is not None
        assert result.retry_count == 1


# ---------------------------------------------------------------------------
# Test 4: All retries exhausted -> returns None (ABORTED_PERSONA)
# ---------------------------------------------------------------------------


class TestAllRetriesExhausted:
    """All 3 attempts fail -> run() returns None (ABORTED_PERSONA path)."""

    @patch("pmacs.agents.base.httpx.Client")
    def test_all_attempts_fail_returns_none(
        self, mock_client_cls, runner, evidence_packets
    ):
        invalid_json = "not json"

        mock_client = MagicMock()
        mock_client.post.return_value = _make_llm_response(invalid_json)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(runner, "_get_active_backend", return_value=(
            "llama_server", {"url": "http://127.0.0.1:8080", "structured_output": "gbnf"},
        )):
            result = runner.run(evidence_packets)

        assert result is None
        # MAX_RETRIES + 1 = 3 total attempts
        assert mock_client.post.call_count == MAX_RETRIES + 1

    @patch("pmacs.agents.base.httpx.Client")
    def test_connection_refused_retries_then_exhausts(
        self, mock_client_cls, runner, evidence_packets
    ):
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(runner, "_get_active_backend", return_value=(
            "llama_server", {"url": "http://127.0.0.1:8080", "structured_output": "gbnf"},
        )):
            result = runner.run(evidence_packets)

        assert result is None
        assert mock_client.post.call_count == MAX_RETRIES + 1

    @patch("pmacs.agents.base.httpx.Client")
    def test_timeout_retries_then_exhausts(
        self, mock_client_cls, runner, evidence_packets
    ):
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.TimeoutException("Read timeout")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(runner, "_get_active_backend", return_value=(
            "llama_server", {"url": "http://127.0.0.1:8080", "structured_output": "gbnf"},
        )):
            result = runner.run(evidence_packets)

        assert result is None
        assert mock_client.post.call_count == MAX_RETRIES + 1

    @patch("pmacs.agents.base.httpx.Client")
    def test_empty_llm_output_retries_then_exhausts(
        self, mock_client_cls, runner, evidence_packets
    ):
        mock_client = MagicMock()
        mock_client.post.return_value = _make_llm_response("")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(runner, "_get_active_backend", return_value=(
            "llama_server", {"url": "http://127.0.0.1:8080", "structured_output": "gbnf"},
        )):
            result = runner.run(evidence_packets)

        assert result is None
        assert mock_client.post.call_count == MAX_RETRIES + 1


# ---------------------------------------------------------------------------
# Test 5: Simulation mode — deterministic output without LLM call
# ---------------------------------------------------------------------------


class TestSimulationMode:
    """simulation_mode=True returns deterministic output via simulation module."""

    def test_simulation_mode_returns_persona_output(self, evidence_packets):
        sim_runner = GrowthHunterRunner(
            cycle_id="sim-cycle-001",
            audit_writer=None,
            simulation_mode=True,
        )

        with patch.object(sim_runner, "_get_active_backend", return_value=(
            "llama_server", {"url": "http://127.0.0.1:8080", "structured_output": "gbnf"},
        )), patch("pmacs.agents.base.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = sim_runner.run(evidence_packets)

        # simulation_mode=True triggers fallback after all retries exhausted
        assert result is not None
        assert isinstance(result, PersonaOutput)
        assert result.persona.value == "growth_hunter"
        assert result.ticker == "AAPL"
        assert result.model_hash == "simulation"

    def test_simulation_output_is_deterministic(self, evidence_packets):
        sim_runner = GrowthHunterRunner(
            cycle_id="sim-cycle-002",
            audit_writer=None,
            simulation_mode=True,
        )

        with patch.object(sim_runner, "_get_active_backend", return_value=(
            "llama_server", {"url": "http://127.0.0.1:8080", "structured_output": "gbnf"},
        )), patch("pmacs.agents.base.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.side_effect = httpx.ConnectError("no server")
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result1 = sim_runner.run(evidence_packets)

        sim_runner2 = GrowthHunterRunner(
            cycle_id="sim-cycle-002",
            audit_writer=None,
            simulation_mode=True,
        )
        with patch.object(sim_runner2, "_get_active_backend", return_value=(
            "llama_server", {"url": "http://127.0.0.1:8080", "structured_output": "gbnf"},
        )), patch("pmacs.agents.base.httpx.Client") as mock_client_cls2:
            mock_client2 = MagicMock()
            mock_client2.post.side_effect = httpx.ConnectError("no server")
            mock_client2.__enter__ = MagicMock(return_value=mock_client2)
            mock_client2.__exit__ = MagicMock(return_value=False)
            mock_client_cls2.return_value = mock_client2

            result2 = sim_runner2.run(evidence_packets)

        assert result1 is not None and result2 is not None
        assert result1.raw_output == result2.raw_output


# ---------------------------------------------------------------------------
# Test 6: Temperature bumps correctly on retries (0.2 -> 0.25 -> 0.30)
# ---------------------------------------------------------------------------


class TestTemperatureBumps:
    """Verify temperature increases by +0.05 on each retry."""

    @patch("pmacs.agents.base.httpx.Client")
    def test_temperature_increments_on_retries(
        self, mock_client_cls, runner, evidence_packets
    ):
        valid_json = json.dumps(_valid_growth_hunter_json())

        temperatures_seen: list[float] = []

        def capture_post(url, json=None, **kwargs):
            if json and "temperature" in json:
                temperatures_seen.append(json["temperature"])

            call_idx = len(temperatures_seen) - 1
            if call_idx < 2:
                return _make_llm_response("bad json")
            return _make_llm_response(valid_json)

        mock_client = MagicMock()
        mock_client.post.side_effect = capture_post
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(runner, "_get_active_backend", return_value=(
            "llama_server", {"url": "http://127.0.0.1:8080", "structured_output": "gbnf"},
        )):
            result = runner.run(evidence_packets)

        assert result is not None
        assert result.retry_count == 2
        assert temperatures_seen == [
            0.2,
            0.2 + TEMP_BUMP,
            0.2 + 2 * TEMP_BUMP,
        ]

    @patch("pmacs.agents.base.httpx.Client")
    def test_temperature_on_first_attempt_success(
        self, mock_client_cls, runner, evidence_packets
    ):
        """When the first attempt succeeds, temperature should be base (0.2)."""
        valid_json = json.dumps(_valid_growth_hunter_json())

        captured_temp: list[float] = []

        def capture_post(url, json=None, **kwargs):
            if json and "temperature" in json:
                captured_temp.append(json["temperature"])
            return _make_llm_response(valid_json)

        mock_client = MagicMock()
        mock_client.post.side_effect = capture_post
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(runner, "_get_active_backend", return_value=(
            "llama_server", {"url": "http://127.0.0.1:8080", "structured_output": "gbnf"},
        )):
            result = runner.run(evidence_packets)

        assert result is not None
        assert result.temperature == 0.2
        assert result.retry_count == 0
        assert captured_temp == [0.2]

    @patch("pmacs.agents.base.httpx.Client")
    def test_max_temperature_after_all_retries(
        self, mock_client_cls, runner, evidence_packets
    ):
        """After 3 failed attempts, temperature reached 0.2 + 2*0.05 = 0.30."""
        temperatures_seen: list[float] = []

        def capture_post(url, json=None, **kwargs):
            if json and "temperature" in json:
                temperatures_seen.append(json["temperature"])
            return _make_llm_response("not json")

        mock_client = MagicMock()
        mock_client.post.side_effect = capture_post
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(runner, "_get_active_backend", return_value=(
            "llama_server", {"url": "http://127.0.0.1:8080", "structured_output": "gbnf"},
        )):
            result = runner.run(evidence_packets)

        assert result is None
        assert len(temperatures_seen) == MAX_RETRIES + 1
        assert temperatures_seen[-1] == 0.2 + MAX_RETRIES * TEMP_BUMP


# ---------------------------------------------------------------------------
# Test 7: JSON extraction from wrapped output
# ---------------------------------------------------------------------------


class TestJsonExtraction:
    """Verify _extract_json handles markdown-wrapped and surrounding text."""

    def test_extract_json_from_markdown_code_block(self, runner):
        raw = '```json\n{"ticker": "AAPL", "p_up": 0.5}\n```'
        extracted = runner._extract_json(raw)
        parsed = json.loads(extracted)
        assert parsed["ticker"] == "AAPL"

    def test_extract_json_with_surrounding_text(self, runner):
        raw = 'Here is the analysis: {"ticker": "MSFT", "p_up": 0.6} End of output.'
        extracted = runner._extract_json(raw)
        parsed = json.loads(extracted)
        assert parsed["ticker"] == "MSFT"

    def test_extract_json_pure_json(self, runner):
        raw = '{"ticker": "TSLA", "p_up": 0.7}'
        extracted = runner._extract_json(raw)
        parsed = json.loads(extracted)
        assert parsed["ticker"] == "TSLA"

    def test_extract_json_no_json_returns_raw(self, runner):
        raw = "no json here"
        extracted = runner._extract_json(raw)
        assert extracted == "no json here"
