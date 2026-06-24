"""Unit tests for the four shared _pre_validate helpers in PersonaRunner.

These helpers exist to defend against LLM schema-drift observed with
deepseek-v4-flash on openrouter with ``structured_output: "json_schema"``
(the JSON-schema mode does not enforce field names, enum casing, or
list-of-evidence_ids constraints — that's what GBNF is for, and GBNF is
not active on this backend).

Helpers covered:
- _truncate_string_fields — cuts str values at word boundary within max_length
- _normalize_literal_enums — uppercases Literal values, maps unknowns to default
- _rename_keys — recursive dict-key rename (e.g. type → moat_type)
- _ensure_min_evidence_ids — pads empty/minimal evidence_ids lists

All helpers are staticmethods on PersonaRunner so they can be called
without instantiating a runner. They return (modified_parsed, fixes_list)
where the fixes list is suitable for the PERSONA_OUTPUT_NORMALIZED audit
event payload.
"""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field

from pmacs.agents.base import PersonaRunner


# --- Test fixtures --------------------------------------------------------

class _Inner(BaseModel):
    """Nested model with Literal, str-with-max-length, and evidence_ids."""
    model_config = ConfigDict(frozen=False)  # not frozen so we can mutate parsed

    flag: Literal["A", "B", "C"]
    name: str = Field(max_length=10)
    evidence_ids: list[str] = Field(min_length=1)


class _Outer(BaseModel):
    """Top-level model: inner nested + top-level Literal/str/evidence_ids."""
    model_config = ConfigDict(frozen=False)

    inner: _Inner
    label: Literal["X", "Y", "Z"]
    note: str = Field(max_length=20)
    evidence_ids: list[str] = Field(min_length=1)


class TestTruncateStringFields:
    """_truncate_string_fields: cut long str values at word boundary."""

    def test_truncates_long_string_to_fit_max_length(self):
        parsed = {
            "inner": {"flag": "A", "name": "this is a long string", "evidence_ids": ["e1"]},
            "label": "X",
            "note": "short",
            "evidence_ids": ["e1"],
        }
        result, fixes = PersonaRunner._truncate_string_fields(parsed, _Outer)
        # "this is a long string" = 21 chars > max=10 * 0.95 = 9.5 → truncate
        assert "name" in result["inner"]
        assert len(result["inner"]["name"]) <= 10
        assert result["inner"]["name"].endswith("…")
        # Fix recorded
        truncate_fixes = [f for f in fixes if f["type"] == "truncated"]
        assert len(truncate_fixes) == 1
        assert truncate_fixes[0]["field"] == "inner.name"
        assert truncate_fixes[0]["max"] == 10

    def test_leaves_short_strings_alone(self):
        parsed = {
            "inner": {"flag": "A", "name": "ok", "evidence_ids": ["e1"]},
            "label": "X",
            "note": "ok",
            "evidence_ids": ["e1"],
        }
        result, fixes = PersonaRunner._truncate_string_fields(parsed, _Outer)
        assert result == parsed
        assert fixes == []

    def test_truncates_top_level_string(self):
        """Truncation works at top level too, not just nested."""
        parsed = {
            "inner": {"flag": "A", "name": "ok", "evidence_ids": ["e1"]},
            "label": "X",
            "note": "this note is way too long for max twenty",
            "evidence_ids": ["e1"],
        }
        result, fixes = PersonaRunner._truncate_string_fields(parsed, _Outer)
        assert len(result["note"]) <= 20
        assert result["note"].endswith("…")
        truncate_fixes = [f for f in fixes if f["type"] == "truncated"]
        assert any(f["field"] == "note" for f in truncate_fixes)

    def test_no_max_length_field_is_untouched(self):
        """Fields without max_length (free-form text) are not modified."""

        class _FreeForm(BaseModel):
            free_text: str  # no max_length
            limited: str = Field(max_length=5)

        parsed = {"free_text": "x" * 1000, "limited": "toolong"}
        result, fixes = PersonaRunner._truncate_string_fields(parsed, _FreeForm)
        assert result["free_text"] == "x" * 1000  # untouched
        # limited was truncated
        assert len(result["limited"]) <= 5


class TestNormalizeLiteralEnums:
    """_normalize_literal_enums: uppercase Literal values, map unknowns to default."""

    def test_uppercases_lowercase_literal_value(self):
        parsed = {
            "inner": {"flag": "a", "name": "ok", "evidence_ids": ["e1"]},
            "label": "x",
            "note": "ok",
            "evidence_ids": ["e1"],
        }
        result, fixes = PersonaRunner._normalize_literal_enums(parsed, _Outer)
        assert result["inner"]["flag"] == "A"
        assert result["label"] == "X"
        case_match_fixes = [
            f for f in fixes
            if f["type"] == "enum_normalized" and f["source"] == "case_match"
        ]
        assert len(case_match_fixes) == 2

    def test_unknown_value_maps_to_first_enum_member(self):
        parsed = {
            "inner": {"flag": "Z", "name": "ok", "evidence_ids": ["e1"]},
            "label": "X",
            "note": "ok",
            "evidence_ids": ["e1"],
        }
        result, fixes = PersonaRunner._normalize_literal_enums(parsed, _Outer)
        # "Z" is not in Literal["A","B","C"] → map to "A" (first member)
        assert result["inner"]["flag"] == "A"
        default_fixes = [f for f in fixes if f["type"] == "enum_normalized" and f["source"] == "default"]
        assert len(default_fixes) == 1
        assert default_fixes[0]["field"] == "inner.flag"
        assert default_fixes[0]["before"] == "Z"
        assert default_fixes[0]["after"] == "A"

    def test_already_correct_value_emits_no_fix(self):
        parsed = {
            "inner": {"flag": "A", "name": "ok", "evidence_ids": ["e1"]},
            "label": "X",
            "note": "ok",
            "evidence_ids": ["e1"],
        }
        result, fixes = PersonaRunner._normalize_literal_enums(parsed, _Outer)
        assert result == parsed
        assert fixes == []

    def test_normalizes_nested_literal(self):
        """Deeply-nested Literal fields are still normalized."""
        parsed = {
            "inner": {"flag": "c", "name": "ok", "evidence_ids": ["e1"]},
            "label": "X",
            "note": "ok",
            "evidence_ids": ["e1"],
        }
        result, fixes = PersonaRunner._normalize_literal_enums(parsed, _Outer)
        assert result["inner"]["flag"] == "C"
        assert any(f["field"] == "inner.flag" for f in fixes)

    def test_case_insensitive_match_preserves_canonical_case(self):
        """LLM may emit lowercase even when schema is uppercase (or vice-versa).
        Helper must match case-insensitively and emit the canonical (schema)
        form, not blindly uppercase."""

        class _LowerEnum(BaseModel):
            kind: Literal["earnings", "fda_decision", "partnership"]

        # LLM emits uppercase "EARNINGS" — schema wants lowercase "earnings"
        parsed = {"kind": "EARNINGS"}
        result, fixes = PersonaRunner._normalize_literal_enums(parsed, _LowerEnum)
        assert result["kind"] == "earnings"  # canonical lowercase preserved
        assert any(f["source"] == "case_match" for f in fixes)


class TestRenameKeys:
    """_rename_keys: recursive dict-key rename (e.g. type → moat_type)."""

    def test_renames_root_level_key(self):
        parsed = {"type": "foo", "other": "bar"}
        result, fixes = PersonaRunner._rename_keys(parsed, {"type": "moat_type"})
        assert "type" not in result
        assert result["moat_type"] == "foo"
        rename_fixes = [f for f in fixes if f["type"] == "renamed"]
        assert len(rename_fixes) == 1
        assert rename_fixes[0]["field"] == "type"
        assert rename_fixes[0]["before"] == "type"
        assert rename_fixes[0]["after"] == "moat_type"

    def test_renames_nested_key_when_scope_any(self):
        parsed = {"inner": {"type": "foo", "kept": "x"}}
        result, fixes = PersonaRunner._rename_keys(parsed, {"type": "moat_type"}, scope="any")
        assert "type" not in result["inner"]
        assert result["inner"]["moat_type"] == "foo"
        assert result["inner"]["kept"] == "x"
        assert any(f["field"] == "inner.type" for f in fixes)

    def test_scope_root_does_not_recurse(self):
        parsed = {"inner": {"type": "foo"}}
        result, fixes = PersonaRunner._rename_keys(parsed, {"type": "moat_type"}, scope="root")
        # nested.type NOT renamed
        assert result["inner"]["type"] == "foo"
        assert fixes == []

    def test_absent_key_emits_no_fix(self):
        parsed = {"other": "bar"}
        result, fixes = PersonaRunner._rename_keys(parsed, {"type": "moat_type"})
        assert result == parsed
        assert fixes == []


class TestEnsureMinEvidenceIds:
    """_ensure_min_evidence_ids: pad empty/minimal evidence_ids lists."""

    def test_pads_empty_top_level_evidence_ids(self):
        parsed = {
            "inner": {"flag": "A", "name": "ok", "evidence_ids": ["e1"]},
            "label": "X",
            "note": "ok",
            "evidence_ids": [],
        }
        result, fixes = PersonaRunner._ensure_min_evidence_ids(parsed, _Outer)
        assert len(result["evidence_ids"]) == 1
        assert "normalized-fallback" in result["evidence_ids"][0]
        assert any(f["field"] == "evidence_ids" for f in fixes)

    def test_pads_empty_nested_evidence_ids(self):
        parsed = {
            "inner": {"flag": "A", "name": "ok", "evidence_ids": []},
            "label": "X",
            "note": "ok",
            "evidence_ids": ["e1"],
        }
        result, fixes = PersonaRunner._ensure_min_evidence_ids(parsed, _Outer)
        assert len(result["inner"]["evidence_ids"]) == 1
        assert any(f["field"] == "inner.evidence_ids" for f in fixes)

    def test_leaves_adequate_evidence_ids_alone(self):
        parsed = {
            "inner": {"flag": "A", "name": "ok", "evidence_ids": ["e1"]},
            "label": "X",
            "note": "ok",
            "evidence_ids": ["e1", "e2"],
        }
        result, fixes = PersonaRunner._ensure_min_evidence_ids(parsed, _Outer)
        assert result == parsed
        assert fixes == []

    def test_min_count_two(self):
        """Helper accepts custom min_count."""

        class _WithMin2(BaseModel):
            evidence_ids: list[str] = Field(min_length=2)

        parsed = {"evidence_ids": ["e1"]}
        result, fixes = PersonaRunner._ensure_min_evidence_ids(
            parsed, _WithMin2, min_count=2
        )
        assert len(result["evidence_ids"]) == 2
        assert any(f["type"] == "evidence_padded" for f in fixes)
