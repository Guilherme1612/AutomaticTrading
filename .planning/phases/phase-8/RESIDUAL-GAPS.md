# Phase 8 Residual Gaps Audit (2026-06-29)

**Auditor:** Explore agent + manual grep scan
**Triggered by:** Operator directive "commit all, start phase 8 polish" after `4ec5a10`
**Baseline:** Phase 8 = Phase 15 in spec terminology (`spec/Phases.md` lines 609-647). Phase 8 ship (May 2026) deposited CONTEXT/PLAN/REVIEW/SUMMARY stack in `.planning/phases/phase-8/`. This audit cross-checks the spec's 16 deliverables + 8 exit tests against the current working tree at commit `4ec5a10`.

---

## TL;DR

**12 of 16 deliverables SHIPPED. 1 PARTIAL. 3 RESIDUAL.**

Phase 15 is approximately 75% built. Three atomic tasks remain; combined they close all 8 exit tests empirically. Each task is one PR worth of work.

---

## 1. Quick Status Table

| # | Phase 15 Deliverable | Status | Evidence |
|---|----------------------|--------|----------|
| 1 | Agents animations (progress bars, Sankey, Math view) | SHIPPED | `app.js:1540-1635`, `static/sankey.js`, `style.css:658-690` |
| 2 | Pipeline drag-drop kanban refinement | PARTIAL | drag works (`pipeline.html:296-365`), **priority bands have no UI** |
| 3 | Dashboard sparklines + time-window | SHIPPED | `app.js:1488-1800`, `dashboard.html:135-165` |
| 4 | Cmd-K command palette | SHIPPED | `app.js:585-825` (tickers, pages, actions, audit search) |
| 5 | Keyboard shortcuts (§13.6) | SHIPPED | `app.js:1005-1100`, `tests/accessibility/test_keyboard.py` |
| 6 | Accessibility audit (§13.7) | PARTIAL | 766 lines of structural tests pass; **axe-playwright-python missing from deps** |
| 7 | Performance profiling (§20.1) | SHIPPED (framework) | `ops/profile_cycle.py`, 3 unit tests |
| 8 | Memory profiling (§20.2) | SHIPPED (framework) | `ops/profile_memory.py`, 3 unit tests |
| 9 | `ops/spec_consistency.py` | SHIPPED | 338 lines + 22 unit tests |
| 10 | `ops/backup_verify.py` | SHIPPED | 462 lines + 15 tests including E2E |
| 11 | `ops/audit_chain_verify.py` | SHIPPED | 142 lines + 6 tests |
| 12 | `docs/operator_runbook.md` | SHIPPED | 372 lines |
| 13 | Empty/Loading/Error states (§13.4) | SHIPPED | 4 components, 8 of 8 templates |
| 14 | Notification policy (§13.5) | SHIPPED | `app.js:466-595`, settings UI |
| 15 | "Copy for Claude Code" button | SHIPPED | `app.js:1269-1400`, 7 surfaces |
| 16 | TOTP gate removal | SHIPPED | `adb7c98` (per spec note) |

---

## 2. Exit Test Status

| # | Test | Status | Why |
|---|------|--------|-----|
| 1 | 8 workflows ≤3 clicks | PASS (structural) | No empirical test exists — needs Task 4 |
| 2 | 10-ticker cycle ≤3h | PASS (framework) | `TOTAL_BUDGET_S=10800`; constants say "20" — Task 3 |
| 3 | RAM <50GB peak | PASS (framework) | `PEAK_BUDGET_GB=50.0` |
| 4 | Audit chain verifies after 100+ cycles | PASS | `test_audit_chain_verify.py` 200-entry test |
| 5 | `spec_consistency.py` passes | PASS | 22 unit tests |
| 6 | Backup + restore E2E | PASS | `test_backup_restore.py:150` |
| 7 | axe-core zero critical on 7 pages | **GAP** | `tests/accessibility/test_a11y.py:328` auto-skips without `axe-playwright-python` |
| 8 | Toasts/modals/shortcuts function | PASS (structural) | Manual checklist per §13.5/§13.6 |

---

## 3. Residual Gap Files

- `pyproject.toml` — add `axe-playwright-python>=0.1.0` to `[a11y]` extras
- `uv.lock` — regenerate after `pyproject.toml` edit
- `ops/profile_cycle.py:26` — row label says "20 admitted symbols"; spec says 10
- `tests/performance/test_cycle_throughput.py:73` — uses `admitted_symbols=16`
- `pmacs/web/templates/pipeline.html` — no priority-band swimlane UI (data model + backend ready)
- `pmacs/web/static/style.css` — no `.priority-band` chip styles
- `pmacs/web/static/app.js` — drag handler would need P-band update
- `tests/e2e/` — no `test_workflows.py` for empirical 3-click assertion
- `spec/Source.md` §13.5/13.6 — still describe TOTP gate (per `adb7c98` commit message)

---

## 4. Atomic Tasks (priority ordered, recommended execution)

### Task 1 — Wire `axe-playwright-python` to unskip empirical scan (HIGHEST ROI)

Closes exit test #7. One dep addition + lock regen + scan-run.

```bash
# In pyproject.toml [a11y] extras, add:
#   "axe-playwright-python>=0.1.0"
.venv/bin/python -m pip install axe-playwright-python
# or: uv pip sync
.venv/bin/python -m pytest tests/accessibility/test_a11y.py::TestAxeCoreEmpirical -v
```

Acceptance: scan runs in CI without skip; if any violations surface, file follow-up fixes.

Files touched: `pyproject.toml`, `uv.lock`

### Task 2 — Add Pipeline priority-band UI (P1/P2/P3/P4 lanes)

Closes deliverable #2. Backend supports it; operator has no visible UI.

Design: 5th swimlane "Priority Queue" above the verdict kanban, 4 sub-columns P1-P4. Drag any verdict-card into a P-lane → `POST /pipeline/queue/reorder` with `from_band`/`to_band`. Topbar P1 depth badge.

Files: `pipeline.html` (kanban layout), `style.css` (`.priority-band-*` tokens), `app.js` (drop handler), `tests/e2e/test_pipeline.py` (drag-into-P1 test)

### Task 3 — Reconcile 10-ticker constants

One-shot DRIFT cleanup. Three files, all single-line edits.

Files: `ops/profile_cycle.py:26` ("20" → "10", recompute budget = 2700s), `tests/performance/test_cycle_throughput.py:73` (`admitted_symbols=16` → `10`, rename test), verify `spec/Phases.md:640` and `spec/Source.md:506` already say 10

### Task 4 — Empirical runbook walkthrough (Task 1's evidence)

Closes exit test #1 with empirical proof. Playwright `tests/e2e/test_workflows.py` covering all 8 workflows.

Files: `tests/e2e/test_workflows.py` (new), `docs/operator_runbook.md` (cite the test)

### Task 5 — Spec sync pass (§13-20 TOTP + 10-ticker)

Paperwork. One-shot prose edit of two spec files.

Files: `spec/Source.md` §13.5/§13.6 (HMAC + CSRF, not TOTP), `spec/Phases.md` (verify 10-ticker text consistent)

---

## 5. Recommended Execution Order

Task 1 (1 hour, mechanical) → Task 3 (15 min, mechanical) → Task 5 (30 min, prose) → Task 2 (half day, UI) → Task 4 (half day, E2E)

**Total: ~1.5 working days to close all 8 exit tests empirically.**

---

## 6. Verification Anchor (needs real-system runs)

- Exit tests #2 (3-hour cycle) and #3 (50GB RAM peak) need `pmacs-nervous` running with model loaded. Run on M1 Max 64GB host.
- Exit test #7 (axe-core) needs the dev server running for all 7 pages.
- Exit test #1 (3-click workflows) needs Task 4's empirical suite.

---

## 7. Plan File References

- Original CONTEXT.md (May 8 2026): `.planning/phases/phase-8/CONTEXT.md`
- Original PLAN.md: `.planning/phases/phase-8/PLAN.md` (32.8K)
- Original SUMMARY.md: `.planning/phases/phase-8/SUMMARY.md` (documents shipped items)

This audit supplements — does not replace — those planning docs.
