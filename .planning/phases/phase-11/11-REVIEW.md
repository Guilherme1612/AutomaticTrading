---
phase: 11-polish-operator-experience
reviewed: 2026-05-13T20:14:00Z
depth: standard
files_reviewed: 11
files_reviewed_list:
  - tests/accessibility/conftest.py
  - tests/accessibility/test_a11y.py
  - tests/accessibility/test_keyboard.py
  - tests/accessibility/test_reduced_motion.py
  - tests/accessibility/test_viewport.py
  - tests/performance/test_audit_chain_scale.py
  - tests/performance/test_cycle_throughput.py
  - tests/performance/test_memory_budget.py
  - tests/performance/test_backup_restore.py
  - tests/e2e/test_operator_workflows.py
  - tests/e2e/test_first_30_days.py
findings:
  critical: 1
  warning: 6
  info: 7
  total: 14
status: issues_found
---

# Phase 11: Test File Code Review Report

**Reviewed:** 2026-05-13T20:14:00Z
**Depth:** standard
**Files Reviewed:** 11
**Status:** issues_found

## Summary

Reviewed 11 test files spanning accessibility (Wave 3), performance (Wave 4), and E2E (Wave 5) tests for Phase 15 (Polish). The test suite has reasonable coverage of exit test criteria but several issues were found: one critical bug (walrus operator side-effect creating an unused module-level variable), six warnings (weak assertions, fixture duplication, missing teardown, missing edge-case tests), and seven informational items.

---

## Critical Issues

### CR-01: Walrus operator creates module-level `PROFILER_DIR` variable with side-effect

**File:** `tests/performance/test_backup_restore.py:22`
**Issue:** The line `sys.path.insert(0, str(PROFILER_DIR := PROJECT_ROOT / "ops"))` uses a walrus operator at module scope. This creates a `PROFILER_DIR` name in the module namespace, but the *actual* variable used on line 23 is `PROFILER_DIR` -- however, if Python ever reorders or delays module-level execution, or if a linter/type-checker strips the assignment-expression, the import on line 23 will fail with `NameError: name 'PROFILER_DIR' is not defined`. The walrus operator is designed for inline use inside expressions; using it purely for side-effect assignment at module scope is a code smell that confuses linters and could break under import optimizations.

More importantly, `sys.path.insert(0, ...)` at module level is fragile: it runs once at import time and mutates global state. If another test module imports this file or if pytest collects in a different order, the path manipulation may not behave as expected.

**Fix:**
```python
# tests/performance/test_backup_restore.py
# Replace line 22 with:
_OPS_DIR = PROJECT_ROOT / "ops"
sys.path.insert(0, str(_OPS_DIR))
from backup_verify import do_backup, do_restore, do_verify, STORES
```

---

## Warnings

### WR-01: `_make_client` uses `yield` without `@pytest.fixture` -- no teardown guaranteed

**File:** `tests/e2e/test_first_30_days.py:19`
**Issue:** The function `_make_client` on line 19 is a plain function (not a pytest fixture) that uses `yield` to return a TestClient. While it is called by fixtures `empty_client` and `populated_client` via `yield from`, the `patch` context manager inside `_make_client` is never properly exited. When using `yield from`, the caller's `yield` statement does not guarantee the generator's cleanup runs when the pytest fixture scope ends -- the `with patch(...)` block relies on generator finalization, which is not deterministic in CPython. The patch may leak to other tests if garbage collection is delayed.

**Fix:** Use a proper context manager pattern or make `_make_client` an actual fixture with explicit cleanup:
```python
@pytest.fixture
def empty_client(tmp_path):
    """Client with no data -- simulates Day 1 pre-first-cycle."""
    from fastapi.testclient import TestClient
    # ... setup ...
    with patch("pmacs.web.config.get_config", return_value=test_config):
        from pmacs.web.app import app
        client = TestClient(app, raise_server_exceptions=False)
        yield client
    # patch is cleaned up when `with` block exits after yield
```
Extract the table creation into a helper function to avoid duplication.

### WR-02: Weak assertion in `test_keyboard_shortcuts_have_aria`

**File:** `tests/accessibility/test_a11y.py:100-102`
**Issue:** The conditional assertion `if 'cmd-k' in html.lower() or 'command-palette' in html.lower()` followed by `assert 'aria-label' in html or 'role=' in html` is nearly useless. The second assertion checks for *any* `aria-label` or `role=` anywhere in the HTML -- not specifically on the command palette element. If the command palette exists but lacks accessible attributes, this test will still pass as long as some other element has an `aria-label`.

**Fix:** When the command palette element is found, parse the specific element and verify it has accessibility attributes:
```python
def test_keyboard_shortcuts_have_aria(self, dashboard_client):
    response = dashboard_client.get("/")
    html = response.text.lower()
    if 'command-palette' in html or 'cmd-k' in html:
        # Find the specific command palette element
        import re
        palette = re.search(r'<[^>]*(command-palette|cmd-k)[^>]*>', html, re.IGNORECASE)
        if palette:
            element = palette.group()
            assert 'aria-label' in element or 'role=' in element, \
                "Command palette element lacks aria-label or role"
```

### WR-03: Weak assertion in `test_overflow_prevention` -- checks only that "overflow" exists in CSS

**File:** `tests/accessibility/test_viewport.py:39-47`
**Issue:** The test `test_no_horizontal_scrollbar_at_200_percent` only asserts `"overflow" in css.lower()`. This passes if `overflow: visible` (the default) appears anywhere, which does not prevent horizontal scrollbars. The test name promises 200% zoom verification but checks nothing about 200% or even `overflow-x: hidden`.

**Fix:**
```python
def test_no_horizontal_scrollbar_at_200_percent(self):
    css_path = (
        Path(__file__).resolve().parents[2]
        / "pmacs" / "web" / "static" / "style.css"
    )
    css = css_path.read_text()
    assert "overflow-x" in css.lower() and ("hidden" in css.lower() or "clip" in css.lower()), \
        "CSS should use overflow-x: hidden or clip to prevent horizontal scroll"
```

### WR-04: Weak assertion in `test_reduced_motion_disables_transitions`

**File:** `tests/accessibility/test_reduced_motion.py:37-43`
**Issue:** The assertion `assert "0s" in rm_block or "none" in rm_block` is too broad. `"none"` appears in many CSS contexts (e.g., `display: none`, `border: none`). The test should specifically check for `transition-duration: 0s` or `transition: none` rather than just the substring "none".

**Fix:**
```python
def test_reduced_motion_disables_transitions(self):
    rm_idx = self.css.find("prefers-reduced-motion")
    rm_block = self.css[rm_idx:rm_idx + 2000]
    assert "transition-duration" in rm_block or "transition:" in rm_block, \
           "Reduced-motion block should override transition properties"
    # Verify the override sets 0s or none
    has_override = (
        "transition-duration: 0s" in rm_block
        or "transition-duration:0s" in rm_block
        or "transition: none" in rm_block
    )
    assert has_override, "Reduced-motion should set transition-duration: 0s or transition: none"
```

### WR-05: Weak assertions in operator workflow tests -- checking for substring existence rather than specific elements

**File:** `tests/e2e/test_operator_workflows.py:115-116`
**Issue:** Multiple workflow tests use assertions like `assert "add" in html and "ticker" in html`. These are compound substring checks that can pass from unrelated content. For example, "add" appears in "address" and "ticker" could appear in page metadata. The test for workflow 21.2 (`test_run_again_now_element_exists`) checks for `"run again" in html or "override" in html or "skip" in html` -- this is a three-way OR that will pass if the word "skip" appears anywhere (even in navigation text).

**Fix:** Use more specific selectors. Check for button/link elements with those labels:
```python
def test_add_ticker_element_exists(self, workflow_client):
    response = workflow_client.get("/universe")
    html = response.text.lower()
    # Check for button or link with "add ticker" text
    assert re.search(r'<(?:button|a)[^>]*>[^<]*add\s+ticker', html, re.IGNORECASE), \
        "No 'Add Ticker' button or link found on /universe"
```

### WR-06: Duplicate fixture definitions across test files -- maintenance risk

**File:** `tests/accessibility/conftest.py:18`, `tests/e2e/test_operator_workflows.py:20`, `tests/e2e/test_first_30_days.py:19`
**Issue:** Three nearly identical fixtures create TestClient instances with SQLite setup. `conftest.py` defines `dashboard_client`, `test_operator_workflows.py` defines `workflow_client`, and `test_first_30_days.py` defines `_make_client`/`empty_client`/`populated_client`. All three create the same table structure, insert similar data, and patch the same config. If the schema changes, all three must be updated in lockstep.

**Fix:** Extract the shared fixture into a common conftest. Create `tests/conftest.py` or `tests/shared_fixtures.py` with parameterized client factories:
```python
# tests/conftest.py
@pytest.fixture
def dashboard_client(tmp_path, with_data=False):
    """Shared TestClient fixture. Parameterize with_data for empty/populated."""
    ...
```
Then have each subdirectory's conftest import and specialize as needed.

---

## Info

### IN-01: `test_sparkline_hover_respects_reduced_motion` and `test_toast_animation_respects_reduced_motion` do not actually test reduced-motion behavior

**File:** `tests/accessibility/test_reduced_motion.py:45-52`
**Issue:** Both tests only check that the CSS class names "sparkline" and "toast" exist in the CSS file. They do not verify any interaction between reduced-motion and these elements. The test names imply behavioral validation that is not performed.

### IN-02: `test_viewport_guard_in_base_template` uses three-way OR making the assertion vague

**File:** `tests/accessibility/test_viewport.py:21`
**Issue:** `assert "1024" in html or "wider" in html.lower() or "viewport" in html.lower()` -- the word "viewport" will almost always be present due to the viewport meta tag, making the 1024px guard check trivially pass.

### IN-03: `test_sidebar_collapse_at_narrow_widths` does not test sidebar collapse behavior

**File:** `tests/accessibility/test_viewport.py:49-57`
**Issue:** The test only checks that "sidebar" or "collapse" appears in the CSS. It does not verify any responsive breakpoint or collapse mechanism.

### IN-04: `test_js_detects_reduced_motion` accepts `matchMedia` as sufficient

**File:** `tests/accessibility/test_reduced_motion.py:67`
**Issue:** The assertion `assert "prefers-reduced-motion" in self.js or "matchMedia" in self.js` accepts `matchMedia` alone as evidence of reduced-motion detection. `matchMedia` could be used for any media query (dark mode, print, etc.).

### IN-05: `test_empty_holdings_state` has no assertion beyond status code

**File:** `tests/e2e/test_first_30_days.py:182-185`
**Issue:** The comment says "Should not show broken tables or error messages" but the test only checks `status_code == 200`. It should verify the empty-state rendering (e.g., no `<table>` with missing rows, or presence of an "empty state" message).

### IN-06: `test_budget_values_match_spec` in test_cycle_throughput.py uses hardcoded phase name keys

**File:** `tests/performance/test_cycle_throughput.py:58-63`
**Issue:** The test accesses `PHASE_BUDGETS["Phase 0: Gatekeeper"]` etc. using exact string keys. If any key is renamed in `ops/profile_cycle.py`, the test will raise `KeyError` rather than a clear assertion error. Consider using constants or a less fragile lookup.

### IN-07: `test_kill_switch_button_exists` assertion could match unrelated content

**File:** `tests/accessibility/test_keyboard.py:105-109`
**Issue:** `assert "kill" in html.lower() and ("switch" in html.lower() or "engage" in html.lower())` -- "kill" could match "kill switch" but also content about "killing processes" in debug text. Similarly "engage" is a common word.

---

_Reviewed: 2026-05-13T20:14:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
