#!/usr/bin/env python3
"""Cross-file reference checker for PMACS spec files.

Verifies that every operator-facing section in spec/Source.md has at least one
back-reference in spec/Architecture.md. Ensures the spec stays internally
consistent as changes are made.

Usage:
    python ops/spec_consistency.py [--spec-dir DIR] [--verbose] [--json]

Exit codes:
    0 = all sections referenced (PASS)
    1 = one or more sections unreferenced (FAIL)
    2 = error (spec files not found, parse failure)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Sections that are structural/meta/descriptive — not implementation promises.
SKIP_SECTIONS: set[int] = {
    0,   # Cross-reference index
    2,   # Operator persona — descriptive, not an implementation promise
    11,  # Failure modes operator accepts — design philosophy, not code
    23,  # First 30 days — onboarding narrative, not implementation spec
    24,  # Backup, recovery, multi-machine — operational guide (docs/operator_runbook.md)
    25,  # Versioning and updates — operational procedure
    26,  # Out of scope (v1) — explicitly excluded from implementation
    27,  # Glossary — reference material
    28,  # Connection to companion files — meta/structural
}


@dataclass
class SectionCheck:
    """Result of checking a single Source.md section."""

    section: int
    title: str
    referenced: bool
    reference_lines: list[str] = field(default_factory=list)


@dataclass
class ConsistencyResult:
    """Overall result of the consistency check."""

    passed: bool
    source_sections: list[SectionCheck] = field(default_factory=list)

    @property
    def missing(self) -> list[SectionCheck]:
        return [s for s in self.source_sections if not s.referenced]


def find_project_root() -> Path:
    """Walk upward from cwd to find the directory containing spec/Source.md."""
    current = Path.cwd()
    for _ in range(20):
        candidate = current / "spec" / "Source.md"
        if candidate.is_file():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return Path.cwd()


def parse_section_numbers(source_text: str) -> dict[int, str]:
    """Parse Source.md and return {section_number: section_title}.

    Looks for lines matching ``## N. Title`` or ``### N.N Title``.
    Only top-level numbered sections (## N) are collected as primary keys.
    """
    sections: dict[int, str] = {}
    # Match ## N. or ## N — top-level numbered sections
    pattern = re.compile(r"^##\s+(\d+)\.\s+(.+)$", re.MULTILINE)
    for match in pattern.finditer(source_text):
        num = int(match.group(1))
        title = match.group(2).strip()
        sections[num] = title
    return sections


def parse_sub_section_numbers(source_text: str) -> dict[str, str]:
    """Parse sub-sections like ### 7.1, 7.2 for richer reporting."""
    subs: dict[str, str] = {}
    pattern = re.compile(r"^###\s+(\d+\.\d+)\s+(.+)$", re.MULTILINE)
    for match in pattern.finditer(source_text):
        key = match.group(1)
        title = match.group(2).strip()
        subs[key] = title
    return subs


def find_back_references(arch_text: str) -> dict[int, list[str]]:
    """Parse Architecture.md for back-references to Source.md sections.

    Handles:
    - ``Source.md §14`` (single)
    - ``Source.md §14-§20`` (range)
    - ``Source.md §7.2`` (sub-section, counts as reference to §7)
    """
    refs: dict[int, list[str]] = {}
    # Track span of every range match so singles can skip overlapping regions
    range_spans: list[tuple[int, int, str]] = []

    # Range references: Source.md §14-§20 or Source.md §14-20
    range_pattern = re.compile(
        r"Source\.md\s*§\s*(\d+)\s*[-–]\s*§?\s*(\d+)"
    )
    for match in range_pattern.finditer(arch_text):
        start, end = int(match.group(1)), int(match.group(2))
        line_start = arch_text.rfind("\n", 0, match.start()) + 1
        line_end = arch_text.find("\n", match.end())
        line = arch_text[line_start:line_end].strip()
        for num in range(start, end + 1):
            refs.setdefault(num, []).append(line)
        range_spans.append((match.start(), match.end(), line))

    # Single references: Source.md §N or Source.md §N.M
    # Also handles table format: `Source.md` | §N (pipe-separated)
    single_pattern = re.compile(r"Source\.md[\s`|]*§\s*(\d+)(?:\.\d+)?")
    for match in single_pattern.finditer(arch_text):
        # Skip if this match falls inside a range match
        if any(rs <= match.start() < re for rs, re, _ in range_spans):
            continue
        num = int(match.group(1))
        line_start = arch_text.rfind("\n", 0, match.start()) + 1
        line_end = arch_text.find("\n", match.end())
        line = arch_text[line_start:line_end].strip()
        refs.setdefault(num, []).append(line)

    return refs


def check_consistency(
    spec_dir: Path | None = None,
) -> ConsistencyResult:
    """Run the full consistency check.

    Parameters
    ----------
    spec_dir
        Path to the directory containing Source.md and Architecture.md.
        Defaults to ``<project_root>/spec/``.

    Returns
    -------
    ConsistencyResult with pass/fail status and per-section details.
    """
    if spec_dir is None:
        root = find_project_root()
        spec_dir = root / "spec"

    source_path = spec_dir / "Source.md"
    arch_path = spec_dir / "Architecture.md"

    if not source_path.is_file():
        print(f"ERROR: {source_path} not found", file=sys.stderr)
        sys.exit(2)
    if not arch_path.is_file():
        print(f"ERROR: {arch_path} not found", file=sys.stderr)
        sys.exit(2)

    source_text = source_path.read_text(encoding="utf-8")
    arch_text = arch_path.read_text(encoding="utf-8")

    sections = parse_section_numbers(source_text)
    back_refs = find_back_references(arch_text)

    results: list[SectionCheck] = []
    for num in sorted(sections.keys()):
        title = sections[num]
        if num in SKIP_SECTIONS:
            continue
        ref_lines = back_refs.get(num, [])
        results.append(
            SectionCheck(
                section=num,
                title=title,
                referenced=len(ref_lines) > 0,
                reference_lines=ref_lines,
            )
        )

    passed = all(r.referenced for r in results)
    return ConsistencyResult(passed=passed, source_sections=results)


def format_report(result: ConsistencyResult, *, verbose: bool = False) -> str:
    """Format a human-readable report."""
    lines: list[str] = []
    status = "PASS" if result.passed else "FAIL"
    lines.append(f"Spec Consistency Check: {status}")
    lines.append("")

    total = len(result.source_sections)
    referenced = sum(1 for s in result.source_sections if s.referenced)
    lines.append(f"Sections checked: {total}")
    lines.append(f"Referenced:       {referenced}")
    lines.append(f"Missing:          {total - referenced}")
    lines.append("")

    if result.missing:
        lines.append("Unreferenced Source.md sections:")
        for s in result.missing:
            lines.append(f"  - section {s.section}: {s.title}")
        lines.append("")

    if verbose:
        lines.append("Detailed results:")
        for s in result.source_sections:
            marker = "OK" if s.referenced else "MISSING"
            lines.append(f"  [{marker}] section {s.section}: {s.title}")
            if s.reference_lines:
                # Show up to 3 reference lines
                for ref in s.reference_lines[:3]:
                    lines.append(f"         -> {ref[:100]}")
                if len(s.reference_lines) > 3:
                    lines.append(
                        f"         ... and {len(s.reference_lines) - 3} more"
                    )
        lines.append("")

    return "\n".join(lines)


def format_json(result: ConsistencyResult) -> str:
    """Format results as JSON for CI consumption."""
    data = {
        "pass": result.passed,
        "total": len(result.source_sections),
        "referenced": sum(1 for s in result.source_sections if s.referenced),
        "missing": len(result.missing),
        "results": [
            {
                "section": s.section,
                "title": s.title,
                "referenced": s.referenced,
                "reference_count": len(s.reference_lines),
                "references": s.reference_lines[:5],
            }
            for s in result.source_sections
        ],
    }
    return json.dumps(data, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check cross-references between Source.md and Architecture.md"
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
        help="Print each check result with reference details",
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
