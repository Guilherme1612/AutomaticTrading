"""Unit tests for /agents page template (Source.md §15.5).

Verifies the Jun 29 /agents-page audit fixes:

1. The {% block scripts %} override loads /static/sankey.js so the
   Communication Layer "Signals" tab renders the Sankey diagram. The
   old template never overrode the block, so the script tag was missing
   and d3-sankey never initialized.

2. Hero ticker truncation: long ticker symbols must not overflow the
   flex-col parent. Static SSR'd spans get ``truncate max-w-[10rem]``
   to match the inline-JS dynamic update behavior.

3. CITATION_GAP warning chip: when a persona card is complete but has
   no narrative (key_signal / analysis / evidence_cited all empty), the
   card shows an amber "Audit: no narrative" chip so the operator sees
   WHY the card body is empty without diving into the audit log.
"""

from __future__ import annotations

from pathlib import Path

import pytest


TEMPLATE_PATH = Path(__file__).parent.parent.parent / "pmacs/web/templates/agents.html"


# ─── Sankey.js script tag (1a) ─────────────────────────────────────────────


def test_agents_template_loads_sankey_script():
    """The agents template must override {% block scripts %} with sankey.js."""
    content = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "{% block scripts %}" in content, (
        "agents.html must override {% block scripts %} from base.html"
    )
    assert "/static/sankey.js" in content, (
        "agents.html must include <script src=/static/sankey.js> to drive the Signals tab"
    )


def test_agents_template_sankey_script_deferred():
    """The sankey script must defer so it doesn't block first paint."""
    content = TEMPLATE_PATH.read_text(encoding="utf-8")
    # Find the line with /static/sankey.js
    for line in content.splitlines():
        if "/static/sankey.js" in line:
            assert "defer" in line, f"sankey.js script must defer: {line!r}"
            break
    else:
        pytest.fail("sankey.js script tag not found")


def test_agents_template_sankey_in_scripts_block_not_content_block():
    """Sankey.js must be inside {% block scripts %}, not the content block.

    base.html has both {% block content %} and {% block scripts %}. Putting
    the script in the content block loads it inside <main> which is wrong.
    """
    content = TEMPLATE_PATH.read_text(encoding="utf-8")
    scripts_block_start = content.find("{% block scripts %}")
    content_block_start = content.find("{% block content %}")
    scripts_block_end = content.find("{% endblock %}", scripts_block_start)

    sankey_pos = content.find("/static/sankey.js")
    assert sankey_pos > scripts_block_start, (
        "sankey.js must be inside the {% block scripts %} block"
    )
    assert sankey_pos < scripts_block_end, (
        "sankey.js must be inside the {% block scripts %} block"
    )


# ─── Hero ticker truncation (1d) ──────────────────────────────────────────


def test_agents_template_hero_ticker_has_truncate_class():
    """The SSR'd hero ticker spans must have truncate max-w-[10rem]."""
    content = TEMPLATE_PATH.read_text(encoding="utf-8")
    # The 3 hero ticker spans: current_ticker conditional (if/elif/else)
    for line in content.splitlines():
        if 'id="current-ticker"' in line:
            assert "truncate" in line, f"hero ticker span must have truncate class: {line!r}"
            assert "max-w-[10rem]" in line, f"hero ticker span must constrain width: {line!r}"


# ─── CITATION_GAP warning chip (1e) ───────────────────────────────────────


def test_agents_template_has_audit_failure_chip():
    """The persona card template must render a CITATION_GAP chip when
    the persona is complete but produced no narrative."""
    content = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "Audit: no narrative" in content, (
        "agents.html must render an amber chip when status='complete' "
        "but no narrative fields are populated"
    )


def test_audit_failure_chip_only_renders_on_complete_with_no_narrative():
    """The chip condition must check status=='complete' AND no narrative."""
    content = TEMPLATE_PATH.read_text(encoding="utf-8")
    # Find the chip block
    chip_start = content.find('Audit: no narrative')
    # Walk backward to find the {% if %} that gates it
    chip_if = content.rfind("{% if", 0, chip_start)
    chip_block = content[chip_if:chip_start]
    assert "complete" in chip_block, "chip must only render when status=='complete'"
    assert "not (persona.key_signal or persona.analysis or persona.evidence_cited)" in chip_block, (
        "chip must check that no narrative fields are populated"
    )


# ─── Sankey DOM dependencies ──────────────────────────────────────────────


def test_agents_template_has_d3_loaded_via_base():
    """d3.min.js is loaded in base.html — verify base.html still loads it."""
    base_path = TEMPLATE_PATH.parent / "base.html"
    base_content = base_path.read_text(encoding="utf-8")
    assert "/static/vendor/d3.min.js" in base_content, (
        "base.html must continue to load d3.min.js (sankey.js depends on it)"
    )


# ─── Defensive: rr_ratio template guard (covered in memo.html) ────────────


def test_agents_template_does_not_depend_on_rr_ratio():
    """rr_ratio is a memo-page concept; agents.html should not reference it."""
    content = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "rr_ratio" not in content, (
        "agents.html should not reference rr_ratio (memo page only)"
    )
