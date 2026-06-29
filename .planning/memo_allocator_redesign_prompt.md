# Memo Redesign for Allocators — Build Prompt

**Audience:** Hedge fund PM or high-net-worth individual who reads the memo to decide whether to size or pass.
**File location:** `/memo/{ticker}` in PMACS.
**Status today:** Presentation-style pastel deck with 16 sections, all original data retained. Aesthetic layer is done; this prompt covers the **allocator-grade content, hierarchy, and decision-support** layer that the deck still owes the reader.
**Companion files (read first):** `spec/Source.md` (§13 dashboard, §16 ticker page), `spec/Architecture.md` (§9 engines, §12 cycle), `spec/Agents.md` (§4-§13 personas). This prompt follows the same conflict-resolution rule (Source wins on vision, Architecture wins on implementation, Agents wins on LLM contracts).

---

## 0. Prompt contract

This is an executable build prompt, not a wishlist. Each item has:

- **What it is** (one sentence)
- **Why a PM/HNWI cares** (the user's actual question)
- **Inputs** (which schema fields / engine outputs already exist)
- **Output** (the contract on the page)
- **Acceptance** (what "done" looks like in the DOM)
- **Anti-pattern** (what the spec forbids here)

Build in the order listed. Items 1–4 are the highest signal-per-line-of-CSS; items 5–10 are the supporting density; items 11–15 are deletions and sidebar re-flowing; items 16–22 are the secondary nav / behavior layer.

---

## 1. Replace hero conviction ring with EV/share at current price

**What:** The hero primary visual becomes *expected value per share at current price*, not conviction %.

**Why a PM cares:** Conviction is output; EV is decision input. PMs look at "what does this become" before "how sure am I."

**Inputs:**
- `ForwardValuationEngine.price_target_6_12mo` (existing, Phase 7c)
- `ForwardValuationEngine.price_target_bull / base / bear` (existing)
- `forward_valuation.equity_floor` (existing)
- Live price from yfinance (existing in `_fetch_period_prices`)

**Output on page:**
```
┌─ EXPECTED VALUE ──────────────┐
│                                 │
│         $84.20                  │  ← weighted: 0.6×base + 0.25×bull + 0.15×bear
│                                 │
│   current  $61.40               │
│   upside   +37.2%               │
│   floor    $48.10  (distress)   │
│                                 │
└─────────────────────────────────┘
```

**Acceptance:**
- Center number is the weighted EV, formatted to 2 decimals + currency
- Below: current price, upside %, equity floor (small, only if floor > 0)
- Conviction ring stays — *behind* the EV number, faded, as secondary signal
- ARIA: `role="figure"` with `aria-label="Expected value $84.20, 37% upside on $61.40 current"`

**Anti-pattern:** Do not let an LLM compute the weighted EV. Python weights the three targets by `conviction` and outputs the single number; the LLM only produces the three targets (existing contract in `ForwardValuationEngine`).

---

## 2. Add R:R (risk:reward) as a single hero number

**What:** A single `1 : X` number next to the EV card, computed from the proposed stop, target, and current price.

**Why a PM cares:** PMs decline most pitches on R:R alone. A 1:1 setup is a coin flip; 1:3 is the threshold; 1:5+ is rare. This is the most-skipped number on every sell-side note.

**Inputs:**
- `valuation.stop_price` (existing — catastrophe net 15%)
- `forward_valuation.price_target_base` (existing)
- Live current price (existing)

**Output on page:**
```
R:R   1 : 3.4   (target $84 / stop $52 / current $61)
```

**Acceptance:**
- Number formats `1 : 3.4`; second number is `(target - current) / (current - stop)` rounded to 0.1
- Below in micro-text: `target / stop / current` so the math is auditable
- Color semantics: R:R ≥ 3 = emerald, 2–3 = amber, 1–2 = crimson, <1 = crimson + `R:R < 1 — pass`
- ARIA: full formula in `aria-label`

**Anti-pattern:** Never derive stop in the template. The stop is a stored decision field. If the analyst hasn't set one, show "—" with a tooltip linking to the position-sizing card (§3).

---

## 3. Add position-sizing card to the hero

**What:** A 4-row mini-card beside the EV/R:R pair, showing max shares at three portfolio-risk budgets.

**Why a PM cares:** PMs think in *contracts / shares / risk*. Showing them the executable size alongside the thesis closes the loop. Without it, the memo is research; with it, the memo is a ticket.

**Inputs:**
- `risk.max_single_position_pct` (from `config/risk.toml`)
- `paper.portfolio_value` (from `holdings` table sum)
- `forward_valuation.price_target_base`
- `valuation.stop_price`

**Output on page:**
```
SIZE AT 1% / 2% / 5% PORTFOLIO RISK
  $5,000 portfolio

  risk 1%   →   80 sh   ($4,912)
  risk 2%   →  160 sh   ($9,824)
  risk 5%   →  400 sh   ($24,560)
  cap 20%   →  162 sh   ($9,944)   ← binding
```

**Acceptance:**
- Three rows, plus a "binding cap" row that highlights whichever constraint (portfolio %, single-position %, or buying-power) is most restrictive
- Share count is integer; total cost shown to 2 decimals
- The binding constraint gets a small lock icon and amber border
- All math in Python (`engines/sizing.py` — new or extended); template never computes share count

**Anti-pattern:** Never expose `cash_available` or `margin` to the template — paper trading hides leverage entirely. Only `portfolio_value` is exposed.

---

## 4. Catalyst calendar as a horizontal timeline

**What:** Replace the flat `13-CATALYSTS` grid with a left-to-right 12-month timeline; each catalyst is a node on the date axis.

**Why a PM cares:** Catalysts are *when*, not *what*. A PM needs to know "is the next event 2 weeks or 6 months away" to size the patience required.

**Inputs:**
- `catalysts: [{date, label, expected_impact: HIGH|MED|LOW, source: "filing"|"event"|"estimate"}]` (existing in memo writer schema)

**Output on page:**
```
2025-Q4          2026-Q1          2026-Q2          2026-Q3
   │                │                │                │
   ●─────●──────────●────────────────●────────────────●─●──●
   │     │          │                │                │  │
  Q3    Q4       Jan 28          Mar 14          May 6  │
  earn  earn    Phase III      FDA             Investor │
  (H)   (H)    readout (H)     decision (M)   day (L)  ...(no more)
```

**Acceptance:**
- One row per month, scaled to fit the section width; nodes are 10×10 circles
- Node color: H = indigo-600, M = indigo-400, L = indigo-200
- Hovering (and keyboard focus) reveals a popover: date + label + impact + source citation
- Future catalysts only; past catalysts go in a collapsed "What already happened" drawer
- ARIA: `<ol>` with each catalyst as an `<li>`; popover is `role="dialog"`

**Anti-pattern:** Do not render catalysts LLM-style as bullet lists. The schema already emits structured `{date, impact}` — use them. If a date is missing, omit the catalyst, do not guess.

---

## 5. Promote "What would change my mind" out of section 15 into the hero

**What:** A 3-bullet box at the top of the thesis column, each bullet explicit and falsifiable.

**Why a PM cares:** Pre-commitment to exit. The PM wants to know *before* they enter what would make them leave.

**Inputs:**
- `counter_thesis: [{claim, falsifier, source}]` (existing in memo writer schema, often underused)

**Output on page:**
```
WHAT WOULD CHANGE MY MIND

  1.  Margin compresses below 38% in any quarter     → exit
  2.  Top customer churn without a replacement       → exit
  3.  Insider selling cluster > $5M in 30 days       → exit

  [view all 8 → lineage]
```

**Acceptance:**
- Exactly 3 bullets visible; rest behind a "view all" link
- Each bullet ends with a colored arrow + verb (`exit`, `trim`, `reassess`)
- The full list (8 typically) lives in lineage; this is the curated top-3
- If `counter_thesis` is empty: render a neutral `No exit criteria defined — research-grade only` chip in the same slot

**Anti-pattern:** Do not let an LLM fabricate the falsifier. The persona is instructed to emit `{claim, falsifier}` pairs; if the LLM emits only claims, the template surfaces them but marks the falsifier cell as `unspecified`.

---

## 6. Add comparable-transactions table to the bull case

**What:** In section 02 (thesis) or 04 (financials), a 5-row table of recent comparable M&A / take-privates in the same vertical.

**Why a PM cares:** Bull cases on growth-tech often hinge on "gets acquired at N× revenue." The table validates that N is real.

**Inputs:**
- New field: `comparable_transactions: [{date, target, acquirer, ev_revenue_multiple, ev_ebitda_multiple, vertical}]` (memo writer schema addition; require analyst to fill at most 5)

**Output on page:**
```
COMPARABLE TRANSACTIONS (last 24 months)
  Date   Target           Acquirer        EV/Rev   EV/EBITDA   Vertical
  ──────────────────────────────────────────────────────────────────────
  2025-Q2   CompA          StratA          12.4×     28.1×      Data infra
  2024-Q4   CompB          StratB           8.9×     22.3×      Data infra
  ...
```

**Acceptance:**
- Up to 5 rows; empty state is `No recent comps in this vertical — bull case requires analyst review` (amber chip)
- Multiples formatted to 1 decimal + × suffix
- Median row at the bottom (Python-computed, not LLM) in bold

**Anti-pattern:** Do not let the LLM invent transactions. If `comparable_transactions` is empty, render the warning; do not hallucinate "examples" from the LLM's training. The schema field must be filled by the analyst or marked empty.

---

## 7. Insider + 13F activity into a single hero strip

**What:** Collapse the existing Insider section into a 1-line strip in the hero: net insider buying 90d, top-3 13F adds/drops.

**Why a PM cares:** Smart money flow is one of the three highest-signal external data points. It doesn't need a section — it needs a sentence.

**Inputs:**
- `insider_activity_90d: {net_buys_usd, net_sells_usd, cluster_count}` (existing)
- `thirteen_f_changes: {adds: [{fund, weight_delta}], drops: [{fund, weight_delta}]}` (existing)

**Output on page:**
```
INSIDERS  net +$4.2M 90d (3 clusters)   │   13F  Tiger +18bp · Coatue +12bp · Renaissance -8bp
```

**Acceptance:**
- One line, ~80 chars wide on desktop; wraps to two lines on mobile
- `+` is emerald, `-` is crimson
- Click → expanded drawer with full transaction list (drawer is shared with the existing lineage panel)

**Anti-pattern:** No fabricated 13F moves. If the data is stale (>7 days), show `stale 11d` in amber.

---

## 8. Short interest & borrow health — one row in financial snapshot

**What:** Add three metrics to section 04 (financial snapshot): days-to-cover, cost-to-borrow, SI as % of float.

**Why a PM cares:** HNWI-as-allocator uses IBKR and cares about borrow; PMs at hedge funds care about crowded shorts. Both want this on the card.

**Inputs:**
- New fields in ticker fundamentals: `short_interest.days_to_cover`, `short_interest.cost_to_borrow_pct`, `short_interest.si_pct_float` (yfinance supports these; add to `_fetch_period_prices` cache)

**Output on page (one new row in financial snapshot grid):**
```
SHORT INTEREST              Days to cover    1.4     Cost to borrow   0.8%
                            SI % of float    2.1%    Utilization      34%
```

**Acceptance:**
- Two columns × two metrics; same visual treatment as the other snapshot rows
- Each metric has a sparkline of the 90-day trend (existing `format_xy_poly` macro)
- If data unavailable: `—` with `data pending from yfinance` micro-text

**Anti-pattern:** No estimated or interpolated values. If yfinance didn't return it, show `—`; never fill with a heuristic.

---

## 9. Risk/reward 2×2 matrix (replaces flat risk list)

**What:** Replace the linear risk heatmap with a 2D probability × impact matrix.

**Why a PM cares:** Linear risk lists force the analyst to hide probability behind adjectives. A 2×2 forces honesty: "we think this is a 40%-probability, 20%-impact risk" is a different commitment than "we think this is a serious risk."

**Inputs:**
- `risks: [{name, probability: LOW|MED|HIGH, impact: LOW|MED|HIGH, mitigation}]` (existing; need to ensure probability and impact are scored)

**Output on page:**
```
                       IMPACT
                  LOW       MED        HIGH
              ┌────────┬──────────┬──────────┐
       HIGH   │        │          │  ·R3     │
   P          │        │          │  data    │
   R          │        │          │  breach  │
   O          ├────────┼──────────┼──────────┤
   B    MED   │        │  ·R1     │  ·R2     │
              │        │  key    │  macro   │
              │        │  cust   │  shock   │
              ├────────┼──────────┼──────────┤
       LOW    │  ·R4   │          │          │
              │  FX    │          │          │
              └────────┴──────────┴──────────┘
```

**Acceptance:**
- 9-cell grid (3×3); risks plotted as colored dots
- Hover/focus reveals: name + mitigation in a popover
- Color: P-LOW + I-LOW = emerald, HIGH/HIGH = crimson, diagonal = amber
- Click a cell → drawer with all risks in that bucket

**Anti-pattern:** Don't let the LLM score probability. The analyst or the persona emits `{name, probability, impact, mitigation}` per risk; the template plots. If the analyst emits only `{name, severity}`, fall back to the old flat list and log a `RISK_MATRIX_INCOMPLETE` audit event.

---

## 10. Scenario console → scenario deck with weighted expected price

**What:** Reformat the existing scenario console to make the weighted expected price the most prominent element per column.

**Why a PM cares:** Right now the three columns are visually equal. The PM needs to see at a glance: "we think base is 60% likely, bull 25%, bear 15%; here's what each means in dollars."

**Inputs:** Existing `ForwardValuationEngine` outputs per scenario.

**Output on page (per column header):**
```
BASE CASE  ─────────────────
  60% likely   ←  NEW: top of column
  $84 / +37%   ←  NEW: bold, large
  price target $84.20
  ↑ from $61.40
  EV/EBITDA 24×
  ...
```

**Acceptance:**
- Each column gets a header strip: probability, then price target + upside, then details below
- Probability is the same across all three columns (must sum to 1); Python validates and warns if not
- The "weighted" line is computed by Python at render time, shown below the three columns:
  ```
  WEIGHTED EXPECTED PRICE: $84.20  (+37% upside)
  ```

**Anti-pattern:** Probabilities are not free-text. They are floats, parsed at the persona boundary, and any LLM drift triggers the existing `_pre_validate.normalize_enum` helper.

---

## 11. Delete the agents grid from the primary deck

**What:** The 8-card agents section (currently section 14) moves to a popover from a 1-line summary.

**Why a PM cares:** A PM either trusts the ensemble or doesn't. The cards don't change that — they create noise.

**Acceptance:**
- Replace section 14 with a 1-line strip in the hero or top of thesis:
  ```
  8 personas · σ=0.04 (high agreement) · [view breakdown]
  ```
- "view breakdown" opens a slide-out drawer with the existing 8 cards
- The section is *removed* from the section numbering (renumber 14→16 down); the deck stays 16 sections but sections 14 and 15 (what-would-change, position) collapse into the hero per §5 and §3

**Anti-pattern:** Do not delete the underlying persona data. It still flows into lineage and the drawer. We're removing *rendering*, not *storage*.

---

## 12. Delete the standalone reverse-DCF section

**What:** Section 12 (reverse DCF) folds into the hero metric.

**Why a PM cares:** Reverse DCF is an *input* to the price target, not a second opinion on the main opinion. Showing it standalone confuses the allocator into thinking it's an alternative thesis.

**Acceptance:**
- Reverse DCF's implied growth rate appears as a micro-text line under the EV hero number:
  ```
  implied growth: 18% / yr (reverse DCF)
  ```
- The section is removed from the deck; renumber 13–16 down by 1
- Reverse DCF data still lives in the lineage view

**Anti-pattern:** Do not show the reverse-DCF curve chart in the hero. It belongs in lineage, not in the allocatable summary.

---

## 13. Move sections 14–16 (after §11 deletion) to a sticky right sidebar

**What:** Agents summary, full what-would-change-my-mind list, and position-sizing details live in a sticky `aside` on desktop.

**Why a PM cares:** The deck flow becomes narrative (hero → thesis → scenarios → financials → risks → catalysts) without 4 reference sections interrupting it. Reference is *available*, not *inserted*.

**Acceptance:**
- Desktop (≥1200px): right rail 320px wide, sticky `top: 1rem`, contains:
  - R:R + sizing detail (full version, not the hero mini)
  - All 8 personas in compact form (just name + conviction + 1-line rationale)
  - Full "what would change my mind" list (not just top-3)
  - Catalysts (full list, not just the 12-month timeline)
- Tablet (768–1199px): rail collapses to the bottom; sections stack
- Mobile (<768px): rail is a sticky bottom sheet accessible from a `Reference ▾` button

**Anti-pattern:** No critical decision data lives *only* in the rail. Hero shows the actionable summary (EV, R:R, sizing, top-3 exits). Rail is for the long form.

---

## 14. Thesis prose → thesis bullets with causal chain

**What:** Section 02 (investment thesis) becomes a 3–5 bullet structure, each bullet `Premise → Mechanism → Outcome → Number`.

**Why a PM cares:** Long-form thesis prose reads as research. Bulleted chains read as *position*. A PM reads bullet 1 and bullet 5 and decides.

**Acceptance:**
- Existing prose is preserved verbatim in a `<details>` collapsed under the bullets
- Each bullet follows the schema:
  ```
  ① [PREMISE]  →  [MECHANISM]  →  [OUTCOME]  →  [$XX or +XX%]
  ```
- Bullets are emitted by the memo writer persona in the existing schema; add a `thesis_bullets: [{premise, mechanism, outcome, number}]` field
- If `thesis_bullets` is empty, fall back to prose and log `THESIS_BULLETS_UNAVAILABLE`

**Anti-pattern:** No LLM-style "conclusion" bullet. Every bullet must end in a number or a falsifiable claim.

---

## 15. Decision strip color semantics

**What:** BUY / HOLD / SELL get distinct, high-contrast treatments in the hero verdict pill.

**Why a PM cares:** Currently the three are visually similar pastels. SELL should look like *stop*. HOLD should look like *wait*. BUY should look like *act now*.

**Acceptance:**
- BUY: emerald background `bg-emerald-500/15`, emerald border, emerald-700 text, with a small `↑` glyph
- HOLD: indigo background `bg-indigo-500/15`, indigo border, no glyph
- SELL: crimson background `bg-rose-500/15`, crimson border, rose-700 text, with `↓` glyph
- PASS (new): slate background `bg-slate-500/15`, slate border, `—` glyph
- Verdict pill is 18px tall, 12px font-weight 600, in the hero

**Anti-pattern:** Do not introduce a fifth color. The four outcomes map to four colors max; if a fifth verdict appears (e.g., `RESTRICTED`), it gets the slate treatment with `?` glyph.

---

## 16. PASS as a first-class verdict

**What:** Add `PASS` to the verdict enum alongside BUY/HOLD/SELL.

**Why a PM cares:** A PM actively *passing* on a stock is a feature, not a bug. Showing PASS with the same weight as HOLD dilutes both.

**Acceptance:**
- Verdict enum in memo writer schema gains `PASS` (existing 3 → 4)
- Trigger conditions for PASS (analyst persona decides):
  - `comps_empty AND growth < 10%` → likely PASS
  - `R:R < 1.5` → likely PASS
  - `conviction < 0.45` → may be PASS
- Hero shows PASS with the slate treatment per §15
- Decision strip shows `Decision: PASS — [reason]`

**Anti-pattern:** PASS is not "we couldn't decide." It's an *active* no-bid. The reason field is required; empty PASS is invalid.

---

## 17. Evidence registry → inline popovers (not a 10-row table)

**What:** Section 10 (evidence registry) collapses to nothing; evidence IDs become clickable chips next to every claim they support.

**Why a PM cares:** Inline trust beats a table at scroll position 5000. A PM clicking `[1]` next to "30% margin" wants to know *which filing*, not browse a registry.

**Acceptance:**
- Every `evidence_id` rendered as a small `[n]` chip beside its claim
- Clicking opens a popover: `evidence_id` + freshness timestamp + source URL + lineage link
- The full registry is in lineage (sidebar)
- Section 10 is removed from primary numbering

**Anti-pattern:** Evidence freshness must be real. If the underlying evidence is stale (>24h for prices, >7d for filings), the chip is amber with a `stale` tooltip.

---

## 18. Auto-played scroll reveals become once-per-session

**What:** The `.reveal` class triggers on first load, then renders statically for the rest of the session.

**Why a PM cares:** A PM re-reading the memo before adding to a position does not want cinematic re-entry animations every visit.

**Acceptance:**
- Use `sessionStorage["memo-revealed-{ticker}"]` to gate the reveal class
- First visit: animations play
- Subsequent visits within session: sections render immediately, no transition
- New session (browser close): animations play again
- `prefers-reduced-motion`: never animate, ever

**Anti-pattern:** Do not store in `localStorage` — that would persist across sessions for days/weeks, defeating the "first visit per session" intent.

---

## 19. Dark mode tint pastels → deep desaturated indigo

**What:** In dark mode, the section backgrounds shift from pastel indigo to deep desaturated indigo (`#1e1b4b` family).

**Why a PM cares:** Pastels on dark backgrounds look muddy and unprofessional. Deep indigo preserves the "presentation deck" feel without the chalky look.

**Acceptance:**
- Light mode: pastel `linear-gradient(180deg, #eef2ff, #e0e7ff)` (already done)
- Dark mode: `linear-gradient(180deg, #1e1b4b 0%, #312e81 100%)` with white text and adjusted accent colors
- Decision pill colors in dark: emerald-500 → emerald-400, rose-500 → rose-400, etc. (one shade lighter for contrast)
- Hero numbers in dark: white at 95% opacity, not pure white

**Anti-pattern:** Do not change the dark-mode *structure*. Only the palette. The 16 sections, the sidebar, the popovers all stay where they are.

---

## 20. Freshness strip in the hero

**What:** A 1-line strip below the verdict: `Price 14:23 EDT · Filings Q1 2025 · Sentiment 2h ago · Model X`.

**Why a PM cares:** A PM always asks "is this fresh?" Answering it inline kills 3 follow-up clicks.

**Acceptance:**
- Each timestamp has a freshness color: green <1h, amber <24h, red >24h
- "Model X" is the active backend name (existing field); shows nothing if local mode hides the model name per spec §1 (mode-pure inference)

**Anti-pattern:** Never render a model name in local mode (per `Architecture.md §16` mode-pure inference).

---

## 21. Section renumbering

**What:** After deletions in §11, §12, §17, sections renumber to:

```
00  Hero (with EV / R:R / Sizing / What-would-change / Freshness)
01  Scenario console (with weighted EV)
02  Investment thesis (bullets + collapsed prose)
03  Business model
04  Financial snapshot (adds short interest)
05  Industry KPIs
06  Growth & durability
07  Risk matrix (2×2, replaces heatmap)
08  Bull / bear
09  Adversarial pressure test
10  Catalysts (horizontal timeline)
[SIDEBAR]
   - Sizing detail (full)
   - Persona ensemble (compact 8)
   - What-would-change (full list)
   - Catalysts (full list)
[LEGACY MOVED]
   - Evidence registry → inline chips
   - Reverse DCF → hero micro-text
   - Agents full grid → sidebar drawer
```

**Acceptance:** Section numbers in the left rail jump-to nav reflect the new numbering. The "00" prefix on hero stays for visual ordering.

---

## 22. Build order and exit test

Build in this order:

1. §1 (EV hero) + §2 (R:R) — single biggest "would I click buy?" lift
2. §3 (sizing card) — closes the loop on actionability
3. §4 (catalyst timeline) — PM-skim-first win
4. §5 (promote what-would-change) — 5 minutes of template work
5. §15 (verdict colors) + §16 (PASS verdict) — 5 minutes of CSS + enum
6. §10 (scenario deck weighted EV) — reuses §1 components
7. §6 (comps table) — schema addition + render
8. §7 (insider/13F strip) — pure template work
9. §8 (short interest row) — yfinance cache extension
10. §9 (risk matrix 2×2) — schema validation + render
11. §14 (thesis bullets) — schema addition + persona prompt update
12. §20 (freshness strip) — trivial template
13. §18 (reveal-once) — JS sessionStorage gate
14. §11 (delete agents grid) + §12 (delete reverse DCF) + §17 (evidence inline) — renumbering
15. §13 (sticky sidebar reflow) — layout surgery
16. §19 (dark mode deep indigo) — palette swap
17. §21 (final section renumbering) — verification pass

**Phase exit test (every item must pass before declaring done):**

- [ ] PM can answer "what's the expected price?" in <2 seconds without scrolling past hero
- [ ] PM can answer "what's the R:R?" in <2 seconds
- [ ] PM can answer "how many shares at 2% risk?" in <2 seconds
- [ ] PM can see all catalysts on a 12-month timeline
- [ ] PM can identify top-3 exit criteria without scrolling
- [ ] PM can see insider/13F activity in 1 line, click for detail
- [ ] PM sees PASS as a first-class outcome, not a degenerate HOLD
- [ ] Page works in dark mode without muddy pastels
- [ ] Page works on mobile (390px) with sidebar collapsed to bottom sheet
- [ ] All 31 existing memo unit tests still pass
- [ ] No `evidence_id` chip is rendered without a freshness color
- [ ] No fabricated comps, no fabricated catalysts, no fabricated 13F moves — empty states render as chips, not invented data
- [ ] Section numbering matches §21 final layout
- [ ] All five PMACS non-negotiables still hold: LLMs never sign trades, never math, every state transition hash-chained, mode-pure inference, operator owns kill switch
- [ ] No anti-pattern violations from `Architecture.md §16` introduced (especially: no `json.dumps` for audit, no `holding.state = "X"` direct mutation, no runtime prompt edits, no auto-promoted mutations)

---

## 23. What this prompt deliberately does NOT do

- It does not add new personas. The 14 existing personas stay.
- It does not change the memo writer's Pydantic schema contracts without listing the new fields (§6, §8, §14).
- It does not propose a new page. Everything lives at `/memo/{ticker}`.
- It does not propose new LLM calls. The Python engines (§1, §3, §10 math) absorb what was previously template-improvised.
- It does not touch the kill switch, mode ladder, or audit chain.

If a follow-up needs any of those, it must be a separate prompt that cites this one and the relevant spec section.

---

## 24. Spec anchors to cite when committing

When this lands, the implementation commits should reference:

- `Source.md §13.1` (visual identity tokens — already updated for pastel deck)
- `Source.md §16.8` (ticker page — adjacent surface)
- `Architecture.md §9.4b` (ForwardValuationEngine — reuses for §1, §10)
- `Architecture.md §16` (anti-patterns — gate every commit)
- `Agents.md §11` (MemoWriter persona — add `thesis_bullets`, `comparable_transactions` schema fields)
- `Phases.md` (this becomes Phase 8b, post-Polish, pre-LIVE)

Add a section to `spec/Source.md` titled **§16.8b — Allocator-grade memo layout** with the §1–§22 contracts above, so future Claude Code sessions can pick this up without re-deriving from this prompt.