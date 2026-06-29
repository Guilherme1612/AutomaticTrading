# /agents Page Audit — Jun 29, 2026

## TL;DR

The /agents page renders correctly from a template/DOM standpoint, but **85% of it shows placeholder content** because **no SOLO/orchestrator cycle has succeeded since the database was last cleared**. Three cycles on Jun 29 (SOLO-PLTR, SOLO-NBIS, SOLO-ONDS) all aborted at Crucible with `severity=0.00 after 0 cycle(s)` because the cross-persona auditor rejected the personas' outputs as `CONCLUSION_UNSUPPORTED` / `CITATION_GAP`. Multiple cycles from Jun 23/24 had the same fate.

As a result the page shows:
- 14 of 16 persona cards: `Idle — waiting for cycle`
- Crucible: `Severity 0% — survives` (no real run)
- Decision Summary: 2 identical "HOLD Conviction 0%" lines (ONDS, NBIS) with no thesis content
- Session Stats: 2 HOLDs, 0% Avg Conviction, Best Ticker = `—`
- Communication Layer "Signals" tab: empty ("Run a cycle to see agent signals")
- Communication Layer "Conviction" tab: works (deterministic formula display)
- /memo/{ticker}: stub Crucible-abort message, no real thesis

## Severity classification

**P0 (blocks operator decision-making):**
1. **No successful cycles exist in the database.** Every memo is an identical 239-char abort stub. PMACS has never produced a real thesis. Persona signal → memo → conviction chain is broken.
2. **Cross-persona auditor is over-strict.** Rejects every persona with CONCLUSION_UNSUPPORTED (severity 0.8) or CITATION_GAP (severity 0.3-0.5). Symptom in audit log:
   - `[auditor:catalyst_summarizer] No reasoning provided; conclusion probabilities are not supported by any analysis.`
   - `[auditor:short_interest] Persona cites ... but provides no reasoning text. Without reasoning, it is impossible to determine whether the conclusion ... is valid.`
   - `[auditor:insider_activity] Both insider_activity and short_interest cite evidence with no conflicting evidence_ids, but no conflict exists. No flag necessary.`
   Root cause uncertain — could be (a) LLM outputs aren't reasoning in the `analysis` field the auditor expects, (b) auditor thresholds are too tight, or (c) `_pre_validate` helper strips reasoning during normalization.

**P1 (operational/UX bugs):**
3. **`<script src="/static/sankey.js">` is missing from `agents.html`.** D3 is loaded, but `PMACS_SANKEY` module is never registered → Signals + Conviction tabs have nothing to render. This is why "Signals" always shows `Run a cycle to see agent signals.`
4. **`/memo/{ticker}` template crashes on Crucible-abort memos:** `'rr_ratio' is undefined` at `pmacs/web/templates/memo.html:221` kills uvicorn. Memo JSON for abort stubs has no `rr_ratio` key. uvicorn was dead for ~5 minutes during this audit.
5. **`agent_signals` is empty in every persisted memo.** The orchestrator's `if "agent_signals" not in memo_dict` branch builds signals from `arbitrated.persona_outputs` — but arbitration never produces `persona_outputs` when auditors reject everything (`severe_abort → no_valid_directional_probs`). So even when a cycle "succeeds" the failure cascade, the memo has zero per-persona data for the Sankey diagram to display.

**P2 (visual polish / minor):**
6. **Verdict label on /memo/{ticker} shows `None`** instead of "HOLD" for cycles that abort without setting `verdict` on the row. The DB row has `verdict='HOLD'` but memo_json does not have a `verdict` key — the template reads from memo_json.
7. **`/agents/sankey-data` for `Signals` tab returns `evidence_sources: []`** when no cycle has succeeded since restart — gates the visualization on first load even when persona data exists in DB.
8. **Stale-data problem is real but working as designed.** SSE `/events` reconnects when uvicorn restarts. Session stats/persistence across navigation works (verified ONDS hero survives navigation to /ticker/ONDS and back).

## Findings by area

### 1. Data flow (backend → DB)

**Confirmed**: `pmacs.db` `memos` table has 2 rows (ONDS, NBIS). Both are **identical 465-byte JSON** stubs:
```json
{
  "verdict_line": "HOLD — Crucible aborted (severity 0.00)",
  "thesis": "Crucible adversarial review aborted this symbol at severity 0.00 after 0 cycle(s)...",
  "p_up": 0.3, "p_flat": 0.4, "p_down": 0.3,
  "conviction": 0.0,
  "crucible_severity": 0.0, "crucible_iterations": 0,
  "abort_reason": "crucible_abort"
}
```

`agent_signals: 0`, `rr_ratio: None`, `forward_valuation: None`.

**Decision-thesis_summary** for both ONDS and NBIS comes from `decisions.thesis_summary` which has the same abort text.

**Cycles table** has 3 rows (PLTR, NBIS, ONDS) all `state=CLOSED`. No `state=OPEN`. No successful cycle in the entire DB.

### 2. Persona accuracy

Cannot rank personas because no cycle has produced real signals. From the audit log of `failure_classifications`, the personas that get flagged most often in the most recent cycles:

| Persona         | # flags | Severity typical | Trigger |
|-----------------|---------|------------------|---------|
| insider_activity | 3 | 0.8 | "No transactions found + balanced 0.33 probs" → CONCLUSION_UNSUPPORTED |
| short_interest   | 3 | 0.5 | "Cites finra short data but reasoning absent" → CITATION_GAP |
| growth_hunter    | 2 | 0.5 | "Cites price/ARR but reasoning not present" → CITATION_GAP |
| forensics        | 2 | 0.5 | "No reasoning text" → CITATION_GAP |
| catalyst_summarizer | 1 | 0.8 | No reasoning |
| moat_analyst     | 1 | 0.8 | No reasoning |
| macro_regime     | 1 | 0.8 | No reasoning |

**Hypothesis**: The LLM is producing structured output (p_up/p_down/p_flat/confidence) but **not filling the `analysis` or `reasoning` text field**. The auditor's CITATION_GAP/CONCLUSION_UNSUPPORTED rules both require narrative reasoning — without it, every persona gets flagged. Either the prompts don't ask for reasoning explicitly, or the JSON schema accepts a missing `analysis` field without making it required.

### 3. Front-end / visual

**Verified via Playwright (1280×900 viewport)**:
- All 16 persona card regions render with `data-persona` attribute correctly
- 14 of 16 cards render `Idle — waiting for cycle` (the placeholder state)
- Crucible card shows persisted stub state (Severity 0%, Survives)
- Hero shows `Last Analyzed: ONDS` with link to `/ticker/ONDS`
- Decision Summary shows 2 link cards to `/memo/ONDS` and `/memo/NBIS`
- Session Stats: 2 decisions, 0% Avg Conviction, Best Ticker `—` (no real data)

**Visual layouts**:
- "Last Analyzed" h3 (12px, font-weight 600, letter-spacing 1.2px) and ONDS link (30px JetBrains Mono weight 900) are stacked vertically in `.flex-col` parent — no overlap (false alarm from earlier screenshot perception).
- **Hero text isn't blurry** — no text-shadow, no opacity, no filter. The "blurry" perception was from screenshot compression artifacts in the earlier full-page PNG renders. Confirmed via `getComputedStyle`.

**Communication Layer tabs** (Process / Signals / Conviction):
- Process: works (5 stage labels animate, status depends on DB state)
- Signals: shows `"Run a cycle to see agent signals."` because `sankey.js` script tag is missing from `agents.html`
- Conviction: works (SSR'd formula with Direction, Maturity, Crucible, EV Factor, Conviction)

### 4. Persistence across navigation/reload

- **SSE `/events` reconnects after uvicorn restart**: works. Showed "Reconnecting…" while server was dead, "Connected" after restart.
- **/agents data persists across page navigation**: verified clicking ONDS hero link → /ticker/ONDS → back to /agents — hero, decision summary, session stats all intact.
- **/agents page reload**: works (verified after uvicorn restart).
- **/memo/{ticker} crashes on stub memos**: confirmed. Got `'rr_ratio' is undefined` → uvicorn died. Recovered after restart because `rr_ratio` somehow became defined (likely first-call path vs second-call path initialization order — needs investigation).

### 5. Outstanding console-level issues

- **Console errors during initial load (before restart)**: `ERR_CONNECTION_REFUSED` on `/events` and `/api/health/detail` — these were the dead-uvicorn artifacts.
- **Console after restart: 0 errors** — page is clean.
- **No d3-sankey warnings** — sankey.js isn't loaded at all.

## Recommendations

### Immediate (kill the bleed)
1. **Add the missing script tag**: `<script src="/static/sankey.js" defer></script>` to `agents.html`. This unblocks the Signals tab and the entire Communication Layer visualization.
2. **Guard the rr_ratio template reference**: In `pmacs/web/templates/memo.html` line 221, use `{{ "%.2f"|format(rr_ratio|default(0)) }}` or `{{ "%.2f"|format(rr_ratio or 0) }}` so abort-stub memos don't crash uvicorn.
3. **Fix verdict display on memo**: Memo JSON for aborts lacks `verdict` key — template reads from memo_json (None). Either write `verdict` to memo_dict from the abort path, OR read from DB `decisions.verdict` as a fallback.

### Medium (re-enable cycle output)
4. **Make persona reasoning required in the schema**: Add a min_length validator on `analysis` / `reasoning` so the persona can't return `p_up/p_down/p_flat` without producing narrative. Audit logs suggest the LLM is omitting this field.
5. **Tune the auditor**: Re-read `spec/Agents.md` §11d for what CITATION_GAP / CONCLUSION_UNSUPPORTED thresholds are spec-mandated. The current flags are at severity 0.3-0.8 which is putting Crucible into "abort" territory with `severity=0.00,iterations=0` because no personas survive.
6. **Add agent_signals preservation**: In `orchestrator.py` lines 3224-3247, build `agent_signals` from the raw `persona_results` even when arbitration fails, so the page can still show what each persona said before the cycle aborted.

### Defer
7. **Visual polish** (text rendering, blur perception) — not actually a bug per `getComputedStyle`.
8. **Forward valuation display** — Phase 7c metric exists in pipeline (PR #5) but no cycle has run to populate it.
9. **Universe expansion audit** — 10 tickers in queue but only 3 have been attempted as SOLO. Add CELH/INMD runs to confirm pattern.
