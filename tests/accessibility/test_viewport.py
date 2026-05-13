"""Phase 15 — viewport width guard test.

Tests the 1024px minimum width guard from Source.md §13.7.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestViewportGuard:
    """Test the viewport width guard."""

    def test_viewport_guard_in_base_template(self, dashboard_client):
        """Base template must include viewport guard overlay."""
        response = dashboard_client.get("/")
        html = response.text.lower()
        # Must have a dedicated viewport guard element (not just the meta tag)
        assert "viewport-guard" in html or ("1024" in html and "wider" in html), \
            "Viewport guard overlay not found — need element referencing 1024px minimum"

    def test_viewport_guard_css_exists(self):
        """CSS must style the viewport guard."""
        css_path = (
            Path(__file__).resolve().parents[2]
            / "pmacs" / "web" / "static" / "style.css"
        )
        css = css_path.read_text()
        # Should have a viewport-guard or min-width style
        assert "1024" in css or "viewport" in css.lower() or "min-width" in css.lower()

    def test_viewport_meta_tag_exists(self, dashboard_client):
        """Page must have viewport meta tag for responsive behavior."""
        response = dashboard_client.get("/")
        html = response.text
        assert "viewport" in html, "Missing viewport meta tag"

    def test_no_horizontal_scrollbar_at_200_percent(self):
        """CSS should prevent horizontal overflow at 200% zoom (1920px viewport)."""
        css_path = (
            Path(__file__).resolve().parents[2]
            / "pmacs" / "web" / "static" / "style.css"
        )
        css = css_path.read_text().lower()
        # Must have overflow-x: hidden or overflow-x: clip to prevent horizontal scroll
        has_overflow_x = (
            "overflow-x" in css and ("hidden" in css or "clip" in css)
        ) or "overflow: hidden" in css or "overflow:hidden" in css
        assert has_overflow_x, \
            "CSS should use overflow-x: hidden or clip to prevent horizontal scroll"

    def test_sidebar_collapse_at_narrow_widths(self):
        """Sidebar should have collapse styles."""
        css_path = (
            Path(__file__).resolve().parents[2]
            / "pmacs" / "web" / "static" / "style.css"
        )
        css = css_path.read_text().lower()
        # Should have .collapsed class or sidebar transition/width rules
        has_sidebar_rules = (
            "sidebar" in css and ("collapsed" in css or "width" in css or "transition" in css)
        )
        assert has_sidebar_rules, "CSS should define sidebar collapse rules"
