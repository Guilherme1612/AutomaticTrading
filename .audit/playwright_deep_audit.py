#!/usr/bin/env python3
"""Deep UI audit: contrast, overflow, accessible names, mode badge, network errors."""
from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright, Page

BASE_URL = os.environ.get("PMACS_AUDIT_URL", "http://127.0.0.1:8001")
OUT_DIR = Path(__file__).resolve().parent
REPORT_PATH = OUT_DIR / "deep_audit_report.json"

PAGES = [
    ("dashboard", "/"),
    ("agents", "/agents"),
    ("pipeline", "/pipeline"),
    ("universe", "/universe"),
    ("cortex", "/cortex"),
    ("debug", "/debug"),
    ("settings", "/settings"),
    ("ticker_msft", "/ticker/MSFT"),
    ("memo_msft", "/memo/MSFT"),
    ("wizard_welcome", "/wizard/"),  # rendered when wizard state is reset to step 1
    ("wizard_home", "/wizard/"),     # rendered from current wizard state
]

VIEWPORTS = {
    "mobile": (375, 812),
    "tablet": (768, 1024),
    "desktop": (1440, 900),
}
class _WizardStateManager:
    """Backup/restore wizard completion state so the audit can render the welcome page.

    The wizard route treats any existing mode_history as 'already configured' and
    redirects to the dashboard. To capture the first-run welcome page we must
    temporarily hide mode_history rows and clear the wizard_completed flag.
    """
    def __init__(self) -> None:
        self._backup_step = 1
        self._backup_completed = False
        self._backup_mode_rows: list[tuple] = []

    def capture(self) -> None:
        from pmacs.config import data_dir
        from pmacs.storage.sqlite import connect
        db_path = data_dir() / "pmacs.db"
        if not db_path.exists():
            return
        conn = connect(db_path)
        try:
            step_row = conn.execute("SELECT value FROM wizard_state WHERE key = ?", ("wizard_current_step",)).fetchone()
            completed_row = conn.execute("SELECT value FROM wizard_state WHERE key = ?", ("wizard_completed",)).fetchone()
            self._backup_step = int(step_row[0]) if step_row else 1
            self._backup_completed = (completed_row and completed_row[0] == "1")
            if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='mode_history'").fetchone():
                self._backup_mode_rows = conn.execute("SELECT * FROM mode_history").fetchall()
        finally:
            conn.close()

    def set_welcome(self) -> None:
        self._mutate(step=1, completed=False, mode_history_rows=[])

    def restore(self) -> None:
        self._mutate(step=self._backup_step, completed=self._backup_completed, mode_history_rows=self._backup_mode_rows)

    def _mutate(self, step: int, completed: bool, mode_history_rows: list[tuple]) -> None:
        from pmacs.config import data_dir
        from pmacs.storage.sqlite import connect
        db_path = data_dir() / "pmacs.db"
        conn = connect(db_path)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS wizard_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("INSERT OR REPLACE INTO wizard_state (key, value) VALUES (?, ?)", ("wizard_current_step", str(step)))
            conn.execute("INSERT OR REPLACE INTO wizard_state (key, value) VALUES (?, ?)", ("wizard_completed", "1" if completed else "0"))
            if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='mode_history'").fetchone():
                conn.execute("DELETE FROM mode_history")
                if mode_history_rows:
                    cols = len(mode_history_rows[0])
                    placeholders = ",".join("?" * cols)
                    conn.executemany(f"INSERT INTO mode_history VALUES ({placeholders})", mode_history_rows)
            conn.commit()
        finally:
            conn.close()


_wizard = _WizardStateManager()


def apply_theme(page: Page, theme: str) -> None:
    page.evaluate(
        """(theme) => {
            localStorage.setItem('pmacs-theme', theme);
            if (theme === 'dark') document.documentElement.classList.add('dark');
            else document.documentElement.classList.remove('dark');
        }""",
        theme,
    )


def relative_luminance(rgb: str) -> float:
    """Compute WCAG relative luminance from rgb(r,g,b)."""
    nums = [int(x) / 255.0 for x in rgb.replace("rgb(", "").replace(")", "").split(",")]
    def tf(c):
        return c / 12.92 if c <= 0.03928 else pow((c + 0.055) / 1.055, 2.4)
    r, g, b = nums
    return 0.2126 * tf(r) + 0.7152 * tf(g) + 0.0722 * tf(b)


def contrast(c1: str, c2: str) -> float:
    l1, l2 = relative_luminance(c1), relative_luminance(c2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def get_effective_background(el) -> str:
    """Walk up until a non-transparent background is found."""
    # Implemented in JS evaluate
    pass


def run_checks(page: Page) -> dict:
    return page.evaluate(r"""() => {
        function isHidden(el) {
            if (!el) return true;
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0' || (rect.width === 0 && rect.height === 0);
        }
        function isInHiddenModal(el) {
            let p = el;
            while (p && p !== document.body) {
                if (p.classList && p.classList.contains('hidden')) {
                    // Hidden modals with inert or display:none are not reachable.
                    if (p.inert || window.getComputedStyle(p).display === 'none') return false;
                    return true;
                }
                p = p.parentElement;
            }
            return false;
        }
        const results = {
            horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth,
            modeBadgeMissing: false,
            modeBadgeEmpty: false,
            missingAccessibleName: [],
            missingAlt: [],
            zeroSizeVisibleInteractive: [],
            hiddenModalFocusable: false,
            lowContrast: [],
        };

        // Mode badge
        const badge = document.querySelector('.mode-badge');
        if (!badge) results.modeBadgeMissing = true;
        else if (!badge.textContent.trim()) results.modeBadgeEmpty = true;

        // Images missing alt
        document.querySelectorAll('img').forEach(img => {
            if (!isHidden(img) && !img.hasAttribute('alt') && !img.getAttribute('aria-label')) {
                results.missingAlt.push(img.src || 'img');
            }
        });

        // Interactive accessible names
        document.querySelectorAll('button, a, [role="button"]').forEach(el => {
            if (isHidden(el) || isInHiddenModal(el)) return;
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) {
                if (el.textContent.trim()) results.zeroSizeVisibleInteractive.push(el.textContent.trim().slice(0,40));
                return;
            }
            const hasName = (
                (el.textContent || '').trim() ||
                el.getAttribute('aria-label') ||
                el.getAttribute('aria-labelledby') ||
                el.getAttribute('title') ||
                el.querySelector('svg[aria-label]')
            );
            if (!hasName) {
                results.missingAccessibleName.push(el.tagName + (el.id ? '#' + el.id : ''));
            }
        });

        // Hidden modal focusable (just flag if any)
        document.querySelectorAll('#cmd-k [tabindex], #shortcut-overlay [tabindex], #blocking-modal button, #confirm-modal button').forEach(el => {
            if (isInHiddenModal(el)) results.hiddenModalFocusable = true;
        });

        // Contrast sampling: leaf text nodes
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
        let node;
        while ((node = walker.nextNode())) {
            const parent = node.parentElement;
            if (!parent || isHidden(parent) || isInHiddenModal(parent)) continue;
            const txt = node.textContent.trim();
            if (!txt || txt.length < 2) continue;
            const style = window.getComputedStyle(parent);
            const fontSize = parseFloat(style.fontSize);
            if (!fontSize) continue;
            const weight = parseInt(style.fontWeight, 10) || 400;
            const isLarge = fontSize >= 18 || (fontSize >= 14 && weight >= 700);
            const threshold = isLarge ? 3.0 : 4.5;
            const fg = style.color;
            // effective background
            let bgEl = parent;
            let bg = 'rgb(255,255,255)';
            while (bgEl && bgEl !== document.documentElement) {
                const bs = window.getComputedStyle(bgEl).backgroundColor;
                if (bs && bs !== 'rgba(0, 0, 0, 0)' && bs !== 'transparent') { bg = bs; break; }
                bgEl = bgEl.parentElement;
            }
            // approximate contrast using regex
            const rgbRe = /rgb\((\d+),\s*(\d+),\s*(\d+)\)/;
            const m1 = fg.match(rgbRe);
            const m2 = bg.match(rgbRe);
            if (m1 && m2) {
                function lum(rgb) {
                    const [r,g,b] = rgb.slice(1).map(x => parseInt(x)/255).map(c => c <= 0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4));
                    return 0.2126*r + 0.7152*g + 0.0722*b;
                }
                const l1 = lum(m1);
                const l2 = lum(m2);
                const ratio = (Math.max(l1,l2)+0.05)/(Math.min(l1,l2)+0.05);
                if (ratio < threshold) {
                    results.lowContrast.push({text: txt.slice(0,40), ratio: parseFloat(ratio.toFixed(2)), el: parent.tagName + (parent.id?'#'+parent.id:'') + (parent.className ? '.'+parent.className.split(' ').slice(0,3).join('.') : '')});
                }
            }
        }
        return results;
    }
    """)


def run_audit() -> None:
    report = {"base_url": BASE_URL, "runs": []}
    _wizard.capture()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for page_name, path in PAGES:
            if page_name == "wizard_welcome":
                _wizard.set_welcome()
            elif page_name == "wizard_home":
                _wizard.restore()
            for theme in ["light", "dark"]:
                for vp_name, (width, height) in VIEWPORTS.items():
                    context = browser.new_context(viewport={"width": width, "height": height})
                    page = context.new_page()
                    network_errors = []
                    page.on("response", lambda resp: network_errors.append((resp.status, resp.url)) if resp.status >= 400 else None)

                    page.goto(BASE_URL, wait_until="domcontentloaded")
                    apply_theme(page, theme)
                    try:
                        page.goto(f"{BASE_URL}{path}", wait_until="load", timeout=60000)
                    except Exception as e:
                        report["runs"].append({
                            "page": page_name, "theme": theme, "viewport": vp_name,
                            "error": str(e), "network_errors": network_errors,
                        })
                        context.close()
                        continue
                    page.wait_for_timeout(2500)
                    checks = run_checks(page)
                    checks["network_errors"] = network_errors
                    checks["page_url"] = page.url
                    report["runs"].append(checks)
                    print(f"✓ {page_name} {theme} {vp_name}")
                    context.close()
        browser.close()
    # Leave wizard state restored to original
    _wizard.restore()
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nDeep report: {REPORT_PATH}")


if __name__ == "__main__":
    run_audit()
