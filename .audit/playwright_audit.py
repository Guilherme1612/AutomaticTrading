#!/usr/bin/env python3
"""Render audit: visit every PMACS page at multiple viewports/themes and collect evidence."""
from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright, Page

BASE_URL = os.environ.get("PMACS_AUDIT_URL", "http://127.0.0.1:8001")
OUT_DIR = Path(__file__).resolve().parent
SCREEN_DIR = OUT_DIR / "screenshots"
REPORT_PATH = OUT_DIR / "audit_report.json"

SCREEN_DIR.mkdir(parents=True, exist_ok=True)

# Pages to visit (GET routes that render HTML).
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
    ("wizard_home", "/wizard/"),       # rendered from current wizard state
]


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

# Viewports: name -> (width, height)
VIEWPORTS = {
    "mobile": (375, 812),
    "tablet": (768, 1024),
    "desktop": (1440, 900),
}

# Themes to test
THEMES = ["light", "dark"]


def apply_theme(page: Page, theme: str) -> None:
    page.evaluate(
        """(theme) => {
            localStorage.setItem('pmacs-theme', theme);
            if (theme === 'dark') document.documentElement.classList.add('dark');
            else document.documentElement.classList.remove('dark');
        }""",
        theme,
    )


def wait_for_page_ready(page: Page, name: str) -> None:
    """Wait until the main content is present and no active htmx indicator."""
    try:
        page.wait_for_selector("main#main-content", state="visible", timeout=5000)
    except Exception:
        pass
    # Some pages load HTMX partials; allow a short settle window.
    page.wait_for_timeout(800)


def collect_basic_checks(page: Page) -> dict:
    """Run lightweight in-page checks for broken images, zero-size buttons, focusable hidden elements."""
    return page.evaluate("""() => {
        const badImages = [];
        document.querySelectorAll('img').forEach(img => {
            if (!img.complete || img.naturalWidth === 0) {
                const rect = img.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) badImages.push(img.src || img.alt || 'img');
            }
        });
        const zeroButtons = [];
        document.querySelectorAll('button, [role="button"], a').forEach(el => {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 && rect.height === 0 && el.offsetParent !== null && el.textContent.trim()) {
                zeroButtons.push(el.textContent.trim().slice(0, 40));
            }
        });
        const hiddenFocusable = [];
        function isInert(el) {
            if (el.inert) return true;
            if (el.getAttribute('tabindex') === '-1') return true;
            if (el.getAttribute('aria-hidden') === 'true') return true;
            let p = el.parentElement;
            while (p) {
                if (p.inert || p.getAttribute('aria-hidden') === 'true') return true;
                p = p.parentElement;
            }
            return false;
        }
        document.querySelectorAll('a, button, input, select, textarea, [tabindex]:not([tabindex="-1"])').forEach(el => {
            const style = window.getComputedStyle(el);
            if ((style.display === 'none' || style.visibility === 'hidden') && !el.disabled && !isInert(el)) {
                hiddenFocusable.push(el.tagName + (el.id ? '#'+el.id : ''));
            }
        });
        return { badImages, zeroButtons, hiddenFocusable };
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
            for theme in THEMES:
                for vp_name, (width, height) in VIEWPORTS.items():
                    context = browser.new_context(viewport={"width": width, "height": height})
                    page = context.new_page()
                    console_logs: list[dict] = []
                    page_errors: list[str] = []

                    def on_console(msg):
                        text = msg.text
                        # Expected teardown / parser warnings; not page defects.
                        if msg.type == "warning" and (
                            "SSE connection lost" in text
                            or "parser-blocking" in text
                        ):
                            return
                        console_logs.append({
                            "type": msg.type,
                            "text": text,
                            "location": str(msg.location),
                        })

                    def on_error(err):
                        page_errors.append(str(err))

                    page.on("console", on_console)
                    page.on("pageerror", on_error)

                    # Load base URL first to set theme before the target page.
                    page.goto(BASE_URL, wait_until="domcontentloaded")
                    apply_theme(page, theme)
                    # SSE /events keeps the connection open, so networkidle is unreliable.
                    page.goto(f"{BASE_URL}{path}", wait_until="load", timeout=60000)
                    wait_for_page_ready(page, page_name)
                    # Extra settle time for HTMX partials and data fetches.
                    page.wait_for_timeout(2000)

                    checks = collect_basic_checks(page)

                    # Screenshot after scroll-to-top to capture full page.
                    page.evaluate("window.scrollTo(0,0)")
                    shot_name = f"{page_name}__{theme}__{vp_name}.png"
                    shot_path = SCREEN_DIR / shot_name
                    page.screenshot(path=str(shot_path), full_page=True)

                    entry = {
                        "page": page_name,
                        "path": path,
                        "theme": theme,
                        "viewport": vp_name,
                        "screenshot": str(shot_path.relative_to(OUT_DIR)),
                        "console": console_logs,
                        "page_errors": page_errors,
                        "checks": checks,
                    }
                    report["runs"].append(entry)
                    print(f"✓ {page_name} {theme} {vp_name} — {len(console_logs)} console logs, {len(page_errors)} page errors")
                    context.close()
        browser.close()

    _wizard.restore()
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nReport written to {REPORT_PATH}")
    print(f"Screenshots in {SCREEN_DIR}")


if __name__ == "__main__":
    run_audit()
