"""Grammar loader for llama.cpp GBNF grammars (Agents.md §3)."""

from __future__ import annotations

from pathlib import Path

_GRAMMARS_DIR = Path(__file__).parent


def load_grammar(persona_name: str) -> str:
    """Load a GBNF grammar file for the given persona.

    Args:
        persona_name: Persona identifier (e.g. 'gatekeeper', 'test').

    Returns:
        Grammar string contents.

    Raises:
        FileNotFoundError: If no grammar file exists for the persona.
    """
    grammar_path = _GRAMMARS_DIR / f"{persona_name}.gbnf"
    if not grammar_path.exists():
        raise FileNotFoundError(f"No grammar file for persona '{persona_name}': {grammar_path}")
    return grammar_path.read_text(encoding="utf-8")
