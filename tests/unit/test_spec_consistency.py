"""Tests for ops/spec_consistency.py -- spec cross-reference verification."""

import json
import textwrap
from pathlib import Path

import pytest

from ops.spec_consistency import (
    ConsistencyResult,
    SectionCheck,
    check_consistency,
    find_back_references,
    format_json,
    format_report,
    parse_section_numbers,
)


# ---------------------------------------------------------------------------
# Fixtures: synthetic spec content
# ---------------------------------------------------------------------------

SOURCE_MD_MINIMAL = textwrap.dedent("""\
    # Source.md

    ## 0. Cross-reference index

    This is the index.

    ## 14. Page: Dashboard

    Dashboard shows portfolio state.

    ## 99. Made-up section

    This section is intentionally unreferenced.
""")

ARCH_MD_REFERENCES_14 = textwrap.dedent("""\
    # Architecture.md

    ## 0. Cross-reference index

    ## 4. Process topology

    Implements `Source.md §14` promise 2 (dashboard rendering).
""")


# ---------------------------------------------------------------------------
# Tests: parse_section_numbers
# ---------------------------------------------------------------------------

class TestParseSectionNumbers:
    def test_extracts_numbered_sections(self):
        text = "## 14. Page: Dashboard\nSome content\n## 15. Page: Agents\n"
        result = parse_section_numbers(text)
        assert result == {14: "Page: Dashboard", 15: "Page: Agents"}

    def test_extracts_single_section(self):
        text = "## 14. Page: Dashboard\n"
        result = parse_section_numbers(text)
        assert result == {14: "Page: Dashboard"}

    def test_ignores_subsections(self):
        text = "## 14. Page: Dashboard\n### 14.1 Portfolio summary card\n"
        result = parse_section_numbers(text)
        assert result == {14: "Page: Dashboard"}

    def test_empty_input(self):
        result = parse_section_numbers("")
        assert result == {}

    def test_no_numbered_sections(self):
        text = "## Introduction\nSome text\n## Conclusion\n"
        result = parse_section_numbers(text)
        assert result == {}


# ---------------------------------------------------------------------------
# Tests: find_back_references
# ---------------------------------------------------------------------------

class TestFindBackReferences:
    def test_single_reference(self):
        text = "Implements `Source.md §14` promise."
        refs = find_back_references(text)
        assert 14 in refs
        assert len(refs[14]) == 1

    def test_range_reference(self):
        text = "Implements Source.md §14-§20 (UI pages)"
        refs = find_back_references(text)
        for num in range(14, 21):
            assert num in refs, f"Expected section {num} to be referenced"
            assert len(refs[num]) == 1

    def test_range_without_second_section_sign(self):
        text = "Implements Source.md §14-20 (UI pages)"
        refs = find_back_references(text)
        for num in range(14, 21):
            assert num in refs

    def test_multiple_references_to_same_section(self):
        text = (
            "Line with Source.md §14 first ref.\n"
            "Another line with Source.md §14 second ref.\n"
        )
        refs = find_back_references(text)
        assert len(refs[14]) == 2

    def test_no_references(self):
        refs = find_back_references("Some text without references.")
        assert refs == {}

    def test_sub_section_reference(self):
        """Source.md §7.2 should count as a reference to section 7."""
        text = "Conviction scoring (Source.md §7.2)"
        refs = find_back_references(text)
        assert 7 in refs


# ---------------------------------------------------------------------------
# Tests: check_consistency (with temp dirs)
# ---------------------------------------------------------------------------

class TestCheckConsistency:
    def test_all_sections_referenced(self, tmp_path: Path):
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "Source.md").write_text(
            "## 14. Page: Dashboard\nDashboard content.\n"
        )
        (spec_dir / "Architecture.md").write_text(
            "Implements `Source.md §14` dashboard rendering.\n"
        )
        result = check_consistency(spec_dir=spec_dir)
        assert result.passed is True
        assert len(result.missing) == 0

    def test_missing_reference_detected(self, tmp_path: Path):
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "Source.md").write_text(
            "## 14. Page: Dashboard\n## 99. Made-up section\n"
        )
        (spec_dir / "Architecture.md").write_text(
            "Implements `Source.md §14` only.\n"
        )
        result = check_consistency(spec_dir=spec_dir)
        assert result.passed is False
        missing_nums = [s.section for s in result.missing]
        assert 99 in missing_nums
        assert 14 not in missing_nums

    def test_skip_section_zero(self, tmp_path: Path):
        """Section 0 (cross-reference index) should not be checked."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "Source.md").write_text(
            "## 0. Cross-reference index\nIndex content.\n"
        )
        (spec_dir / "Architecture.md").write_text(
            "No references to Source.md here.\n"
        )
        result = check_consistency(spec_dir=spec_dir)
        # Section 0 is skipped, so with no other sections to check, it passes
        assert result.passed is True

    def test_range_covers_multiple_sections(self, tmp_path: Path):
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "Source.md").write_text(
            "## 14. Page: Dashboard\n## 15. Page: Agents\n## 16. Page: Pipeline\n"
        )
        (spec_dir / "Architecture.md").write_text(
            "Implements Source.md §14-§16 (UI pages)\n"
        )
        result = check_consistency(spec_dir=spec_dir)
        assert result.passed is True

    def test_missing_source_file(self, tmp_path: Path):
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "Architecture.md").write_text("Content\n")
        with pytest.raises(SystemExit) as exc_info:
            check_consistency(spec_dir=spec_dir)
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# Tests: output formatters
# ---------------------------------------------------------------------------

class TestFormatJson:
    def test_json_output_structure(self):
        result = ConsistencyResult(
            passed=True,
            source_sections=[
                SectionCheck(section=14, title="Page: Dashboard", referenced=True,
                             reference_lines=["Implements Source.md §14"]),
            ],
        )
        output = format_json(result)
        data = json.loads(output)
        assert data["pass"] is True
        assert "results" in data
        assert len(data["results"]) == 1
        assert data["results"][0]["section"] == 14
        assert data["results"][0]["referenced"] is True

    def test_json_output_missing(self):
        result = ConsistencyResult(
            passed=False,
            source_sections=[
                SectionCheck(section=14, title="Page: Dashboard", referenced=True,
                             reference_lines=["ref"]),
                SectionCheck(section=99, title="Missing", referenced=False,
                             reference_lines=[]),
            ],
        )
        output = format_json(result)
        data = json.loads(output)
        assert data["pass"] is False
        assert data["missing"] == 1


class TestFormatReport:
    def test_pass_report(self):
        result = ConsistencyResult(
            passed=True,
            source_sections=[
                SectionCheck(section=14, title="Page: Dashboard", referenced=True),
            ],
        )
        report = format_report(result, verbose=False)
        assert "PASS" in report

    def test_fail_report(self):
        result = ConsistencyResult(
            passed=False,
            source_sections=[
                SectionCheck(section=99, title="Missing", referenced=False),
            ],
        )
        report = format_report(result, verbose=False)
        assert "FAIL" in report
        assert "99" in report

    def test_verbose_report(self):
        result = ConsistencyResult(
            passed=True,
            source_sections=[
                SectionCheck(section=14, title="Page: Dashboard", referenced=True,
                             reference_lines=["Implements Source.md §14"]),
            ],
        )
        report = format_report(result, verbose=True)
        assert "[OK]" in report
        assert "Source.md §14" in report


# ---------------------------------------------------------------------------
# Integration test: run against real spec files
# ---------------------------------------------------------------------------

class TestRealSpecFiles:
    """Run against the actual spec/ directory.

    This test documents the current state of the spec. It passes whether the
    spec is fully consistent or not -- the assertion only checks that the
    checker runs without error and produces a valid result.
    """

    def test_real_spec_files(self):
        root = Path(__file__).resolve().parent.parent.parent
        spec_dir = root / "spec"
        if not (spec_dir / "Source.md").is_file():
            pytest.skip("spec/Source.md not found (not in project root)")

        result = check_consistency(spec_dir=spec_dir)

        # The test always passes -- it just reports the current state.
        # If sections are missing, print them for visibility.
        if not result.passed:
            missing = [f"  section {s.section}: {s.title}" for s in result.missing]
            print(f"\nUnreferenced sections in current spec:\n" + "\n".join(missing))
        else:
            print(f"\nAll {len(result.source_sections)} sections referenced. PASS.")

        # Verify structural invariants (not pass/fail)
        assert len(result.source_sections) > 0, "Should find at least some sections"
        for s in result.source_sections:
            assert s.section > 0 or s.section == 0, f"Invalid section number: {s.section}"
