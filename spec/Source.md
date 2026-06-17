# PMACS — Source

**File 1 of 4. Vision, philosophy, operator surface.**

> Companion files: `Architecture.md` (build, processes, IPC, storage), `Agents.md` (personas, prompts, structured-output contracts, Crucible, Failure Diagnostic Engine, Mutation Engine reasoning), `Phases.md` (build sequence and mode promotion gates).
>
> **Reading order for Claude Code:** This file first — it tells you *what* PMACS is and *why* every architectural decision in the other three files exists. Then `Architecture.md` for *how* it's built. Then `Agents.md` when you touch any LLM-producing code path. Then `Phases.md` when you decide what to build next.
>
> **If anything across the four files contradicts:** This file wins for *vision and operator-facing behavior.* `Architecture.md` wins for *implementation specifics.* `Agents.md` wins for *LLM contracts.* `Phases.md` wins for *what gets built when.*
>
> **Section anchors are stable.** The other three files cite this file as `Source.md §<n>`. If you renumber a section, search-replace across all four.

---

## Table of contents

```
0.   Cross-reference index
1.   System identity
2.   Operator persona — who PMACS is for
3.   Vision
4.   Trust contract — what PMACS promises and refuses
5.   Five non-negotiables
6.   Decision rights matrix
7.   Holding philosophy
8.   Universe philosophy
9.   Mode ladder
10.  The flywheel — why the system gets smarter
11.  Failure modes the operator accepts
12.  Run wizard
13.  The dashboard application
14.  Page: Dashboard
15.  Page: Agents
16.  Page: Pipeline
17.  Page: Universe
18.  Page: Cortex
19.  Page: Debug
20.  Page: Settings
21.  Operator workflows
22.  The day-in-the-life narrative
23.  The first 30 days
24.  Backup, recovery, multi-machine
25.  Versioning and updates
26.  Out of scope (v1)
27.  Glossary
28.  Connection to companion files
```

---

## 0. Cross-reference index

When this file references something defined elsewhere, the pointer is explicit. Use these to navigate the spec:

| Concept | Lives in | Section |
|---|---|---|
| 7-layer stack, processes, IPC | `Architecture.md` | §2, §4 |
| Repo tree, file responsibilities | `Architecture.md` | §3 |
| Storage schemas (Kuzu, Qdrant, DuckDB, SQLite, audit) | `Architecture.md` | §8 |
| Audit and debug log formats | `Architecture.md` | §5 |
| Cycle orchestration sequence | `Architecture.md` | §12 |
| Kill switch triggers and disengagement | `Architecture.md` | §13 |
| Memory hierarchy (Working / Episodic / Semantic / Immutable) | `Architecture.md` | §15 |
| Mutation Engine process and lifecycle | `Architecture.md` | §10 |
| Anti-patterns (what Claude Code must not do) | `Architecture.md` | §16 |
| Per-persona prompts, schemas, sanity validators | `Agents.md` | §4-§13 |
| Crucible adversarial loop | `Agents.md` | §16 |
| Failure Diagnostic Engine — the 18 taxonomy types | `Agents.md` | §15 |
| Mutation Engine reasoning and candidate generation | `Agents.md` | §17 |
| Episodic context injection (short-term memory in prompts) | `Agents.md` | §18 |
| Build phases (Phase 1 through Phase N) | `Phases.md` | §2 |
| Mode promotion and demotion gates (numerical) | `Phases.md` | §3 |
| Per-phase exit tests | `Phases.md` | §2.x |

---

## 1. System identity

| Field | Value |
|---|---|
| **Name** | PMACS — Portfolio Management and Catalyst Automation System |
| **Kind** | Single-operator, local-first, catalyst-driven, LLM-assisted decision engine with deterministic arbitration (LLM backend operator-selectable: local inference or cloud API) |
| **Holding style** | Thesis-bound. Hold while thesis is valid and risk-adjusted. No time-based forced exits. |
| **Cadence** | Boot-driven. One cycle per 24h+ gap detected at startup. Manual single-ticker re-runs from UI. |
| **Host** | Apple M1 Max, 64GB unified memory, macOS |
| **Inference primary** | `llama-server` (llama.cpp) + unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_XL |
| **Inference secondary** | Ollama (operator-selectable via Settings) |
| **Brokers** | Alpaca paper (v1, day 1). IBKR earmarked for future live (Lisbon residency precludes Alpaca live). |
| **Currency** | USD primary display and trading. EUR secondary toggle. Source: ECB daily reference rate. |
| **Universe** | Operator-curated growth-tech across Nasdaq + NYSE. Optional Nasdaq-100 overlay. ~16 ticker seed. |
| **Capital (paper)** | $5,000 simulated. 20% max single position ($1,000). |
| **Modes** | SHADOW + PAPER (concurrent) → PAPER_VALIDATED → LIVE_EARLY → LIVE_STANDARD → LIVE_EXPANDED |
| **Realistic timeline** | ~6 weeks to first PAPER cycles; 3-6 months to PAPER_VALIDATED depending on boot frequency |
| **Network** | UI + orchestration are `localhost`-only. No telemetry. No external observation of operator decisions. LLM backend is operator-selectable: local inference (default; fully offline, `pf`-blocked) **or** cloud API (OpenRouter/Anthropic/OpenAI) when the operator opts in. |
| **Tone** | Notion aesthetic. Information-dense without clutter. Numbers in JetBrains Mono. |

---

## 2. Operator persona — who PMACS is for

PMACS is built for one specific operator profile. Designing for everyone else is wasted surface area. The operator is:

A solo investor with **multi-year audited returns above the broad market** (~70% annualized over 2.5 years through bottleneck analysis, compute trends, supply-chain reasoning), who wants to scale their decision-making rigor across more names than they can manually track. They are not a quant looking for a black box. They are a thesis-driven investor who builds conviction through structured reading — earnings calls, 10-Ks, supply-chain mapping, insider activity, short interest, FDA cycles — and then waits.

They are technically literate enough to run a local LLM stack, debug a Python service, edit a TOML config, and read a hash-chained audit log. They are not a software engineer; they are an operator who happens to code. They prefer a tool that respects their judgment over a tool that hides its reasoning.

They invest in companies they understand: smaller, growing names with widening moats (NBIS, NU, SWMR, ASTS, KDK), and select platform-scale Nasdaq-100 names with structural advantages. They explicitly do not chase momentum on names they cannot articulate a thesis for.

They are based in Lisbon, trade in USD, hold positions for weeks to months, and accept that any system will be wrong sometimes — but they want to know precisely *why* it was wrong, and have the system get less wrong over time.

**What they bring:** thesis generation, ticker selection, conviction at the level of "I want to bet on this story," override authority on any system decision, capital allocation across PMACS-driven and operator-driven decisions.

**What they delegate to PMACS:** evidence gathering at scale, multi-perspective analysis, arbitration math, sizing math, position monitoring, stop-loss execution, calibration and self-improvement, audit-grade record-keeping.

**What PMACS does not delegate back:** mode promotion to live capital, ticker additions to universe, kill-switch disengagement, mutation promotions on substantive system components, force-exit on active positions. These all require explicit operator action via operator confirmation.

---

## 3. Vision

PMACS produces a probability-distributed verdict on each tracked ticker — not a buy signal, but a structured belief about future price evolution paired with risk-adjusted sizing.

It uses local LLMs (one base model, multiple personas with distinct prompts) for narrative analysis, and Python deterministic engines for math, sizing, arbitration, and execution. **LLMs never decide; they produce structured-output-constrained signals that deterministic Python combines, validates, and turns into trade plans.** The LLM cannot directly cause a trade. The math does. This is structural, not procedural.

The system runs autonomously when the operator's machine is on, requires no per-trade input in paper mode, and graduates to live capital only after empirical performance gates pass. Its purpose is not to maximize a return target, but to maximize the **probability-weighted ratio of upside realization to downside drawdown** across thesis-driven holdings in high-alpha growth-tech names.

The system improves itself through a **Mutation Engine** that observes its own failures via the **Failure Diagnostic Engine**, proposes variants of its own components, validates them through shadow A/B tests, and either surfaces all improvements to the operator for review. No mutation is ever auto-applied — the operator confirms every change via operator confirmation, ensuring the base system cannot be degraded by the flywheel itself.

**The flywheel is the product.** A static system frozen at v1 quality is uninteresting. A system that gets sharper every month — more accurate calibration, better persona prompts, tighter thresholds, learned operator preferences — is what justifies running it locally for years. Every decision feeds the flywheel. Every failure becomes training data. Every override becomes a learned preference. Nothing is wasted.

---

## 4. Trust contract — what PMACS promises and refuses

A vision document without a trust contract is marketing. This is the binding statement of behavior the operator can rely on, and the things PMACS deliberately refuses to do.

**PMACS promises:**

1. **Every decision is auditable in deterministic detail.** Hash-chained immutable log. The operator can prove what the system saw, what each persona said, how they were weighted, what the Crucible found, what was sized, what was traded, and what filled — for every cycle, forever.

2. **No telemetry, no external observation of operator decisions.** PMACS never phones home with operator behavior, holdings, or cycle outcomes. The audit log replicates only to operator-controlled destinations. The LLM backend is operator-selectable: local inference (the default, fully offline and `pf`-blocked) or a cloud API (OpenRouter/Anthropic/OpenAI). When the operator opts into a cloud backend, analysis prompts and evidence are sent to that provider — this privacy trade-off is explicit, operator-chosen, and surfaced in Settings; it never enables telemetry or external observation of the operator's decisions.

3. **The operator controls the kill switch.** PMACS can engage it autonomously when triggered. Only the operator can disengage it, with a typed reason.

4. **Trade plans are signed.** Every order PMACS submits is Ed25519-signed by the math process. An LLM cannot sign. A compromised dashboard cannot sign. The signing key never leaves the math process.

5. **Mode promotion is operator-gated.** Moving from PAPER to PAPER_VALIDATED, or from any PAPER mode to any LIVE mode, requires operator confirmation. The system never decides on its own to start using real money.

6. **The system tells the operator when it does not know.** Every decision carries `matured_sources_used` (how many independent sources have enough track record to be trusted), conviction (a single number reflecting both probability and Crucible severity), and an explicit verdict tier (STRONG_BUY, BUY, HOLD, SKIP). Bootstrap-mode decisions are flagged.

7. **Failures are classified, not hidden.** When a holding stops out, when a thesis invalidates, when a Crucible attack succeeds — the Failure Diagnostic Engine assigns a taxonomy code and writes a `FailedAssumption` node into the lineage graph. The operator sees the pattern across cycles.

**PMACS refuses to:**

1. **Hold positions on time alone.** No 90-day forced exits. The 90-day mark triggers a re-evaluation; the position holds if thesis and numbers still work.

2. **Trade names the operator did not curate.** No screener-driven universe expansion. The operator decides what universe means.

3. **Backtest against historical data the model was trained on.** It is epistemically invalid. SHADOW mode is the only forward-test that exists.

4. **Use sentiment from Reddit, X, or social platforms.** They are noise plus prompt-injection surface. The operator's own thesis does not need their consensus.

5. **Auto-promote prompt mutations.** Persona prompts shape reasoning; changes to them must pass operator confirmation after stat-significant A/B validation.

6. **Hide model integrity failures.** GGUF SHA256 mismatch on startup engages the kill switch. The operator must manually update the hashes file to resume.

7. **Optimize for a return target.** No 3-5% monthly aspiration calibrates any threshold. The system optimizes for upside-to-drawdown ratio. Return targets corrupt sizing.

---

## 5. Five non-negotiables

These are repeated in `Architecture.md §1.1-1.15` as code-level invariants. They appear here because the operator should understand what is structurally guaranteed, not just culturally preferred.

1. **LLMs never sign trades.** Trades are Ed25519-signed by `pmacs-execution`. An LLM cannot directly cause a trade.

2. **LLMs never math.** Probabilities are combined, sized, and arbitrated by Python. LLMs produce structured outputs only — GBNF-constrained on llama-server, JSON-Schema-constrained on Ollama, Pydantic-validated, sanity-validated.

3. **Every state transition is hash-chained.** Including audit log entries, holding state changes (via the state machine), and mode promotions. Tampering with one line breaks the chain. Cortex verifies on startup and hourly.

4. **Backend is operator-selectable; local is the default.** With the local backend, the inference process is `pf`-blocked from internet egress and no analysis data leaves the machine. The operator may instead opt into a cloud API backend (OpenRouter/Anthropic/OpenAI) via Settings — an explicit, auditable choice that trades the offline-privacy guarantee for accessibility. Regardless of backend, PMACS emits no telemetry and performs no external observation of operator decisions.

5. **Operator owns the kill switch.** Disengagement requires an explicit operator action (typed reason). The system can engage it. Only the operator can lift it.

---

## 6. Decision rights matrix

Who decides what. Operator-only decisions are operator-gated at submission. System-only decisions are deterministic and auditable. Shared decisions are system-proposed, operator-confirmed.

| Decision | System | Operator | Both |
|---|---|---|---|
| Cycle initiation (boot-detected) | ✓ | | |
| Cycle initiation (manual) | | ✓ | |
| Single-ticker re-run | | ✓ | |
| Phase 0 gatekeeper admittance | ✓ | | |
| Phase 1 persona signals | ✓ | | |
| Arbitration | ✓ | | |
| Crucible attack | ✓ | | |
| EV computation | ✓ | | |
| Sizing computation | ✓ | | |
| Conviction tier mapping | ✓ | | |
| Trade plan generation | ✓ | | |
| Trade plan signing | ✓ | | |
| Trade submission (PAPER) | ✓ | | |
| Trade submission (LIVE, autonomy mode) | ✓ | | |
| Trade submission (LIVE, manual approval mode) | | | ✓ |
| Stop-loss execution | ✓ | | |
| Catastrophe-net stop placement | ✓ | | |
| Position-level force exit | | ✓ | |
| Add ticker to universe | | ✓ (operator-confirmed) | |
| Remove ticker from universe (no active position) | | ✓ (operator-confirmed) | |
| Remove ticker from universe (with active position) | | ✓ (operator-confirmed, force-exit ack) | |
| Universe flag (ADV below threshold, halt, etc.) | ✓ | | |
| Mode promotion to PAPER_VALIDATED | | ✓ (operator-confirmed, after gates pass) | |
| Mode promotion to any LIVE mode | | ✓ (operator-confirmed, after gates pass) | |
| Mode demotion (system-initiated on regression) | ✓ | | |
| Kill switch engagement | ✓ | ✓ | |
| Kill switch disengagement | | ✓ (operator-confirmed, typed reason) | |
| Mutation candidate proposal (auto-detected) | ✓ | | |
| Mutation candidate proposal (operator-authored) | | ✓ | |
| Mutation A/B test execution | ✓ | | |
| Mutation recommendation (all) | ✓ | | |
| Mutation approval (all, operator-confirmed) | | ✓ (operator-confirmed) | |
| Mutation rollback (auto, regression-detected) | ✓ | | |
| Mutation rollback (operator-initiated) | | ✓ (operator-confirmed) | |
| Settings: display preferences | | ✓ | |
| Settings: risk thresholds | | ✓ (operator-confirmed) | |
| Settings: persona enable/disable | | ✓ (operator-confirmed) | |
| Settings: API credentials | | ✓ (operator-confirmed) | |
| Audit log replication target | | ✓ (operator-confirmed) | |
| Per-trade approval requirement toggle | | ✓ (operator-confirmed) | |
| Queue priority (per-cycle and persistent) | | ✓ | |
| Pin ticker | | ✓ | |
| Override a SKIP verdict (force into pipeline) | | ✓ | |
| Override a STRONG_BUY/BUY (block trade) | | ✓ (in autonomy-on mode, this is an override) | |

The pattern: **the system handles everything mechanical and deterministic; the operator handles every decision with a money or trust dimension.** Operator confirmation is the friction layer for operator decisions that change system behavior or move money.

---

## 7. Holding philosophy

### 7.1 Thesis-bound, not time-bound

A position is held while three conditions all hold:

1. **The thesis is still valid.** The narrative bet — "NBIS captures incremental AI-cloud demand from constrained hyperscalers," "ASTS ramps satellite-cellular service revenue with verifiable subscriber additions," "HIMS expands into chronic care without GLP-X regulatory blowback" — still maps to observed reality.

2. **The numbers still work.** EV remains positive after recomputation against current price. Sharpe contribution remains positive. Position-level drawdown is within configured limit.

3. **No hard exit fires.** Stop-loss, catastrophe-net stop, kill switch, halt, delisting, catalyst invalidation.

There is no 90-day forced exit. **The 90-day mark is a mandatory thesis re-evaluation trigger.** The full Phase 1 pipeline re-runs on the holding. If thesis still holds and numbers still work, the position continues with a refreshed `thesis_review_due_date` 90 days forward. If either breaks, the position transitions to `EXIT_THESIS_INVALIDATED`.

This is not a swing-trading system. The intended holding distribution is heavy-tailed: many positions exit within weeks (catalyst resolved, thesis confirmed or broken), some hold for months (slow narrative compounding), a few hold for years (10-bagger thesis playing out). The system is comfortable with all three. The operator does not have to anchor on a duration.

### 7.2 Conviction tiers

The system maps internal probability + EV + sizing + Crucible result to four operator-facing verdicts.

| Verdict | Condition | UI color | Behavior |
|---|---|---|---|
| **STRONG_BUY** | conviction ≥ 0.6, full-size position approved, all gates pass | green-700 | Trade auto-submitted in PAPER. Highlighted in Pipeline. |
| **BUY** | conviction ≥ 0.3, normal-size position approved, all gates pass | green-500 | Trade auto-submitted in PAPER. |
| **HOLD** | currently held, thesis still valid, weekly re-eval passed | blue-500 | Position maintained. Stop and trailing recomputed. |
| **SKIP** | abort_disagreement, abort_pre_llm, abort_llm, abort_risk, or conviction < 0.3 | zinc-400 | No trade. Reason recorded. Eligible for next cycle re-evaluation. |

The conviction formula is computed by `pmacs/engines/conviction.py` and its precise form is in `Architecture.md §9.2`. Its operator-facing behavior:

- **The Crucible can downgrade a high-probability signal.** A 0.7 directional probability with severe Crucible flaws becomes a SKIP. This is intentional. The Crucible exists precisely to override pattern-matched optimism.
- **Maturity haircuts conviction at low source counts.** A signal supported by zero mature sources is bootstrap-only and conviction tops out at ~0.5 regardless of probability strength. This is intentional. The system distrusts itself early.
- **EV multiple cap protects from "high probability, low expected return."** A 0.8 directional probability with EV barely above the minimum threshold caps conviction. If the bet does not pay enough to justify the risk, the system does not bet, however confident it is in the direction.

### 7.3 Re-evaluation cadence

The system has four re-evaluation rhythms:

| Trigger | What runs | Why |
|---|---|---|
| Intraday during RTH (every 30 min) | StopLossMonitor on active positions | Risk containment. Independent process. Uses cached intraday quotes. |
| Per cycle (boot-triggered) | Phase 0 gatekeeper + Phase 1 pipeline on candidates + light re-check on active holdings (catalyst resolution check, stop-loss recompute, technical breach) | Routine pipeline. Catches resolutions and incremental risk shifts. |
| Weekly | Full Phase 1 re-evaluation on every active holding | Thesis re-test against fresh evidence. Personas re-read filings, news, technicals. Crucible attacks the thesis again. |
| Monthly | Thesis-aging review report | Operator-visible report. The system writes a structured "what we believed, what we observed, what we believe now" memo per active holding. Operator can request rewrite or close. |

The cadence is asymmetric on purpose. Stop-losses fire fast; thesis re-evaluation is slow. Markets move quickly; theses do not.

---

## 8. Universe philosophy

### 8.1 Operator-curated, not index-anchored

Index inclusion is a heuristic, not a thesis. PMACS's universe is the set of tickers the operator believes can produce non-trivial alpha — typically smaller, growing companies with widening moats (Peter Lynch-style 10-baggers) and select Nasdaq-100 names with structural compute, platform, or distribution advantages.

The default seed (16 tickers — operator-extensible via Settings):

| Ticker | Company | Exchange | Note |
|---|---|---|---|
| ONDS | Ondas Holdings | Nasdaq | Drone networking |
| NU | Nu Holdings | NYSE | LATAM neobank |
| TEM | Tempus AI | Nasdaq | AI-driven precision medicine |
| ZETA | Zeta Global | Nasdaq | AI marketing platform |
| HIMS | Hims & Hers Health | NYSE | Telehealth + chronic care |
| NBIS | Nebius Group | Nasdaq | AI-cloud infrastructure |
| MELI | MercadoLibre | Nasdaq | LATAM commerce + fintech |
| RKLB | Rocket Lab | Nasdaq | Small-launch + space systems |
| ASTS | AST SpaceMobile | Nasdaq | Satellite-direct-to-cellular |
| FIG | Figma | NYSE | Design-tool platform |
| KDK | Kodiak AI | Nasdaq | Autonomous trucking. SPAC merger Sept 2025. |
| AUR | Aurora Innovation | Nasdaq | Autonomous trucking |
| GRAB | Grab Holdings | Nasdaq | SE Asia super-app |
| RBRK | Rubrik | NYSE | Data security / cyber |
| AAOI | Applied Optoelectronics | Nasdaq | Optical networking |
| SWMR | Swarmer Inc | Nasdaq | Drone autonomy. March 2026 IPO — very limited history. |

### 8.2 Limited-history flagging

Any ticker with fewer than 90 trading days of OHLCV history is admitted but **flagged**. The Phase 0 gatekeeper applies a stacked 50% conviction haircut on top of the normal bootstrap haircut. SWMR-class positions will be tiny ($25-50 on a $5K portfolio) until they accumulate history. This is a deliberate constraint against the operator's own enthusiasm on fresh IPOs.

Limited-history tickers are also excluded from the Mutation Engine's A/B base population. Statistical tests on n<90 are noise.

### 8.3 Add, remove, flag

- **Add:** Operator enters a ticker in Universe → admittance check (ADV ≥ $1M average, OHLCV available from at least one source, not halted, not delisted) → confirm → admitted. Limited-history flag applied automatically if days_of_history < 90.

- **Remove:** Operator triggers via Universe page. With no active position: confirm → removed. With active position: confirm + explicit force-exit acknowledgment → position closed → removed.

- **Flag (system, never auto-remove):** Daily ADV check; halt/delisting check. Failures surface as flags in the Universe page. Operator decides whether to remove.

- **Index overlay (optional):** Settings toggle adds the current Nasdaq-100 to the universe. Default off. The operator's curated names take priority in queue construction.

### 8.4 Why no screener-driven expansion

A screener applied at scale becomes the operator's effective thesis. PMACS would converge to "growth at this market cap with this revenue profile." The operator's edge is *thesis quality on names they understand*, not screener efficiency. The system refuses to dilute that edge by importing a screener.

The compromise is the index overlay. If the operator wants the Nasdaq-100 baseline plus their picks, they can have both. But the default is curated.

---

## 9. Mode ladder

The mode ladder is the path from "system is learning" to "system is trusted with money." It is monotonic for promotions, with auto-demotion possible on regression (see `Phases.md §3.5`).

### 9.1 The six modes

| Mode | Capital | Execution | Purpose |
|---|---|---|---|
| **SHADOW** | $0 | Audit-only. Signals captured. Math gate runs. No fake-trade recorded. | Validates the evidence pipeline, agents, math gate against real market without execution noise. **Always concurrent with PAPER.** |
| **PAPER** | $5,000 simulated | Real-time fake execution against Alpaca paper API. Bootstrap haircut active. | Sources mature. Mutation baseline accumulates. System learns its own behavior. |
| **PAPER_VALIDATED** | $5,000 simulated | Full sizing. Mature sources only. Mutation Engine fully active. | Performance-hardened. Ready for live. |
| **LIVE_EARLY** | Real $ via IBKR | Real execution. Capped position size (10% of capital initially). | First real-money mode. Operator-gated promotion (operator-confirmed). |
| **LIVE_STANDARD** | Real $ via IBKR | Real execution. Full sizing. | Steady-state. |
| **LIVE_EXPANDED** | Real $ via IBKR | Extended universe. Larger position cap. | Optional. Only after sustained performance. |

### 9.2 Concurrency model

```
Day 1:   SHADOW (audit-only) + PAPER (bootstrap, $5K)
         Mutation Engine: dormant

Day ~50 PAPER cycles:  Mutation Engine activates (SHADOW A/B testing; recommendations surface to operator)

Day ~90 PAPER cycles + gates pass:  Operator promotes to PAPER_VALIDATED
         SHADOW continues for audit-only consistency

Day ~180 PAPER_VALIDATED + gates pass:  Operator promotes to LIVE_EARLY
         Real broker (IBKR), capped position, capital from operator
```

Promotion gates and demotion triggers are numerical and live in `Phases.md §3`.

### 9.3 What stays autonomous

In paper mode, the entire pipeline auto-executes — signal → arbitration → sizing → trade plan → Alpaca paper submission. The operator can observe in real time, override individual decisions, or pause via kill-switch. **Operator approval per trade is not required.** This is intentional: paper exists precisely to test full autonomy with no real money at stake. Per-trade approval would corrupt the test.

In live modes, paper-mode autonomy is preserved by default. The Settings panel offers an operator-confirmed toggle to require per-trade operator approval (recommended OFF for full autonomy, ON for risk-aversion or while initially trusting the system at LIVE_EARLY).

### 9.4 What auto-demotion looks like

The system can demote itself without operator action when performance regresses. Demotion is one tier at a time. Demotion engages a 10-cycle observation period before any further promotion attempt.

| From | To | Trigger |
|---|---|---|
| LIVE_EXPANDED | LIVE_STANDARD | Rolling 20-cycle Sharpe < 0 OR drawdown > 12% |
| LIVE_STANDARD | LIVE_EARLY | Rolling 20-cycle Sharpe < 0 OR drawdown > 14% |
| LIVE_EARLY | PAPER_VALIDATED | Rolling 20-cycle Sharpe < 0 OR drawdown > 16% |
| PAPER_VALIDATED | PAPER | Rolling 30-cycle Brier > 0.32 OR Sharpe < -0.3 |

Demotion fires the kill switch first, then transitions mode after operator disengages. The operator can challenge the demotion via an operator-confirmed mode override. This is a deliberate friction; demotion exists to protect capital, not to be casually undone.

---

## 10. The flywheel — why the system gets smarter

The flywheel is not marketing language. It is the only justification for running PMACS for years instead of months. A static system frozen at v1 is a script. A system that observably gets sharper is a tool that compounds.

### 10.1 What gets better

The flywheel improves four things measurably:

1. **Calibration.** The Brier score on directional probabilities decreases as resolutions accumulate. Personas that are over-confident learn to widen their distributions. Personas that are under-confident learn to sharpen.

2. **Source weighting.** Some sources prove predictive on some ticker types and not others. Form 4 insider clustering predicts well in mid-cap names; less well in mega-cap. The system learns these weights per source, per sector, over time.

3. **Persona prompts.** When the Failure Diagnostic Engine clusters failures around a specific reasoning pattern (e.g., "MoatAnalyst consistently fails to consider competitive entry on platform-extension theses"), the Mutation Engine proposes a prompt variant addressing it. Operator-promoted variants enter production. The reasoning literally improves.

4. **Operator preferences.** Override Learning clusters operator overrides. When the operator consistently overrides SKIPs on names with specific characteristics (e.g., recent IPOs above $X market cap), the system learns the pattern and surfaces those decisions earlier in future cycles, even if the verdict is unchanged.

### 10.2 Where the flywheel lives in code

| Component | File | What it does |
|---|---|---|
| FailureDiagnosticEngine | `pmacs/engines/failure_diagnostic.py` | Classifies every terminal-state holding into one of 18 taxonomy types. Writes `FailedAssumption` graph nodes. |
| CalibrationEngine | `pmacs/engines/calibration.py` | Refits probability mappings against resolution history. Rejects refits below confidence threshold. |
| CausalAttribution | `pmacs/engines/causal_attribution.py` | Apportions credit/blame for each resolution to evidence and personas. |
| OverrideLearning | `pmacs/engines/override_learning.py` | Clusters operator overrides; surfaces patterns. |
| LessonsEngine | `pmacs/engines/lessons.py` | Extracts structured lessons; RAG-augmented retrieval at next decision. |
| **MutationEngine** | `pmacs/engines/mutation.py` + `pmacs/mutation/*` | Active flywheel. Proposes, A/B tests, promotes variants. |
| FlywheelHealth | `pmacs/engines/flywheel_health.py` | Monitors all of the above. Gates LIVE capacity expansion. |

The **Mutation Engine** is the single component that distinguishes PMACS's flywheel from every other "self-improving" claim. Most systems do passive learning (calibration refits, weight adjustments). PMACS does active learning: it generates candidate variants of its own components, runs them in parallel with production, statistically validates them, and promotes the winners. The lifecycle, mutation dimensions, and operator interaction model are detailed in `Architecture.md §10` and `Agents.md §17`.

### 10.3 What the operator sees

The operator does not interact with calibration math directly. The flywheel surfaces through:

- **Conviction reflects current calibration.** As personas sharpen, conviction moves with them.
- **The Mutation Engine panel in Settings.** Pending candidates with stat-sig and effect size. Recent auto-promotions log. Recent operator-promoted mutations. Recent rollbacks. Each entry is a one-line operator-readable diff.
- **The FailureDiagnostic view in the Pipeline page** (per ticker). When a holding stops out, the operator sees the taxonomy classification immediately, and can browse historical failures of the same type across tickers.
- **The FlywheelHealth widget on the Dashboard.** Brier trajectory, Sharpe trajectory, mutation throughput, calibration gap status.

### 10.4 What the operator never sees

- Raw weight matrices.
- Internal embeddings.
- Mutation candidates in the first 5 cycles of A/B (per spec — too early to be informative; just noise on the dashboard).
- Failed Mutation candidates that were rejected without promotion (visible in audit log; not surfaced in UI to avoid clutter).

The flywheel is observable when it matters and silent when it does not.

---

## 11. Failure modes the operator accepts

Honest disclosure. PMACS will be wrong. The system is built to *fail less over time*, not to *not fail.* The operator must accept these failure modes to use the system.

1. **The system will sometimes lose money on a high-conviction trade.** Probability is not certainty. A 0.7 directional probability is wrong 30% of the time. PMACS sizes for this; the operator must hold steady through it.

2. **The system will sometimes pass on a winner.** SKIP verdicts on names that subsequently rally are inevitable when source maturity is low or Crucible severity is high. The Pipeline page records every SKIP for postmortem.

3. **The system will sometimes stop out into a reversal.** Stop-hunting is real. The Failure Diagnostic Engine has a specific taxonomy code (`STOP_HUNTED`) for it. The Mutation Engine watches the cluster.

4. **The thesis re-evaluation will sometimes break a thesis prematurely.** Markets retest narratives. PMACS may exit on a temporary thesis dent that would have recovered. Forced re-entry has friction; the operator can override via single-ticker re-run.

5. **Mutation auto-promotions will sometimes be wrong.** The conservative thresholds (p < 0.05 AND Cohen's d > 0.2 AND magnitude < 10%) are calibrated to keep this rare, not impossible. Auto-rollback after 50-cycle regression is the safety net.

6. **The system can get the calibration wrong on new tickers for the first ~90 days.** Limited-history haircuts are designed for this; they will sometimes be too aggressive (missed opportunity) and sometimes too lenient (oversized loss).

7. **Operator overrides are sometimes wrong.** The OverrideLearning engine measures this. Patterns where the operator consistently overrides into bad outcomes become surfaced patterns.

8. **The audit chain integrity check can produce false positives on disk corruption.** When this happens, the kill switch engages and the operator must restore from backup. The system prefers a false-positive halt over a real-positive miss.

9. **Alpaca paper does not simulate real order books.** Paper fills are idealized — no slippage, no partial fills on illiquid names, no market-impact modeling. PAPER_VALIDATED performance will be slightly optimistic compared to live. The system acknowledges this gap; the LIVE_EARLY position-size cap is the mitigation.

10. **External APIs go down.** Polygon outages, Alpaca outages, EDGAR slowdowns. CRITICAL source failures abort the affected cycle; IMPORTANT failures degrade with confidence haircuts; NICE_TO_HAVE failures are silent.

11. **The model file can change underneath the system.** GGUF quantizations are not stable across upstream re-releases. PMACS verifies SHA256 on startup and refuses to run on mismatch. This will sometimes block a legitimate upgrade until the operator updates `model_hashes.toml`.

The operator's role is to read the failure pattern over months, not to react to single failures. The audit log is built for this kind of forensics.

---

## 12. Run wizard

The wizard runs once on initial install. It is the only setup flow. After completion, the system is in SHADOW + PAPER mode and ready to run cycles. Settings handles all subsequent changes.

### 12.1 Step sequence

The wizard is 10 steps. Each step blocks until passed. The operator can quit and resume; state is checkpointed at every step.

**Step 1 — Welcome and system identity check.**
Detects hardware. Confirms M1 family with at least 32GB RAM. Warns if less than 64GB (system will run but cycle times will be longer). Displays detected configuration for operator confirmation.

**Step 2 — Inference backend detection.**
Detects llama-server (default backend). If absent, shows install instructions (`brew install llama.cpp` or build from source) and blocks until verified. Optionally detects Ollama as alternate backend; surfaces but does not require.

**Step 3 — Model download and verification.**
Default model: `unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_XL` (~21GB).
Checks if already on disk. Shows progress bar if downloading. Verifies SHA256 against `config/model_hashes.toml`. Tests inference: sends a short prompt, confirms structured output works.

**Step 4 — macOS Keychain setup.**
Operator enters credentials for each of:
- Alpaca paper API key + secret
- Polygon.io API key
- Finnhub API key
- FRED API key
- SEC EDGAR User-Agent string (legal requirement for SEC API access)
- Optional: openFDA, FINRA, IR-page-monitoring credentials.

Each stored under service name `pmacs.<category>.<key>` in macOS Keychain. Never as environment variables. Never in config files. Never in the repository. The wizard tests each credential against a small read query before accepting.

**Step 4.5 — Embedding model setup.**
Download `BAAI/bge-base-en-v1.5` via `sentence-transformers`. Verify model loads and produces a 768-dim vector on test input. ~420MB download, ~1.2GB RAM during inference. Runs on CPU.

**Step 5 — Database initialization.**
Initializes KuzuDB, Qdrant, DuckDB, SQLite, and the audit log. Writes the genesis audit entry with `prev_sha256 = "0" * 64`. Runs schema migrations via `ops/migrate.py`.

**Step 6 — Data source connectivity ping.**
One ping per source. Displays a green/red matrix. CRITICAL sources must pass to proceed. IMPORTANT and NICE_TO_HAVE sources can fail without blocking; the wizard shows a warning and offers retry.

**Step 7 — Universe seed.**
Displays the default 16-ticker seed. Operator can deselect any, add more, or accept as-is. Each ticker validated for OHLCV availability before admission. Limited-history flags applied automatically.

**Step 8 — Cycle preferences.**
Confirms display currency (USD primary). Confirms timezone for "EOD" semantics (default US/Eastern, 16:30 ET). No schedule needed — PMACS is boot-driven.

**Step 9 — Smoke-test cycle.**
Runs one full pipeline against synthetic fixture data. Verifies all engines execute, all DBs are writable, the audit chain validates, the kill-switch trigger works. Displays the result. Operator can inspect the synthetic outputs to understand what real cycles will look like.

**Step 10 — Promote to SHADOW + PAPER.**
The audit log records the first mode promotion. Wizard exits. Dashboard opens at `localhost:8000`.

### 12.2 Wizard re-entry

The wizard is one-shot under normal use. Re-running it via `pmacs wizard --reset` from CLI wipes all state and starts over. This is destructive (deletes all DBs, audit log, holdings, and history) and requires operator confirmation. The operator should never need to re-run unless reinstalling on a new machine.

### 12.3 Wizard design language

The wizard uses the same Notion aesthetic as the dashboard. Each step is a single full-window panel with a progress strip at the top showing 10 dots. Forward-only navigation; operator can quit at any step and resume at the same step on next launch. No "back" button mid-step (each step's success persists state and cannot be partially undone).

Animations: 200ms cross-fade between steps. No unnecessary transitions. The wizard should feel competent and brief, not playful.

Error states: every failure surfaces a specific error code, the spec section it relates to, and a "copy for Claude Code" button. The wizard is the operator's first impression of how PMACS handles errors — it must demonstrate the same clarity the runtime debug log provides.

---

## 13. The dashboard application

### 13.1 Visual identity

**Aesthetic:** Notion-derived but elevated. Spacious, neutral, data-first, single accent — with selective, restrained polish: a gradient PMACS wordmark, a glassmorphic sidebar (subtle `backdrop-blur` over the surface), soft `*-soft` tints for status, and gentle elevation shadows on cards and the command palette. Gradients and glass are used for chrome accents only, never on data. Data is the content; chrome stays out of the way.

**Color tokens (semantic Tailwind layer).** The UI is built on a named token layer rather than raw palette utilities, so light/dark themes and future re-skins flow from one place. Classes reference the token name (`bg-surface`, `text-text-primary`, `bg-accent-soft text-accent`, `bg-crucible`), and the build maps each token to concrete light/dark values.

| Token family | Roles |
|---|---|
| `surface`, `surface-elevated`, `surface-sunken` | page background, cards/panels, recessed wells |
| `border`, `border-subtle` | dividers, card edges, subtle hairlines |
| `text-primary`, `text-secondary`, `text-muted` | body, labels, de-emphasized metadata |
| `accent`, `accent-soft` | primary actions, BUY emphasis, active nav (`bg-accent-soft text-accent`) |
| `positive`, `positive-soft` | gains, STRONG_BUY emphasis |
| `negative`, `negative-soft` | losses, ERROR level, destructive |
| `warning` | degraded/caution |
| `crucible` | Crucible severity, HOLD/SKIP emphasis |

Theme follows system preference by default. Manual light/dark toggle (`#theme-toggle`) and density toggle (`#density-toggle`) live in Settings → Appearance.

**Typography:** fonts are **self-hosted** (`/static/vendor/fonts/fonts.css`, `woff2` subsets) — no CDN call, consistent with the offline-capable posture even when a cloud LLM backend is selected.

- **Inter** (variable weight) for all UI text.
  Sizes: 12 (caption), 14 (body), 16 (subhead), 20 (head), 28 (page title).
- **JetBrains Mono** for: prices, percentages, hashes, ticker symbols, audit lines, conviction scores, all numeric data, code snippets.

**Spacing:** Tailwind default 4px base. Page gutter: 32px. Card padding: 24px. Section gap: 16px. Tight numeric tables: 12px row padding.

### 13.2 Chrome (persistent across pages)

**Top bar.** Fixed, 56px (`h-14`) tall, `role="banner"`:
- Far-left on narrow viewports: a hamburger button (`lg:hidden`, `aria-label="Toggle navigation"`) that opens the mobile sidebar overlay.
- Left: PMACS wordmark — monospace, **gradient text** (`gradient-text`), `aria-label="PMACS"`.
- Center-left: mode badge (`SHADOW + PAPER`, `PAPER_VALIDATED`, `LIVE_EARLY`, etc.) — `bg-positive-soft text-positive` when in a PAPER tier, `bg-accent-soft text-accent` otherwise; `aria-label="Current mode: …"`.
- Center: current cycle indicator (`role="status" aria-live="polite"`, hidden on `<sm`). When idle: last-cycle summary. When running: animated progress with the current ticker.
- Right: kill switch button (always visible, hover turns it `bg-negative`), `aria-label="Kill switch"`, with a visible focus ring.

**Left sidebar.** Fixed, 240px (`w-60`) wide, **glassmorphic** (translucent over surface), `role="navigation" aria-label="Main navigation"`; collapsible to an icon rail (`#sidebar-toggle`), and on `<lg` it is hidden behind the hamburger + overlay (`#mobile-sidebar-overlay`):
- Page nav: Dashboard, Agents, Pipeline, Universe, Cortex, Debug, Settings. Each item = icon + label + a `kbd` shortcut hint (shown `lg:` only). Active page highlighted `active bg-accent-soft text-accent`.
- Bottom: a live "system online" dot + "Operator" label, and a command-palette search button (`aria-label="Open command palette (Cmd-K)"`, `K` kbd hint).

**Main content.** Scrollable. Page-specific.

**Toast notifications.** Bottom-right. Stack of up to 5. Auto-dismiss 5s for info, sticky for warning/error until clicked. Toasts come from SSE event stream and from operator action confirmations.

**Modal dialogs.** For operator confirmations, destructive actions, and high-friction operator decisions. Always carry: action description, consequences, "Type SYMBOL to confirm" for destructive, confirm field, cancel button.

**Cmd-K command palette.** Available everywhere. Type to search:
- Tickers (jump to Pipeline filtered to that ticker)
- Pages (jump to)
- Quick actions ("run cycle now", "engage kill switch", "promote NBIS to priority")
- Audit search (recent cycle IDs, error codes)

### 13.3 Component library (reusable patterns)

**Card.** `bg-surface-elevated`, 1px `border` (or `border-subtle`), rounded (≈12–16px radius), 24px padding. Hover state: soft elevation shadow. Gradients/glass are reserved for chrome (wordmark, sidebar), not data cards.

**StatBlock.** Vertical layout. Label (text-secondary, 12px). Value (large, 28px, JetBrains Mono if numeric). Optional delta (small, colored by sign).

**Sparkline.** Inline, 24px tall, full-width. Single line. No axes. No grid. Hover reveals point values.

**TickerChip.** Rounded pill, 4px padding, monospace ticker, optional verdict color border. Click → Pipeline filtered to ticker.

**PersonaCard.** Used in Agents page. 320px tall card with persona name, role, status, progress bar, output summary. Detailed in §15.

**EventRow.** Used in feeds and debug log. Single line: timestamp (mono, muted), level badge (colored), source (mono), message (truncated). Click to expand.

**Sankey (custom, D3-based).** Used in Agents page communication-layer view. Smooth animated path transitions. Hover reveals flow values.

**Diff viewer.** Used in Mutation Engine review. Side-by-side or unified. Syntax-highlighted. Operator can copy either side.

### 13.4 State design philosophy

**Empty states.** Every page has a meaningful empty state. Pre-first-cycle: explanation of what will appear and "Run smoke-test cycle" call-to-action. Post-cycle but no holdings: explanation that this is normal during bootstrap. The empty state is never a generic "No data."

**Loading states.** No spinners. Every loading state shows *what* is loading (e.g., "Fetching Polygon EOD for 16 tickers") and an ETA based on rolling cycle averages. If ETA exceeds 30s, also show a cancel button. Skeleton placeholders use the same shape as the eventual content.

**Error states.** Every error surfaces:
- The error code (canonical, see `Architecture.md §5.2`)
- A one-sentence operator-readable description
- A "What this means" expander (3-5 sentences)
- A "What to try" section (specific actions)
- A "Copy for Claude Code" button (paste-ready prompt)
- A link to the relevant section in the spec

The system never shows a stack trace to the operator unless they explicitly request raw mode. The Debug page shows raw payloads.

### 13.5 Notification policy

The system surfaces events to the operator with intentional restraint. Over-notification is the failure mode being designed against.

| Event | Surface | Sound |
|---|---|---|
| Trade filled (PAPER) | Toast, info, 5s | None |
| Trade filled (LIVE) | Toast, info, persistent until clicked | None (Mac system click) |
| Stop-loss triggered | Toast, warning, persistent | System click |
| Kill switch engaged | Modal, red, blocks UI until dismissed | System alert |
| Cycle complete | Toast, info, 5s | None |
| Mutation candidate ready for review | Toast, info, 5s + Settings badge | None |
| Mutation approved by operator | Toast, info, 5s | None |
| Audit chain failure | Modal, red, blocks UI | System alert |
| Disk low | Toast, warning, persistent | None |
| Reconciliation mismatch | Toast, warning, persistent | None |
| Source connectivity degraded (IMPORTANT) | Toast, warning, 30s | None |
| Source connectivity degraded (NICE_TO_HAVE) | Silent (Cortex page only) | None |
| Source connectivity recovered (IMPORTANT+) | Toast, info, 5s | None |

The operator can adjust notification levels in Settings → Notifications. The kill switch and audit chain failure modals are non-disable-able.

### 13.6 Keyboard shortcuts

| Shortcut | Action |
|---|---|
| Cmd-K | Open command palette |
| Cmd-1 through Cmd-7 | Jump to page (Dashboard, Agents, Pipeline, Universe, Cortex, Debug, Settings) |
| Cmd-R | Refresh current page (re-fetch from server) |
| Cmd-/ | Show keyboard shortcut overlay |
| / | Focus search/filter on current page |
| Esc | Close modal, close drawer, dismiss toast |
| Cmd-Shift-K (in Agents page) | Engage kill switch (with typed confirmation modal: type KILL to confirm) |
| ? | Show contextual help for current page |

### 13.7 Accessibility

- Respects `prefers-reduced-motion`: all animations (Sankey, progress bars, Math view transitions) are disabled when this media query is active. Static equivalents are shown.
- **Target:** all color combinations meet WCAG 2.1 AA contrast, verified in CI via axe-core (`tests/accessibility/test_a11y.py::TestAxeCoreCI`, browser tests skipped by default). The redesign currently has open AA violations tracked as a separate a11y-remediation task; AA compliance is the standard the pages are held to, not yet a clean pass.
- Every interactive element is keyboard-accessible. Tab order is meaningful.
- Focus states are clearly visible (`focus-visible` accent outline with offset, never `outline: none` without replacement).
- Screen readers: every icon is `aria-hidden` and paired with an adjacent `aria-label`/text; dialogs use `role="dialog" aria-modal="true"`. Live regions (`aria-live`) for the cycle indicator, toasts, and SSE-driven UI updates.
- Browser back/forward: HTMX pushes URL state for every page navigation and drawer open/close. Back button returns to previous page or closes the drawer. Forward works correspondingly.
- The dashboard works at 200% browser zoom without horizontal scrollbars.
- **Responsive, not viewport-gated.** The earlier "use a wider window below 1024px" guard has been removed. The layout is responsive on a single `lg:` breakpoint: at `≥lg` the glassmorphic sidebar is pinned; below `lg` it collapses behind a hamburger button and a dismissible overlay (`#mobile-sidebar-overlay`). It remains a desktop-first tool, but no longer blocks narrow viewports.

---

## 14. Page: Dashboard

**Purpose:** at-a-glance system health and portfolio summary. The operator's home base.

**Path:** `/`

**Layout (top to bottom; responsive — a right-rail appears on wide viewports, content reflows to a single column when narrow):**

### 14.1 Portfolio summary card

Top of page. Full width.

- **Current value** (very large, JetBrains Mono): `$5,234.67` USD primary, `€4,891.20` muted secondary
- **Day change** (medium, colored): `+$47.23 (+0.91%)`
- **Time-window selector** (chip group): 1D / 1W / 1M / 3M / YTD / All — selecting changes the sparkline below and a delta below the value
- **Sparkline** (24px tall, full-width, single line)
- **Window delta**: `+$234.67 (+4.7%) since 1M ago`

### 14.2 Mode and cycle status card

- **Mode badge** large: `SHADOW + PAPER` (colored by tier)
- **Last cycle**: `Tue Apr 30 14:32 UTC, duration 1h 47m, 14 tickers processed, 3 STRONG_BUY, 4 BUY, 5 HOLD, 2 SKIP`
- **Next cycle**: `Triggered on next boot detection (24h+ gap from last)` OR active progress bar if cycle running
- **Run cycle now** button (primary action, `accent`)

### 14.3 Risk metrics row

Five StatBlocks horizontally:

- **Sharpe** (rolling 20 cycles): `0.84`
- **Sortino**: `1.12`
- **Max drawdown** (rolling): `-4.2%`
- **Win rate** (closed positions): `64% (16 of 25)`
- **Avg R/R** (closed): `2.1x`

### 14.4 Active positions table

Per row: ticker, entry date, entry price, current price, MtM %, conviction-at-entry, current re-eval status (HOLD / WARNING / EXIT_SIGNAL), days held, time to next weekly re-eval. Sortable by any column. Click row → Pipeline page filtered to that ticker.

### 14.5 Recent decisions feed

Last 20 decisions across all cycles. Per row: timestamp, ticker chip, verdict, reason summary (truncated 60 chars). Click → drills into that cycle's full decision lineage on Pipeline page.

### 14.6 System health card (right rail or bottom on narrow)

- Audit chain status indicator (green dot + "Verified at HH:MM" / red dot + "BREAK at HH:MM")
- Disk free
- Clock drift
- Heartbeats: per-process status pills
- Last 24h debug-event severity histogram (info/warn/error/critical counts)

### 14.7 Mutation Engine summary card

Reflects the engine's lifecycle state. Before activation (the first 50 PAPER cycles) it shows the **Dormant** state with "Activates after 50 PAPER cycles" and a `Candidates` count of 0. Once active, it summarizes candidates under test / pending operator review / recently approved / recently rolled back.

- Click → Settings → Mutation Diff

### 14.8 Empty states

Pre-first-cycle: a single hero card with explanation of what will appear. After 0 holdings but cycles complete: positions table shows "No active holdings — cycles are running but no STRONG_BUY signals have cleared all gates yet. This is normal during bootstrap. See Pipeline → Recent SKIPs for verdict reasoning."

---

## 15. Page: Agents

**Purpose:** real-time visibility into the per-cycle pipeline. **The most important page in PMACS.** This is where the operator watches the system think.

**Path:** `/agents`

**Design intent.** This page must answer "Why did the system reach this verdict?" without requiring the operator to read code or audit logs. Animations make abstract data flow concrete. The page must feel like watching a team of analysts work in coordination, even though the personas operate independently.

### 15.1 Layout

The page has three primary regions plus a collapsible bottom strip:

```
+-------------------------------------------------------+
|  Queue strip                                          |
+-------------------------------------------------------+
|                                                       |
|  Current ticker panel              | Decision summary |
|  (dominant area)                   | (right rail)     |
|                                    |                  |
|  --- Gatekeeper + 7 analysis + Crucible ---| Phase progress   |
|  ---        (9 persona cards, grid)     ---     | Verdict tier     |
|                                    | Trade plan       |
|  Communication layer viz           |                  |
|                                                       |
+-------------------------------------------------------+
|  Cycle log strip (collapsible)                        |
+-------------------------------------------------------+
```

### 15.2 Queue strip

Horizontal scrollable list at top. Each ticker is a chip showing:
- Ticker symbol (mono)
- Phase indicator (small dot: gray queued, blue running, green done, red aborted)
- Conviction-so-far (number, only after Phase 1 completes)

Drag to reorder (operator-initiated priority change). Click to jump current ticker panel to that ticker's run. The currently-running ticker is highlighted with accent border and animated pulse.

### 15.3 Current ticker panel — header

Above the persona row:
- **Ticker** large: `NBIS` (JetBrains Mono, 28px)
- **Company name** subdued: `Nebius Group`
- **Current phase** badge: `Phase 1 — Research`
- **Elapsed**: `02:14`
- **ETA**: `~01:46 remaining` (based on rolling average for this cycle position)

### 15.4 Persona row

Nine cards in a responsive grid (`grid-cols-3`):

1. Gatekeeper
2. Catalyst Summarizer
3. Growth Hunter
4. Moat Analyst
5. Macro Regime
6. Insider Activity
7. Short Interest
8. Forensics
9. Crucible (visually distinct — adversarial tester)

Each card shows a `ready`/`idle` status indicator and a one-line role description (e.g. Gatekeeper "Final gate check", Crucible "Adversarial testing"). Before a cycle runs the row shows a "Waiting for cycle" state.

(Note on the redesign: **Gatekeeper is now shown as a card** in this row — though it is the deterministic Phase-0 gate, the operator wanted its pass/fail visible inline. **MemoWriter is not a separate card** here; the memo draft surfaces in the decision summary / Memo page rather than as a tenth persona tile.)

Each persona card displays:

- **Persona name** (header, 16px, bold)
- **Role** (one-line description, 12px, muted): e.g. "Reads filings + qualitative narrative; outputs moat strength score and directional probability"
- **Status indicator**:
  - Queued: gray dot, "Waiting"
  - Running: animated progress bar showing token output rate (estimated from rolling-average tokens/sec; corrects on completion)
  - Complete: green checkmark, time taken
  - Failed: red X, error code
- **On complete**: directional probability bars (horizontal, three segments p_up / p_flat / p_down with labels), `historical_n` count, `rolling_brier`, weight in arbitration
- **Persona-specific summary line**: e.g. "Moat strength: 0.74 — strong distribution + data network effects" for MoatAnalyst; "3 red flags raised: declining gross margin, rising DSO, related-party transactions" for Forensics

**Interactions:**
- Hover: card lifts (transform: translateY(-2px), shadow-md)
- Click: card expands to drawer overlay showing:
  - Full memo (operator-readable summary)
  - Evidence citations (clickable to evidence detail)
  - Raw structured output (JSON viewer with expand/collapse)
  - This persona's last 10 outputs on this ticker (track record)
  - This persona's rolling Brier on this ticker (`persona_ticker_affinity` data)

### 15.5 Communication layer visualization

Below the persona row. The most distinctive UX element in PMACS.

**Toggle** (chip group at top of viz region, `data-comm-view`): **Process** / **Signals** / **Conviction**. The Process view is the default (its chip carries `aria-pressed="true"`); the Signals (`#viz-signals`) and Conviction (`#viz-conviction`) panels start `hidden`.

**Process view** (default, `#viz-process`). A horizontal timeline showing the cycle stages:
```
Evidence → Personas → Arbitration → Crucible → Sizing → Risk Gate → Verdict
```
Each stage is a node. Lines connect them. As the cycle progresses, completed stages fill with their result (e.g., Arbitration node shows `p_up=0.62, p_flat=0.20, p_down=0.18`). Pending stages are gray.

**Signals view** (`#viz-signals`). A Sankey diagram (D3, served data from `/agents/sankey-data`: `evidence_sources`, `personas`, `flows`, `stages`). On the left: evidence sources (SEC filings, EOD, Form 4, news, IR pages). In the middle: personas. On the right: the Arbitrated output. Flow widths correspond to evidence relevance weights. Hover over a flow reveals which specific evidence pieces flowed. After Arbitration, a second smaller Sankey shows Arbitrated → Crucible (colored by severity); a successful Crucible flip is drawn as an override arrow.

**Conviction view** (`#viz-conviction`). The actual numbers. Per persona: `p_up`, `p_flat`, `p_down`, weight (after Brier-derived adjustment). Below: the arbitration formula computed step-by-step, then the conviction formula computed step-by-step. Operator-readable, full transparency — useful for auditing why a verdict came out the way it did.

**Animation.** Process view animates left-to-right as stages complete. Signals view re-renders smoothly when a persona completes (D3 enter/update/exit transitions, ~200ms). Conviction view fills in numbers progressively. None of this is critical-path; the dashboard's responsiveness is unaffected by animation rendering.

### 15.6 Decision summary right rail

Always visible. Fills in as the pipeline progresses:

- **Phase 0 (Gatekeeper)**: PASSED / ABORTED with reason
- **Phase 1 (Research)**: persona count complete / total
- **Arbitration**: `p_up=0.62, p_flat=0.20, p_down=0.18, matured_sources=3, decision=PROCEED`
- **Crucible**: `severity=0.18, attacks=2, defended=2`
- **EV**: `+0.24 (1.6x minimum)`
- **Sizing**: `target=$487 (4.87 shares), bootstrap_haircut=0.80, limited_history=N/A`
- **Risk Gate**: PASSED
- **Conviction**: `0.46`
- **Verdict**: `BUY`
- **Trade plan**: `BUY 4.87 shares NBIS @ market` with signed-by indicator

After fill received: trade plan updates to show fill details.

### 15.7 Cycle log strip

Collapsible. Bottom of page. SSE-fed live stream of debug events scoped to the current cycle. Filterable by severity. Same component as Debug page, scoped.

### 15.8 No-active-cycle state

When no cycle is running, the page shows the most recent completed cycle in the same layout, with an indicator at top: "Cycle completed Tue 14:32. Showing last cycle. [Run new cycle]"

### 15.9 Power user: cycle compare

Cmd-K → "Compare cycles" → select two cycle IDs → side-by-side view of the same ticker across both cycles. Shows what changed (different evidence available, different persona outputs, different Crucible result, different verdict). Useful for understanding why a re-run produced a different answer.

---

## 16. Page: Pipeline

**Purpose:** ticker-centric view across cycles. Where the operator manages active positions and reviews historical verdicts.

**Path:** `/pipeline`

### 16.1 Layout

A four-column **kanban** (`grid-cols-4`): STRONG_BUY / BUY / HOLD / SKIP. Cards come from current and recent cycles and are **draggable between columns**; ordering and column placement persist via the queue API (§16.5). There is **no separate priority-band right rail** — the kanban columns *are* the queue.

### 16.2 Top filter bar

A single compact bar above the board:
- **Verdict selector** (`#verdict-filter-select`): All Verdicts / STRONG_BUY / BUY / HOLD / SKIP.
- **Search** input (placeholder `Filter...`) — matches ticker.
- **Queue info**: a "Filtered" count of cards currently shown.

(The earlier multi-select verdict/state/sector/date-range filter set was simplified to this verdict-dropdown-plus-search bar in the redesign.)

### 16.3 Verdict columns

Each column is color-coded by tier and carries a dashed-border empty placeholder when no cards qualify:

**STRONG_BUY column** (`bg-positive-soft`). Highest-conviction tickers from the most recent cycle. Empty: "No strong buy signals".

**BUY column** (`bg-accent-soft`). Lower-conviction buy tier.

**HOLD column** (`bg-crucible`). Currently active positions — the "live portfolio" view.

**SKIP column** (`bg-crucible`). Aborted/skipped tickers from recent cycles.

Columns are drop targets (`ondragover`/`ondrop`); the column header shows aggregate stats (count, summed/avg conviction). Drag-and-drop handlers are always present even when a column is empty.

### 16.4 Per-ticker card

Each draggable card (`data-ticker`, `data-verdict`, `draggable`) shows:
- **Ticker** (mono, bold) — a link to the ticker's **Memo page** (`/memo/{ticker}`).
- Current price + key financials when available (revenue growth, gross margin, free cash flow, fair value / upside).
- Conviction score (numeric + small bar).
- Two actions: **Re-run** (`queueTickerNow(ticker)` — single-ticker re-run; queues behind a running cycle or starts immediately, audit-logging `cycle_initiated_by_operator` with `single_ticker=true`) and **Memo** (link to `/memo/{ticker}`).

### 16.5 Queue management (drag-and-drop, no separate rail)

Queue management happens **on the board itself**, not in a side rail. Operators drag cards within and between columns; placement and order persist through the queue API:
- `POST /pipeline/queue/reorder` — persist a card's column/position (drag-drop).
- `POST /pipeline/queue/pin` — pin a ticker so it survives across cycles.
- `POST /pipeline/queue/promote` — promote a ticker for the next cycle.
- `POST /pipeline/queue/scheme` (save) and `GET` (load) — named, recallable board arrangements ("schemes").

(The earlier P1–P4 multi-band priority rail was removed; `from_band`/`to_band` survive only as an internal reorder-payload detail.)

### 16.6 Ticker detail → the Memo page

Clicking a card's ticker navigates to the dedicated **Memo page** (`/memo/{ticker}`) rather than a slide-in drawer. The Memo page carries the full final memo, per-persona memos, historical decisions, position lineage (if held), and failure history (FailedAssumption nodes), with a re-run action — the content the old drawer described, now a routable, deep-linkable page.

### 16.7 Empty states

Pre-first-cycle: explanation card. Mid-cycle (running): "Cycle in progress — verdicts will appear as tickers complete. See Agents page for live view."

---

## 17. Page: Universe

**Purpose:** manage the set of tickers PMACS considers.

**Path:** `/universe`

### 17.1 Layout

- **Heading:** "Ticker Universe".
- **Top bar:** an **Add Ticker** button and a **Bulk Actions** button.
- **Group-by tabs** (segmented control, first tab active = `bg-accent-soft text-accent`): **All / Watchlist / Portfolio / Sectors**. (The redesign replaced the earlier five-way group-by selector — GICS/sub-sector/market-cap/exchange/status — with these four tabs.)
- **Ticker list:** a table of rows (per §17.2). When the universe is empty it shows an empty state — "No tickers yet" with an "Add your first ticker" call-to-action.

### 17.2 Per-ticker row

- Ticker (mono)
- Name
- Exchange
- Sector / sub-sector tag
- Market cap
- ADV (90-day average)
- Days of history
- Status badges:
  - "Limited history" (amber) — fewer than 90 days
  - "Flagged: ADV below threshold" (amber)
  - "Active position" (blue)
  - "Halted" (red)
  - "Pinned to queue" (purple)
- Last-cycle conviction (numeric + small bar)
- Hover: row actions appear (edit sub-sector tag, remove ticker)

### 17.3 Add ticker modal

Operator types ticker symbol. The system auto-fills name, exchange, sector via data API. Live admittance check displayed:
- ADV (must be >= $1M)
- OHLCV available from at least one source
- Not halted
- Not delisted
- Days of history (just informational; <90 triggers limited-history flag)

Submit → confirm → admitted. Audit log records the admission.

### 17.4 Bulk actions

Select multiple tickers (checkboxes). Bulk options:
- Tag with operator-defined sub-sector (e.g., "AI infrastructure," "fintech LATAM," "drone autonomy")
- Remove (operator-confirmed, with confirmation if any have active positions)

### 17.5 Index overlay toggle

Top-right of page. When enabled, the current Nasdaq-100 is added to the universe (system fetches list from index data source on toggle). When disabled, the Nasdaq-100 names are removed (operator-curated names persist). State is tracked in audit log.

### 17.6 Sub-sector taxonomy (operator-tagged, free-form)

The system uses GICS sectors as a baseline (auto-applied on add). Operator can additionally tag sub-sectors as free-form labels — typically thematic ("AI infrastructure," "satellite-direct-cellular," "autonomous trucking," "LATAM neobank"). These tags drive grouping in this page and feed the Mutation Engine's persona-affinity dimension (some personas may perform better on certain themes).

---

## 18. Page: Cortex

**Purpose:** system-level health and integrity monitoring. The operator's "is the machine itself OK" page.

**Path:** `/cortex`

### 18.1 Layout

Six panels in a 2x3 grid (single column on narrow viewports):

### 18.2 Audit chain panel

- Status indicator (green / red)
- Last verified at
- Total entries
- Current chain head SHA (clickable to copy)
- Re-verify button (runs full chain validation, may take 30-60s for a year of audit log)

### 18.3 Cross-DB consistency panel

- KuzuDB / Qdrant / DuckDB / SQLite reconciliation status (4 indicators)
- Last reconciled at
- Drift rows count
- Re-reconcile button

### 18.4 Process status panel

Per process (cortex, cortex-self-check, nervous, execution, dashboard, stoploss, mutation, inference):
- Heartbeat age
- Restart count last 24h
- BROKEN_CRASH_LOOP flag if applicable
- PID (for operator forensics)

### 18.5 Disk / clock / network panel

- Disk free per relevant volume
- NTP drift
- Source connectivity matrix (one row per data source, status indicator, last successful fetch, last error)

### 18.6 Kill switch panel

- Current state (ARMED / ENGAGED) — large, color-coded
- Recent triggers history (last 10)
- **Engage manually** button (no confirmation step; engagement is always the safer option)
- **Disengage** button (typed reason; only enabled when ENGAGED and trigger condition resolved)

### 18.7 Model integrity panel

- GGUF SHA256 last verified
- mmproj SHA256 (if multimodal)
- Last model swap date
- Active model name
- Backend (llama-server / Ollama)

---

## 19. Page: Debug

**Purpose:** the developer's window. Every event in the system, filterable, with one-click "copy for Claude Code" output.

**Path:** `/debug`

### 19.1 Layout

- **Filter bar (top):** level, process, component, error_code, cycle_id, ticker, time range, free-text search
- **Event stream:** newest at top, SSE-fed live
- **Saved filters (right rail):** operator can save common combinations

### 19.2 Event row

Single line:
- Timestamp (mono, muted)
- Level badge (color-coded: DEBUG gray, INFO blue, WARN amber, ERROR red, CRITICAL purple)
- Process name (mono)
- Component name
- Error code (if any, mono)
- Message (truncated, full text on hover)

Click row → expands inline showing:
- Full payload (JSON viewer)
- Traceback (if any)
- spec_ref (link to relevant section in Source/Architecture/Agents/Phases)
- suggested_fix_keywords (chip group)
- **Copy for Claude Code** button — produces a paste-ready prompt with event payload + spec section reference + repro steps

### 19.3 Quick filter shortcuts

Above the event list, persistent chip group:
- "Errors only" (level=ERROR or CRITICAL)
- "Current cycle" (cycle_id = active)
- "Last hour"
- "LLM events" (component starts with "agent.")
- "Trade events" (component starts with "trade." or "execution.")

---

## 20. Page: Settings

**Purpose:** all runtime-editable configuration. operator-confirmed for sensitive changes.

**Path:** `/settings`

### 20.1 Layout

A single scrollable page laid out in a small grid of cards. The redesign **consolidated the old 13-section settings IA into seven focused sections**; deeper/structural configuration moved into the **setup wizard** (`/wizard/*` — broker keychain, inference provider, universe seed, cycle prefs) and into versioned **TOML config** (`config/risk.toml`, `config/crucible.toml`, `config/mutation.toml`, etc., see §20.8 / CLAUDE.md). Settings holds only what the operator changes at runtime. The seven sections (each verified by `tests/e2e/test_settings_page.py`):

### 20.2 Appearance

- **Theme** toggle (`#theme-toggle`, system / light / dark).
- **Density** toggle (`#density-toggle`, compact / comfortable / spacious).
- **Currency** select (`#currency-select`, USD / EUR).
- **Timezone** select (`#timezone-select`, EOD timezone).

### 20.3 AI Provider

The runtime expression of the operator-selectable backend (§4):

- **Mode toggle** (`#inference-mode-toggle`): **Local** vs **Cloud API**.
- **Local panel** (`#inference-local-panel`): radio list of local providers (e.g. `llama-server`, `Ollama`) from the inference registry, each showing its `id` and active model.
- **Cloud panel** (`#inference-cloud-panel`): cloud-API provider selection (OpenRouter / Anthropic / OpenAI); API keys are Keychain-stored, never shown or logged.
- **Test connection** button (`#inference-test-btn`): pings the selected backend and reports success/failure.

### 20.4 Budget

Token-cost guardrails for cloud backends:

- **Daily cap** (`#cost-daily-cap-input`) and **Monthly cap** (`#cost-monthly-cap-input`) in dollars.
- **Usage bars** (`#settings-cost-daily-bar`, `#settings-cost-monthly-bar`) showing spend against each cap.

### 20.5 Notifications

Per-event notification level (see §13.5). Each event row has a select: **Toast / Toast + Sound / Modal / Silent**, persisted via `POST /api/settings/notifications`. The `kill_switch_engaged` and `audit_chain_failure` rows are **locked** (rendered disabled) — those modals are non-disable-able.

### 20.6 Reset Progress

Destructive reset of accumulated state:

- **Reset** button (`#reset-btn`), gated by a **confirmation checkbox** (`#reset-confirm-check`) that must be ticked before the action is allowed.

### 20.7 Mutation Diff

Operator review surface for the Mutation Engine (the advisor — §10, Architecture.md §10). A **Mutations** panel lists proposed candidates; opening one shows a **diff modal** (`#diff-modal`) with a side-by-side/unified **diff container** (`#diff-container`) of the proposed change against current production config. **All mutations require operator confirmation to apply — no auto-promote.** Stat-sig threshold for recommendations: p<0.05, Cohen's d>0.20, n≥20. Approve applies the candidate; reject closes it.

### 20.8 Keyboard Shortcuts

- **Shortcut overlay** (`#shortcut-overlay`): the keyboard-shortcut reference dialog (also reachable via `Cmd-/`), listing the bindings in §13.6.
- **Command palette** (`#cmd-k`, `Cmd-K`): fuzzy jump to pages, tickers, and quick actions.

### 20.13 What is *not* in Settings

These are code-versioned. Changing them requires a code change and process restart, intentionally:

- Arbitration formula
- Conviction formula
- Audit log format
- Database schemas
- Cycle order sequence (`Architecture.md §12`)
- Anti-pattern thresholds (live in `pmacs/constants.py` and CI-tested)
- The 18 failure taxonomy types (`Agents.md §15`)

---

## 21. Operator workflows

Specific tasks the operator performs, with the screen sequence. Each workflow is designed to take three clicks or fewer for routine actions, with operator confirmation friction only at the points where it earns its cost.

### 21.1 "I want to add a new ticker"

1. Cmd-K → type "add ticker" → Enter (or navigate to Universe → Add ticker button)
2. Type symbol (e.g., `RDDT`) → live admittance check fills in
3. If passes: confirm → submit → toast "RDDT added to universe; will appear in next cycle"

If the ticker fails admittance: error message specifies which check failed (e.g., "ADV $432K, below $1M threshold"). Operator can override the threshold in Settings (operator-confirmed).

### 21.2 "I want to override a SKIP and force NBIS into the pipeline"

1. Pipeline page → SKIP column → find NBIS card
2. Click "Run again now" button on the card
3. Toast confirms: "NBIS queued for next cycle"

The single-ticker re-run runs after the current cycle ends (Q9 design call). It is logged with `operator_initiated=true`. If the re-run produces a different verdict, OverrideLearning records the pattern.

### 21.3 "I want to investigate why HIMS got stopped out"

1. Pipeline page → search "HIMS" → click most recent card
2. Detail drawer opens → scroll to "Failure history"
3. See FailedAssumption nodes with taxonomy classification (e.g., `STOP_HUNTED` or `THESIS_INVALIDATED_FUNDAMENTAL`)
4. Click any FailedAssumption to see the evidence at the time, the persona outputs, and the actual price action that triggered the stop

If the operator wants to compare with similar past failures: Cmd-K → "Failures by taxonomy" → STOP_HUNTED → see all stops in that bucket across tickers.

### 21.4 "I want to review and approve a mutation candidate"

1. Dashboard → Mutation Engine card → "1 pending operator review" → click
2. Settings → Mutation Diff → pending candidates
3. Click the candidate row → expands to show:
   - Dimension and target
   - Diff (production vs candidate, side-by-side)
   - Sample size, effect size, p-value, Cohen's d
   - Direction of effect (e.g., "candidate Brier 0.27 vs production 0.31, lower is better")
   - Audit lineage (which failures triggered the candidate generation)
4. Click **Promote** → confirmation modal → Submit
5. Toast: "Mutation promoted. 30-cycle probation period active. Auto-rollback armed if regression > baseline."

### 21.5 "I want to promote PAPER → PAPER_VALIDATED"

1. Dashboard → mode badge → click → opens mode-management modal showing current gates
2. Modal shows: "PAPER → PAPER_VALIDATED requires: ≥90 PAPER cycles ✓ | ≥200 trades ✓ | Brier ≤ 0.30 (current 0.28) ✓ | Sharpe ≥ 0.0 (current 0.71) ✓ | Drawdown ≤ 15% (current 8.2%) ✓ | All gates pass ✓"
3. **Promote** button enabled → click → confirmation modal → Submit
4. Audit log records mode change. Mode badge updates.

### 21.6 "I want to engage the kill switch immediately"

1. Top bar → kill switch button → click
2. Confirmation modal: "Engage kill switch? All trading halts. Stop-loss execution continues. No confirmation required to engage."
3. Click **Engage** (no confirmation needed for engagement; it's the safer option)
4. Top bar kill switch button turns red. Toast: "Kill switch ENGAGED. To disengage: Cortex page."

To disengage:
1. Cortex page → Kill switch panel
2. Verify trigger condition resolved
3. Click **Disengage** → modal asks for a typed reason
4. Submit → switch disengages, audit logs the disengagement with operator identity

### 21.7 "I want to inspect the system before market open"

1. Open laptop → wait ~10s for launchd to start processes
2. Cortex page → check audit chain (green) + process heartbeats (all green) + disk + clock + sources
3. Dashboard → check active positions, last cycle status
4. If gap > 24h: cycle auto-initiates. Operator can watch on Agents page, or close laptop and let it run.
5. If gap < 24h: no cycle runs unless operator clicks "Run cycle now" from Dashboard

### 21.8 "I want to add a sub-sector tag to a group of tickers"

1. Universe page → group-by: exchange → see all tickers
2. Select RKLB, ASTS (checkboxes)
3. Bulk actions → "Tag sub-sector" → type "space + satellite" → Submit
4. Tags appear on rows. Group-by selector now offers "space + satellite" as a filter.

The Mutation Engine reads these tags via `persona_ticker_affinity` to learn whether specific personas perform better on specific themes.

---

## 22. The day-in-the-life narrative

This is the felt experience of running PMACS week-to-week, week 6 of PAPER mode.

**Morning.** Operator opens the MacBook. launchd starts the eight PMACS processes in dependency order. Cortex notices the last completed cycle was 27 hours ago and auto-initiates a new one.

Operator opens dashboard at `localhost:8000`. The Agents page is showing the first ticker of the cycle (NBIS) being processed. The Sankey is animating as evidence flows to the 9 personas. ETA shows 4 minutes for this ticker, ~1.5 hours for the full cycle.

NBIS comes in as STRONG_BUY (conviction 0.72). The Crucible card shows severity 0.18 — minor flaws found, not blocking. Operator clicks the Crucible card; the critique points out that NBIS's GPU-supply assumption depends on a single Q3 earnings call quote. Fair point, but the broader thesis still holds.

Operator does not need to approve the trade. PAPER mode is autonomous. The trade plan is signed and submitted to Alpaca paper. Toast: "NBIS: filled at $87.23, 3 shares, $261.69 paper position opened."

**Mid-morning.** Cycle continues. Operator goes to make coffee. When they return, the cycle has completed: 16 tickers processed, 1 STRONG_BUY (filled), 2 BUY (1 filled, 1 blocked by max-position-count — queued for next cycle), 2 HOLD on existing positions (all maintained), 11 SKIP.

Operator checks the Mutation Engine card on the Dashboard. It shows "Activates after 50 PAPER cycles (current: 42)." Not yet active — the system needs ~8 more cycles to accumulate a stable baseline. The operator notes this and moves on.

**Afternoon.** Operator's machine remains on. StopLossMonitor checks active positions every 30 minutes. One notification pops: HIMS has hit stop-loss, MARKET sell submitted, fill received at -3.2% from stop. Holding state machine transitions to STOPPED_OUT. FailedAssumption node written to KuzuDB. The Failure Diagnostic Engine classifies this as `STOP_HUNTED` — close inspection of price action shows a sharp reversal after the stop fill.

Operator clicks the toast → Pipeline page filtered to HIMS → drawer shows the stop sequence and the failure classification. Frustrating but legible. The system shows exactly what happened.

**Evening.** Operator closes the laptop. PMACS goes quiet. Catastrophe-net broker-side stops on all 3 active positions remain in place at Alpaca.

**Three days later.** Operator boots back up. Cortex notices 73-hour gap. Logs `RESUME_GAP`. Refreshes data. Runs one cycle (not catch-up cycles for the missed days). All 3 active positions get full re-evaluation. One has weakened thesis (post-earnings guidance cut); system flags as exit candidate. Operator reviews, agrees, manually triggers exit (or system can auto-exit if thesis-aging rule fires).

This is the rhythm. PMACS runs when the operator does. It is not a service running in the cloud. It is a co-pilot the operator boots.

---

## 23. The first 30 days

The onboarding journey, day by day, for an operator who just completed the wizard.

**Day 1.** Wizard completes. System enters SHADOW + PAPER. Operator runs the smoke-test cycle once during wizard setup. After wizard, operator clicks "Run cycle now" on Dashboard to kick off a real cycle. First cycle: ~2 hours (slow because no rolling-average data yet to estimate ETAs). Most personas have `historical_n=0` so all signals are bootstrap. Conviction caps at ~0.5 system-wide. Several tickers admit but most produce SKIP verdicts due to bootstrap-low-confidence + Crucible severity. Operator reviews each SKIP in Pipeline; understands the system is learning.

**Days 2-7.** Operator boots ~daily. One cycle per day. Each cycle adds 16 resolutions to the corpus. By day 7, `historical_n` per persona starts climbing into the 80-100 range. First mature signals appear (those that have crossed the n>=30 threshold). Conviction starts breaching 0.5 on some tickers. First few BUY verdicts. First few PAPER fills.

**Days 8-14.** First few catalyst resolutions land (earnings releases for any tickers on calendar). The Resolution events feed CalibrationEngine. First calibration refit triggered (auto-applied if confidence threshold met). Dashboard's Brier metric updates.

**Days 15-21.** First active holdings start hitting their first weekly re-evaluations. Some thesis re-tests pass (conviction maintained). Some find weakened thesis and exit. Operator sees the Pipeline → drawer → re-eval history showing the system's reasoning over time.

**Days 22-30.** Mutation Engine activation threshold (50 PAPER cycles, currently default) approaches. By day 30 with daily booting, ~30-35 cycles are in. Mutation Engine still dormant. Operator sees the dashboard's Mutation Engine card showing "Activates after 50 PAPER cycles (current: 32)."

By day 30, the system has accumulated:
- ~30-35 cycles
- ~150-200 holdings (active and resolved)
- ~50-80 catalyst resolutions
- ~200-400 persona outputs
- A first calibration refit on most personas
- A meaningful Sharpe and Brier rolling metric
- A handful of FailedAssumption nodes with taxonomy classifications
- A clear sense, for the operator, of which personas are sharpening fastest

Operator decides whether to keep going toward PAPER_VALIDATED (~60 more cycles to gate-pass), or pause for thesis review.

---

## 24. Backup, recovery, multi-machine

### 24.1 What gets backed up

- **Audit log** (most critical). Replicated hourly via operator-configured rsync to operator-controlled offsite. Hash chain re-verified on the destination.
- **All five databases.** Daily snapshot to operator-configured offsite location. Snapshot includes Kuzu graph, Qdrant collections, DuckDB analytics, SQLite OLTP. Snapshot is consistent (cycle-boundary-aligned).
- **Configuration.** `model_registry.json`, `risk.toml`, `crucible.toml`, `mutation.toml`, `model_hashes.toml`. Versioned in git in `config/`. Not auto-replicated; operator commits.
- **Keychain entries.** *Not* backed up by PMACS (would require exporting secrets). Operator backs up macOS Keychain themselves via macOS-native means.

### 24.2 Recovery scenarios

**Disk failure on primary Mac.** Operator restores from offsite backup to a new Mac. Re-runs wizard with `--restore <snapshot>` flag, which skips DB initialization and points to the restored DBs. Audit chain re-validated end-to-end before system enters operational mode.

**Corruption mid-cycle.** Cortex's hourly audit verification catches it. Kill switch engages. Operator runs `pmacs ops verify-and-rebuild` from CLI. The tool walks back from the last verified audit entry, replays cycle events, rebuilds DB state. If unrecoverable: restore from last good snapshot.

**Accidental destructive operation.** The wizard's `--reset` flag is destructive but operator-confirmed. Audit retention means even after a reset, the audit log can be re-imported from offsite to recover historical context.

### 24.3 Multi-machine considerations

PMACS is designed for single-machine operation. The operator can have a second Mac as a backup, but the two should not run PMACS concurrently against the same broker — duplicate orders.

Recommended pattern for the operator with two Macs:
- Primary Mac runs PMACS daily.
- Secondary Mac is bare (PMACS installed but not running).
- Audit log replicates to a NAS or cloud storage both Macs can read.
- DB snapshots replicate to the same shared destination.
- If primary fails: secondary restores from latest snapshot, runs `--restore`, takes over.

There is no "active-active" mode. The cycle lock (flock-based, see `Architecture.md §12`) prevents simultaneous cycles on the same DBs even if the operator misconfigures, but this is a safety net, not a feature.

---

## 25. Versioning and updates

PMACS follows a deliberate release cadence that respects the audit invariant.

### 25.1 Version semantics

`vMAJOR.MINOR.PATCH`:
- **PATCH:** bug fixes, no schema change, no audit format change. Safe to apply immediately.
- **MINOR:** new features, backward-compatible schema migrations, no audit format change. Migration runs on next startup; operator confirms.
- **MAJOR:** breaking changes (audit format, schema-incompatible). Migration is one-way and requires an operator-confirmed explicit "migrate" command.

### 25.2 Update flow

PMACS does not auto-update. The operator runs `pmacs update --check` from CLI; system reports available version, summarizes changes (from a version-pinned `CHANGELOG.md`), and offers to update. Update is a `git pull` + dependency reconciliation + DB migration.

Major-version updates require:
- Audit log archive of the current version (the chain becomes immutable for the prior version)
- Genesis entry of a new audit chain for the new version (linked to prior chain head via cross-reference event)
- operator confirmation
- A smoke-test cycle on synthetic fixtures before the system enters operational mode in the new version

### 25.3 Model file updates

When upstream releases a new GGUF (e.g., Qwen3.6-35B-A3B → Qwen3.7), the operator manually updates `config/model_hashes.toml` with the new SHA256 and triggers a model swap via Settings → AI Provider. The system performs a smoke-test inference before accepting the new model. If the smoke test fails, the swap is rolled back.

---

## 26. Out of scope (v1)

These are deliberate non-features. Each is excluded for a reason. Adding any requires an explicit ADR in `Architecture.md`.

- **Short positions, options, futures, crypto.** Risk model and Crucible are calibrated for long-equity. Adding these without recalibration is unsafe.
- **Brokers other than Alpaca paper (v1) and IBKR earmarked (v2 live).** Each broker integration carries credential, reconciliation, and corporate-action surface.
- **Telemetry, external observation of operator decisions.** Structurally excluded regardless of LLM backend. (Cloud LLM calls are *not* excluded — they are an operator-selectable backend; see §4.)
- **Multi-user, multi-operator, team collaboration.** Single-operator simplifies the trust contract dramatically.
- **Tax reporting (Modelo 3, Anexo J for Portugal).** Tax is the operator's responsibility offline.
- **Mobile app.** Dashboard is desktop-web on `localhost`. Mobile would require remote access, which conflicts with the localhost-only UI.
- **SMS, email, or push alerting.** Notifications are in-UI. Future versions may add macOS native notifications via `osascript`.
- **Backtesting against historical LLM outputs.** Epistemically invalid (the model's training data contains the test period's future).
- **Self-play, agent-vs-agent training.** Deferred to v2.
- **Automatic universe expansion via screener.** Operator-curated only — see §8.4.
- **LLM-generated mutation candidates.** v1 mutation candidates are deterministic-rule-generated. LLM-as-mutation-author is deferred to v2 to avoid an unconstrained self-modification loop.
- **Live trading via Alpaca.** Lisbon residency. IBKR is the live path.

---

## 27. Glossary

| Term | Definition |
|---|---|
| **Arbitration** | Deterministic combination of multiple persona signals into a single probability distribution. |
| **Bootstrap haircut** | Position-size reduction applied when fewer than N mature sources contributed to a decision. |
| **Catalyst** | A discrete event expected to resolve part of a thesis (earnings, FDA decision, product launch). |
| **Catastrophe-net** | Broker-side stop placed at entry, ~15% below entry. Failsafe if PMACS is offline. |
| **Conviction** | Operator-facing scalar mapping probability + Crucible severity + EV multiple to a verdict tier. |
| **Crucible** | Adversarial persona that attacks the combined Arbitrated output. |
| **CPS budget** | Crucible Per-Signal time budget (90s default; 2 rewrite cycles max; defaults to NO_TRADE on hit). |
| **Cycle** | One full pipeline run across the universe. Boot-triggered. |
| **Episodic memory** | Rolling 5/30/90-day aggregates. Lives in DuckDB. Injected into persona prompts as context. |
| **Evidence** | A typed packet of source data (filing, quote, news article) with provenance and timestamp. |
| **FDE** | Failure Diagnostic Engine. Classifies terminal-state holdings into 18 taxonomy types. |
| **Flywheel** | The self-improving feedback loop: cycles → resolutions → calibration → mutations → better cycles. |
| **GBNF** | GGML BNF — grammar format for constraining llama-server output structure. |
| **Holding** | A position record. Has state (CANDIDATE → ACTIVE → terminal). |
| **Immutable memory** | Hash-chained audit log. Forever-retention. |
| **Kill switch** | System-wide trade halt. operator confirmation to disengage. |
| **Limited-history** | Ticker with fewer than 90 trading days of OHLCV. Stacked conviction haircut applied. |
| **Mode ladder** | The promotion path SHADOW + PAPER → PAPER_VALIDATED → LIVE_*. |
| **Mutation Engine** | Active-flywheel sub-system that proposes and validates variants of system components. |
| **PAPER** | Mode where simulated trades execute against Alpaca paper API with $5K. |
| **Persona** | An LLM with a specific system prompt, temperature, and structured-output contract. Same base model. |
| **R/R** | Reward-to-risk ratio. (Target gain divided by max acceptable loss). |
| **Semantic memory** | Full-history graph + vector + analytics stores (Kuzu, Qdrant, DuckDB). |
| **SHADOW** | Audit-only mode. Captures signals and math gate output, no fake-trades. Always concurrent with PAPER. |
| **Source maturity** | A source's `historical_n` ≥ 30 (default). Below = immature, contributes with bootstrap haircut. |
| **Thesis-bound** | Holding philosophy: position held while thesis is valid, not for fixed duration. |
| **TradePlan** | Ed25519-signed instruction sent from `pmacs-nervous` to `pmacs-execution`. |
| **Verdict tier** | Operator-facing classification: STRONG_BUY / BUY / HOLD / SKIP. |
| **Working memory** | Per-cycle scratch state in SQLite. |

---

## 28. Connection to companion files

This section is the explicit map between this file and the other three. When you finish reading Source.md, here is what each companion file gives you and where to enter it.

### 28.1 → Architecture.md

`Architecture.md` is *how* PMACS is built. Read it when:

- You need to know which process owns which responsibility (§2, §4).
- You're adding a new file, function, or schema and need to know where it lives (§3).
- You're touching storage and need the schema (§8).
- You're emitting an audit event or a debug event and need the format (§5).
- You're modifying the cycle pipeline and need the canonical sequence (§12).
- You're adding a kill-switch trigger or modifying disengagement (§13).
- You're working with the Mutation Engine process and need the lifecycle (§10).
- You're adding a Pydantic model and need the v2 conventions (§1.1).

The cross-reference index in §0 of `Architecture.md` mirrors §0 here.

### 28.2 → Agents.md

`Agents.md` is *the LLM contract.* Read it when:

- You're modifying any persona's system prompt (§4-§13).
- You're authoring a new GBNF grammar or JSON Schema (§4-§13 per persona).
- You're working on the Crucible's adversarial loop (§16).
- You're touching the Failure Diagnostic Engine taxonomy (§15).
- You're working on Mutation Engine candidate generation logic (§17).
- You're injecting episodic context into prompts (§18).
- You're adding a sanity validator (§4-§13).

`Agents.md` reads at the level of "what does the LLM see, what does it return, and what guarantees does the validator add."

### 28.3 → Phases.md

`Phases.md` is *what gets built when, and what counts as done.* Read it when:

- You're starting a new ticket and need to know which build phase it belongs to (§2).
- You're considering a mode promotion and need the gate numbers (§3).
- You're tempted to skip ahead — `Phases.md` enforces the dependency order.
- You're writing a test and need the per-phase exit-test criteria (§2.x).

`Phases.md` is the build sequence with explicit dependencies between phases. Each phase has a numbered exit test that must pass before the next phase begins.

### 28.4 What this file does NOT contain

To preserve the integrity of the four-file split:

- **No code.** Source.md does not contain Python. Code lives in `Architecture.md` (engines, schemas) and `Agents.md` (prompts, grammars).
- **No SQL or schema definitions.** Live in `Architecture.md §8`.
- **No exact prompt text.** Lives in `Agents.md §4-§13`.
- **No build-order enforcement.** Lives in `Phases.md §2`.

If you find yourself wanting to put one of these in Source.md, that is the signal it belongs in another file.

### 28.5 The four-file invariant

If a concept appears in Source.md as a behavior the operator can rely on, it must have a corresponding implementation in `Architecture.md` (often more than one section) and may have prompt/contract details in `Agents.md`. If it has a build dependency, it appears in `Phases.md`. Concepts must not exist in only one file when they describe operator-facing behavior.

This invariant is checked by `ops/spec_consistency.py` — a CI script that scans for cross-file references and ensures every Source.md operator-promise has at least one Architecture.md implementation pointer.

---

*End of Source.md. v1. Pair with Architecture.md, Agents.md, Phases.md.*
