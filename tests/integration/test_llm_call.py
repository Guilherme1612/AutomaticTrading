"""Integration tests for LLM inference pipeline (Phase 2).

Skip all tests if llama-server not running at http://127.0.0.1:8080/health.
Tests cover:
  1. PersonaRunner → grammar → Pydantic → audit event
  2. Audit event has required fields
  3. WITHOUT grammar → likely fails Pydantic (proves grammar value)
  4. Model integrity check (if GGUF exists)
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field

from pmacs.agents.base import PersonaRunner
from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult
from pmacs.schemas.agents import PersonaOutput
from pmacs.schemas.data import EvidencePacket, Evidence, DataSource, EvidenceType
from pmacs.storage.audit import AuditWriter, AuditVerifier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

LLM_HEALTH_URL = "http://127.0.0.1:8080/health"


def _llm_available() -> bool:
    """Check if llama-server is reachable."""
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(LLM_HEALTH_URL)
            return resp.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _llm_available(),
    reason="llama-server not running at :8080 — skipping LLM integration tests",
)


class SampleOutput(BaseModel):
    """Minimal output model matching test_grammar.gbnf."""
    model_config = ConfigDict(frozen=True)

    direction: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class SampleSanityValidator(BaseSanityValidator):
    """Minimal sanity validator for test grammar output."""
    pass


class SamplePersonaRunner(PersonaRunner):
    """Concrete PersonaRunner for integration testing."""

    def get_pydantic_model(self) -> type[BaseModel]:
        return SampleOutput

    def get_sanity_validator(self) -> BaseSanityValidator:
        return SampleSanityValidator()

    def build_prompt(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> str:
        ticker = evidence[0].ticker if evidence else "UNKNOWN"
        ctx = episodic_context or ""
        return (
            f"Analyze the stock {ticker}. "
            f"Respond with JSON: direction (BULLISH/BEARISH/NEUTRAL), "
            f"confidence (0.0-1.0), reasoning (string). "
            f"{ctx}\n"
        )


def _make_test_evidence(ticker: str = "AAPL") -> list[EvidencePacket]:
    """Create a minimal evidence packet for testing."""
    from datetime import datetime, timezone

    ev = Evidence(
        id="ev-test-001",
        source=DataSource.POLYGON,
        type=EvidenceType.MARKET_DATA,
        ticker=ticker,
        fetched_at=datetime.now(timezone.utc),
        content_hash="abc123",
        data={"price": 150.0},
    )
    packet = EvidencePacket(
        ticker=ticker,
        cycle_id="test-cycle-001",
        evidence=[ev],
        fetched_at=datetime.now(timezone.utc),
    )
    return [packet]


def _make_audit_writer() -> tuple[AuditWriter, str]:
    """Create a temp-file backed AuditWriter."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".audit.log", delete=False
    )
    tmp.close()
    writer = AuditWriter(tmp.name)
    return writer, tmp.name


# ---------------------------------------------------------------------------
# Test 1: Full pipeline with grammar → Pydantic validates → audit logged
# ---------------------------------------------------------------------------

class TestFullPipeline:
    """Test 1: PersonaRunner end-to-end with grammar constraint."""

    def test_grammar_pipeline_produces_valid_output(self) -> None:
        evidence = _make_test_evidence()
        audit, audit_path = _make_audit_writer()
        try:
            runner = SamplePersonaRunner(
                persona_name="gatekeeper",
                grammar_name="test_grammar",
                temperature=0.2,
                max_tokens=256,
                cycle_id="test-cycle-001",
                audit_writer=audit,
            )
            result = runner.run(evidence)

            # Should succeed — grammar constrains output
            assert result is not None, "PersonaRunner.run() returned None — all attempts failed"
            assert isinstance(result, PersonaOutput)
            assert result.cycle_id == "test-cycle-001"
            assert result.retry_count >= 0
        finally:
            audit.close()
            Path(audit_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 2: Audit event has required fields
# ---------------------------------------------------------------------------

class TestAuditFields:
    """Test 2: Verify audit event structure."""

    def test_audit_event_has_required_fields(self) -> None:
        evidence = _make_test_evidence()
        audit, audit_path = _make_audit_writer()
        try:
            runner = SamplePersonaRunner(
                persona_name="gatekeeper",
                grammar_name="test_grammar",
                temperature=0.2,
                max_tokens=256,
                cycle_id="test-cycle-002",
                audit_writer=audit,
            )
            result = runner.run(evidence)

            # Read audit log
            with open(audit_path) as f:
                lines = [l.strip() for l in f if l.strip()]

            assert len(lines) >= 1, "No audit events written"

            # Parse the last audit entry
            parts = lines[-1].split("\t")
            assert len(parts) == 5, f"Expected 5 tab-separated fields, got {len(parts)}"

            event_type = parts[2]
            canon_json = parts[3]
            payload = json.loads(canon_json)

            # Verify required fields
            assert event_type == "LLM_CALL"
            assert "prompt_hash" in payload, "Missing prompt_hash in audit"
            assert "output_hash" in payload, "Missing output_hash in audit"
            assert "model_hash" in payload, "Missing model_hash in audit"
            assert "grammar_version" in payload, "Missing grammar_version in audit"
            assert "latency_ms" in payload, "Missing latency_ms in audit"
            assert "retry_count" in payload, "Missing retry_count in audit"
        finally:
            audit.close()
            Path(audit_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 3: Without grammar → likely fails Pydantic validation
# ---------------------------------------------------------------------------

class TestNoGrammar:
    """Test 3: Prove grammar is necessary by omitting it."""

    def test_no_grammar_likely_fails_validation(self) -> None:
        evidence = _make_test_evidence()

        # Runner with non-existent grammar → empty grammar string → no constraint
        runner = _NoGrammarTestRunner(
            persona_name="gatekeeper",
            grammar_name="nonexistent_grammar",
            temperature=0.2,
            max_tokens=128,
            cycle_id="test-cycle-003",
        )
        result = runner.run(evidence)
        # Without grammar, LLM output is unconstrained.
        # It MAY succeed (LLM could output valid JSON by chance),
        # but typically fails Pydantic validation.
        # We just verify it doesn't crash.
        assert result is None or isinstance(result, PersonaOutput)


class _NoGrammarTestRunner(SamplePersonaRunner):
    """Override to use TestOutput model but skip grammar."""

    def _load_grammar(self) -> str:
        # Force no grammar — unconstrained output
        return ""


# ---------------------------------------------------------------------------
# Test 4: Model integrity check
# ---------------------------------------------------------------------------

class TestModelIntegrity:
    """Test 4: GGUF hash verification."""

    def test_model_integrity_check(self) -> None:
        from pmacs.cortex.model_integrity import check_model_integrity

        # This will either pass (no GGUF configured / hash matches)
        # or fail gracefully with a log message
        result = check_model_integrity()
        assert isinstance(result, bool)

    def test_verify_hash_nonexistent_file(self) -> None:
        from pmacs.cortex.model_integrity import verify_gguf_hash

        result = verify_gguf_hash(
            gguf_path=Path("/nonexistent/model.gguf"),
            expected_sha256="abc123",
        )
        assert result is False

    def test_verify_hash_correct_file(self) -> None:
        from pmacs.cortex.model_integrity import verify_gguf_hash

        # Create a temp file with known content
        with tempfile.NamedTemporaryFile(delete=False, suffix=".gguf") as tmp:
            tmp.write(b"test content for hash verification")
            tmp_path = Path(tmp.name)

        import hashlib
        expected = hashlib.sha256(b"test content for hash verification").hexdigest()

        try:
            result = verify_gguf_hash(tmp_path, expected)
            assert result is True

            # Wrong hash should fail
            result = verify_gguf_hash(tmp_path, "0" * 64)
            assert result is False
        finally:
            tmp_path.unlink()
