# Phase 11: REVIEWS.md — Polish, Performance, Operator Experience

**Phase:** 11 (PMACS Phase 15 — LIVE-READY)
**Review Date:** 2026-05-13
**Reviewers:** Claude (gsd-code-reviewer) — two independent passes
**Files Reviewed:** 24 (13 production + 11 test)
**Total Findings:** 25 (2 Critical, 11 Warning, 12 Info)

---

## Reviewer: Claude (Pass 1 — W1-W2 Production Code)

**Files:** data.py, dashboard.py, settings.py, base.html, dashboard.html, agents.html, cortex.html, debug.html, pipeline.html, settings.html, universe.html, error_state.html, app.js

### Critical (1)

| ID | File | Issue |
|----|------|-------|
| C1 | `app.js:412-419` | **XSS via innerHTML in Cmd-K palette.** `renderCmdKResults` concatenates `item.name` (which includes user-typed `query.toUpperCase()`) directly into innerHTML. Typing `<img onerror=...>` in search injects arbitrary HTML. Local-only mitigates blast radius, but violates project security posture. **Fix:** Add `escapeHtml()` utility or use `textContent` + DOM construction. |

### Warning (5)

| ID | File | Issue |
|----|------|-------|
| W1 | `settings.py:83` vs `app.js:202-204` vs `settings.html:68` | **Non-disableable event lists disagree across 3 layers.** Backend blocks `kill_switch`+`audit_chain_failure`; template disables `kill_switch`+`error`; JS hardcodes `kill_switch_engaged` (different key). Operator could suppress kill switch alert via key mismatch. |
| W2 | `app.js:522,528-531` | **innerHTML with server-sourced data** in `fetchCycleComparison`. Cycle IDs from user input and `err.message` from server injected without escaping. Use `textContent` + DOM construction. |
| W3 | `dashboard.py:44` | **Portfolio value is a tautology:** `5000.0 - position_value + position_value` always equals 5000. After any trade, displayed value is wrong. Needs cash ledger integration. |
| W4 | `app.js:720` | **Kill switch engage is a stub** — TODO comment, no backend call. Button changes color locally but kill switch is NOT actually engaged. Violates Five Non-Negotiables. Must POST to `/api/kill-switch/engage`. |
| W5 | `dashboard.html:42-56` + `dashboard.py:82-95` | **Sparkline window buttons use HTMX but API returns JSON**, not HTML. Clicking window buttons injects raw JSON into the sparkline div, destroying the SVG. Either return HTML partials or handle entirely in JS. |

### Info (5)

| ID | File | Issue |
|----|------|-------|
| I1 | `app.js:15,464,523` | Hardcoded `http://127.0.0.1:8000` URLs — should use relative paths |
| I2 | `data.py:671`, `dashboard.py:60` | `disk_free_gb` hardcoded to 50 — should use `shutil.disk_usage("/")` |
| I3 | `settings.html:60,68` | Dead event key `"error"` — listed in UI but has no notification handler in app.js |
| I4 | `pipeline.html:243` | `console.log('Move', ticker, 'to verdict', verdict)` debug artifact in production |
| I5 | `data.py:639` | Port 8001/8000 display inconsistency in Cortex process list |

---

## Reviewer: Claude (Pass 2 — W3-W5 Test Code)

**Files:** conftest.py, test_a11y.py, test_keyboard.py, test_reduced_motion.py, test_viewport.py, test_audit_chain_scale.py, test_cycle_throughput.py, test_memory_budget.py, test_backup_restore.py, test_operator_workflows.py, test_first_30_days.py

### Critical (1)

| ID | File | Issue |
|----|------|-------|
| C2 | `test_backup_restore.py:22` | **Walrus operator at module scope** for side-effect assignment: `sys.path.insert(0, str(PROFILER_DIR := PROJECT_ROOT / "ops"))`. Fragile under linter optimization. Use separate `_OPS_DIR = PROJECT_ROOT / "ops"` variable. |

### Warning (6)

| ID | File | Issue |
|----|------|-------|
| W6 | `test_first_30_days.py:19` | **`_make_client` uses `yield` without `@pytest.fixture`** — `with patch(...)` cleanup not guaranteed via `yield from`. Patch may leak between tests. |
| W7 | `test_a11y.py:100-102` | **Weak assertion** — checks for ANY `aria-label` or `role=` in full HTML, not on command palette element specifically. |
| W8 | `test_viewport.py:39-47` | **Weak assertion** — `"overflow" in css` passes with `overflow: visible` (default). Should check `overflow-x: hidden` or `clip` specifically. |
| W9 | `test_reduced_motion.py:37-43` | **Weak assertion** — `"none" in rm_block` matches `display: none`, `border: none`, etc. Should check `transition-duration: 0s` specifically. |
| W10 | `test_operator_workflows.py:115-116` | **Weak assertions** — compound substring checks (`"add" in html and "ticker" in html`) match unrelated content. Three-way ORs trivially pass. |
| W11 | conftest.py + 2 test files | **Duplicate fixture definitions** — 3 near-identical TestClient fixtures with same schema setup. Extract to shared `tests/conftest.py`. |

### Info (7)

| ID | File | Issue |
|----|------|-------|
| I6 | `test_reduced_motion.py:45-52` | Tests named for behavioral validation but only check CSS class name existence |
| I7 | `test_viewport.py:21` | Three-way OR assertion trivially passes because "viewport" is in meta tag |
| I8 | `test_viewport.py:49-57` | Sidebar collapse test only checks word "sidebar" in CSS, not breakpoint behavior |
| I9 | `test_reduced_motion.py:67` | Accepts `matchMedia` alone as reduced-motion evidence — could be any media query |
| I10 | `test_first_30_days.py:182-185` | `test_empty_holdings_state` only checks status 200, not empty-state rendering |
| I11 | `test_cycle_throughput.py:58-63` | Hardcoded string keys for phase budgets — fragile to renames |
| I12 | `test_keyboard.py:105-109` | `"kill" in html` matches unrelated debug text about "killing processes" |

---

## Exit Test Coverage Assessment

| Exit Test | Wave | Test File(s) | Coverage |
|-----------|------|-------------|----------|
| 1. 8 workflows ≤ 3 clicks | W5 | test_operator_workflows.py | PARTIAL — weak assertions (W10) |
| 2. Full cycle ≤ 3 hours | W4 | test_cycle_throughput.py | COVERED — assertions on phase budgets |
| 3. RAM < 50GB peak | W4 | test_memory_budget.py | COVERED — per-process RSS assertions |
| 4. Audit chain 100+ entries | W4 | test_audit_chain_scale.py | COVERED — chain + tamper detection |
| 5. spec_consistency.py passes | W4 | (ops tool) | COVERED |
| 6. Backup → wipe → restore | W4 | test_backup_restore.py | COVERED — has walrus issue (C2) |
| 7. axe-core zero critical | W3 | test_a11y.py | PARTIAL — weak aria assertion (W7) |
| 8. Notifications/shortcuts | W3+W5 | test_keyboard.py, test_operator_workflows.py | PARTIAL — weak assertions |

---

## Recommended Fix Priority

### Must Fix Before LIVE-READY
1. **C1** (XSS in Cmd-K) — Security issue, straightforward fix
2. **W4** (Kill switch stub) — Five Non-Negotiables violation
3. **W1** (Non-disableable event mismatch) — Could suppress kill switch alerts

### Should Fix Before LIVE-READY
4. **W2** (innerHTML in cycle compare) — Second XSS vector
5. **W5** (Sparkline window buttons broken) — Feature doesn't work
6. **W3** (Portfolio value tautology) — Display will be wrong after first trade

### Should Fix (Tests)
7. **C2** (Walrus operator) — Code smell, easy fix
8. **W6** (yield without fixture) — Patch leak risk
9. **W7-W10** (Weak assertions) — Tests don't validate what they claim

### Nice to Have
10. **I1-I12** — Hardcoded URLs, dead code, duplicate fixtures, debug artifacts

---

## Positive Findings

- Error boundary pattern is **identically applied** across all 7 templates — no deviation
- Sparkline data loader handles empty/missing DuckDB **gracefully** at every layer (data.py → template → JS)
- HTMX afterSwap correctly reinitializes SSE, sidebar, and Sankey
- Jinja2 auto-escaping used throughout (no unsafe `{{{`)
- No `eval()` usage anywhere in app.js
- All SQL queries use parameterized statements
- Notification validation has strict whitelist (`toast`, `toast+sound`, `modal`, `none`)
- Audit chain scale test covers both integrity verification AND tamper detection
- Test infrastructure covers all 8 exit tests from the plan

---

_Reviews completed: 2026-05-13_
_Reviewers: Claude (gsd-code-reviewer) — two independent passes_
