"""Drift guard: Agents communication-layer tab labels must agree between spec
(Source.md §15.5) and implementation (agents.html). Web review Finding G.

Earlier drafts named the toggle Process / Network / Math; the shipped UI is
Process / Signals / Conviction, which carries the same substance (Signals = the
Sankey "Network" view; Conviction = the step-by-step "Math" formula breakdown).
Spec §15.5 was retitled to match the implementation (see the "Label reconciliation"
note there). This test pins the agreement so label/implementation drift (the kind
documented in spec_drift_jun16) cannot recur silently.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENTS_HTML = ROOT / "pmacs" / "web" / "templates" / "agents.html"
SOURCE_MD = ROOT / "spec" / "Source.md"

# The canonical tab labels (implementation = spec).
TABS = ("Process", "Signals", "Conviction")


def _tab_buttons(html: str) -> list[str]:
    """Extract the comm-layer toggle button labels, in order."""
    buttons = re.findall(r'data-comm-view="(\w+)"[^>]*aria-pressed="(?:true|false)">(\w+)</button>', html)
    return [label for _view, label in buttons]


def test_agents_html_has_the_three_canonical_tabs():
    html = AGENTS_HTML.read_text(encoding="utf-8")
    labels = _tab_buttons(html)
    assert labels == list(TABS), (
        f"agents.html comm-layer tabs are {labels}; spec §15.5 says {list(TABS)}"
    )


def test_spec_155_matches_implementation_labels():
    """Spec §15.5 must name Process / Signals / Conviction (not Network / Math)."""
    src = SOURCE_MD.read_text(encoding="utf-8")
    section = src[src.index("### 15.5 Communication layer visualization"):src.index("### 15.6")]
    assert "**Process** / **Signals** / **Conviction**" in section, (
        "spec §15.5 toggle is not Process / Signals / Conviction — spec/impl drift"
    )
    # And must NOT carry the old Process / Network / Math toggle as the primary label.
    assert "**Process** / **Network** / **Math**" not in section, (
        "spec §15.5 still lists the old Process / Network / Math toggle — retitle to match impl"
    )


def test_spec_155_documents_the_label_reconciliation():
    """The mapping (Signals=Network, Conviction=Math) must be recorded so the
    rename is not mistaken for dropped substance."""
    src = SOURCE_MD.read_text(encoding="utf-8")
    section = src[src.index("### 15.5 Communication layer visualization"):src.index("### 15.6")]
    assert "Signals" in section and "Network" in section, "§15.5 missing the Signals↔Network mapping"
    assert "Conviction" in section and "Math" in section, "§15.5 missing the Conviction↔Math mapping"


def test_conviction_tab_shows_formula_breakdown():
    """The 'Math' substance (step-by-step conviction formula) must actually be in
    the Conviction tab — each factor with its formula caption."""
    html = AGENTS_HTML.read_text(encoding="utf-8")
    assert "p_up − p_down" in html, "Conviction tab missing Direction formula caption"
    assert "1 − sev" in html, "Conviction tab missing Crucible formula caption"
    assert "ev / 1.5" in html, "Conviction tab missing EV-factor formula caption"
    assert "HOLD ≥ 0.05" in html and "STRONG_BUY ≥ 0.40" in html, (
        "Conviction tab missing the bootstrap tier thresholds"
    )


def test_signals_tab_has_sankey_and_vote_table():
    """The 'Network' substance (Sankey + per-agent vote table) must be in Signals."""
    html = AGENTS_HTML.read_text(encoding="utf-8")
    assert "sankey-data" in html, "Signals tab missing the Sankey data endpoint"
    assert 'id="signals-table"' in html, "Signals tab missing the per-agent vote table"
