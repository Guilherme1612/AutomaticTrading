"""PersonaRunner base class — three-layer LLM validation pipeline (Agents.md §3).

Layer 1: llama-server HTTP call with GBNF grammar constraint
Layer 2: Pydantic model_validate() parse
Layer 3: Sanity validator

Retry: up to 2 retries with +0.05 temperature bump per retry (3 total attempts).
On all failures: log ABORTED_LLM debug event, return None.
"""

from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from pmacs.agents.grammars import load_grammar
from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult
from pmacs.logsys import log_debug
from pmacs.schemas.agents import PersonaOutput
from pmacs.schemas.data import EvidencePacket
from pmacs.storage.audit import AuditWriter


LLAMA_SERVER_URL = "http://127.0.0.1:8080/completion"
MAX_RETRIES = 2  # 2 retries = 3 total attempts
TEMP_BUMP = 0.05


class PersonaRunner(ABC):
    """Abstract base for all persona runners.

    Subclasses must implement:
        - get_pydantic_model() -> type[BaseModel]
        - get_sanity_validator() -> BaseSanityValidator
        - build_prompt(evidence, episodic_context) -> str
    """

    def __init__(
        self,
        persona_name: str,
        model_config: dict[str, Any] | None = None,
        grammar_name: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        cycle_id: str = "",
        audit_writer: AuditWriter | None = None,
    ) -> None:
        self.persona_name = persona_name
        self.model_config = model_config or {}
        self.grammar_name = grammar_name or persona_name
        self.base_temperature = temperature
        self.max_tokens = max_tokens
        self.cycle_id = cycle_id
        self._audit = audit_writer

    @abstractmethod
    def get_pydantic_model(self) -> type[BaseModel]:
        """Return the Pydantic model class for this persona's output."""

    @abstractmethod
    def get_sanity_validator(self) -> BaseSanityValidator:
        """Return the sanity validator instance for this persona."""

    @abstractmethod
    def build_prompt(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> str:
        """Build the LLM prompt from evidence packets and optional episodic context."""

    def run(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> PersonaOutput | None:
        """Execute the three-layer validation pipeline.

        1. Call llama-server with grammar
        2. Parse response through Pydantic model_validate
        3. Run sanity validator

        On any layer failure: retry with +0.05 temp.
        After 3 total failures: log ABORTED_LLM, return None.
        """
        grammar_text = self._load_grammar()
        prompt = self.build_prompt(evidence, episodic_context)
        model_cls = self.get_pydantic_model()
        validator = self.get_sanity_validator()
        model_hash = self._get_model_hash()

        last_error: str = ""

        for attempt in range(MAX_RETRIES + 1):
            current_temp = self.base_temperature + (attempt * TEMP_BUMP)
            latency_ms: float = 0.0
            raw_output: str = ""

            # Layer 1: HTTP call to llama-server
            try:
                t0 = time.monotonic()
                raw_output = self._call_llm(prompt, grammar_text, current_temp)
                latency_ms = (time.monotonic() - t0) * 1000
            except httpx.ConnectError as exc:
                last_error = f"LLM connection refused: {exc}"
                log_debug(
                    "LLM_CALL_FAILED",
                    payload={"persona": self.persona_name, "attempt": attempt, "error": str(exc)},
                    level="WARN",
                    error_code="LLM_CONNECTION_REFUSED",
                    cycle_id=self.cycle_id,
                    msg=f"Attempt {attempt + 1}: connection refused",
                )
                continue
            except httpx.TimeoutException as exc:
                last_error = f"LLM timeout: {exc}"
                log_debug(
                    "LLM_CALL_FAILED",
                    payload={"persona": self.persona_name, "attempt": attempt, "error": str(exc)},
                    level="WARN",
                    error_code="LLM_TIMEOUT",
                    cycle_id=self.cycle_id,
                    msg=f"Attempt {attempt + 1}: timeout",
                )
                continue
            except Exception as exc:
                last_error = f"LLM call error: {exc}"
                log_debug(
                    "LLM_CALL_FAILED",
                    payload={"persona": self.persona_name, "attempt": attempt, "error": str(exc)},
                    level="WARN",
                    error_code="LLM_OUTPUT_EMPTY",
                    cycle_id=self.cycle_id,
                    msg=f"Attempt {attempt + 1}: unexpected error",
                )
                continue

            if not raw_output or not raw_output.strip():
                last_error = "LLM returned empty output"
                log_debug(
                    "LLM_CALL_FAILED",
                    payload={"persona": self.persona_name, "attempt": attempt},
                    level="WARN",
                    error_code="LLM_OUTPUT_EMPTY",
                    cycle_id=self.cycle_id,
                    msg=f"Attempt {attempt + 1}: empty output",
                )
                continue

            # Layer 2: Pydantic validation
            parsed: dict[str, Any] | None = None
            try:
                # Try to extract JSON from the raw output (may have surrounding text)
                json_str = self._extract_json(raw_output)
                parsed = json.loads(json_str)
                model_instance = model_cls.model_validate(parsed)
                parsed = model_instance.model_dump()
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = f"Pydantic parse failed: {exc}"
                log_debug(
                    "LLM_PARSE_FAILED",
                    payload={
                        "persona": self.persona_name,
                        "attempt": attempt,
                        "raw_snippet": raw_output[:200],
                        "error": str(exc),
                    },
                    level="WARN",
                    error_code="GBNF_PARSE_FAIL",
                    cycle_id=self.cycle_id,
                    msg=f"Attempt {attempt + 1}: parse/validation failed",
                )
                # Audit the failed attempt
                self._audit_llm_call(
                    prompt=prompt,
                    output=raw_output,
                    model_hash=model_hash,
                    grammar_version=self.grammar_name,
                    retry_count=attempt,
                    latency_ms=latency_ms,
                    success=False,
                )
                continue

            # Layer 3: Sanity validation
            sanity_result: SanityResult = validator.validate(parsed, evidence)
            if not sanity_result.passed:
                last_error = f"Sanity check failed: {sanity_result.reason}"
                log_debug(
                    "LLM_SANITY_FAILED",
                    payload={
                        "persona": self.persona_name,
                        "attempt": attempt,
                        "reason": sanity_result.reason,
                    },
                    level="WARN",
                    error_code="SANITY_VALIDATION_FAIL",
                    cycle_id=self.cycle_id,
                    msg=f"Attempt {attempt + 1}: sanity check failed",
                )
                self._audit_llm_call(
                    prompt=prompt,
                    output=raw_output,
                    model_hash=model_hash,
                    grammar_version=self.grammar_name,
                    retry_count=attempt,
                    latency_ms=latency_ms,
                    success=False,
                )
                continue

            # All three layers passed — build PersonaOutput
            persona_output = PersonaOutput(
                persona=self._get_persona_enum(),
                ticker=self._extract_ticker(evidence),
                cycle_id=self.cycle_id,
                raw_output=raw_output,
                grammar_version=self.grammar_name,
                model_hash=model_hash,
                temperature=current_temp,
                retry_count=attempt,
            )

            # Audit successful call
            self._audit_llm_call(
                prompt=prompt,
                output=raw_output,
                model_hash=model_hash,
                grammar_version=self.grammar_name,
                retry_count=attempt,
                latency_ms=latency_ms,
                success=True,
            )

            return persona_output

        # All attempts exhausted
        log_debug(
            "LLM_ABORTED",
            payload={
                "persona": self.persona_name,
                "attempts": MAX_RETRIES + 1,
                "last_error": last_error,
            },
            level="WARN",
            error_code="ABORTED_LLM",
            cycle_id=self.cycle_id,
            msg=f"All {MAX_RETRIES + 1} attempts failed for {self.persona_name}",
        )
        return None

    def _load_grammar(self) -> str:
        """Load GBNF grammar text for this persona."""
        try:
            return load_grammar(self.grammar_name)
        except FileNotFoundError:
            return ""

    def _call_llm(
        self, prompt: str, grammar: str, temperature: float, timeout: float = 120.0
    ) -> str:
        """Call llama-server HTTP API.

        Args:
            prompt: The full prompt text.
            grammar: GBNF grammar string (empty string = no grammar).
            temperature: Sampling temperature.
            timeout: HTTP timeout in seconds.

        Returns:
            The 'content' field from the llama-server response.
        """
        body: dict[str, Any] = {
            "prompt": prompt,
            "temperature": temperature,
            "n_predict": self.max_tokens,
        }
        if grammar:
            body["grammar"] = grammar

        with httpx.Client(timeout=timeout) as client:
            response = client.post(LLAMA_SERVER_URL, json=body)
            response.raise_for_status()
            data = response.json()
            return data.get("content", "")

    def _audit_llm_call(
        self,
        prompt: str,
        output: str,
        model_hash: str,
        grammar_version: str,
        retry_count: int,
        latency_ms: float,
        success: bool,
    ) -> None:
        """Write an audit event for the LLM call."""
        if self._audit is None:
            return

        self._audit.append(
            event_type="LLM_CALL",
            payload={
                "persona": self.persona_name,
                "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest()[:16],
                "output_hash": hashlib.sha256(output.encode()).hexdigest()[:16],
                "model_hash": model_hash,
                "grammar_version": grammar_version,
                "retry_count": retry_count,
                "latency_ms": round(latency_ms, 1),
                "success": success,
            },
            cycle_id=self.cycle_id,
        )

    def _get_model_hash(self) -> str:
        """Get the configured model hash from config, or empty string.

        Derives the hash key from gguf_path (filename without .gguf extension)
        to look up the correct hash in model_hashes.
        """
        try:
            from pmacs.config import load_config
            config = load_config()
            # Derive model name from gguf_path: strip path and .gguf suffix
            gguf_path = config.resources.gguf_path
            if gguf_path:
                model_name = Path(gguf_path).stem
                return config.model_hashes.get(model_name, "")
            # Fallback: single-model config
            if len(config.model_hashes) == 1:
                return next(iter(config.model_hashes.values()))
        except (ImportError, AttributeError):
            pass
        return ""

    def _get_persona_enum(self):
        """Convert persona_name string to PersonaName enum."""
        from pmacs.schemas.agents import PersonaName
        try:
            return PersonaName(self.persona_name)
        except ValueError:
            return PersonaName.GATEKEEPER

    def _extract_ticker(self, evidence: list[EvidencePacket]) -> str:
        """Extract ticker from first evidence packet, or empty string."""
        if evidence:
            return evidence[0].ticker
        return ""

    @staticmethod
    def _extract_json(raw: str) -> str:
        """Extract JSON object from raw LLM output.

        Handles cases where the model wraps JSON in markdown code blocks
        or surrounding text.
        """
        text = raw.strip()
        # Try to find JSON object boundaries
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]
        return text
