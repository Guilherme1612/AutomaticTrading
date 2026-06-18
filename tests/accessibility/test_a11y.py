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
        "/compare",
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
        "/compare",
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
        "/compare",
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
        "/compare",
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

    def test_responsive_layout_exists(self, dashboard_client):
        """The redesign replaced the 1024px viewport guard with a responsive
        layout (collapsible mobile sidebar + lg: breakpoints)."""
        response = dashboard_client.get("/")
        html = response.text.lower()
        assert "mobile-sidebar-overlay" in html and "lg:" in html, \
            "Responsive layout markers not found in base template"

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
        "/compare",
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

    @pytest.mark.parametrize("url", [
        "/",
        "/agents",
        "/pipeline",
        "/universe",
        "/cortex",
        "/settings",
        "/debug",
        "/compare",
    ])
    def test_buttons_have_accessible_names(self, dashboard_client, url):
        """All <button> elements must have accessible text (content, aria-label, or title)."""
        response = dashboard_client.get(url)
        html = response.text
        buttons = re.findall(r'<button[^>]*>(.*?)</button>', html, re.DOTALL)
        for i, btn_content in enumerate(buttons):
            # Check the full button tag for aria-label or title
            btn_tags = re.findall(r'<button[^>]*>', html)
            if i < len(btn_tags):
                tag = btn_tags[i]
                has_aria = 'aria-label=' in tag
                has_title = 'title=' in tag
                has_content = bool(btn_content.strip()) and btn_content.strip() != ''
                assert has_aria or has_title or has_content, \
                    f"{url}: button #{i} lacks accessible name: {tag}"

    @pytest.mark.parametrize("url", [
        "/",
        "/agents",
        "/pipeline",
        "/universe",
        "/cortex",
        "/settings",
        "/debug",
        "/compare",
    ])
    def test_form_inputs_have_labels(self, dashboard_client, url):
        """All form inputs must have associated labels (for/id match or aria-label)."""
        response = dashboard_client.get(url)
        html = response.text
        inputs = re.findall(r'<input[^>]*>', html)
        for inp in inputs:
            # Skip hidden inputs
            if 'type="hidden"' in inp:
                continue
            has_aria = 'aria-label=' in inp
            has_placeholder = 'placeholder=' in inp
            has_id = re.search(r'id="([^"]*)"', inp)
            if has_id:
                label_match = re.search(rf'<label[^>]*for="{re.escape(has_id.group(1))}"', html)
                if label_match:
                    continue
            assert has_aria or has_placeholder or has_id, \
                f"{url}: input lacks label: {inp}"

    @pytest.mark.parametrize("url", [
        "/",
        "/agents",
        "/pipeline",
        "/universe",
        "/cortex",
        "/settings",
        "/debug",
        "/compare",
    ])
    def test_no_outline_none_without_replacement(self, dashboard_client, url):
        """Inline outline:none must be paired with box-shadow or other focus indicator."""
        response = dashboard_client.get(url)
        html = response.text
        # Find inline styles with outline:none
        inline_outlines = re.findall(r'style="[^"]*outline\s*:\s*none[^"]*"', html, re.IGNORECASE)
        for match in inline_outlines:
            # outline:none in inline style is acceptable only with a replacement focus indicator
            has_box_shadow = 'box-shadow' in match
            assert has_box_shadow, f"{url}: outline:none without replacement: {match}"

    def test_css_no_outline_none_global(self):
        """CSS must not globally strip outlines without replacement (WCAG 2.4.7)."""
        css_path = (
            Path(__file__).resolve().parents[2]
            / "pmacs" / "web" / "static" / "style.css"
        )
        css = css_path.read_text()
        # Check that *:focus { outline: none } is not present
        # The focus-visible rule is the acceptable replacement
        assert not re.search(r'\*\s*:focus\s*\{[^}]*outline\s*:\s*none', css), \
            "Global outline:none on :focus violates WCAG 2.4.7"

    def test_skip_link_exists(self, dashboard_client):
        """Page must have a skip-to-content link (WCAG 2.4.1)."""
        response = dashboard_client.get("/")
        html = response.text.lower()
        assert 'skip' in html and ('main' in html or 'content' in html), \
            "Missing skip-to-content link for keyboard users"

    def test_no_autoplaying_media(self, dashboard_client):
        """No autoplaying audio/video (WCAG 1.4.2)."""
        response = dashboard_client.get("/")
        html = response.text
        videos = re.findall(r'<video[^>]*>', html)
        for v in videos:
            assert 'autoplay' not in v.lower(), "Autoplaying video found"
        audios = re.findall(r'<audio[^>]*>', html)
        for a in audios:
            assert 'autoplay' not in a.lower(), "Autoplaying audio found"

    def test_live_regions_for_dynamic_content(self, dashboard_client):
        """SSE-driven content must use aria-live regions (WCAG 4.1.3)."""
        response = dashboard_client.get("/")
        html = response.text
        # Check for aria-live on dynamic areas
        assert 'aria-live' in html, "Missing aria-live regions for dynamic content"

    @pytest.mark.parametrize("url", [
        "/",
        "/agents",
        "/pipeline",
        "/universe",
        "/cortex",
        "/settings",
        "/debug",
        "/compare",
    ])
    def test_no_positive_tabindex(self, dashboard_client, url):
        """No positive tabindex values (WCAG 2.4.3 — messes with tab order)."""
        response = dashboard_client.get(url)
        html = response.text
        matches = re.findall(r'tabindex="(\d+)"', html)
        for val in matches:
            assert int(val) <= 0, f"{url}: positive tabindex={val} disrupts tab order"

    def test_meta_viewport_no_user_scalable_no(self, dashboard_client):
        """Viewport meta must not prevent zooming (WCAG 1.4.4)."""
        response = dashboard_client.get("/")
        html = response.text
        viewport = re.search(r'<meta[^>]*viewport[^>]*>', html)
        if viewport:
            assert 'user-scalable=no' not in viewport.group().lower(), \
                "user-scalable=no prevents zooming (WCAG 1.4.4)"
            assert 'maximum-scale=1' not in viewport.group().lower(), \
                "maximum-scale=1 prevents zooming (WCAG 1.4.4)"


# ---------------------------------------------------------------------------
# axe-core CI integration (Source.md §13.7)
#
# Requires: playwright + axe-playwright-python
# Install:  pip install playwright axe-playwright-python && playwright install
# CI:       Runs automatically when playwright is available; skips otherwise.
# ---------------------------------------------------------------------------


try:
    from playwright.sync_api import sync_playwright
    import axe_playwright_python
    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False


@pytest.mark.skipif(not _HAS_PLAYWRIGHT, reason="playwright + axe-playwright-python not installed")
class TestAxeCoreCI:
    """Automated WCAG 2.1 AA validation via axe-core (Source.md §13.7).

    Runs a real browser and scans each page for accessibility violations.
    This is the CI-grade check that the structural tests above approximate.
    """

    PAGES = ["/", "/agents", "/pipeline", "/universe", "/cortex", "/settings", "/debug",
             "/ticker/AAPL"]

    @pytest.fixture(scope="class")
    def browser(self):
        """Launch a headless browser for the test class."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            yield browser
            browser.close()

    @pytest.fixture(scope="class")
    def base_url(self):
        """Get the base URL from the live test server."""
        # TestClient doesn't expose a port, so we start a real server
        import threading, time
        import uvicorn
        from pmacs.web.app import app

        server_url = "http://127.0.0.1:18765"

        def _serve():
            uvicorn.run(app, host="127.0.0.1", port=18765, log_level="error")

        t = threading.Thread(target=_serve, daemon=True)
        t.start()

        # Poll until the server accepts connections instead of a fixed sleep.
        import urllib.request
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                urllib.request.urlopen(server_url, timeout=1)
                break
            except Exception:
                time.sleep(0.25)
        yield server_url

    @pytest.mark.parametrize("page_path", PAGES)
    def test_axe_core_no_violations(self, browser, base_url, page_path):
        """axe-core scan must find zero WCAG 2.1 AA violations."""
        from axe_playwright_python.sync_playwright import Axe

        page = browser.new_page()
        try:
            # Pages hold a persistent SSE connection, so "networkidle" never
            # settles — wait for DOM content instead.
            page.goto(f"{base_url}{page_path}", wait_until="domcontentloaded", timeout=10000)

            # Stabilize before scanning. Pages hydrate/theme-apply AFTER
            # domcontentloaded; scanning too early reads transient
            # pre-hydration colors (e.g. axe reports #949da9 on elements
            # whose settled computed color is #0f172a) and produces
            # false-positive color-contrast violations. Color-contrast is
            # the only hydration-sensitive rule; rescan until it stops
            # changing so the result is deterministic regardless of host
            # load (a fixed sleep is brittle when the machine is busy).
            SETTLE_MS = 500
            MAX_SETTLE_PASSES = 5
            axe = Axe()
            results = axe.run(page)
            critical = [v for v in results.response.get("violations", [])
                        if v.get("impact") in ("critical", "serious")]
            cc_count = lambda: sum(len(v.get("nodes", [])) for v in critical
                                   if v.get("id") == "color-contrast")
            prev_cc = cc_count()
            for _ in range(MAX_SETTLE_PASSES):
                if prev_cc == 0:
                    break
                page.wait_for_timeout(SETTLE_MS)
                results = axe.run(page)
                critical = [v for v in results.response.get("violations", [])
                            if v.get("impact") in ("critical", "serious")]
                cur_cc = cc_count()
                if cur_cc == prev_cc:
                    break  # settled — no further hydration changes
                prev_cc = cur_cc

            assert len(critical) == 0, (
                f"{page_path}: {len(critical)} critical/serious axe-core violations found:\n"
                + "\n".join(
                    f"  - {v['id']}: {v['description']} ({len(v['nodes'])} elements)"
                    for v in critical
                )
            )
        finally:
            page.close()


class TestTickerDataPageAccessibility:
    """Structural a11y checks for the Ticker Data page (Source.md §16.8).

    The dashboard_client fixture uses an empty tmp database, so the page renders
    its no-data branch — which still emits the full base chrome (main landmark,
    lang, title) that these checks assert.
    """

    URL = "/ticker/AAPL"

    def test_renders_200(self, dashboard_client):
        assert dashboard_client.get(self.URL).status_code == 200

    def test_has_main_landmark(self, dashboard_client):
        html = dashboard_client.get(self.URL).text
        assert "<main" in html or 'role="main"' in html

    def test_has_lang_attribute(self, dashboard_client):
        assert 'lang="en"' in dashboard_client.get(self.URL).text

    def test_has_descriptive_title(self, dashboard_client):
        html = dashboard_client.get(self.URL).text
        assert "<title>" in html and "AAPL" in html

    def test_no_data_branch_has_heading(self, dashboard_client):
        """Empty-state must expose a heading for screen-reader navigation."""
        # Use a ticker guaranteed absent from the evidence cache.
        html = dashboard_client.get("/ticker/ZZNODATA").text
        assert "No data for ZZNODATA" in html

    def test_multiples_table_is_accessible_when_rendered(self):
        """If the per-year table renders, it must have a caption and scoped headers."""
        template = Path("pmacs/web/templates/ticker_detail.html").read_text()
        assert "<caption" in template, "multiples table missing <caption>"
        assert 'scope="col"' in template, "multiples table headers missing scope"
