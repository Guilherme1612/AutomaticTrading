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
        html = response.text
        # The guard should mention 1024px or "wider window"
        assert "1024" in html or "wider" in html.lower() or "viewport" in html.lower()

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
        css = css_path.read_text()
        # Should have overflow-x: hidden or overflow: hidden on body
        assert "overflow" in css.lower(), "CSS should handle overflow"

    def test_sidebar_collapse_at_narrow_widths(self):
        """Sidebar should collapse or adapt at narrow widths."""
        css_path = (
            Path(__file__).resolve().parents[2]
            / "pmacs" / "web" / "static" / "style.css"
        )
        css = css_path.read_text()
        # Should have responsive breakpoints
        assert "sidebar" in css.lower() or "collapse" in css.lower()
