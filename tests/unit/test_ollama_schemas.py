"""Tests for Ollama JSON Schema equivalents of GBNF grammars."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pmacs.agents.schemas_json import PERSONAS, load_schema

SCHEMAS_DIR = Path(__file__).resolve().parent.parent.parent / "pmacs" / "agents" / "schemas_json"
GRAMMARS_DIR = Path(__file__).resolve().parent.parent.parent / "pmacs" / "agents" / "grammars"


# ---------------------------------------------------------------------------
# All 9 schemas are valid JSON Schema
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("persona", sorted(PERSONAS))
def test_schema_is_valid_json(persona: str) -> None:
    """Each schema file parses as valid JSON."""
    path = SCHEMAS_DIR / f"{persona}.json"
    assert path.exists(), f"Schema file missing: {path}"
    with open(path, encoding="utf-8") as f:
        schema = json.load(f)
    assert isinstance(schema, dict)
    assert schema.get("type") == "object"
    assert "properties" in schema
    assert "required" in schema


@pytest.mark.parametrize("persona", sorted(PERSONAS))
def test_schema_has_json_schema_header(persona: str) -> None:
    """Each schema declares $schema."""
    schema = load_schema(persona)
    assert "$schema" in schema
    assert "draft-07" in schema["$schema"]


# ---------------------------------------------------------------------------
# Schema properties match GBNF grammar fields
# ---------------------------------------------------------------------------

def _gbnf_fields(grammar_path: Path) -> set[str]:
    """Extract top-level field names from a GBNF root rule."""
    with open(grammar_path, encoding="utf-8") as f:
        content = f.read()
    # Find the root rule line — contains the top-level object fields
    for line in content.splitlines():
        if line.strip().startswith("root ::="):
            # Extract all quoted field names
            fields = set()
            in_string = False
            i = 0
            while i < len(line):
                if line[i] == '"' and not in_string:
                    # Start of a field name
                    j = line.index('"', i + 1)
                    field = line[i + 1:j]
                    # Only include if it looks like a field name (snake_case)
                    if "_" in field or field in ("ticker",):
                        fields.add(field)
                    i = j + 1
                else:
                    i += 1
            return fields
    return set()


@pytest.mark.parametrize("persona", sorted(PERSONAS))
def test_schema_fields_match_gbnf(persona: str) -> None:
    """Schema required fields are a superset of GBNF root fields."""
    gbnf_path = GRAMMARS_DIR / f"{persona}.gbnf"
    assert gbnf_path.exists(), f"GBNF grammar missing: {gbnf_path}"

    gbnf_fields = _gbnf_fields(gbnf_path)
    schema = load_schema(persona)
    schema_props = set(schema.get("properties", {}).keys())

    # Every GBNF root field should appear in schema properties
    missing = gbnf_fields - schema_props
    assert not missing, f"Schema {persona} missing fields from GBNF: {missing}"


# ---------------------------------------------------------------------------
# load_schema loader
# ---------------------------------------------------------------------------

def test_load_schema_returns_dict() -> None:
    """load_schema returns a parsed dict."""
    schema = load_schema("macro_regime")
    assert isinstance(schema, dict)
    assert "properties" in schema


def test_load_schema_unknown_persona_raises() -> None:
    """load_schema raises ValueError for unknown persona."""
    with pytest.raises(ValueError, match="Unknown persona"):
        load_schema("nonexistent_persona")


def test_load_schema_all_personas() -> None:
    """All 9 personas load without error."""
    for persona in PERSONAS:
        schema = load_schema(persona)
        assert isinstance(schema, dict)
        assert len(schema.get("properties", {})) > 0


# ---------------------------------------------------------------------------
# Probability fields have [0, 1] bounds
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("persona", sorted(PERSONAS))
def test_probability_fields_bounded(persona: str) -> None:
    """p_up, p_flat, p_down fields have minimum=0, maximum=1."""
    schema = load_schema(persona)
    props = schema.get("properties", {})
    for prob_field in ("p_up", "p_flat", "p_down"):
        if prob_field in props:
            prop = props[prob_field]
            # May be a simple type or oneOf
            if isinstance(prop.get("type"), str):
                assert prop.get("minimum") == 0, f"{persona}.{prob_field} missing minimum=0"
                assert prop.get("maximum") == 1, f"{persona}.{prob_field} missing maximum=1"


# ---------------------------------------------------------------------------
# Enum fields have correct values
# ---------------------------------------------------------------------------

def test_macro_regime_enum_values() -> None:
    """macro_regime enum values match GBNF."""
    schema = load_schema("macro_regime")
    regimes = schema["properties"]["regime"]["enum"]
    assert set(regimes) == {
        "EXPANSION", "LATE_CYCLE", "CONTRACTION",
        "RECOVERY", "REGIME_SHIFT", "UNCERTAIN",
    }


def test_crucible_attack_type_enum() -> None:
    """crucible attack_type enum matches GBNF."""
    schema = load_schema("crucible")
    attack_items = schema["properties"]["attacks"]["items"]
    attack_types = attack_items["properties"]["attack_type"]["enum"]
    assert set(attack_types) == {
        "LOGICAL_HOLE", "CITATION_GAP", "COUNTERARGUMENT",
        "OVERLOOKED_RISK", "BASE_RATE_NEGLECT",
    }
