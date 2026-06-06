#!/usr/bin/env python3
"""Cross-file reference checker for all 4 PMACS spec files.

Verifies that section cross-references (File.md SSN) point to valid sections.
Checks Source.md <-> Architecture.md, Agents.md, Phases.md cross-references.
Reports broken or dangling references.

Usage:
    python ops/spec_consistency.py                  # Full check
    python ops/spec_consistency.py --spec-dir DIR   # Custom spec directory
    python ops/spec_consistency.py --verbose         # Detailed output
    python ops/spec_consistency.py --json            # JSON for CI

Exit codes:
    0 = all references valid (PASS)
    1 = broken references found (FAIL)
    2 = error (spec files not found)

Spec ref: Phases S15, Architecture.md S0
Item: 15.8
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# The 4 canonical spec files
SPEC_FILES = [
    "Source.md",
    "Architecture.md",
    "Agents.md",
    "Phases.md",
]


@dataclass
class SectionRef:
    """A cross-reference found in a spec file."""

    source_file: str       # File containing the reference
    source_line: int       # Line number where the reference appears
    target_file: str       # Referenced file name (e.g., "Architecture.md")
    target_section: str    # Referenced section (e.g., "4.1" or "14")
    line_text: str         # The full line text (truncated)
    valid: bool = False    # Whether the target section exists


@dataclass
class CheckResult:
    """Overall result of the consistency check."""

    passed: bool
    total_refs: int = 0
    valid_refs: int = 0
    invalid_refs: int = 0
    references: list[SectionRef] = field(default_factory=list)
    sections_by_file: dict[str, set[str]] = field(default_factory=dict)

    @property
    def broken(self) -> list[SectionRef]:
        return [r for r in self.references if not r.valid]


def find_project_root() -> Path:
    """Walk upward from cwd to find directory containing spec/Source.md."""
    current = Path.cwd()
    for _ in range(20):
        if (current / "spec" / "Source.md").is_file():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return Path.cwd()


def parse_sections(text: str) -> set[str]:
    """Extract all section numbers from a spec file.

    Matches both top-level (## N.) and sub-section (### N.N, #### N.N.N).
    Returns a set of section strings like {"1", "1.1", "1.1.3", "14", "14.2"}.
    """
    sections: set[str] = set()

    # Top-level: ## N. Title or ## N Title
    for m in re.finditer(r"^##\s+(\d+)[.\s]", text, re.MULTILINE):
        sections.add(m.group(1))

    # Sub-section: ### N.N or ### N.N.N
    for m in re.finditer(r"^###\s+(\d+\.\d+(?:\.\d+)?)", text, re.MULTILINE):
        sections.add(m.group(1))

    # Sub-sub-section: #### N.N.N
    for m in re.finditer(r"^####\s+(\d+\.\d+\.\d+)", text, re.MULTILINE):
        sections.add(m.group(1))

    return sections


def extract_cross_references(text: str, source_file: str) -> list[SectionRef]:
    """Find all cross-references in the form File.md SSN or File.md SSN.N.

    Handles:
    - Architecture.md S4.1
    - Source.md S14-S20 (range -- expand to individual sections)
    - Agents.md S17.4
    - Phases.md S2
    - Also pipe-separated: Architecture.md | S4
    """
    refs: list[SectionRef] = []

    # Range references: File.md SSN-SSM  or  File.md SSN - SSM
    range_pattern = re.compile(
        r"(\w+\.md)\s*§\s*(\d+(?:\.\d+)?)\s*[-–]\s*§?\s*(\d+(?:\.\d+)?)"
    )
    range_spans: list[tuple[int, int]] = []

    for match in range_pattern.finditer(text):
        target_file = match.group(1)
        start = match.group(2)
        end = match.group(3)
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        line_text = text[line_start:line_end].strip()[:120]
        line_num = text[:match.start()].count("\n") + 1

        # Expand range -- only integer top-level sections
        try:
            s = int(start.split(".")[0])
            e = int(end.split(".")[0])
            for n in range(s, e + 1):
                refs.append(SectionRef(
                    source_file=source_file,
                    source_line=line_num,
                    target_file=target_file,
                    target_section=str(n),
                    line_text=line_text,
                ))
        except ValueError:
            refs.append(SectionRef(
                source_file=source_file,
                source_line=line_num,
                target_file=target_file,
                target_section=start,
                line_text=line_text,
            ))

        range_spans.append((match.start(), match.end()))

    # Single references: File.md SSN or File.md SSN.N or File.md SSN.N.N
    single_pattern = re.compile(
        r"(\w+\.md)\s*§\s*(\d+(?:\.\d+)*(?:\.\d+)?)"
    )
    for match in single_pattern.finditer(text):
        # Skip if inside a range match
        if any(rs <= match.start() < re for rs, re in range_spans):
            continue

        target_file = match.group(1)
        target_section = match.group(2)
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        line_text = text[line_start:line_end].strip()[:120]
        line_num = text[:match.start()].count("\n") + 1

        refs.append(SectionRef(
            source_file=source_file,
            source_line=line_num,
            target_file=target_file,
            target_section=target_section,
            line_text=line_text,
        ))

    return refs


def check_consistency(spec_dir: Path | None = None) -> CheckResult:
    """Run the full consistency check across all 4 spec files."""
    if spec_dir is None:
        root = find_project_root()
        spec_dir = root / "spec"

    # Validate spec files exist
    file_contents: dict[str, str] = {}
    for fname in SPEC_FILES:
        fpath = spec_dir / fname
        if not fpath.is_file():
            print(f"ERROR: {fpath} not found", file=sys.stderr)
            sys.exit(2)
        file_contents[fname] = fpath.read_text(encoding="utf-8")

    # Parse all sections from each file
    sections_by_file: dict[str, set[str]] = {}
    for fname, text in file_contents.items():
        sections_by_file[fname] = parse_sections(text)

    # Extract all cross-references from each file
    all_refs: list[SectionRef] = []
    for fname, text in file_contents.items():
        all_refs.extend(extract_cross_references(text, fname))

    # Validate each reference
    for ref in all_refs:
        target_sections = sections_by_file.get(ref.target_file)
        if target_sections is None:
            # Unknown file -- this is a reference to a non-spec file, skip
            ref.valid = False
            continue

        # Check if section exists (exact match or parent section exists)
        if ref.target_section in target_sections:
            ref.valid = True
        else:
            # Check parent section (e.g., "4.1" -> check if "4" exists)
            parent = ref.target_section.split(".")[0]
            ref.valid = parent in target_sections

    valid_count = sum(1 for r in all_refs if r.valid)
    invalid_count = len(all_refs) - valid_count

    return CheckResult(
        passed=invalid_count == 0,
        total_refs=len(all_refs),
        valid_refs=valid_count,
        invalid_refs=invalid_count,
        references=all_refs,
        sections_by_file=sections_by_file,
    )


def format_report(result: CheckResult, *, verbose: bool = False) -> str:
    """Format a human-readable report."""
    lines: list[str] = []
    status = "PASS" if result.passed else "FAIL"
    lines.append(f"Spec Consistency Check: {status}")
    lines.append("")

    lines.append(f"Total cross-references: {result.total_refs}")
    lines.append(f"Valid references:       {result.valid_refs}")
    lines.append(f"Broken references:      {result.invalid_refs}")
    lines.append("")

    # Per-file section counts
    lines.append("Sections found per file:")
    for fname in SPEC_FILES:
        count = len(result.sections_by_file.get(fname, set()))
        lines.append(f"  {fname:20s} {count} sections")
    lines.append("")

    broken = result.broken
    if broken:
        lines.append("BROKEN REFERENCES:")
        for ref in broken:
            lines.append(
                f"  {ref.source_file}:{ref.source_line} -> "
                f"{ref.target_file} §{ref.target_section}"
            )
            lines.append(f"    {ref.line_text}")
        lines.append("")

    if verbose:
        lines.append("All cross-references:")
        for ref in result.references:
            marker = "OK" if ref.valid else "BROKEN"
            lines.append(
                f"  [{marker:6s}] {ref.source_file}:{ref.source_line} -> "
                f"{ref.target_file} §{ref.target_section}"
            )
        lines.append("")

    return "\n".join(lines)


def format_json(result: CheckResult) -> str:
    """Format results as JSON for CI consumption."""
    data = {
        "pass": result.passed,
        "total_refs": result.total_refs,
        "valid_refs": result.valid_refs,
        "invalid_refs": result.invalid_refs,
        "sections_per_file": {
            fname: sorted(sections, key=lambda s: [int(p) for p in s.split(".")])
            for fname, sections in result.sections_by_file.items()
        },
        "broken_references": [
            {
                "source_file": ref.source_file,
                "source_line": ref.source_line,
                "target_file": ref.target_file,
                "target_section": ref.target_section,
                "line_text": ref.line_text,
            }
            for ref in result.broken
        ],
    }
    return json.dumps(data, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check cross-references between all 4 PMACS spec files"
    )
    parser.add_argument(
        "--spec-dir",
        type=Path,
        default=None,
        help="Path to spec directory (default: auto-detect from cwd)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print every reference with validity status",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON results for CI pipelines",
    )
    args = parser.parse_args()

    result = check_consistency(spec_dir=args.spec_dir)

    if args.json:
        print(format_json(result))
    else:
        print(format_report(result, verbose=args.verbose))

    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
