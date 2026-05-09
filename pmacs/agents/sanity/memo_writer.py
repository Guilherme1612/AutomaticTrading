"""MemoWriter sanity validator — persona-specific checks (Agents.md §13).

Checks:
- verdict_line starts with STRONG_BUY / BUY / HOLD / SKIP
- key_evidence items are non-empty strings
"""

from __future__ import annotations

from typing import Any

from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult

VALID_VERDICT_PREFIXES = ("STRONG_BUY", "BUY", "HOLD", "SKIP")


class MemoWriterSanity(BaseSanityValidator):
    """Sanity validator for MemoWriter persona output."""

    def _persona_checks(
        self, output: dict[str, Any], evidence: list[Any]
    ) -> SanityResult:
        verdict_line = output.get("verdict_line", "")

        # verdict_line must start with a valid prefix
        if not verdict_line or not any(
            verdict_line.startswith(prefix) for prefix in VALID_VERDICT_PREFIXES
        ):
            return SanityResult(
                passed=False,
                reason=(
                    f"verdict_line must start with one of {VALID_VERDICT_PREFIXES}, "
                    f"got: '{verdict_line[:50]}'"
                ),
            )

        # key_evidence items must be non-empty
        key_evidence = output.get("key_evidence", [])
        for i, item in enumerate(key_evidence):
            if not item or not str(item).strip():
                return SanityResult(
                    passed=False,
                    reason=f"key_evidence[{i}] is empty",
                )

        return SanityResult(passed=True)
