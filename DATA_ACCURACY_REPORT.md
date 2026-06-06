# PMACS Financial Data Accuracy Report

**Date**: 2026-06-06
**Scope**: 62 memos across 18 tickers in `data/pmacs.db`
**Methodology**: Memo claims extracted from SQLite `memos` table vs live data from Yahoo Finance and StockAnalysis.com

---

## Executive Summary

Systematic data accuracy problems were found across the memo system. Revenue figures are consistently stale or understated for major tickers. NBIS exhibits catastrophic inconsistency across its 8 memos. Margin percentages are generally more accurate than absolute dollar figures. The root cause appears to be stale or incomplete data ingestion from upstream providers (primarily Finnhub), combined with LLM hallucination filling gaps.

**Overall accuracy by category:**

| Category | Accuracy | Notes |
|---|---|---|
| Gross margin % | High (within 0.5pp) | GOOGL, META, NVDA all close |
| Operating margin % | High (within 0.5pp) | GOOGL, META, NVDA close; HIMS wrong sign |
| Revenue (absolute) | Low (20-50% off) | GOOGL $339B vs $423B; NVDA $130B vs $254B |
| FCF (absolute) | Low (30-50% off) | GOOGL $84B vs $64B; META $26B vs $46B |
| Growth rates | Medium (variable) | NBIS 528% vs 684%; HIMS 33% vs 78% |
| Valuation multiples | Low | NBIS P/E 792x vs 88-100x; P/S 123x vs 78x |

---

## Ticker-by-Ticker Comparison

### NBIS — Most Critical (8 memos, extreme inconsistency)

**Memo revenue estimates across 8 memos (all dated 2026-06-06):**

| Time | Revenue Claim | Fair Value | Range |
|---|---|---|---|
| 08:09 | $102M TTM | $45.56 | $15.19-$91.12 |
| 10:48 | Disclosed as growth rate only | $75 | $40-$120 |
| 10:58 | $500M TTM | $45 | $20-$80 |
| 11:00 | N/A | $85 | $40-$150 |
| 11:02 | $400M | $140 | $60-$280 |
| 11:35 | ~$750M | $85 | $40-$150 |
| 11:42 | None (SKIP) | None | None |
| 15:09 | ~$1.2B TTM | $152.37 | $68.35-$341.75 |

**Actual data (Yahoo Finance, 2026-06-06):**

| Metric | Memo (latest) | Actual | Delta |
|---|---|---|---|
| TTM Revenue | ~$1.2B | $877.9M | +37% overstated |
| Revenue growth YoY | +528% | +684% (Yahoo) | Understated by 156pp |
| Gross margin | 72.1% | ~72% | Accurate |
| Operating margin | -70.5% | ~-70% | Accurate |
| P/E ratio | 792x | 87.62-100.26 | 8-9x overstated |
| P/S ratio | 123x | 78.51 | 1.6x overstated |
| Q4'25 Revenue | N/A | $227.7M | Missing |
| Q3'25 Revenue | N/A | $146.1M | Missing |
| Q2'25 Revenue | N/A | $105.1M | Missing |
| Q1'25 Revenue | N/A | $50.9M | Missing |

**NBIS Forward Estimates (Analyst Consensus):**

| Year | Low | Average | High |
|---|---|---|---|
| 2026E Revenue | $1.5B | $3.1B | $5.2B |
| 2027E Revenue | $3.5B | $8.3B | $12.3B |

**NBIS Specific Claims Verification:**

| Claim | Verdict | Evidence |
|---|---|---|
| Management guides $6.9B-$11.5B for 2027 | Plausible | Analyst high estimate is $12.3B; average is $8.3B. The $6.9B-$11.5B range falls within analyst forecasts. Microsoft $17B multiyear deal provides a credible base. |
| 2026 run rate $7-9B | Aggressive but possible | Analyst average for 2026 is $3.1B. $7-9B would require 2.3-2.9x the average estimate. Possible only if major new contracts are not yet modeled by analysts. |
| Q1 ARR ahead of projections | Likely | Q1'25 revenue of $50.9M grew to Q4'25 $227.7M, a 4.5x increase within one year. ARR acceleration is consistent with this trajectory. |

---

### GOOGL (Alphabet)

| Metric | Memo | Actual | Delta | Accuracy |
|---|---|---|---|---|
| Revenue | $339.4B | $422.5B TTM / $402.8B FY2025 | -19.7% / -15.8% | SIGNIFICANTLY OFF |
| Gross margin | 60.4% | 60.37% | +0.03pp | ACCURATE |
| Operating margin | 32.7% | 32.69% | +0.01pp | ACCURATE |
| FCF | ~$84B | $64.4B TTM | +30.4% | SIGNIFICANTLY OFF |

**Root cause**: Revenue figure appears to be from an older fiscal year (possibly FY2023 or early FY2024 estimate). FCF overstatement may conflate operating cash flow with FCF.

---

### META (Meta Platforms)

| Metric | Memo | Actual | Delta | Accuracy |
|---|---|---|---|---|
| Revenue | $172.2B | $201.0B FY2025 | -14.3% | SIGNIFICANTLY OFF |
| Gross margin | 81.9% | 82.0% | -0.1pp | ACCURATE |
| Operating margin | 41.2% | 41.44% | -0.24pp | ACCURATE |
| FCF | ~$26.3B | $46.1B | -42.9% | SEVERELY OFF |

**Root cause**: Revenue is likely FY2024 data ($156.6B). FCF figure is dramatically wrong — possibly from an earlier year when Meta was investing heavily in metaverse with negative FCF.

---

### NVDA (NVIDIA)

| Metric | Memo | Actual | Delta | Accuracy |
|---|---|---|---|---|
| Revenue | $130.5B | $253.5B TTM / $215.9B FY2026 | -48.5% / -39.6% | SEVERELY OFF |
| Gross margin | 74.2% | 74.15% | +0.05pp | ACCURATE |
| Operating margin | 64.0% | 64.02% | -0.02pp | ACCURATE |
| FCF | "not disclosed" | $119.1B TTM | Missing | WRONG |

**Root cause**: Revenue appears to be from FY2025 ($130.5B matches). The data is one fiscal year behind. FCF claim of "not disclosed" is simply false.

---

### HIMS (Hims & Hers Health)

| Metric | Memo | Actual | Delta | Accuracy |
|---|---|---|---|---|
| Revenue | $1.2B | $2.21B TTM | -45.7% | SEVERELY OFF |
| Revenue growth | 32.8% | 78% TTM | -45.2pp | SEVERELY OFF |
| Operating margin | -1.3% | 5.68% | Wrong sign | SEVERELY OFF |
| Profit margin | -0.6% | 6.05% | Wrong sign | SEVERELY OFF |

**Root cause**: Data appears to be from 1-2 years ago when HIMS was still pre-profitability. The company has since reached sustained profitability with significant revenue acceleration.

---

### PANW (Palo Alto Networks)

| Metric | Memo | Actual | Delta | Accuracy |
|---|---|---|---|---|
| Revenue | N/A | $9.22B FY2025 | Missing | MISSING DATA |
| Operating margin | N/A | 11.59% | Missing | MISSING DATA |
| Gross margin | N/A | 73.41% | Missing | MISSING DATA |
| Revenue growth | 19.5% | 14.87% | +4.63pp | OFF |

**Root cause**: Major financial metrics marked as N/A despite being publicly available. Growth rate is from an earlier period.

---

### Other Tickers (Limited Data in Memos)

| Ticker | Memos | Key Finding |
|---|---|---|
| ASTS | Multiple | Early-stage company; limited public data makes verification difficult |
| INFQ | 1 | Insufficient public data for verification |
| KOD | 1 | Insufficient public data for verification |
| NOK | 1 | Stable large-cap; memo data likely reasonable but not verified |
| NU | 1 | Insufficient data in memos for comparison |
| ONDS | 1 | Small cap; limited public data |
| OUST | Multiple | Crucible severity 0.35 (lowest among universe) — data quality may be better for this ticker |
| RBRK | 1 | Insufficient public data for verification |
| RKLB | Multiple | Space company; limited financial history |
| SWMR | 1 | Insufficient public data for verification |
| TEM | 1 | Insufficient public data for verification |
| ZETA | 1 | Insufficient public data for verification |

---

## Systematic Patterns in Data Inaccuracies

### Pattern 1: Revenue Data is Stale (HIGH SEVERITY)

Every major ticker with verifiable data shows revenue figures that are 15-50% below actuals. The data appears to lag by one fiscal year or more.

**Affected**: GOOGL (-20%), META (-14%), NVDA (-49%), HIMS (-46%), NBIS (wildly inconsistent)

**Likely cause**: Finnhub free-tier data delays, or the data pipeline fetches annual rather than trailing-twelve-months figures.

### Pattern 2: NBIS Data is Catastrophically Inconsistent (CRITICAL)

Eight memos for NBIS produced revenue estimates ranging from $102M to $1.2B with no convergence. This suggests the data source returns inconsistent or incomplete data for this ticker, and the LLM fills gaps with hallucinated numbers.

**Evidence**: Revenue claims: $102M, $500M, $400M, $750M, $1.2B — a 12x range across the same day.

### Pattern 3: Margin Percentages Are Reliable

Gross margin and operating margin percentages are consistently accurate (within 0.5pp for GOOGL, META, NVDA). This suggests the data source returns these fields reliably.

**Consistently accurate**: GOOGL margins (both within 0.1pp), META margins (both within 0.3pp), NVDA margins (both within 0.1pp).

### Pattern 4: Dollar-Value Metrics Are Unreliable

Absolute dollar figures (revenue, FCF, market cap) are consistently wrong. Percentage-derived metrics (margins, growth rates) are more reliable but still problematic for smaller/more volatile companies.

### Pattern 5: Small-Cap / New-IPO Data is Missing or Hallucinated

Tickers like PANW have major metrics marked N/A. NBIS has wildly varying estimates. This suggests the data source has poor coverage for non-mega-cap or recently-listed stocks, and the system does not distinguish between "data not available" and "data is zero."

### Pattern 6: FCF Data is Particularly Bad

FCF figures are off by 30-43% (GOOGL +30%, META -43%). NVDA's FCF was marked "not disclosed" when it is $119B. This field appears to be unreliable across the board.

---

## Root Cause Analysis

1. **Finnhub data quality**: The free-tier Finnhub API appears to return stale or incomplete data. Known issue (see MEMORY.md: "Finnhub returns % not fractions; 4 code locations multiplied *100 causing all corrupted data"). Previous fix addressed unit confusion but not staleness.

2. **No data freshness validation**: The pipeline does not appear to check whether fetched data is current (e.g., verifying the fiscal year of revenue figures).

3. **LLM gap-filling**: When data sources return N/A or incomplete data, the LLM generates plausible-looking but incorrect numbers rather than flagging the gap.

4. **NBIS-specific**: As a relatively new stock with limited public financial history, Finnhub may have particularly poor coverage, leading to the 12x variance across memos.

---

## Recommendations

1. **Add data freshness checks**: Validate that revenue/earnings data corresponds to the most recent fiscal year or TTM period. Reject stale data.

2. **Cross-reference multiple sources**: Compare Finnhub data against Yahoo Finance (already used for some tickers per MEMORY.md). Flag discrepancies >10%.

3. **Never allow LLM to fill financial data gaps**: If a field is N/A from the data source, pass N/A to the LLM and instruct it to explicitly state "data not available" rather than estimating.

4. **Add NBIS-specific handling**: Given the extreme inconsistency, consider fetching NBIS data from a more reliable source (SEC filings, Yahoo Finance, or StockAnalysis).

5. **Prioritize TTM over annual figures**: Several memos appear to use annual figures from completed fiscal years rather than trailing twelve months, causing systematic understatement for fast-growing companies.

6. **Validate forward estimates against analyst consensus**: The memo system used ~$3.5B for NBIS FY2027E when analyst consensus is $8.3B. Forward estimates should be cross-checked against multiple analyst sources.

---

## Data Sources

- Yahoo Finance (finance.yahoo.com) — TTM revenue, margins, growth rates, P/E, P/S
- StockAnalysis.com — Quarterly revenue breakdowns, analyst estimates
- PMACS SQLite database (`data/pmacs.db`, table `memos`) — All memo claims
- All actual data retrieved 2026-06-06
