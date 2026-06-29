# Agent 3 — Code Simplification + Dead-Code Pass (Phase 7c working tree)

**Scope:** `pmacs/agents/base.py`, `pmacs/agents/memo_writer.py`, `pmacs/nervous/orchestrator.py`, `pmacs/schemas/personas.py`, `pmacs/web/app.py`, `pmacs/web/routes/memo.py`, `pmacs/web/routes/pipeline.py`, 6 templates, 5 spec files, `AGENTS.md`, the PLAN.

**Status:** All findings are NON-BLOCKING. No edits applied (read-only audit). 14 findings, 0 BLOCKER, 0 HIGH, 6 MED, 8 LOW.

**Prior audit context (from session memory):**
- `memo_long_only_jun29.md`: 4 files already fixed — do not re-flag.
- `jinja_context_var_desync.md`: templates reload but routes don't — guard with `is defined`.
- `state_region_contract.md`: shared `loading_state/empty_state/error_state` + dispatcher.
- `dual_memo_paths_gap.md`: orchestrator wave-2 persists; pipeline.py does not (verified).
- `cycle_paths_solo_vs_orchestrator.md`: SOLO uses orchestrator (Task #8 Part B, e872b12).
- `keychain_canonical_only.md`: pipeline.py uses canonical `"pmacs.data.finnhub"` — clean.

## Findings (ordered by severity)

### S-001 — MED — Dead module-level shim
- File: pmacs/nervous/orchestrator.py:L5491-L5496
- Smell: `def _current_mode(db_path) -> str:` at module level is a 1-line wrapper that calls `CycleOrchestrator._current_mode(db_path)`. The staticmethod at L5247 already does the same SQL.
- Only caller is `initiate_cycle` at L5308 (module-level). Inside the class, L3188/L3270 bind to the staticmethod via MRO.
- Fix: Inline `CycleOrchestrator._current_mode(db_path)` at L5308 and delete L5488-L5496 (no other callers). Net: -10 lines.

### S-002 — MED — Pre-existing `_extract_valuation_inputs` is now partially shadowed
- File: pmacs/nervous/orchestrator.py:L1979-L2056 (and sole caller L1795)
- Smell: Phase 7c added `_extract_forward_valuation_inputs` (L2058) that returns (revenue_ttm, shares, net_debt) for the ValuationAgent. The pre-existing `_extract_valuation_inputs` returns (market_cap, fcf_ttm, assumed_growth_pct) for the reverse-DCF engine. Both walk the same `edata` index and both check `data.get("revenueGrowthTTMYoy")` (L2024 vs L2202 in `_build_current_valuation_anchor`). The "revenue growth" primitive is now derived in three places, with `_build_current_valuation_anchor` doing it independently of `_extract_valuation_inputs` so the anchor's TTM growth can disagree with the reverse-DCF's assumed growth.
- Fix: Have `_build_current_valuation_anchor` accept the already-extracted `revenue_ttm` only and have callers pass `assumed_growth_pct` from `_extract_valuation_inputs` so the memo + anchor + reverse-DCF all reference one source of truth for the growth primitive.

### S-003 — MED — Spec drift: `_build_current_valuation_anchor` is undocumented
- File: spec/Phases.md (Phase 7c section L349-L376)
- Smell: Phase 7c added `_build_current_valuation_anchor` (orchestrator L2145-L2249) that grounds the ValuationAgent's exit multiple in the market's current EV/Sales, EV/EBITDA, P/S, and analyst-PT — a non-obvious reconciliation (model-vs-market, model-vs-Wall-Street). The mechanism is a single biggest accuracy lever (per the docstring) but is missing from `spec/Phases.md` L349-L376, `spec/Source.md` §16.9, `spec/Agents.md` §13b, and `spec/Architecture.md` §9.4b.
- Fix: Add a one-paragraph entry to each of: `spec/Phases.md` L360 ("What gets built"), `spec/Architecture.md` §9.4b, `spec/Source.md` §16.9, `spec/Agents.md` §13b. LOW severity for spec drift per audit directive.

### S-004 — MED — `agents.html` L274 `agent_idle_text` is unguarded
- File: pmacs/web/templates/agents.html:L274
- Smell: `{{ agent_idle_text.get(persona.id, 'Idle — waiting for cycle') }}` references `agent_idle_text` directly. Per `jinja_context_var_desync.md` memory, templates reload but routes don't, so a partial context (e.g. SSE update) that omits `agent_idle_text` will 500. The route should always supply it, but the safe pattern is `agent_idle_text is defined` guard or default `{}`.
- Fix: Wrap with `{% if agent_idle_text is defined %}{{ agent_idle_text.get(persona.id, 'Idle — waiting for cycle') }}{% else %}Idle — waiting for cycle{% endif %}` or set `agent_idle_text=agent_idle_text or {}` in the route.

### S-005 — MED — `agents.html` L300 four-arg `tojson` JS injection is fragile
- File: pmacs/web/templates/agents.html:L300
- Smell: `onclick='openAgentModal({{ persona.name | tojson | safe }}, {{ persona.analysis | tojson | safe }}, {{ (persona.evidence_cited or []) | tojson | safe }}, {{ persona.confidence | default(0) | tojson | safe }}, ...)'` is a 4-arg (or 5-arg) call packed into a single `onclick` attribute, with each `| tojson | safe` consuming its own filter chain. If a future arg is added (e.g. `key_signal`) the line becomes a maintenance trap, and the `| safe` filter is the only thing preventing double-escaping (one bug in any of the four pipelines = XSS in the modal).
- Fix: Build the JS payload in the route as a single `agent_modal_payload = {"name": ..., "analysis": ..., ...}` dict, serialize once with `tojson`, and use `onclick='openAgentModal({{ agent_modal_payload | tojson }})'`. Single source of escaping, single `tojson`, one arg.

### S-006 — MED — Comment density drift in `_run_forward_valuation`
- File: pmacs/nervous/orchestrator.py:L1895-L1914
- Smell: The "Extract market primitives BEFORE running the agent" comment block (L1898-L1904) is 7 lines explaining why we extract primitives first. It is followed immediately by the 5-line call to `_build_current_valuation_anchor` and the `agent_context` concatenation, which are themselves self-explanatory. The surrounding code (lines 1935-1956, 1962-1977) is comment-light (2-3 line `# Parse the agent's assumptions` and `# --- Forward valuation ...`). The 7-line comment block breaks the project's comment-density idiom.
- Fix: Trim the comment to 2-3 lines (the "single biggest accuracy lever" line + "anchors exit multiple in current EV/Sales"). The full rationale belongs in the docstring of `_build_current_valuation_anchor` (which already has it).

### S-007 — LOW — `agents.html` L172-L175 `data-persona` and `aria-label` are reduntant
- File: pmacs/web/templates/agents.html:L172, L175
- Smell: `data-persona="{{ persona.id }}"` and `aria-label="{{ persona.name }} analysis status"` are both there for testing/a11y. Not a bug, but L172 is the only `data-*` attribute on the card; if JS reads it from the inner element instead, both should be set on the same element (currently only on the outer card div).
- Fix: Leave as-is, or move both attributes to the same element if JS reads the inner one.

### S-008 — LOW — Inline `tojson` style in agents.html L300 differs from the other 3 onclick sites
- File: pmacs/web/templates/agents.html (search for `onclick=`)
- Smell: Other onclick sites in the templates use `data-*` attributes + a small JS lookup, not inline `tojson` payloads. The single inline-payload site (L300) is the outlier and is what makes S-005 worth doing.
- Fix: Same as S-005.

### S-009 — LOW — `agents.html` L165-L167: `_pu_header`/`_pd_header` defaults are hardcoded
- File: pmacs/web/templates/agents.html:L165-L167
- Smell: `{% set _pu_header = persona.p_up | default(0.33) %}` hardcodes 0.33/0.33 as the default for p_up/p_down. The actual prior in arbitration is also ~0.33 (L244-L246 repeats this), so the duplication is fine but undocumented. No correctness issue.
- Fix: Add a one-line comment noting the default is the arbitration neutral prior.

### S-010 — LOW — Test ID naming consistency (no collision, but verbose)
- File: tests/unit/test_memo_writer_forward_valuation.py:L72, L143, L233
- Smell: New file uses `TestReconciliationBlock`, `TestEnginePassesAnchorsThrough`, `TestOrchestratorAnchor`. No ID collision with `test_valuation_agent_sanity.py` (which has `TestValuationAgentSanity*` — all prefixed with `ValuationAgentSanity`) or with `test_memo_writer_data_quality_warnings.py` (which has `TestDataQualityWiring`, `TestPromptContainsWarning`). All class names are unique.
- Fix: No change required; verified clean.

### S-011 — LOW — `test_demo_dispatch_consolidation.py` deletion is clean
- File: tests/unit/test_demo_dispatch_consolidation.py (staged `D`)
- Smell: Grep across `pmacs/` and `tests/` (excluding `.pyc`) finds zero remaining imports of `demo_dispatch_consolidation` or `test_demo_dispatch`. Only the `__pycache__/test_demo_dispatch_consolidation.cpython-313-pytest-9.0.3.pyc` file remains, which is regenerated on first test run. Verified clean.
- Fix: No change required.

### S-012 — LOW — `pyproject.toml` missing `tomli-w` (read-side fine, write-side falls back to `_dump_toml_flat`)
- File: pyproject.toml:L11
- Smell: `tomli>=2.0;python_version<'3.11'"` is declared for read; `tomli-w` (write) is NOT declared. `pmacs/web/routes/settings.py:L1001-L1004` has a try/except: tries `import tomli_w` and falls back to `_dump_toml_flat(data)` (a 30-line minimal flat-section serializer). The comment at L843-L844 explicitly says "tomli_w is not a declared dependency" and at L991-L993 "tomli_w is not declared". This is intentional but undocumented in `pyproject.toml`.
- Fix: Either (a) add `tomli-w>=1.0` to `pyproject.toml` so the canonical path is used, or (b) add a `# noqa: F401` comment near `import tomli_w` and a one-line note in `pyproject.toml` saying the write-side is intentionally absent. The flat-section fallback loses nested table formatting (only stringifies `[section]\nkey=value`), which is observable as a regression when operators save and re-read a TOML file with nested arrays.

### S-013 — LOW — `_dispatch_personas` is 230 lines — extraction candidate
- File: pmacs/nervous/orchestrator.py:L3817-L4047
- Smell: The method spans (1) runner construction, (2) Rec1 macro-caching, (3) the `_run_slot` inner closure (Rec2 dataless-skip), (4) `ThreadPoolExecutor` dispatch, and (5) result collection. Each is independently testable. Extract `_run_slot` (L3882-L3940) into a module-level helper to make the slot semantics unit-testable without spinning up the whole orchestrator.
- Fix: Extract `_run_slot` and the Rec2 logic into `pmacs/nervous/persona_slot_dispatch.py`. Reduces `_dispatch_personas` to ~80 lines and makes the Rec2 short-circuit testable in isolation (the spec is documented in the comment block, not in a test).

### S-014 — LOW — `app.py` `WizardRedirectMiddleware` exception path always redirects to `/wizard/`
- File: pmacs/web/app.py:L213-L216
- Smell: When `_read_wizard_state()` raises (e.g. `data_dir` not writable, malformed `wizard.json`), the middleware catches `Exception` and redirects to `/wizard/`. This silently converts transient infrastructure errors into "the user hasn't completed the wizard" UX, which is the same redirect path used for the genuine first-run case. Operators will see "Set up the wizard" instead of "DB is unwritable, check disk space."
- Fix: Log the exception (level=`WARN`) before redirecting, and only redirect to `/wizard/` when the state is genuinely missing/empty. On other exceptions, return a 503 with a clear error message. Mirrors the pattern at L51 (`_close_stuck_cycles` catches `Exception` and silently passes — the same anti-pattern, but the impact there is "skip cleanup" vs "lie to the user about wizard state").

## Methodology summary

- **Dead code paths in orchestrator** (per memory `cycle_paths_solo_vs_orchestrator.md`):
  - `_step_13d5_debate` (L1619-L1744) — 6,042 chars, no early-return-before-work branches. All returns are post-collection of wave-2 results. Clean.
  - All 4 bare `return` statements (L915 `_step_universe_sync`, L3556 `_log_call_billing`, L4592 `_step_opportunity_cost`, L5159 `_db_execute_with_retry`) are legitimate early exits documented in their enclosing docstrings. Clean.
  - `_step_universe_sync` L915 is the SOLO-path short-circuit (sets `_universe_tickers` from `self._requested_tickers` and returns — the DB-scan path is intentionally skipped). Clean.

- **Pre-existing dead code that Phase 7c made reachable**:
  - `_extract_valuation_inputs` (L1979) is now reachable only from `_compute_valuation` → `_last_reverse_dcf` pipeline. Confirmed only one caller (L1795). Not dead, but the duplication with `_extract_forward_valuation_inputs` (L2058) and `_build_current_valuation_anchor` (L2145) is the spec drift in S-002.

- **Phase 7c change made code dead (or vice versa)**:
  - No code became dead; the `_build_current_valuation_anchor` addition uses three new local primitives (`ebitda_ttm`, `target_mean`, `rev_growth_ttm_yoy`) that are not extracted via helpers — they're re-derived inside the method (see S-002).

- **Jinja template safety** (per `jinja_context_var_desync.md`):
  - Swept 6 templates; 1 unguarded direct reference (`agent_idle_text` at agents.html L274) — S-004.
  - Other `{{ var }}` references in memo.html, _decisions.html, base.html are all inside `{% if X %}` or `{% if X is defined %}` guards. False positives: heuristic flagged `{{ prev_ticker }}` and `{{ next_ticker }}` at memo.html L45/L56/L79/L87 as "unguarded" but they're actually inside `{% if prev_ticker %}` / `{% if next_ticker %}` blocks (L44, L55, L78, L86). The heuristic was too coarse.
  - `_decisions.html` and `agents.html` recent `<a href="/ticker/{{ t.symbol }}">` wrappers (commit 4a452df): verified the wrapper preserves the inner span's `id` and class, so existing JS that does `$('#current-ticker').text(...)` keeps working. The text content moved from being a direct child of `<span>` to being inside the `<a>`, but `text()` reads the merged text content of all descendants, so no JS breakage. Verified via inspection of L34-L50 of agents.html.

- **TODO/FIXME/HACK sweep** in scope:
  - Orchestrator (5496 lines), base.py (1873 lines), memo_writer.py (306 lines), app.py (387 lines), memo.py (264 lines), pipeline.py (673 lines), all 6 templates: ZERO matches.
  - `pmacs/installer/steps/smoke_test.py:23` is the only known carry-over — already noted as out-of-scope (Agent 1 territory).

- **Comment density drift** (per CLAUDE.md):
  - S-006 flags the 7-line "Extract market primitives BEFORE running the agent" block in `_run_forward_valuation`. Surrounding code uses 2-3 line comments; the block stands out.
  - S-014 is more of a "silently-catch + redirect" anti-pattern than a comment-density issue, but the change set's overall idiom is "fail loud + audit-log", which the wizard exception path violates.

- **Test files**:
  - `test_memo_writer_forward_valuation.py` is new (S-010). Class names: `TestReconciliationBlock`, `TestEnginePassesAnchorsThrough`, `TestOrchestratorAnchor`. No collision with `test_valuation_agent_sanity.py` (which has `TestValuationAgentSanity*`) or `test_memo_writer_data_quality_warnings.py` (which has `TestDataQualityWiring`, `TestPromptContainsWarning`).
  - `test_demo_dispatch_consolidation.py` is staged `D`. No remaining imports anywhere (S-011).

- **Spec consistency** (read-only):
  - Spec §4 of Architecture.md references dispatch parallelization via llama-server's 3-slot pool (L2523, L2546) — the code matches. Clean.
  - `Spec/Phases.md` Phase 7c section L349-L376 lists 6 files but omits `_build_current_valuation_anchor` (S-003). Drift is documented as a finding.
  - Spec §4 mentions wave-2 dispatch (L2534) — matches `_step_13d5_debate`. Clean.
  - `spec/Source.md` §16.9 (referenced from `Agents.md:64` as the source-of-truth for forward-valuation memo display) does NOT mention `current_valuation_anchor`, `model-vs-market`, or `model-vs-Wall-Street` reconciliation — drift is included in S-003.

- **Settings §20 area** (per `settings_section20_expansion.md`):
  - `_dump_toml_flat` lives at `pmacs/web/routes/settings.py:L1026` (NOT in my scope — owned by Agent 1 via the routes cluster, but the missing-dep finding is in `pyproject.toml` which is shared infra). S-012.

- **State region contract** (per `state_region_contract.md`):
  - All 6 templates use `{% include "components/error_state.html" %}` for the shared error/empty dispatcher (memo.html L12, agents.html L12, universe.html L12, settings.html L12).
  - No bare "Loading…" or "No data" state containers found. The "no data yet" / "no data" / "Reloading..." strings are all toast messages or stat labels, not state containers.

- **Dual memo paths gap** (per `dual_memo_paths_gap.md`):
  - Confirmed: only `_step_13mn_post_decision` (L2836+) writes to the `memos` table. The `INSERT INTO memos` at L3064 is the only such write in the codebase. `pmacs/web/routes/pipeline.py` has no `INSERT INTO memos` despite the docstring at L538 saying it "persists a structured memo" — that docstring is stale (S-003-related spec drift).

- **SOLO vs orchestrator paths** (per `cycle_paths_solo_vs_orchestrator.md`):
  - Verified `_step_13d5_debate` runs in `_run_symbol` at L1283 unconditionally after wave-1 commits. The SOLO path (`/api/solo/run` → `_launch_orchestrator_cycle` → `run_cycle` → `_run_symbol`) reaches the same `_step_13d5_debate`. Both paths produce wave-2 outputs and both paths inject `agent_signals` + `crucible_*` into `memo_dict` (L3021-L3058). Clean.
  - `_step_13mn_post_decision` writes to the `memos` table at L3064, so SOLO and orchestrator-cycle paths both persist the same `memos` row. The dual-path convergence from `e872b12` holds.

- **Keychain canonical-only** (per `keychain_canonical_only.md`):
  - `pmacs/web/routes/pipeline.py:430` uses `get_api_key("pmacs.data.finnhub", "api_key")` — canonical long-name slot. Clean.
  - No short-name slots (`openai`, `anthropic`, `fred`, etc.) referenced anywhere in my scope files.

## Severity justification

- **0 BLOCKER, 0 HIGH**: No correctness, security, or data-loss issues. No tests broken. No memory/SOLO/orchestrator path divergence. The Phase 7c changes (commit 0d74fc8, 8056b1b, 4a452df, a1e67c4, d1350ae) are coherent with the pre-existing architecture.
- **6 MED**: Spec drift on a new mechanism (S-003), a shim function (S-001), a duplicated extraction helper (S-002), a single `is defined` gap (S-004), a 4-arg `tojson` injection site (S-005), and one comment-density outlier (S-006).
- **8 LOW**: All the rest are style/extraction/cleanup candidates with no behavioral impact.

## What I deliberately did NOT flag

- The 6 KB `_step_13d5_debate` body itself: no early-return-before-work branches, all returns are post-collection. The method is long but each section is single-purpose (per-slot dispatch, timeout handling, auditor parse, log). Extracting wouldn't materially help readability; a `_dispatch_with_timeout` helper (already exists at L3643) is used elsewhere for the same pattern.
- `pmacs/web/app.py:128-129` (`"pytest" not in _sys.modules`): runtime check is redundant with the same check in `CSRFMiddleware.dispatch` (L146) and `WizardRedirectMiddleware.dispatch` (L210), but each is defensive and the cost is one `in` per request. Not worth refactoring.
- The 3 `data-time-ago` attributes across templates (universe.html L93, agents.html L308, etc.): these are all consumed by a single JS helper to compute "5m ago" / "2h ago" rendering. Consistent pattern. Clean.
- The 7 `_step_13*` method names: 13a, 13b, 13c, 13d, 13d5, 13e, 13e5, 13fg, 13h, 13i, 13j, 13k, 13l, 13mn, 13o, 13p — the numbering is a spec convention (Source.md §15) and matches the spec exactly. Don't normalize.
- `_extract_valuation_inputs` (L1979) and `_extract_forward_valuation_inputs` (L2058) being two separate methods: they serve different engines (reverse-DCF vs forward-valuation) and the input shapes are intentionally different. The duplication of `edata` indexing is a minor smell but consolidating would require a new "evidence indexer" abstraction. Marked S-002 (MED) to surface the drift, not to mandate a refactor.
