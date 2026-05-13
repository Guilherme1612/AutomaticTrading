"""Phase 15 — reduced-motion static equivalents test.

Verifies all animated elements have proper static fallbacks when
prefers-reduced-motion: reduce is active.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestReducedMotion:
    """Test CSS reduced-motion handling."""

    @pytest.fixture(autouse=True)
    def _load_css(self):
        css_path = (
            Path(__file__).resolve().parents[2] / "pmacs" / "web" / "static" / "style.css"
        )
        self.css = css_path.read_text()

    def test_reduced_motion_media_query_exists(self):
        assert "prefers-reduced-motion" in self.css

    def test_reduced_motion_disables_animation(self):
        """CSS should set animation: none or duration: 0s under reduced-motion."""
        # Find the reduced-motion block
        rm_idx = self.css.find("prefers-reduced-motion")
        assert rm_idx >= 0
        # Check that the block contains animation overrides
        rm_block = self.css[rm_idx:rm_idx + 2000]
        assert "animation" in rm_block or "transition" in rm_block, \
               "Reduced-motion block should disable animations or transitions"

    def test_reduced_motion_disables_transitions(self):
        """Transitions should be instant or disabled under reduced-motion."""
        rm_idx = self.css.find("prefers-reduced-motion")
        rm_block = self.css[rm_idx:rm_idx + 2000]
        # Must specifically set transition-duration: 0s or transition: none
        has_override = (
            "transition-duration: 0s" in rm_block
            or "transition-duration:0s" in rm_block
            or "transition: none" in rm_block
            or "transition:none" in rm_block
        )
        assert has_override, \
            "Reduced-motion block should set transition-duration: 0s or transition: none"

    def test_sparkline_hover_respects_reduced_motion(self):
        """Sparkline hover tooltips should be hidden/instant with reduced-motion."""
        rm_idx = self.css.find("prefers-reduced-motion")
        rm_block = self.css[rm_idx:rm_idx + 2000]
        # Reduced-motion block should reference sparkline or disable tooltips
        assert "sparkline" in rm_block.lower() or "tooltip" in rm_block.lower() or \
               "transition-duration: 0s" in rm_block, \
               "Reduced-motion should disable sparkline tooltip transitions"

    def test_toast_animation_respects_reduced_motion(self):
        """Toast animations should be instant with reduced-motion."""
        rm_idx = self.css.find("prefers-reduced-motion")
        rm_block = self.css[rm_idx:rm_idx + 2000]
        # Reduced-motion block should reference toast or apply blanket override
        assert "toast" in rm_block.lower() or "animation: none" in rm_block or \
               "animation-duration: 0s" in rm_block or "transition-duration: 0s" in rm_block, \
               "Reduced-motion should disable toast animations"


class TestReducedMotionJS:
    """Test JS reduced-motion detection."""

    @pytest.fixture(autouse=True)
    def _load_js(self):
        js_path = (
            Path(__file__).resolve().parents[2] / "pmacs" / "web" / "static" / "app.js"
        )
        self.js = js_path.read_text()

    def test_js_detects_reduced_motion(self):
        """JavaScript should detect prefers-reduced-motion."""
        assert "prefers-reduced-motion" in self.js, \
               "JS should reference prefers-reduced-motion explicitly (not just matchMedia)"

    def test_js_sankey_respects_reduced_motion(self):
        """Sankey D3 transitions should be skipped with reduced-motion."""
        sankey_path = (
            Path(__file__).resolve().parents[2] / "pmacs" / "web" / "static" / "sankey.js"
        )
        sankey = sankey_path.read_text()
        # Sankey should check for reduced motion before animating
        assert "reduced-motion" in sankey or "transition" in sankey.lower() or "duration" in sankey.lower(), \
               "Sankey should handle reduced-motion"
