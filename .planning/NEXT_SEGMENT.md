# PMACS Next Segment — Fix & Improve

## System State (2026-06-07)

- Mode: SHADOW + PAPER, bootstrap 9/50 cycles
- Hash chain: INTACT (509 entries)
- Portfolio: $5K paper, 0 positions, 0 trades
- Inference: OpenRouter (deepseek-v4-flash)
- 37 cycles run, 385 verdicts, max conviction ever: 31.74% (ONDS BUY)

---

## P0 — Fix Now

### FIX-1: Re-run 10 failed tickers
- **Tickers:** TEM, ZETA, NU, OUST, KOD, INFQ, SWMR, ASTS, RBRK, NOK
- **Root cause:** CYCLE-20260606T114225 — keyring module not installed, LLM backend couldn't authenticate
- **All scored 0% SKIP** — no agent analysis ran, fail-safe worked correctly
- **Action:** Re-run from Pipeline page or trigger new cycle. Verify keyring/key is available before cycle starts.

### FIX-2: Cycle counter discrepancy
- **Dashboard shows "9 cycles"** but 37 actually ran across audit log
- Counter only counts completed cycles with full agent analysis
- **Action:** Include failed cycles in counter with visual distinction (e.g., "37 cycles (9 completed, 28 partial/failed)")

### FIX-3: ONDS agent-crucible disconnect
- **Agents:** Growth Hunter 70% up, Macro 70% up, Moat 68% up — all bullish
- **Crucible:** Severity 0.66 (second highest) — flags 102x P/S, no profitability, 8% dilution
- **Agents ignore what Crucible catches.** Agents don't see P/S, dilution, or operating losses in their evidence
- **Action:** Feed crucible attack output back into agent evidence for re-evaluation, OR increase crucible weight in arbitration formula

---

## P1 — Improve Agent Quality

### IMP-1: Recalibrate Catalyst Summarizer (perma-bull)
- **Problem:** Averages 58% up across ALL tickers including HIMS (3.8% growth, crucible 68%)
- **Root cause:** Agent focuses on narrative potential (GLP-1, AI, etc.) not on quantitative catalyst probability
- **Fix:** Add catalyst probability scoring grounded in data — not just "event exists = bullish." Weight by historical hit rate of similar catalysts. Reduce weight in arbitration from current level.
- **Prompt file:** `pmacs/agents/prompts/catalyst_summarizer.md`

### IMP-2: Downgrade Short Interest and Insider Activity weights
- **Problem:** Both agents default to neutral when data unavailable
  - Short Interest: "No FINRA short interest data available" in most memos → ~40% up / 30% down always
  - Insider Activity: "No Form 4 filings" in most memos → ~30% up / 32% down always
- **Fix:** When these agents have no data, emit signal with confidence=0 so arbitration ignores them. Don't let defaults anchor conviction. Get FINRA/Form 4 data via polygon/finnhub if possible.
- **Files:** `pmacs/agents/short_interest.py`, `pmacs/agents/insider_activity.py`

### IMP-3: Forensics fair value reconciliation
- **Problem:** NBIS has operating margin -70.5% vs net margin +93.1% (forensic red flag), yet fair value $152 with range up to $342
- **Fix:** When forensics flags earnings manipulation or accounting red flags, clamp fair value to bear-case only. Don't let bull case rely on questionable numbers.
- **File:** MemoWriter or arbitration engine — add forensics severity gate on fair value range

---

## P2 — Improve Operator Experience

### IMP-4: Lower paper position entry threshold
- **Problem:** No positions opened after 37 cycles. Highest conviction: 31.74%. BUY threshold is 30%, STRONG_BUY (60%) needed for entry.
- **Current thresholds:** BUY ≥ 30%, STRONG_BUY ≥ 60%
- **Fix:** For PAPER mode during bootstrap, allow BUY (≥30%) to open a minimal position (e.g., 5% of portfolio instead of full 20%). This generates trade data for Sharpe/drawdown/win-rate calculations.
- **File:** Position sizing logic in `pmacs/engines/sizing.py` or arbitration config

### IMP-5: Add MSFT and AMZN back to universe
- **Problem:** MSFT reached 28% conviction, AMZN reached 30.1% (BUY). Both removed during universe changes.
- **Historical peaks:** AMZN BUY 30.10%, MSFT HOLD 28.00%
- **Fix:** Re-add to universe. These large-cap tech names have the richest data for all 7 agents.

### IMP-6: Persist price data alongside memos
- **Problem:** Decisions table shows "Price: N/A" — can't calculate real-time upside without re-fetching
- **Fix:** Store price at decision time in memo record. Show upside % (fair value vs current) on Pipeline cards.
- **Files:** Memo storage, Pipeline card template

---

## P3 — Polish

### IMP-7: Tailwind CDN → PostCSS
- Console warning on every page: "cdn.tailwindcss.com should not be used in production"
- **Fix:** Install Tailwind as PostCSS plugin, build CSS at deploy time
- **Impact:** Cosmetic only, no functional impact

### IMP-8: "Loading..." SSE indicator
- Bottom of every page shows a persistent "Loading..." status element
- **Fix:** Hide after SSE connection established, or replace with "Connected" indicator

---

## Priority Order

```
FIX-1 (re-run failed) → FIX-3 (crucible weight) → IMP-1 (catalyst calibrate) →
IMP-4 (lower paper threshold) → IMP-2 (downgrade dataless agents) →
IMP-5 (add MSFT/AMZN) → FIX-2 (cycle counter) → IMP-3 (forensics FV gate) →
IMP-6 (persist prices) → IMP-7 (Tailwind) → IMP-8 (SSE indicator)
```

## Key Agent Rankings (for weighting decisions)

| Agent | Value | Action |
|---|---|---|
| Crucible | MVP — safety layer | Keep high weight, feed output back to agents |
| Growth Hunter | Highest alpha | Keep current weight |
| Moat Analyst | Strong differentiation | Keep current weight |
| Macro Regime | Good context | Keep current weight |
| Forensics | Catches accounting issues | Increase weight on fair value |
| Catalyst Summarizer | Perma-bull, low value | **Reduce weight, recalibrate prompt** |
| Short Interest | No data → defaults | **Set confidence=0 when no data** |
| Insider Activity | No data → defaults | **Set confidence=0 when no data** |

## Current Conviction Landscape

| Ticker | Verdict | Conviction | Crucible | Fair Value | Price | Upside |
|---|---|---|---|---|---|---|
| META | HOLD | 25.5% | 35% | $828.80 | $593.02 | +39.8% |
| GOOGL | HOLD | 21.9% | 38% | $395.00 | $368.55 | +7.2% |
| NVDA | HOLD | 17.3% | 48% | $245.00 | $205.12 | +19.4% |
| PANW | HOLD | 15.1% | 48% | $250.00 | $272.07 | -8.1% |
| ONDS | HOLD | 11.9% | 66% | $8.50 | $10.44 | -18.6% |
| NBIS | SKIP | 0.2% | 57.5% | $152.37 | $227.83 | -33.1% |
| HIMS | SKIP | 0.0% | 68% | $18.00 | $26.20 | -31.3% |

## Files Most Likely Touched

```
pmacs/agents/prompts/catalyst_summarizer.md    — prompt recalibration
pmacs/agents/short_interest.py                  — confidence=0 on no data
pmacs/agents/insider_activity.py                — confidence=0 on no data
pmacs/engines/arbitration.py                    — crucible weight, agent weights
pmacs/engines/sizing.py                         — paper threshold relaxation
pmacs/web/templates/pipeline.html               — cycle counter fix, upside %
pmacs/web/templates/dashboard.html              — cycle counter fix
pmacs/memo/memo_writer.py                       — forensics FV gate
pmacs/web/routes/pipeline.py                    — re-run endpoint
config/risk.toml                                — paper position thresholds
```
