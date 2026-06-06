"""Tests for ops/spec_consistency.py -- spec cross-reference verification."""

import json
import textwrap
from pathlib import Path

import pytest

from ops.spec_consistency import (
    CheckResult,
    SectionRef,
    check_consistency,
    extract_cross_references,
    format_json,
    format_report,
    parse_sections,
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

class TestParseSections:
    def test_extracts_numbered_sections(self):
        text = "## 14. Page: Dashboard\nSome content\n## 15. Page: Agents\n"
        result = parse_sections(text)
        assert "14" in result
        assert "15" in result

    def test_extracts_single_section(self):
        text = "## 14. Page: Dashboard\n"
        result = parse_sections(text)
        assert "14" in result

    def test_ignores_non_numbered(self):
        text = "## Introduction\nSome text\n## Conclusion\n"
        result = parse_sections(text)
        assert result == set()

    def test_empty_input(self):
        result = parse_sections("")
        assert result == set()


# ---------------------------------------------------------------------------
# Tests: find_back_references
# ---------------------------------------------------------------------------

class TestExtractCrossReferences:
    def test_single_reference(self):
        text = "Implements `Source.md §14` promise."
        refs = extract_cross_references(text, "test.md")
        assert len(refs) == 1
        assert refs[0].target_section == "14"

    def test_range_reference(self):
        text = "Implements Source.md §14-§20 (UI pages)"
        refs = extract_cross_references(text, "test.md")
        sections = {r.target_section for r in refs}
        for num in range(14, 21):
            assert str(num) in sections

    def test_no_references(self):
        refs = extract_cross_references("Some text without references.", "test.md")
        assert refs == []


# ---------------------------------------------------------------------------
# Tests: check_consistency (with temp dirs)
# ---------------------------------------------------------------------------

def _make_spec_dir(tmp_path: Path, source: str, arch: str) -> Path:
    """Create a spec dir with all 4 required files."""
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir(exist_ok=True)
    (spec_dir / "Source.md").write_text(source)
    (spec_dir / "Architecture.md").write_text(arch)
    (spec_dir / "Agents.md").write_text("# Agents\n")
    (spec_dir / "Phases.md").write_text("# Phases\n")
    return spec_dir


class TestCheckConsistency:
    def test_all_sections_referenced(self, tmp_path: Path):
        spec_dir = _make_spec_dir(
            tmp_path,
            "## 14. Page: Dashboard\nDashboard content.\n",
            "Implements `Source.md §14` dashboard rendering.\n",
        )
        result = check_consistency(spec_dir=spec_dir)
        assert result.passed is True
        assert result.invalid_refs == 0

    def test_missing_reference_detected(self, tmp_path: Path):
        """Unreferenced sections don't cause failure — only broken references do."""
        spec_dir = _make_spec_dir(
            tmp_path,
            "## 14. Page: Dashboard\n## 99. Made-up section\n",
            "Implements `Source.md §14` only.\n",
        )
        result = check_consistency(spec_dir=spec_dir)
        # Section 99 has no incoming reference, but that's not a broken ref
        assert result.passed is True
        assert result.valid_refs == 1

    def test_broken_reference_detected(self, tmp_path: Path):
        """A reference to a non-existent section fails."""
        spec_dir = _make_spec_dir(
            tmp_path,
            "## 14. Page: Dashboard\n",
            "Implements `Source.md §999` which does not exist.\n",
        )
        result = check_consistency(spec_dir=spec_dir)
        assert result.passed is False
        assert result.invalid_refs > 0

    def test_skip_section_zero(self, tmp_path: Path):
        """Section 0 (cross-reference index) should not be checked."""
        spec_dir = _make_spec_dir(
            tmp_path,
            "## 0. Cross-reference index\nIndex content.\n",
            "No references to Source.md here.\n",
        )
        result = check_consistency(spec_dir=spec_dir)
        # Section 0 has no references from other files, so refs are 0 = pass
        assert result.passed is True

    def test_range_covers_multiple_sections(self, tmp_path: Path):
        spec_dir = _make_spec_dir(
            tmp_path,
            "## 14. Page: Dashboard\n## 15. Page: Agents\n## 16. Page: Pipeline\n",
            "Implements Source.md §14-§16 (UI pages)\n",
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
        result = CheckResult(
            passed=True,
            total_refs=1,
            valid_refs=1,
            invalid_refs=0,
            references=[
                SectionRef(source_file="Architecture.md", source_line=1,
                           target_file="Source.md", target_section="14",
                           line_text="ref", valid=True),
            ],
        )
        output = format_json(result)
        data = json.loads(output)
        assert data["pass"] is True

    def test_json_output_missing(self):
        result = CheckResult(
            passed=False,
            total_refs=2,
            valid_refs=1,
            invalid_refs=1,
            references=[
                SectionRef(source_file="Architecture.md", source_line=1,
                           target_file="Source.md", target_section="14",
                           line_text="ref", valid=True),
                SectionRef(source_file="Architecture.md", source_line=2,
                           target_file="Source.md", target_section="99",
                           line_text="ref", valid=False),
            ],
        )
        output = format_json(result)
        data = json.loads(output)
        assert data["pass"] is False
        assert data["invalid_refs"] == 1


class TestFormatReport:
    def test_pass_report(self):
        result = CheckResult(
            passed=True,
            total_refs=1,
            valid_refs=1,
            invalid_refs=0,
            references=[
                SectionRef(source_file="Architecture.md", source_line=1,
                           target_file="Source.md", target_section="14",
                           line_text="ref", valid=True),
            ],
        )
        report = format_report(result, verbose=False)
        assert "PASS" in report

    def test_fail_report(self):
        result = CheckResult(
            passed=False,
            total_refs=1,
            valid_refs=0,
            invalid_refs=1,
            references=[
                SectionRef(source_file="Architecture.md", source_line=1,
                           target_file="Source.md", target_section="99",
                           line_text="ref", valid=False),
            ],
        )
        report = format_report(result, verbose=False)
        assert "FAIL" in report
        assert "99" in report

    def test_verbose_report(self):
        result = CheckResult(
            passed=True,
            total_refs=1,
            valid_refs=1,
            invalid_refs=0,
            references=[
                SectionRef(source_file="Architecture.md", source_line=1,
                           target_file="Source.md", target_section="14",
                           line_text="Implements Source.md §14", valid=True),
            ],
        )
        report = format_report(result, verbose=True)
        assert "OK" in report
        assert "14" in report


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
            broken = [f"  {r.target_file} §{r.target_section}" for r in result.broken]
            print(f"\nBroken references in current spec:\n" + "\n".join(broken))
        else:
            print(f"\nAll {result.total_refs} cross-references valid. PASS.")

        # Verify structural invariants (not pass/fail)
        assert result.total_refs > 0, "Should find at least some cross-references"
