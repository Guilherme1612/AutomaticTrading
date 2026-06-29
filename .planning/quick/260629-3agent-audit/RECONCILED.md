# 3-Agent Audit — Reconciled Plan (2026-06-29)

**Branch:** `phase-7c-valuation`
**Method:** Read-only fan-out → 3 parallel agents → main-loop reconciliation.
**No edits applied.** This document is the plan; edits require explicit operator approval.

## Severity histogram

| Severity | F (frontend) | V (valuation) | S (simplify) | **Total** |
|---|---|---|---|---|
| **BLOCKER** | 2 | 1 | 0 | **3** |
| **HIGH**     | 6 | 3 | 0 | **9** |
| **MED**      | 7 | 7 | 6 | **20** |
| **LOW**      | 5 | 11 | 8 | **24** |
| **Total**    | **20** | **22** | **14** | **56** |

**Headline:** 3 BLOCKERs (2 a11y on `/ticker/{ticker}` tabs + 1 silent-crash vector in `forward_valuation.py`). 9 HIGH (mostly a11y/UX on the workspace + 1 critical test coverage gap for pre-profit EV/Sales path). All three BLOCKERs should land in the first PR.

### Cross-agent dedup / coupling

- **V-002** (distress-signal drops into reverse-DCF) and **S-002** (duplicate evidence extraction across reverse-DCF, forward, and anchor) both touch the `revenueGrowthTTMYoy` primitive. Fix V-002's `is_available` semantics **and** consolidate to one `_extract_valuation_primitives` helper (S-002 fix) in the same PR to avoid reconciliation churn.
- **S-004** (`agent_idle_text` unguarded in `agents.html:274`) — reclassified HIGH per pre-loaded memory `jinja_context_var_desync.md` (unguarded context var 500s every page until route restart). Different severity table number than F-* but worth a callout.
- **V-011** (memo_writer test couples to orchestrator) ↔ **S-013** (extract `_run_slot` to make Rec2 unit-testable). Same fix direction. Do them together.
- **V-006** (memo labels `expected_price_usd` ambiguously) ↔ **S-003** (spec drift on `_build_current_valuation_anchor`). Both point to `spec/Source.md §16.9` being incomplete. Bundle.

### Verified clean (NOT findings — these were checked and pass)

- Equity-floor fix intact (ONDS math: cash → EV → equity produces sane non-negative price) — `pmacs/engines/forward_valuation.py:136-146`
- EV/Sales fallback path intact — `pmacs/engines/forward_valuation.py:107-122`
- No pydantic-v1 violations anywhere in scope
- No LLM math, no `json.dumps` in audit paths, no new `*100` spots (per `data_corruption_root_cause.md` history)
- `_step_13d5_debate` has no early-return-before-work branches (verified clean)
- Ticker-link wrappers (commit `4a452df`) don't break JS that reads `#current-ticker` / `#next-ticker`
- `tests/unit/test_demo_dispatch_consolidation.py` deletion is clean (no stale imports)
- Operator directives honored: no TTM-divergence suppression, no Fwd-P/E magnitude caps proposed

## PR Plan (two PRs total — keeps Phase 7c shippable)

### PR #1 — "Phase 7c commit-readiness + structural a11y fix on /ticker/{ticker}"

**Goal:** Clean ship of the 24-file working tree with all BLOCKERs and HIGHs in scope resolved. Do NOT scope-creep into visual polish.

**Critical files:**
- `pmacs/web/templates/ticker_detail.html` — apply proposed diff at `.planning/quick/260629-3agent-audit/01-proposed-edits/ticker_detail.diff` for F-001 + F-002 + F-005 only (ARIA tablist contract; defer spacing/typography MEDs to PR #2)
- `pmacs/web/static/style.css` — apply `.ws-tab` focus-visible + active-state + chevron block from `01-proposed-edits/style.css.diff`
- `pmacs/engines/forward_valuation.py` — V-001 (replace `cycle_id or None` with explicit non-empty assertion OR fallback to `"unknown"`); V-002 + S-002 (add `base_price_underwater: bool` flag, consolidate evidence extraction to one helper)
- `pmacs/agents/sanity/valuation_agent.py` — V-003 (mirror bull≥base≥bear ordering check in pydantic `model_validator`); V-010 (collect all failures into a list)
- `pmacs/agents/prompts/valuation_agent.md` — V-008 (add `data_gaps` rule for `acquisition>0` in pre-profit scenario); V-009 (audit-log the EV/EBITDA-vs-EV/Sales selection)
- `tests/unit/test_valuation_agent_sanity.py` — V-004 (add `TestValuationAgentSanityPreProfit` class with margin=-0.30 fixture)
- `tests/unit/test_memo_writer_forward_valuation.py` — V-011 + S-013: extract `_run_slot` testability, remove orchestrator coupling
- `pmacs/web/templates/agents.html:274` — S-004: add `{% if agent_idle_text is defined %}` guard
- `pmacs/web/templates/ticker_detail.html:L52-75` — F-008: add `<h1>` to header
- `pmacs/web/templates/ticker_detail.html:L160-164` — F-003: bump tab buttons to ≥44px tap target
- `pmacs/web/templates/ticker_detail.html` — F-006: add icon or text-only backup for RSI chip color signaling
- `spec/Source.md §16.9` + `spec/Architecture.md §9.4b` + `spec/Agents.md §13b` + `spec/Phases.md L360` — S-003: one-paragraph entry per file for `_build_current_valuation_anchor` (the model-vs-market reconciliation mechanism)

**Verification:**
- `.venv/bin/python -m pytest tests/unit -q` → still 1332+ passed (must add ≥6 tests for V-004)
- Manual: navigate `/ticker/NBIS` and tab through with keyboard only — all 5 tabs reachable via Arrow keys, focus visible
- `git status --porcelain` returns to the 24-file baseline plus the SPEC edits plus the new test additions

**Out of scope for PR #1 (deferred to PR #2):** F-007 (ad-hoc `-mt-4`), F-009–F-013 (mobile overflow, chip drift, label tokens), S-001 (dead shim), S-005 (4-arg `tojson` injection site), S-006 (comment density), all V-LOW and S-LOW entries.

### PR #2 — "Visual + structural polish"

**Goal:** Apply the rest of the findings as one backward-compatible polish PR. No spec changes; no engine logic.

**Critical files:**
- `pmacs/web/templates/ticker_detail.html` — F-007 (move `-mt-4` into `loading_state.html`), F-009 (wrapping flex on persona header), F-011 (`text-text-muted` contrast bump), F-013 (micro-offset normalization), F-014 (eyebrow label token)
- `pmacs/web/templates/ticker_detail.html` — F-015 (price truncation), F-016 (`aria-label` on bullets), F-017 (`aria-live="polite"` on HALTED span)
- `pmacs/web/templates/ticker_detail.html` — V-005 (rounding comment), V-006 (memo annotation clarification)
- `pmacs/web/templates/agents.html` — F-019 cross-page consistency (link style audit only — no real change), S-005 (collapse 4-arg `tojson` to single dict + serialize), S-007 (consolidate `data-persona` / `aria-label`)
- `pmacs/nervous/orchestrator.py` — S-001 (inline + delete the `_current_mode` shim), S-006 (trim 7-line comment block to 2-3 lines), S-013 (extract `_run_slot` — already done in PR #1, this is a "follow up" if missed), S-014 (log `WizardRedirectMiddleware` exception instead of silently redirecting)
- `pmacs/agents/valuation_agent.py` — V-012 (remove deferred import)
- `pmacs/schemas/forward_valuation.py` — V-015 (`Literal["ev_ebitda","ev_sales","dcf"]` for `valuation_path`), V-020 (explicit horizon clamp)
- `pyproject.toml` — S-012 (add `tomli-w>=1.0` to dev deps OR document why it's intentionally absent)
- All LOW V-* and S-* items as one wave — cherry-pick the highest-confidence only; defer anything speculative to follow-up

**Verification:**
- `.venv/bin/python -m pytest tests/unit -q` → still 1332+ passed (no test edits in PR #2 unless a HIGH surfaces)
- axe-core run on `/ticker/{ticker}` in dark mode + light mode + 375px viewport — zero criticals
- `git log --oneline` shows PR #1 + PR #2 stacked correctly

### Backlog (NOT PR-blocking — folder for the next sprint)

- V-007 (clamp-then-validate ordering edge case) — need a hand-crafted payload test to confirm; defer
- V-018, V-019, V-021, V-022 — LOW items, all cosmetic/cleanup
- F-018 (sparkline role when branch renders empty) — borderline-a11y, no operator impact
- F-020 (chip dot-size drift) — pairs with F-014 token work
- S-010 (test naming verbosity) — by-name only, no behavior
- S-011 — verified clean, nothing to do
- S-007 (data-persona / aria-label split) — defer in favor of S-005

## Why two PRs (not three, not one)

- **One PR** would mix valuation-engine correctness fixes with visual polish and make the diff unreviewable.
- **Three PRs** would split the a11y tab fix from the other `/ticker` HIGHs unnecessarily — they're all in the same template file.
- **Two PRs** matches the operator's existing commit-strategy (PR #4 + PR #5 stacked on `phase-7c-valuation`, per `valuation_agent_forward_engine.md` memory): first PR unblocks Phase 7c shipping with correctness + minimum a11y contract; second PR is a stylistic sweep that's easy to revert if any F-* finding is wrong.

## Open questions for the operator

1. **V-002 + S-002 fix direction** — does the operator prefer (a) add `base_price_underwater` flag to the result + keep two extractors, or (b) extract one `_extract_valuation_primitives` helper used by all three sites? Option (b) is cleaner but touches more lines; option (a) is surgical.
2. **S-005 fix** — collapse 4-arg `tojson` to a dict in the route, or just commit the diff and accept the maintenance trap? The route change is small but crosses the `pmacs/web/routes/agents.py` boundary which Agent 3 was told to scope-minimize.
3. **PR #2 timing** — land immediately after PR #1, OR queue after Phase 8 (Polish) begins per `spec/Phases.md §2`?

## Files written by the audit (already on disk)

- `.planning/quick/260629-3agent-audit/01-frontend-findings.md` (436 lines, 20 findings)
- `.planning/quick/260629-3agent-audit/01-proposed-edits/ticker_detail.diff` (120 lines, F-001..F-008)
- `.planning/quick/260629-3agent-audit/01-proposed-edits/style.css.diff` (62 lines)
- `.planning/quick/260629-3agent-audit/02-valuation-findings.md` (22 findings, BLOCKER + 3 HIGH)
- `.planning/quick/260629-3agent-audit/03-simplify-findings.md` (14 findings, 0 BLOCKER/HIGH)
- This file: `RECONCILED.md`

## Verification checklist (run after operator approves)

- [ ] `git status --porcelain` shows only the 24 baseline files plus the PR-edits
- [ ] `.venv/bin/python -m pytest tests/unit -q` reports **≥ 1338 passed, 2 skipped** (added tests for V-001, V-002, V-003, V-004 = ≥6 new)
- [ ] axe-core run on `/ticker/NBIS` reports zero critical/blocking issues
- [ ] All 3 BLOCKER findings (F-001, F-002, V-001) resolved with file:line citations
- [ ] All 9 HIGH findings resolved OR explicitly deferred to PR #2 with rationale
- [ ] `RECONCILED.md` updated with a "Done" section once PRs are merged
