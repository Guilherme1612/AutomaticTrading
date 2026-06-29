# Agent 2 — Phase 7c ValuationAgent + ForwardValuationEngine audit

Scope: `pmacs/agents/valuation_agent.py`, `pmacs/agents/sanity/valuation_agent.py`,
`pmacs/engines/forward_valuation.py`, `pmacs/schemas/forward_valuation.py`,
`pmacs/agents/prompts/valuation_agent.md`, `tests/unit/test_valuation_agent_sanity.py`,
`tests/unit/test_memo_writer_forward_valuation.py`.

## Summary

Equity-floor fix (Phase 7c PR #5) **verified intact** at `forward_valuation.py:136-146`.
EV/Sales fallback path **verified intact** at `forward_valuation.py:107-122`. The cash ->
EV -> equity chain produces sane non-negative prices for negative-cash names (ONDS/NBIS).
No pydantic-v1 violations, no SBC math, no `json.dumps` in audit paths, no LLM math.
One real BLOCKER (`_log` `cycle_id or None` defensiveness would crash `log_debug` if the
caller ever passes empty string), and a handful of MED/LOW items — mostly test-coverage
gaps and minor code-smell.

## Findings

- ID: V-001
- Severity: BLOCKER
- File: pmacs/engines/forward_valuation.py:L315
- Root cause: `_log` calls `log_debug(..., cycle_id=cycle_id or None)`; the orchestrator
  always passes a real `cycle_id` today, but `compute_forward_valuation`'s caller contract
  does not enforce non-empty, and `log_debug` raises `ValueError` ("cycle_id REQUIRED for
  cycle-scoped events (Architecture.md §5.2)") for any cycle-scoped event with `None`.
  `FORWARD_VALUATION_COMPUTED` is not in `SYSTEM_EVENT_TYPES`, so an empty `cycle_id`
  in the call path would crash every valuation — no graceful degradation.
- Fix sketch: pass `cycle_id=cycle_id if cycle_id else "unknown"` (or assert non-empty
  at `compute_forward_valuation` entry) instead of `or None`.
- Test needed: add a test calling `compute_forward_valuation(cycle_id="", ...)` and
  assert no crash + a WARN-level log, OR assert the function raises a typed error
  early (whichever is preferred); not covered at `test_memo_writer_forward_valuation.py:163`.

- ID: V-002
- Severity: HIGH
- File: pmacs/schemas/forward_valuation.py:L86-88
- Root cause: `is_available` returns False when `base_price` is exactly `0.0`
  (equity-floored-underwater case). The downstream consequence is the entire
  `ForwardValuationResult` is treated as "unavailable" by `ScenarioPriceEngine`
  per spec §9.4b ("when not available, it falls back to the reverse-DCF grid
  unchanged"), silently dropping a real distress signal — the operator loses
  the base-price-as-floored-zero, the bull/bear range, and the EV/Sales path
  notes. A deep-underwater base (forward EV < net debt) is a legitimate
  valuation, not a missing primitive.
- Fix sketch: differentiate "computed but floored at $0" from "missing primitive"
  — e.g. add a `base_price_underwater: bool` flag on `ForwardValuationResult` and
  gate `is_available` on `base_price is not None` only (or add a separate
  `is_priced` property that is True for both floored-at-0 and positive).
- Test needed: a test that computes a result with `net_debt > forward_ev` and
  asserts `is_available` (or the new flag) reflects the floored-but-priced case;
  not covered at `test_memo_writer_forward_valuation.py:117-128` (only the
  primitives-missing case is tested).

- ID: V-003
- Severity: HIGH
- File: pmacs/agents/sanity/valuation_agent.py:L171-179
- Root cause: the bull>=base>=bear growth-ordering check rejects outputs where
  `g_bull < g_base - 0.001` OR `g_bear > g_base + 0.001`, but uses a STRICT
  ordering (no equality tolerance). A legitimate LLM output where bull==base
  (e.g. "growth stalls in both bull and base") is allowed, but a single base
  value that equals bull OR bear will pass; however, the underlying validator
  in `personas.py:864` (`_check_invariants`) does NOT enforce ordering at all.
  The two layers disagree: the schema-level model validator accepts any
  bull/base/bear tuple, the sanity layer rejects many of them. There is no
  parallel invariant in the schema, so a producer bypassing sanity (or
  pydantic-coerced) could pass a wrong-ordered output downstream.
- Fix sketch: add a `@model_validator(mode="after")` on `ValuationAgentOutput`
  (mirror in `personas.py`) that enforces bull>=base>=bear growth, OR drop the
  sanity check and rely on the schema; current dual-layer is inconsistent.
- Test needed: unit test asserting schema and sanity agree on the bull<base
  rejection; not covered at `test_valuation_agent_sanity.py:242-260`.

- ID: V-004
- Severity: HIGH
- File: tests/unit/test_valuation_agent_sanity.py:1-261
- Root cause: the test file is missing coverage for the pre-profit/EV/Sales
  path the agent is most likely to take (per `crucible_as_primary_filter.md`,
  most current tickers are pre-profit). No test asserts that an LLM output
  where `ebitda_margin <= 0` REQUIRES `exit_sales_multiple`, that the exit
  multiple is in `[0, 100]`, that the "acqui" data_gaps note is required when
  acq>0 in a pre-profit scenario, and that the persona validator passes a
  realistic NBIS/ONDS-shaped output. The `_scenario` fixture (line 22) has
  margin=0.22 (profitable), so the pre-profit branches of `_persona_checks`
  (lines 95-109) are never exercised.
- Fix sketch: add a `TestValuationAgentSanityPreProfit` class with a pre-profit
  fixture (margin=-0.30, exit_sales_multiple=18.0, exit_multiple=None); assert
  the pre-profit path validates, that missing exit_sales_multiple fails, and
  that an out-of-range exit_sales_multiple (>100 or <0) fails.
- Test needed: this is the test gap itself.

- ID: V-005
- Severity: MED
- File: pmacs/engines/forward_valuation.py:L111
- Root cause: `forward_ebitda = round(forward_revenue * margin, 2)` uses the
  raw `forward_revenue` (which is itself rounded to 2dp at line 86), so the
  rounding compounds: organic rounded to 2dp, then multiplied by margin (full
  precision) and re-rounded. For very large revenue bases (e.g. >$10B TTM),
  the 2dp rounding on `forward_revenue` can lose up to $50k of precision per
  scenario. Minor for equities (4-decimal cents precision downstream) but
  worth a comment.
- Fix sketch: keep `organic` (unrounded) and pass it directly to
  `forward_ebitda`; only round the final per-share price.
- Test needed: not load-bearing for correctness at this precision.

- ID: V-006
- Severity: MED
- File: pmacs/engines/forward_valuation.py:L264-273
- Root cause: `expected_price_usd` is computed from `(p_bull/total)*bull_price + ...`
  using the AGENT's scenario probabilities (per spec §9.4b, NOT the Arbitrated
  vector), but the result's `expected_price_usd` is rendered in the memo with
  no annotation distinguishing it from `ScenarioPriceResult.expected_price_usd`
  (which uses the Arbitrated vector). The two are weighted differently and a
  consumer reading the memo cannot tell which weighted scheme produced the
  number. Risk of double-counting in downstream analytics.
- Fix sketch: add a docstring on `ForwardValuationResult.expected_price_usd`
  (and on the memo render) explicitly tagging it as "agent-weighted, not
  arbitrated" so the two are not conflated.
- Test needed: integration-level — confirm memo labels the two differently.

- ID: V-007
- Severity: MED
- File: pmacs/agents/sanity/valuation_agent.py:L19
- Root cause: `_PROB_SUM_TOL = 0.10` accepts a sum of 0.90 OR 1.10 (10% tolerance)
  and silently re-normalizes via `personas.py:876-884`. With pre-validate clamping
  in `valuation_agent.py:_clamp_numeric_fields`, a scenario's `probability_of_occurrence`
  can be clamped from 0.95 to 1.0, producing a degenerate-distribution state AFTER
  clamping (bull 1.0, base 0.0, bear 0.0) that the persona validator (line 887) would
  reject — but the validator runs BEFORE clamping by `_clamp_numeric_fields`
  in the pre-validate chain, so the order is correct. However, the chain
  `_clamp_numeric_fields` then `_ensure_min_evidence_ids` can produce
  an unparseable output downstream if clamp + truncation interact on the
  same field (e.g. a probability field clamped to 1.0 then truncated).
- Fix sketch: make the order explicit: clamp probabilities LAST, after
  everything else, so degenerate detection sees the final values.
- Test needed: end-to-end pre-validate on a hand-crafted payload with
  probability=0.95 to assert the final output is not degenerate.

- ID: V-008
- Severity: MED
- File: pmacs/agents/prompts/valuation_agent.md:L131-156
- Root cause: the prompt asks the LLM for `data_gaps` (list of strings),
  `evidence_ids` (top-level, list), and per-scenario `evidence_ids` (list) and
  `rationale` (string with embedded evidence_id citation). The schema
  (`personas.py:855-862`) requires `evidence_ids` to have `min_length=1` at
  the top level AND `min_length=1` per scenario, but the sanity validator
  (`sanity/valuation_agent.py:38-49`) only checks that cited IDs RESOLVE to
  known packets — it does NOT check that at least one ID from the top-level
  `evidence_ids` is also cited in any scenario's `rationale`. So a degenerate
  output where top-level `evidence_ids` = `["e1"]` but every scenario's
  `rationale` cites a DIFFERENT id (e.g. `e2`, `e3`) passes sanity, but the
  operator sees "e1" in the top-level block with no scenario explanation.
- Fix sketch: add a sanity check that the top-level `evidence_ids` subset
  is referenced in at least one scenario's `rationale` OR the top-level
  rationale.
- Test needed: a sanity test that passes a top-level eid not used in any
  scenario rationale, asserting the new check fails.

- ID: V-009
- Severity: MED
- File: pmacs/engines/forward_valuation.py:L107
- Root cause: `can_ev_ebitda = margin is not None and margin > 0.0 and exit_mult is
  not None and exit_mult > 0.0` — this is correct, BUT `can_ev_sales` is
  checked second with `exit_sales > 0.0`. If a profitable scenario has
  `exit_mult=15.0, exit_sales=10.0, margin=0.25`, the EV/EBITDA path wins
  silently — the agent's `exit_sales_multiple` is ignored. This is a behavior
  choice, but the prompt (lines 24-27) tells the LLM "leave exit_sales_multiple
  null" for profitable names — yet the schema does not enforce that, and a
  careless LLM could populate both. The engine silently picks EV/EBITDA which
  may not match the agent's intent. No audit log captures the choice.
- Fix sketch: if BOTH are provided, log which was selected and why; consider
  enforcing schema-level "pre-profit implies exit_multiple=None" via
  model_validator.
- Test needed: a test with both exit_mult and exit_sales provided to a
  profitable scenario, asserting the chosen path is logged.

- ID: V-010
- Severity: MED
- File: pmacs/agents/sanity/valuation_agent.py:L82-118
- Root cause: the per-scenario check is INSIDE the `for name in ("bull", "base", "bear")`
  loop, so if `bull` fails on `exit_multiple` bounds, base and bear are never checked.
  This means a single early failure masks downstream issues. A bad `base` block could
  hide a structurally invalid `bear` block from review. Spec says "first failure wins"
  is acceptable, but the current ordering makes the "acquisition" branch the last
  check, after the rationale citation branch — an acquisition-without-data-gaps bug
  is only caught when EVERY other check has passed.
- Fix sketch: collect ALL failures and return them as a list, or reorder checks
  by severity (cross-scenario invariants first, per-scenario bounds last).
- Test needed: not load-bearing; current behavior is "fail fast" which is
  defensible.

- ID: V-011
- Severity: MED
- File: tests/unit/test_memo_writer_forward_valuation.py:233-276
- Root cause: `TestOrchestratorAnchor` instantiates the real `CycleOrchestrator`
  with a tmp `db_path` and `audit_path` (and SSEPublisher). The orchestrator is
  in Agent 3's scope (`pmacs/nervous/orchestrator.py`), so this test depends
  on Agent 3's internals staying stable. The test is also not isolated from
  the orchestrator's other side effects (any `_audit` initialization, etc.).
  Risk: this test will fail in the audit-fix branch if Agent 3 changes the
  orchestrator signature.
- Fix sketch: extract `_build_current_valuation_anchor` into a free function
  (e.g. `pmacs/engines/forward_valuation_anchor.py`) and test it directly,
  or pass an orchestrator-protocol mock.
- Test needed: refactor — out of scope for this audit, flag for cross-agent
  coordination.

- ID: V-012
- Severity: LOW
- File: pmacs/agents/valuation_agent.py:L110-112
- Root cause: `_format_evidence` is a `@staticmethod` that does a deferred
  import of `PersonaRunner` (`from pmacs.agents.base import PersonaRunner`).
  The deferred import is unnecessary — `PersonaRunner` is already in the
  module-level namespace via the existing import at line 15 (`from
  pmacs.agents.base import PersonaRunner`). Defensive but redundant.
- Fix sketch: remove the inner import.
- Test needed: none.

- ID: V-013
- Severity: LOW
- File: pmacs/engines/forward_valuation.py:L68
- Root cause: `acq_pct = _coerce_frac(assumptions.get(...)) or 0.0` uses `or`
  to default None->0.0, but `or` also replaces `0.0` with `0.0` (no-op) and
  replaces `0` with `0` (no-op). If `_coerce_frac` ever returns a falsy
  non-zero value (it doesn't today, but is not annotated as float-only),
  `or 0.0` would swallow it. Tighten the type to `float | None` and use an
  explicit `is None` check.
- Fix sketch: `acq_pct_v = _coerce_frac(...); acq_pct = acq_pct_v if
  acq_pct_v is not None else 0.0`.
- Test needed: not load-bearing.

- ID: V-014
- Severity: LOW
- File: pmacs/engines/forward_valuation.py:L301-317
- Root cause: `_log` always emits at INFO level (line 314). The spec
  §16.14 anti-pattern says WARN+ events must have `error_code`; INFO is fine.
  However, the `FORWARD_VALUATION_COMPUTED` event fires on every cycle for
  every ticker, and includes `notes` which can be unbounded (the engine
  joins `; `-separated strings that grow with degradation count). At scale
  this could balloon the debug log. Minor, but a notes-truncation cap would
  be defensive.
- Fix sketch: truncate `notes` to e.g. 500 chars before emitting.
- Test needed: not load-bearing.

- ID: V-015
- Severity: LOW
- File: pmacs/schemas/forward_valuation.py:L19-44
- Root cause: `ForwardScenarioPoint` is `frozen=True` (line 26) but the
  field `valuation_path: str | None = None` is `str`, not `Literal["ev_ebitda",
  "ev_sales"]`. The engine only ever sets one of two values (line 113, 122),
  but a future producer (or a corrupted LLM round-trip) could set a third
  value and the schema would not catch it.
- Fix sketch: tighten to `Literal["ev_ebitda", "ev_sales"]`.
- Test needed: schema-level.

- ID: V-016
- Severity: LOW
- File: pmacs/agents/sanity/valuation_agent.py:L165
- Root cause: rationale citation check uses `eid in rationale` (substring
  match). An evidence_id like `"e1"` would match if the rationale contains
  `"test_e1_passed"` — false positive citation. A more rigorous check
  would word-boundary the match or require the eid appears as a quoted
  token.
- Fix sketch: split rationale on whitespace + punctuation, check membership.
- Test needed: a test with rationale `"e10 cited"` and evidence_id `e1`
  asserting the current (substring) match is flagged as ambiguous.

- ID: V-017
- Severity: LOW
- File: pmacs/engines/forward_valuation.py:L228-230
- Root cause: the degraded result still passes `current_revenue_ttm_usd` (None
  when missing) to `ForwardValuationResult`. The test at
  `test_memo_writer_forward_valuation.py:189-204` exercises this. The
  schema field is `current_revenue_ttm_usd: float | None = None`, so None
  is fine. No bug, but the test name (`test_degraded_result_still_carries_anchors`)
  is the only test verifying the schema-acceptable-None path, so the field
  is well-covered — no action needed.
- Fix sketch: no change.
- Test needed: already covered.

- ID: V-018
- Severity: LOW
- File: pmacs/agents/sanity/valuation_agent.py:L95
- Root cause: `pre_profit = isinstance(margin_v, (int, float)) and float(margin_v)
  <= 0.0` — when `margin_v` is a `bool` (Pydantic can coerce `True`/`False` to
  1.0/0.0 in some flows), `isinstance(True, int) is True` in Python, so
  `pre_profit` would be `True` for `True`. The schema has `ebitda_margin_at_horizon_pct: float`,
  so a bool would be rejected at validation — defensive but not load-bearing.
- Fix sketch: no change.
- Test needed: not load-bearing.

- ID: V-019
- Severity: LOW
- File: tests/unit/test_memo_writer_forward_valuation.py:38
- Root cause: the test fixture `_result` accepts `path="ev_sales"` as default,
  and `test_ev_ebitda_path_shown_when_profitable` (line 94) sets
  `path="ev_ebitda"`. The fixture is per-result, not per-scenario — every
  scenario in the dict gets the same `path`. The engine produces
  per-scenario paths (bull/base/bear independently), so a test fixture with
  one shared `path` is less rigorous than the real engine output. The
  reconciliation assertions would pass for a degenerate engine output where
  bull and base have different paths.
- Fix sketch: extend `_result` to accept per-scenario paths.
- Test needed: a test that exercises mixed-path scenarios (bull EV/EBITDA,
  base EV/Sales, bear EV/Sales) and asserts the memo renders all three paths.

- ID: V-020
- Severity: LOW
- File: pmacs/engines/forward_valuation.py:L198-202
- Root cause: horizon is clamped silently to [6, 12] with a `notes_bits` note
  but NO log_debug event. If a caller passes `horizon_months=0` (data error),
  the engine silently uses 6 and the operator has no signal that the
  intended horizon was lost.
- Fix sketch: emit a WARN-level `FORWARD_VALUATION_HORIZON_CLAMPED` event
  with `original=horizon_months, clamped=horizon`.
- Test needed: a test calling `compute_forward_valuation(horizon_months=0,...)`
  and asserting a WARN log line.

- ID: V-021
- Severity: LOW
- File: pmacs/agents/prompts/valuation_agent.md:L98
- Root cause: prompt says "must sum to ~1.0" for probabilities — but the
  schema validator (`personas.py:871`) enforces only `±0.10` tolerance
  AND silently re-normalizes if within tolerance. This means an LLM
  output of 0.30/0.40/0.30 stays as-is, but 0.35/0.40/0.30 (sum 1.05)
  is re-normalized to 0.33/0.38/0.29 — a small mutation the agent did not
  intend. The prompt should warn that normalization happens.
- Fix sketch: add a one-liner to the prompt: "If the sum is between 0.90
  and 1.10 the engine will renormalize to exactly 1.0 — emit a sum closer
  to 1.0 to avoid silent renormalization."
- Test needed: a test asserting that sum=1.05 is renormalized and
  noted in the output.

- ID: V-022
- Severity: LOW
- File: pmacs/agents/valuation_agent.py:L77
- Root cause: `_log_normalization` is called with `ticker=parsed.get("ticker", "")`
  — if the LLM returns no `ticker` (rare but possible), the normalization
  log is unattributed. The orchestrator already has `ticker` in scope; passing
  it explicitly would be more robust.
- Fix sketch: accept `ticker` as a parameter to `run` and pass through
  to `_pre_validate`.
- Test needed: not load-bearing (orchestrator always supplies ticker).

## Cross-checks (not findings, just verification)

- **Equity-floor fix**: verified at `forward_valuation.py:136-146`. For ONDS-shape
  inputs (revenue=$96.6M, net_debt=-$556.9M, forward_ev=$5.8B), `equity_value =
  max(0, 5.8B - (-556.9M)) = 6.35B > 0`. For a hypothetical underwater case
  (forward_ev=$100M, net_debt=$200M), the floor kicks in and the note is set.
  Already covered at `tests/unit/test_forward_valuation.py:278` (Agent 3 scope).

- **EV/Sales fallback**: verified at `forward_valuation.py:107-122`. For
  pre-profit (margin<=0) with `exit_sales_multiple=30.0`, the path produces
  `forward_ev = forward_revenue * 30.0` and a non-negative price. The path
  is unit-tested at `test_memo_writer_forward_valuation.py:163-185` and
  `test_full_reconciliation_line_renders` (line 73).

- **Pydantic v2 compliance**: verified — all files in scope use `model_config =
  ConfigDict(...)` (not `class Config:`), `model_validate` (not `parse_obj`),
  `field_validator` / `model_validator(mode="after")` (not `@validator`).
  Zero `from pydantic.v1 import` matches.

- **No LLM math**: verified — the agent emits ASSUMPTIONS only, the engine
  computes the price (Architecture.md §1.6 compliance). The prompt explicitly
  says "You DO NOT emit a price." (line 7).

- **No `json.dumps` in audit paths**: verified — orchestrator uses `_json.loads`
  (line 1947) for parsing the LLM JSON, not for emitting audit events.

- **No `cycle_id=None` on audit-emitting functions in scope**: the only
  occurrence is `forward_valuation.py:315` (`cycle_id=cycle_id or None`),
  flagged as V-001. All other cycle_id usages pass a real value.

- **No SBC double-application**: verified — the engine does not apply SBC
  adjustments anywhere (no `sbc` references in `forward_valuation.py`).
  The prompt mentions `annual_sbc` (line 58) as a fundamentals
  observation, not as a math input. Spec §9.4b does not require SBC
  adjustment for the forward-valuation path, only for reverse-DCF (where
  it lives in Agent 3's `reverse_dcf.py`).

- **Multiples from fundamentals (yfinance primary)**: verified — the
  `current_ev_sales` anchor is computed by the orchestrator from
  `fundamentals_{ticker}_metrics` (yfinance) and is passed to
  `compute_forward_valuation` (line 1975). The engine does not fetch
  multiples; it only consumes the passed `current_ev_sales` and
  `analyst_target_mean_usd` for memo reconciliation.

- **Mutations**: verified — `model_dump()` at orchestrator line 1967-1969
  produces a copy, no production-state mutation. The persona validator's
  `object.__setattr__` mutation (personas.py:882-884) is on a frozen model
  bypass using `object.__setattr__` — a known Pydantic pattern, not a bug.

## Test coverage matrix

| Behavior | test_valuation_agent_sanity.py | test_memo_writer_forward_valuation.py |
|---|---|---|
| Evidence_id resolution | covered (L83-95) | n/a |
| Probability sum ±0.10 | covered (L147-154) | n/a |
| Degenerate dist (all bull) | covered (L156-163) | n/a |
| Degenerate dist (all bear) | covered (L165-172) | n/a |
| Horizon bounds [6,12] | covered (L103-111) | n/a |
| Exit multiple bounds | covered (L113-125) | n/a |
| Margin bounds | covered (L127-139) | n/a |
| Margin trajectory sign | covered (L180-194) | n/a |
| Acq>0 requires LOW/MODERATE | covered (L202-209) | n/a |
| Acq>0 requires data_gaps note | covered (L211-218) | n/a |
| Acq LOW + note passes | covered (L220-226) | n/a |
| Rationale cites evidence_id | covered (L234-239) | n/a |
| Growth ordering (bull>=base>=bear) | covered (L247-260) | n/a |
| EV/Sales fallback happy path | NOT COVERED | covered (L163-185) |
| EV/Sales fallback for pre-profit scenario in **sanity** | NOT COVERED | n/a (engine only) |
| Equity-floor at $0 (forward_valuation) | n/a (engine) | n/a (Agent 3 test) |
| Reconciliation line in memo | n/a | covered (L73-91) |
| Anchors round-trip through engine | n/a | covered (L163-185) |
| Degraded result preserves anchors | n/a | covered (L187-204) |
| `is_available` false when base_price=0.0 (equity floored) | NOT COVERED | NOT COVERED |
| `cycle_id=""` does not crash engine | NOT COVERED | NOT COVERED |
| `horizon_months=0` clamping | NOT COVERED | NOT COVERED |
| Sum=1.05 silent renormalization | NOT COVERED | n/a |
| Mixed per-scenario paths in memo | n/a | NOT COVERED (V-019) |

22 findings (1 BLOCKER, 3 HIGH, 7 MED, 11 LOW).
