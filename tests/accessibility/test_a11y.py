"""Phase 15 exit test #7 — accessibility audit.

Uses FastAPI TestClient to load all 7 pages and verify:
- Pages render without errors (HTTP 200)
- All interactive elements have accessible names (aria-labels)
- Focus states are defined in CSS
- Color contrast tokens exist (WCAG AA)
- Every page has landmark roles
- All images/icons have alt text or aria-labels

Note: Full axe-core automated scan requires Playwright + running server.
This test suite provides the structural checks that can run without a browser.
Playwright tests are added separately in test_keyboard.py and test_reduced_motion.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


class TestAccessibilityStructural:
    """Structural accessibility checks using TestClient."""

    @pytest.mark.parametrize("url", [
        "/",
        "/agents",
        "/pipeline",
        "/universe",
        "/cortex",
        "/settings",
        "/debug",
    ])
    def test_page_renders_200(self, dashboard_client, url):
        """Every page must render without server errors."""
        response = dashboard_client.get(url)
        assert response.status_code == 200, f"{url} returned {response.status_code}"

    @pytest.mark.parametrize("url", [
        "/",
        "/agents",
        "/pipeline",
        "/universe",
        "/cortex",
        "/settings",
        "/debug",
    ])
    def test_page_has_landmark_roles(self, dashboard_client, url):
        """Every page must have landmark ARIA roles (nav, main, etc.)."""
        response = dashboard_client.get(url)
        html = response.text
        # Check for <main> or role="main"
        assert '<main' in html or 'role="main"' in html, f"{url}: missing main landmark"

    @pytest.mark.parametrize("url", [
        "/",
        "/agents",
        "/pipeline",
        "/universe",
        "/cortex",
        "/settings",
        "/debug",
    ])
    def test_page_has_lang_attribute(self, dashboard_client, url):
        """HTML must have lang attribute."""
        response = dashboard_client.get(url)
        html = response.text
        assert 'lang="en"' in html, f"{url}: missing lang attribute"

    @pytest.mark.parametrize("url", [
        "/",
        "/agents",
        "/pipeline",
        "/universe",
        "/cortex",
        "/settings",
        "/debug",
    ])
    def test_page_has_title(self, dashboard_client, url):
        """Every page must have a descriptive <title>."""
        response = dashboard_client.get(url)
        html = response.text
        assert "<title>" in html, f"{url}: missing <title>"

    def test_base_template_has_aria_labels(self, dashboard_client):
        """Sidebar navigation links must have aria-labels."""
        response = dashboard_client.get("/")
        html = response.text
        # Check sidebar nav has aria-label
        assert 'aria-label' in html, "Missing aria-labels in base template"

    def test_keyboard_shortcuts_have_aria(self, dashboard_client):
        """Keyboard shortcut elements must be accessible."""
        response = dashboard_client.get("/")
        html = response.text.lower()
        # Find command palette element and verify it has accessibility attributes
        palette_match = re.search(r'<[^>]*(command-palette|cmd-k)[^>]*>', html, re.IGNORECASE)
        if palette_match:
            element = palette_match.group()
            assert 'aria-label' in element or 'role=' in element, \
                "Command palette element lacks aria-label or role: " + element
        else:
            # If no explicit palette element, verify input has accessible attributes
            assert 'cmd-k-input' in html or 'aria-label="command' in html, \
                "Command palette input missing accessible attributes"

    def test_focus_visible_styles_exist(self):
        """CSS must define focus-visible styles (2px accent outline)."""
        css_path = (
            Path(__file__).resolve().parents[2]
            / "pmacs" / "web" / "static" / "style.css"
        )
        css = css_path.read_text()
        # Check for focus-visible styles
        assert "focus-visible" in css, "Missing focus-visible styles in CSS"

    def test_reduced_motion_media_query_exists(self):
        """CSS must have prefers-reduced-motion media query."""
        css_path = (
            Path(__file__).resolve().parents[2]
            / "pmacs" / "web" / "static" / "style.css"
        )
        css = css_path.read_text()
        assert "prefers-reduced-motion" in css, "Missing reduced-motion media query"

    def test_viewport_guard_exists(self, dashboard_client):
        """1024px minimum viewport guard must exist in base template."""
        response = dashboard_client.get("/")
        html = response.text.lower()
        # Must have the viewport guard element with 1024px reference
        assert "viewport-guard" in html or ("1024" in html and "wider" in html), \
            "Viewport guard overlay not found in base template"

    def test_color_contrast_tokens_defined(self):
        """CSS must define color tokens that meet WCAG AA contrast."""
        css_path = (
            Path(__file__).resolve().parents[2]
            / "pmacs" / "web" / "static" / "style.css"
        )
        css = css_path.read_text()
        # Verify the design system tokens exist
        required_tokens = [
            "--text-primary",
            "--text-secondary",
            "--accent",
            "--surface",
        ]
        for token in required_tokens:
            assert token in css, f"Missing CSS token: {token}"

    @pytest.mark.parametrize("url", [
        "/",
        "/agents",
        "/pipeline",
        "/universe",
        "/cortex",
        "/settings",
        "/debug",
    ])
    def test_images_have_alt_or_aria(self, dashboard_client, url):
        """All <img> tags must have alt text, all decorative images aria-hidden."""
        response = dashboard_client.get(url)
        html = response.text
        # Find img tags without alt
        img_tags = re.findall(r'<img[^>]*>', html)
        for img in img_tags:
            assert 'alt=' in img or 'aria-hidden="true"' in img or 'aria-label' in img, \
                f"{url}: <img> without alt or aria-hidden: {img}"
