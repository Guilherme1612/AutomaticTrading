# Prompt: Memo Quality Loop + Prior-Memo Reuse + Future-Agent Improvements

> Companion to `/Users/guilherme/.claude/plans/sleepy-knitting-naur.md`. Use this as the high-level directive; the plan file is the execution contract.

## Goal

Make the PMACS memo subsystem **higher quality, more accurate, more fluid, and more confidence-bearing on agents** by:

1. Activating the existing-but-dead memo scorer with a single retry on low scores
2. Re-injecting the FULL prior memo (thesis, fair_value, methodology, evidence, risks, forward expected price) into the next cycle's memo prompt — no more 200-char truncation
3. Closing three known future-agent (ValuationAgent + ForwardValuationEngine) gaps:
   - cross-check vs reverse-DCF (catch LLM hallucination)
   - surface distress flag (`base_price_underwater`) in-band
   - signal probability convergence (when agent's scenarios are nearly equal-weighted)
4. Surfacing **per-persona arbitration weights + per-persona DuckDB calibration** in the memo so the operator can see who drove the verdict and how reliable each persona is on this ticker

## Directives

### Five Non-Negotiables (apply on every commit)
- **LLMs never sign** — no change to execution path.
- **LLMs never math** — ForwardValuationEngine math stays in Python; only the LLM emits assumptions.
- **Hash-chained audit** — every state transition emits an audit event with `prev_sha256` and a `cycle_id`.
- **Mode-pure inference** — no backend switching logic added.
- **Operator owns kill switch** — no auto-promotion, no auto-retry loops beyond the explicit cost-capped 1-retry-on-low-score.

### Implementation rules
- **Additive, never destructive.** Existing fields stay. New fields default to None / empty / no-op.
- **One concern per commit.** Four atomic commits (see plan §"Implementation Plan").
- **Test before commit.** Each commit adds a test file that goes green on its own.
- **No spec changes.** Everything here is anticipated by `spec/Agents.md §13.5` (memo scoring) and `spec/Architecture.md §16.9` (memo persistence). Spec amendment for sanity envelope widening is a separate ticket.
- **Anti-pattern check**: no `json.dumps` for audit (use `canonical_json`); no `cycle_id=None`; no mutating evidence packets in freshness checks; no EUR/USD field; no runtime prompt edits; every WARN+ has an `error_code`.

### Reuse first (no reinventing)
- `pmacs/agents/sanity/memo_scorer.py::score_memo()` — already exists, 6 dimensions, 0-100, A-F grade, retry feedback formatter. **Just call it.**
- `pmacs/agents/sanity/memo_scorer.py::format_retry_feedback()` — already exists. Use it.
- `Arbitrated.persona_weights` (schema) — already exists. Just read it.
- `ForwardValuationResult.base_price_underwater` — already exists. Just expose it.
- `persona_ticker_affinity` DuckDB table — already populated. Extend the SELECT.
- `MemoWriterRunner.set_analytical_context` — already the injection point. Extend the kwargs.

### What NOT to do
- Don't write a new scorer. The existing one is good.
- Don't replace the existing retry loop in `PersonaRunner.run()`. Re-call `run()` once with augmented `_analytical_context`.
- Don't change `memos` table schema — `memo_score`/`memo_grade` columns already exist.
- Don't change the SOLO/demo path — this is the orchestrator path only.
- Don't auto-retry more than once (cost cap: $20/day budget per `risk.toml`).
- Don't surface `base_price_underwater` as a hard veto — it's a *signal*, not a stop-trade flag. Operator decides.

## Acceptance criteria

### Functional (must)
- A SOLO cycle on OUST (or any ticker with available evidence) produces a memo with `memo_score` and `memo_grade` populated in the SQLite row.
- The `/memo/{ticker}` template renders `SCORE 78/100 (Grade B)` (or equivalent).
- A 2nd cycle for the same ticker produces a memo whose `_analytical_context` includes a `Prior Memo Context` block with the prior cycle's full thesis + fair_value + key_evidence (not the truncated 200 chars).
- When `ForwardValuationEngine` is called with `reverse_dcf_fair_value_usd` and the forward base diverges >50%, `ForwardValuationResult.forward_vs_reverse_dcf_warning` is non-empty and surfaces in the memo's `## Forward Valuation` block.
- When `base_price_underwater=True`, the memo shows `⚠ DISTRESS: equity floored at $0`.
- When `|p_bull - p_bear| < 0.10`, the memo shows `LOW-CONFIDENCE FORWARD VALUATION`.
- The memo renders a `## Persona Arbitration Weights` table listing each persona's weight, brier, n, and multiplier.
- All 30+ existing future-agent unit tests pass.

### Non-functional
- No regression to: `test_memo_writer_forward_valuation.py` (9 tests), `test_valuation_agent_sanity.py` (30+ tests), `test_forward_valuation.py`, `test_arbitration.py`, `test_conviction.py`.
- New unit-test files: `test_memo_scorer_integration.py`, `test_memo_prior_summary.py`, `test_forward_valuation_gap.py`.
- Each commit is independently revertible.
- Total LOC delta: target ≤ +500 across all 4 commits (excluding new tests).

## Verification checklist

```bash
# Unit tests (targeted — full suite hangs on wizard pollution per memory)
.venv/bin/python -m pytest tests/unit/test_memo_scorer_integration.py -v
.venv/bin/python -m pytest tests/unit/test_memo_prior_summary.py -v
.venv/bin/python -m pytest tests/unit/test_forward_valuation_gap.py -v
.venv/bin/python -m pytest tests/unit/test_memo_writer_forward_valuation.py -v
.venv/bin/python -m pytest tests/unit/test_valuation_agent_sanity.py -v
.venv/bin/python -m pytest tests/unit/test_forward_valuation.py -v

# Integration smoke
.venv/bin/python -m pmacs.web.app &  # boot uvicorn
curl -s http://localhost:8000/api/cycle/solo?ticker=OUST | jq .
sqlite3 data/pmacs.db "SELECT memo_score, memo_grade FROM memos WHERE ticker='OUST' ORDER BY id DESC LIMIT 1"
# → expect non-NULL values

# Browser check
open http://localhost:8000/memo/OUST
# → expect SCORE pill with grade letter; WARNING lines; PERSONA WEIGHTS table
```

## Files to touch

| File | Reason |
|---|---|
| `pmacs/nervous/orchestrator.py` | Scorer activation (L2884), retry, prior-memo SELECT (L1477), persona_weights plumbing, DuckDB per-persona calibration query |
| `pmacs/agents/sanity/memo_scorer.py` | New helpers: `extract_prior_memo_summary()`, `format_persona_weight_table()` |
| `pmacs/agents/memo_writer.py` | New kwargs on `set_analytical_context`: `memo_feedback`, `persona_weights`, `per_persona_calibration`, `prior_memo_summary`; render them in `_analytical_context` |
| `pmacs/agents/episodic_context.py` | New kwargs on `build_context_brief`: 7 prior-memo fields; emit `## PRIOR MEMO CONTEXT` block |
| `pmacs/engines/forward_valuation.py` | Cross-check vs reverse-DCF, distress surfacing, probability convergence; new kwarg `reverse_dcf_fair_value_usd` |
| `pmacs/schemas/forward_valuation.py` | 3 new fields on `ForwardValuationResult` |
| `pmacs/agents/prompts/valuation_agent.md` | One-line addition to self-critique section about cross-check vs current EV/EBITDA |
| `pmacs/web/templates/memo.html` | Update L111 pill to render `(Grade X)` |
| `tests/unit/test_memo_scorer_integration.py` (NEW) | Scorer activation + retry + persistence tests |
| `tests/unit/test_memo_prior_summary.py` (NEW) | Prior-memo extractor tests |
| `tests/unit/test_forward_valuation_gap.py` (NEW) | Cross-check + distress + convergence tests |

## How to apply

1. Read the plan at `/Users/guilherme/.claude/plans/sleepy-knitting-naur.md`.
2. Read the existing `score_memo()` (`pmacs/agents/sanity/memo_scorer.py:650`) to understand its interface.
3. Ship commits in order: 1 → 2 → 3 → 4. Each commit is independently mergeable.
4. After each commit, run the targeted unit tests for that commit + the regression set.
5. After all 4 commits: integration smoke + browser check.
6. Update `MEMORY.md` with the new findings.

## Related memory

- [[valuation_agent_forward_engine]] — Phase 7c baseline
- [[phase7_resume_point]] — what's already in the working tree
- [[memo_long_only_jun29]] — recent memo path changes (Jun 29)
- [[jun29_audit_findings]] — known test failures to avoid reintroducing
- [[wizard_test_pollution]] — full-suite pytest hangs; use targeted runs
- [[ticker_page_no_suppression_jun23]] — operator directive on accuracy-first
- [[yfinance_primary]] — operator directive: yfinance primary, Finnhub fallback