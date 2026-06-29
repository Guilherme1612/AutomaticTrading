"""Pin the hookify anti-pattern regex against the canonical fixture set.

The hookify rule at `.claude/hookify.pmacs-anti-patterns.local.md` enforces
`Architecture.md §16` anti-patterns in real time during Claude Code Edit/Write/
MultiEdit operations on `pmacs/*.py`. This test pins the rule's regex behavior
so future changes (e.g. tightening patterns, expanding coverage) cannot silently
regress the operator's false-positive rate.

Fixtures are split into three buckets:

- ``true_positives`` — MUST fire (rule warning is correct).
- ``false_positives`` — MUST NOT fire (these would be a regression).
- ``acceptable_false_positives`` — currently fire; operator reviews and dismisses.
  Pinning these prevents accidental "fixes" that either tighten the regex
  (silently reducing coverage) or leave them out of the spec (drifting from
  reality).

The rule's content pattern is the one shipped in the hookify rule file — same
field name (``content``) that the hookify engine maps to BOTH
``Edit.new_string`` AND ``Write.content``. See ``spec/Architecture.md §16.15``
for the full rationale and ``spec/Source.md §27`` for the operator-facing
summary.

This test does NOT exercise the hookify engine itself (no live hook to invoke
in CI). It pins the regex behavior — the actual hookify engine just applies
the regex with ``re.search`` and triggers an action if ``True``.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RULE_PATH = ROOT / ".claude" / "hookify.pmacs-anti-patterns.local.md"


# ---------------------------------------------------------------------------
# Pattern extraction from the hookify rule file
# ---------------------------------------------------------------------------
def _load_content_pattern() -> re.Pattern[str]:
    """Extract and compile the ``content`` field pattern from the hookify rule.

    The rule file is YAML frontmatter + markdown body. We parse the YAML block
    for the conditions list and pull out the condition with ``field: content``
    (NOT ``field: new_text`` — that one does not resolve for Edit operations;
    see ``spec/Architecture.md §16.15`` for the field-name gotcha).
    """
    raw = RULE_PATH.read_text()
    # Find the YAML frontmatter between --- markers
    parts = raw.split("---", 2)
    assert len(parts) >= 3, (
        f"Hookify rule file at {RULE_PATH} must have YAML frontmatter "
        f"delimited by --- markers; got {len(parts)} parts"
    )
    yaml_block = parts[1]
    # Walk lines, collect (field, pattern) for each condition block.
    # Each condition starts with `  - field: <name>` (dash-prefixed list item).
    field = None
    pattern = None
    conditions: list[tuple[str, str]] = []
    for line in yaml_block.splitlines():
        # Strip list-item dash: `  - field: name` → `field: name`
        normalized = line.lstrip().lstrip("-").lstrip()
        if normalized.startswith("field:"):
            field = normalized.split("field:", 1)[1].strip()
        elif normalized.startswith("pattern:") and field is not None:
            pattern = normalized.split("pattern:", 1)[1].strip()
            conditions.append((field, pattern))
            field = None
            pattern = None
    content_conditions = [(f, p) for f, p in conditions if f == "content"]
    assert len(content_conditions) == 1, (
        f"Hookify rule must have exactly one `field: content` condition; "
        f"found {len(content_conditions)}. If this assertion fails, either the "
        f"rule was restructured or someone added a duplicate. See "
        f"spec/Architecture.md §16.15 for the field-name gotcha."
    )
    return re.compile(content_conditions[0][1])


PATTERN = pytest.fixture(scope="module")(_load_content_pattern)
pattern = PATTERN  # lowercase alias for use as a test parameter


def _fires(pattern: re.Pattern[str], content: str) -> bool:
    """Apply the hookify rule's regex to raw content — no Python stripper.

    The hookify engine applies the regex as-is on the Edit.new_string /
    Write.content. There is NO comment/string stripper in the hookify engine
    itself (the /tmp/regex_test.py prototype had one but it was never
    integrated). This means the regex fires on comments and string literals
    that mention the anti-pattern — those are the ``acceptable_false_positives``
    below.
    """
    return bool(pattern.search(content))


# ---------------------------------------------------------------------------
# Fixtures — split into 3 buckets
# ---------------------------------------------------------------------------

# MUST fire. Mirrors the 10 TRUE POSITIVES in /tmp/regex_test.py.
TRUE_POSITIVES = [
    pytest.param('Foo.parse_obj({"x": 1})', id="pydantic_v1_parse_obj"),
    pytest.param('self.holding.state = "X"', id="self_holding_state_eq"),
    pytest.param('    return Foo(x=1).dict()\n', id="indented_dict"),
    pytest.param('Foo.dict()', id="bare_dict"),
    pytest.param('    x = Foo.parse_obj(x).dict()\n', id="chained_parse_dict"),
    pytest.param('holding.state = "X"', id="bare_holding_state_eq"),
    pytest.param(
        'Audit_event("x", json.dumps({"audit": "x"}))',
        id="json_dumps_audit_payload",
    ),
    pytest.param('audit_emitter(cycle_id=None)', id="cycle_id_eq_None"),
    pytest.param('from pydantic.v1 import BaseModel', id="pydantic_v1_import"),
    pytest.param('return Foo(x=1).dict()', id="singleline_dict"),
    # Additional true positives — edge cases not in /tmp/regex_test.py
    pytest.param(
        'self.holding.state = "ABORTED_LLM"  # spec violation',
        id="self_holding_state_with_comment",
    ),
    pytest.param(
        'if x: cycle_id=None  # audit emitter missing cycle_id\n',
        id="cycle_id_eq_None_statement",
    ),
    pytest.param(
        'from pydantic.v1 import BaseModel as BM\n',
        id="pydantic_v1_import_alias",
    ),
    pytest.param(
        'Foo.parse_raw(\'{"x": 1}\')',
        id="parse_raw_string",
    ),
]


# MUST NOT fire. Mirrors the FALSE POSITIVES in /tmp/regex_test.py plus
# additional cases for new operators (Cortex, valuation, schemas).
FALSE_POSITIVES = [
    # json.dumps without "audit" key in payload
    pytest.param('json.dumps({"foo": "bar"})', id="json_dumps_no_audit_key"),
    pytest.param(
        'audit = json.dumps({"name": "x", "data": [1,2,3]})',
        id="audit_is_var_name_not_payload_key",
    ),
    # holding_obj / similar identifiers — `holding.state` requires `holding.` prefix
    pytest.param(
        'holding_obj.state = "ABORTED"  # fine',
        id="holding_obj_not_holding",
    ),
    pytest.param(
        'new_holding.state_machine.transition(...)',
        id="new_holding_qualified",
    ),
    # cycle_id: T = None is a default-param annotation; the regex requires
    # `cycle_id = None` (not `cycle_id: T = None`)
    pytest.param(
        'def f(cycle_id: Optional[str] = None): return cycle_id',
        id="cycle_id_default_param_annotation",
    ),
    # `is None` is a check, not an assignment
    pytest.param(
        'if cycle_id is None: raise ValueError("cycle_id required")',
        id="cycle_id_is_None_check",
    ),
    # model_dump is fine (Pydantic v2)
    pytest.param('Foo(x=1).model_dump()', id="model_dump_is_fine"),
    pytest.param('Foo(x=1).model_dump_json()', id="model_dump_json_is_fine"),
    # Variable / helper names that LOOK like the patterns but aren't
    pytest.param('audit_str = "x"', id="audit_str_var_name"),
    pytest.param('to_dict()', id="to_dict_helper_function"),
    pytest.param('parse_obj_helper()', id="parse_obj_helper_function"),
    # Bare comment line without the literal pattern
    pytest.param('    # Foo.parse_obj test\n', id="bare_comment_line"),
    # String literal alone (e.g. a docstring example) without the pattern
    pytest.param(
        '"""This module audits the cycle."""\n',
        id="docstring_with_audit_word",
    ),
    # Operators frequently used in other anti-patterns — must not fire here
    pytest.param('state_machine.transition(...)', id="proper_state_machine_call"),
    pytest.param('canonical_json(payload)', id="proper_canonical_json"),
    pytest.param('model_validate({"x": 1})', id="proper_model_validate"),
    # 'auditor' (not 'audit') — common in our codebase (RawData, FDE taxonomy)
    pytest.param('RawData(auditor="x")', id="auditor_not_audit"),
    # 'audit' as substring in a string literal (regex is on raw content, but
    # the json.dumps(audit) sub-pattern requires the `audit` token inside
    # the json.dumps call's parentheses)
    pytest.param('msg = "started audit at " + str(now)', id="audit_in_string_only"),
    # Different pydantic import path
    pytest.param('from pydantic import BaseModel', id="pydantic_v2_import"),
]


# CURRENTLY FIRES. Operator reviews and dismisses. Pinning these prevents:
# (a) accidental tightening of the regex that would silently reduce coverage;
# (b) accidental drift between spec and reality (the spec documents these FPs
#     in Source.md §27.3 + Architecture.md §16.15).
ACCEPTABLE_FALSE_POSITIVES = [
    pytest.param(
        '# old code: Foo.parse_obj(x).dict() should be model_dump',
        id="comment_mentioning_parse_obj_and_dict",
    ),
    pytest.param(
        'print("do not call .parse_obj() on this")',
        id="string_mentioning_parse_obj",
    ),
    pytest.param(
        'text = "use .dict() or model_dump"',
        id="string_mentioning_dict",
    ),
    pytest.param(
        '"""Migration note: replace Foo.parse_obj() with model_validate()."""',
        id="docstring_mentioning_parse_obj",
    ),
    pytest.param(
        '"""Migration note:\nholding.state = "ABORTED_LLM"  # replaced by state_machine.transition()\n"""',
        id="docstring_mentioning_holding_state",
    ),
    pytest.param(
        'logger.info("audit_emitter requires cycle_id, never cycle_id=None")',
        id="log_message_mentioning_cycle_id_None",
    ),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("content", TRUE_POSITIVES)
def test_true_positives_must_fire(pattern: re.Pattern[str], content: str) -> None:
    """Real anti-pattern violations must trigger the hookify warning.

    If a TRUE POSITIVE stops firing, the rule has lost coverage and the
    operator's review window has silently widened. Treat as a regression.
    """
    assert _fires(pattern, content), (
        f"Expected hookify rule to FIRE on: {content!r}\n"
        f"Pattern: {pattern.pattern}\n"
        f"This is a regression — true positives must always match."
    )


@pytest.mark.parametrize("content", FALSE_POSITIVES)
def test_false_positives_must_not_fire(pattern: re.Pattern[str], content: str) -> None:
    """Clean code MUST NOT trigger the hookify warning.

    If a FALSE POSITIVE starts firing, either the regex has been widened or
    the production code has regressed (e.g. someone wrote
    `holding_obj.state =` and the operator wants the rule to flag it). Treat
    as a regression in either case.
    """
    assert not _fires(pattern, content), (
        f"Expected hookify rule to NOT FIRE on: {content!r}\n"
        f"Pattern: {pattern.pattern}\n"
        f"This is a regression — clean code must not match."
    )


@pytest.mark.parametrize("content", ACCEPTABLE_FALSE_POSITIVES)
def test_acceptable_false_positives_pinned(pattern: re.Pattern[str], content: str) -> None:
    """Pin the current acceptable-FP set.

    These DO fire today. The operator reviews and dismisses. Pinning them
    prevents:
    - accidental tightening that would silently remove them from coverage
      (no operator-visible signal that the rule changed);
    - accidental drift between this test and ``spec/Source.md §27.3`` /
      ``spec/Architecture.md §16.15`` where the operator-visible docs list
      the acceptable FPs.

    If you intentionally remove one of these from the FP list (by tightening
    the regex to exclude it), update BOTH this test AND the spec citations.
    """
    assert _fires(pattern, content), (
        f"Expected hookify rule to fire on (acceptable FP): {content!r}\n"
        f"Pattern: {pattern.pattern}\n"
        f"If this is intentional (regex tightened), update spec/Source.md §27.3 "
        f"and spec/Architecture.md §16.15 to remove this case from the "
        f"acceptable FP list."
    )


def test_rule_file_exists() -> None:
    """The hookify rule file MUST exist at the canonical location.

    Loader pattern is ``glob('.claude/hookify.*.local.md')`` — file MUST be
    directly in ``.claude/``, NOT in a subdirectory (loader silently ignores
    subdirectories). See ``spec/Architecture.md §16.15`` for the location rule.
    """
    assert RULE_PATH.exists(), (
        f"Hookify rule file not found at {RULE_PATH}. "
        f"Loader pattern: glob('.claude/hookify.*.local.md'). "
        f"File MUST be directly in .claude/, NOT in a subdirectory."
    )


def test_rule_uses_content_field_not_new_text() -> None:
    """The hookify rule MUST use ``field: content``, NOT ``field: new_text``.

    The hookify engine's ``content`` field maps to BOTH ``Write.content`` AND
    ``Edit.new_string``. Using ``field: new_text`` instead causes the rule to
    silently never fire for Edit operations (Write still works). This is a
    documented gotcha — see ``spec/Architecture.md §16.15``.
    """
    raw = RULE_PATH.read_text()
    yaml_block = raw.split("---", 2)[1]
    assert "field: content" in yaml_block, (
        "Hookify rule must declare `field: content` in its conditions. "
        "Do NOT use `field: new_text` — that field does not resolve for "
        "Edit operations. See spec/Architecture.md §16.15."
    )
    # new_text alone (without content) is the wrong pattern
    assert "field: new_text" not in yaml_block, (
        "Hookify rule uses `field: new_text` — this does NOT resolve for "
        "Edit operations (Write works, Edit does not). Use `field: content` "
        "instead. See spec/Architecture.md §16.15."
    )


def test_rule_field_path_targets_pmacs_only() -> None:
    """The rule's ``file_path`` pattern MUST scope to ``pmacs/*.py``.

    The hookify rule must NOT fire on tests/, spec/, docs/, or root-level
    files. The file_path pattern is what limits scope. See
    ``spec/Architecture.md §16.15``.
    """
    raw = RULE_PATH.read_text()
    yaml_block = raw.split("---", 2)[1]
    assert "field: file_path" in yaml_block
    # The pattern on the next line should be a regex anchored to pmacs/
    assert re.search(r"pattern:\s*\^pmacs/", yaml_block), (
        "Hookify rule's file_path pattern must be anchored to `pmacs/` "
        "(e.g. `^pmacs/.*\\.py$`). Tests/, spec/, and root-level files "
        "are out of scope."
    )


def test_rule_covers_all_five_patterns() -> None:
    """The hookify rule MUST cover all 5 patterns documented in spec §16.15.

    If any pattern is missing, the rule has lost coverage. The patterns are:

    - ``holding.state =`` (state machine bypass)
    - ``json.dumps(audit)`` (non-canonical audit serialization)
    - ``cycle_id=None`` (audit emitter missing required cycle_id)
    - ``from pydantic.v1`` (Pydantic v2 only)
    - ``.dict()`` / ``.parse_obj()`` / ``.parse_raw()`` (Pydantic v2 method API)
    """
    raw = RULE_PATH.read_text()
    yaml_block = raw.split("---", 2)[1]
    # Pull out the content pattern
    pattern_match = re.search(
        r"field:\s*content[\s\S]*?pattern:\s*(\S.*)", yaml_block
    )
    assert pattern_match is not None, "No content pattern found"
    pattern_str = pattern_match.group(1).strip()
    expected_subpatterns = [
        r"holding\.state",
        r"json\.dumps\([^)]*audit",
        r"cycle_id\s*=\s*None",
        r"pydantic\.v1",
        r"\.parse_obj\(",
        r"\.parse_raw\(",
        r"\.dict\(\)",
    ]
    for sub in expected_subpatterns:
        assert sub in pattern_str, (
            f"Hookify rule's content pattern is missing sub-pattern: {sub!r}\n"
            f"Current pattern: {pattern_str}\n"
            f"See spec/Source.md §27.3 + spec/Architecture.md §16.15 for the "
            f"5-pattern surface."
        )
