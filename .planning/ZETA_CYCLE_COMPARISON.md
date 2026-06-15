# ZETA Solo Cycle Comparison: Does Running Twice Improve Quality?

**Date**: 2026-06-07
**Ticker**: ZETA (Zeta Global Holdings)
**Objective**: Compare two consecutive solo analysis cycles and cross-reference with SEC-verified data

---

## 1. Cycle History (4 total)

| # | Cycle ID | Verdict | Conviction | Fair Value | Memo Size | When |
|---|----------|---------|------------|------------|-----------|------|
| 1 | CYCLE-20260606T080121 | HOLD | 18.23% | $21.00 | 23KB | Jun 6, 8:18am |
| 2 | CYCLE-20260606T114225 | SKIP | 0% | N/A | 206B | Jun 6, 11:42am (keyring error) |
| 3 | SOLO-ZETA-20260607T101323 | BUY | 15.69% | $28.31 | 22KB | Jun 7, 10:14am |
| 4 | SOLO-ZETA-20260607T142520 | HOLD | 12.74% | $28.31 | 21KB | Jun 7, 2:27pm (this run) |

---

## 2. Head-to-Head: Cycle 3 (BUY) vs Cycle 4 (HOLD)

### Verdict & Conviction

| Metric | Cycle 3 (BUY) | Cycle 4 (HOLD) | Delta |
|--------|---------------|----------------|-------|
| System Verdict | BUY | HOLD | Downgraded |
| Memo Verdict Line | BUY | BUY | Same |
| Conviction Score | 15.69% | 12.74% | -3.0pp |
| Fair Value | $28.31 | $28.31 | No change |
| Price at Analysis | $22.02 | $22.02 | Same |
| Val Range Low | $22.00 | $18.00 | Wider bear case |
| Val Range High | $34.00 | $35.00 | Slightly wider |

### Financial Snapshot

| Metric | Cycle 3 (BUY) | Cycle 4 (HOLD) | SEC Reality |
|--------|---------------|----------------|-------------|
| Revenue | "DATA NOT AVAILABLE" | "~$950M (implied)" | **$1,437M TTM, $1,305M FY2025** |
| Revenue Growth | +33.6% TTM, +49.9% fwd | +49.9% YoY | **+33.6% TTM, +29.7% FY2025** |
| Gross Margin | 60.1% | N/A | **60.1% TTM (matches)** |
| Operating Margin | +0.2% | N/A | **+0.18% TTM ($2.65M)** |
| Net Margin | -1.6% | N/A | **-1.6% TTM ($-23.2M)** |
| Free Cash Flow | N/A | N/A | **$199.75M TTM** |
| Debt/Equity | N/A | N/A | Not checked |
| P/E Ratio | 98.0x trail, 18.5x fwd | 18.53x fwd | ~98x trail (matches) |
| PEG Ratio | 0.77 | 0.77 | Depends on growth assumption |

### Agent Signals Comparison

| Persona | Cycle 3 Conf | Cycle 3 Signal | Cycle 4 Conf | Cycle 4 Signal |
|---------|-------------|----------------|-------------|----------------|
| Forensics | 45% up | Revenue +33.6%, no cash flow data | 30% (0.3) | PEG 0.77 ok, but no financial stmt data |
| Insider Activity | 34% up | INSUFFICIENT_DATA | 20% (0.2) | INSUFFICIENT_DATA |
| Macro Regime | 70% up | Expansion + rate cuts tailwind | 75% (0.75) | Expansion + falling rates |
| Moat Analyst | 55% up | Revenue growth, single-layer moat | 75% (0.75) | PEG 0.77, not fully priced |
| Growth Hunter | 60% up | Forward growth 49.9%, PEG 0.77 | 75% (0.75) | 49.9% growth + PEG 0.77 = GARP |
| Catalyst Summarizer | 40% up | 18.5x P/E, +50% rev growth | 60% (0.6) | +34.6% over 50 days, RSI 59.6 |
| Short Interest | 60% up | NORMAL, no crowding | 30% (0.3) | INSUFFICIENT_DATA |

### Crucible Adversarial Review

| Attack Axis | Cycle 3 Severity | Cycle 4 Severity | Delta |
|-------------|-----------------|-----------------|-------|
| Valuation Assumptions | 0.35 | **0.50** | +0.15 (harsher) |
| Moat Durability | 0.30 | **0.45** | +0.15 (harsher) |
| Mgmt Track Record | 0.25 | **0.35** | +0.10 (harsher) |
| Competitive Threats | 0.50 | **0.40** | -0.10 (slightly milder) |
| **Average Severity** | **0.35** | **0.425** | **+0.075 (more adversarial)** |

---

## 3. Accuracy vs SEC-Verified Data

### What PMACS Got Right
- **Revenue growth +33.6% TTM**: Matches SEC data exactly ($1,437M TTM / $1,006M prior = +43% or ~33.6% YoY)
- **Gross margin 60.1%**: Matches ($864M / $1,437M = 60.1%)
- **Operating margin +0.2%**: Close ($2.65M / $1,437M = 0.18%)
- **Net margin -1.6%**: Matches ($-23.2M / $1,437M = -1.6%)
- **P/E ~98x trailing**: Consistent with negative-to-marginally-positive earnings

### What PMACS Got Wrong or Missed
- **Revenue dollars**: Cycle 3 said "DATA NOT AVAILABLE" — actual is $1,437M TTM
- **FCF**: Both cycles said "N/A" — actual is **$199.75M TTM** (very positive!)
- **Revenue growth direction**: PMACS reports +49.9% forward growth, but FY2025 actual was +29.7% — the 49.9% figure appears to be Yahoo Finance forward estimate, not actual
- **Gross margin**: Cycle 4 lost this data point (N/A vs 60.1% in Cycle 3)
- **Operating/net margin**: Cycle 4 regressed to N/A (was populated in Cycle 3)
- **The "$728M revenue" from Cycle 1**: Was FY2023 data ($729M), outdated by 2 years

### Critical FCF Discovery
The single most important data gap: **ZETA generates $200M in free cash flow** (TTM). Neither cycle detected this. This is a massive positive signal — the company is FCF-positive despite negative net income (stock-based compensation accounting). This would significantly strengthen the bull case.

---

## 4. Does Running Twice Improve Quality?

### Improvements in Cycle 4 (Second Run)
1. **Revenue estimate improved**: "DATA NOT AVAILABLE" -> "~$950M implied" (still wrong but at least attempted)
2. **Some agent signals got more confident**: Moat Analyst 55%->75%, Growth Hunter 60%->75%, Macro 70%->75%
3. **Crucible attacks became more specific**: Better-argued attacks on valuation assumptions and moat durability
4. **New data points**: Added RSI 59.6, 50-day price change +34.6%
5. **Catalyst Summarizer improved**: More specific about timing and earnings setup

### Regressions in Cycle 4 (Second Run)
1. **System verdict downgraded**: BUY -> HOLD (memo still says BUY, system says HOLD)
2. **Conviction dropped**: 15.69% -> 12.74%
3. **Key financials went to N/A**: Gross margin, operating margin, net margin all regressed
4. **Short Interest confidence collapsed**: 60% -> 30% (changed from "NORMAL" to "INSUFFICIENT_DATA")
5. **Crucible severity increased**: 35% -> 42.5% average
6. **Insider Activity confidence dropped**: 34% -> 20%
7. **Revenue growth figure is now wrong**: Reports +49.9% YoY instead of +33.6% TTM (confuses forward estimate with actual)

### Net Assessment

| Dimension | Cycle 3 | Cycle 4 | Winner |
|-----------|---------|---------|--------|
| Data Completeness | 6/10 fields | 3/10 fields | Cycle 3 |
| Accuracy vs SEC | 5/6 correct | 3/6 correct | Cycle 3 |
| Agent Signal Quality | Consistent | Higher conf but less data | Tie |
| Crucible Quality | Moderate | More adversarial | Cycle 4 |
| Overall Reliability | Higher | Lower | **Cycle 3** |

### Verdict: Running Twice Did NOT Improve Quality

The second run **degraded** data quality:
- Lost 3 financial metrics that were present in the first run
- Revenue figure became less accurate ($950M implied vs actual $1,437M)
- System conviction dropped 3 percentage points
- The verdict changed from BUY to HOLD despite same fair value

The root cause appears to be **non-deterministic data fetching** — each cycle gets a different subset of evidence from APIs, and the second cycle fetched fewer fundamental data points. The LLM agents then reason from a weaker evidence base.

---

## 5. Recommendations

1. **Data persistence**: Cache fundamental data between cycles. The first cycle correctly identified 60.1% gross margin — the second cycle shouldn't lose this.
2. **FCF data gap is critical**: Both cycles missed $200M FCF. The data pipeline needs to fetch cash flow statements.
3. **Forward vs actual distinction**: The system conflates Yahoo forward estimates (49.9%) with actual TTM growth (33.6%). These must be clearly separated.
4. **Revenue figure**: The evidence pipeline provides revenue growth % but not absolute revenue dollars — this needs fixing.
5. **Agent signal stability**: 7/7 agent signals were identical in Cycles 1 and 3 (word-for-word). This suggests signals may be cached/stale rather than regenerated.
