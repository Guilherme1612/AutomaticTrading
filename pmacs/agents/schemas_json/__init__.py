"""Ollama JSON Schema equivalents for GBNF grammars.

Each persona has a .json schema file matching its .gbnf grammar.
Used when the Ollama backend requires JSON Schema for structured output.
"""
from __future__ import annotations

import json
from pathlib import Path

_SCHEMAS_DIR = Path(__file__).parent

# Canonical persona names (must match grammar filenames).
PERSONAS = frozenset({
    "macro_regime",
    "catalyst_summarizer",
    "moat_analyst",
    "growth_hunter",
    "insider_activity",
    "short_interest",
    "forensics",
    "crucible",
    "memo_writer",
    # Wave-2 debate + audit personas (Agents.md §11b-§11d)
    "bull_advocate",
    "bear_advocate",
    "cross_persona_auditor",
})


def load_schema(persona: str) -> dict:
    """Load a JSON Schema for the given persona.

    Args:
        persona: One of the canonical persona names in PERSONAS.

    Returns:
        Parsed JSON Schema dict.

    Raises:
        ValueError: If *persona* is not a recognized persona name.
        FileNotFoundError: If the schema file is missing.
    """
    if persona not in PERSONAS:
        raise ValueError(
            f"Unknown persona '{persona}'. Must be one of: {sorted(PERSONAS)}"
        )
    schema_path = _SCHEMAS_DIR / f"{persona}.json"
    with open(schema_path, encoding="utf-8") as f:
        return json.load(f)
