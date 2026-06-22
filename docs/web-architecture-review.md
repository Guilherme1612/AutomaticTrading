# PMACS Web — Architecture & Information-Flow Review

> Scope: **architecture, information flow, and interaction surfaces only.** Visual/CSS styling is explicitly out of scope. No code is changed in this review — this is a design proposal.
>
> Grounded in `spec/Source.md §13–§21`, `spec/Architecture.md`, and the current implementation under `pmacs/web/`. Every finding cites the spec section it diverges from.

---

## 0. What the site is, structurally

**Process model (single combined server).** `pmacs/web/app.py` mounts one FastAPI app on `:8000` that serves the dashboard UI, the write API (ex-nervous), SSE, static assets, and health. Routers live in `pmacs/web/routes/` (dashboard, agents, pipeline, universe, cortex, debug, settings, ticker_data, memo, wizard). Good — the old "nervous + dashboard as two processes both on :8000" plist bug is resolved.

**Navigation.** Left sidebar (`base.html`) with 7 pages — Dashboard `/`, Agents `/agents`, Pipeline `/pipeline`, Universe `/universe`, Cortex `/cortex`, Debug `/debug`, Settings `/settings` — plus two **unlisted** drill-down pages: `/ticker/{ticker}` (fundamentals) and `/memo/{ticker}` (the memo). Wizard `/wizard` is a separate onboarding flow. Number keys 1–7 navigate; Cmd-K palette searches; HTMX does partial `#main-content` swaps with `hx-push-url`.

**Live data path.** SSE: `pmacs-nervous` publishes to `/events`; `app.js` opens an `EventSource`, dispatches each frame by its `stream` field to registered handlers, and reconnects with `Last-Event-ID` resume. A `NOTIFICATION_POLICY` map (Source.md §13.5) routes event types → toast / modal / silent + sound. Separately, a Python `SSEClient` (`pmacs/web/sse_client.py`) subscribes server-side to the same stream for backend consumers.

**Operator-action path.** Write actions are POST routes (queue add/remove/pin/promote/reorder/rerun, cycle start/orchestrator/solo/smoke, force-exit, universe add/remove/bulk, kill-switch engage/disengage, settings notifications/inference/cost, mutation approve). A generic `blocking-modal` in `base.html` + `app.js` provides the typed-confirmation friction layer (Source.md §13.2).

**State components.** `components/{card,statblock,empty_state,error_state,loading_state,ticker_chip}.html` — reusable shells for the §13.4 state philosophy.

---

## 1. Architectural strengths (keep these)

- **Consolidated single server** — one app, one port, one audit-emitting path. Removes the plist/topology drift.
- **SSE with resume** — `Last-Event-ID` replay, exponential reconnect, capped retries, status indicator. Solid.
- **Notification policy as data** — `NOTIFICATION_POLICY` table maps events→surface+sound, with `NON_DISABLEABLE_EVENTS` for kill-switch + audit-chain. This is exactly Source.md §13.5 done right.
- **HTMX partial navigation** — sidebar links swap `#main-content` only, preserving chrome and SSE connection. Fast, no full reload.
- **Top-bar chrome matches spec §13.2** — PMACS wordmark + mode badge + `cycle-indicator` (idle/running with ticker) + `health-strip` + kill-switch button + theme toggle. Verified present.
- **Cortex page** matches §18 well — 6-panel grid (audit chain, cross-DB, processes, disk/clock/network gauges, kill switch, model integrity) with a summary strip.
- **Agents page** is rich and close to §15 — persona grid, communication-layer tabs (Process/Signals/Conviction), decision-summary rail, session stats, live activity feed, Sankey data route.
- **Confirmation primitive exists** — `blocking-modal` is non-Esc-dismissable and used for kill-switch. Right pattern.

---

## 2. Findings — what I would change (architecture/flow), ranked

### A. Ticker drill-down is fragmented — unify into one ticker workspace  *(highest impact)*

**Spec (§16.6 "Single-ticker detail drawer"):** one slide-in drawer per ticker carrying header + final memo + **per-persona memos accordion** + **historical decisions (last 10 cycles)** + **position lineage** (entry, re-evals, stop arming, trailing-stop, MtM history) + **failure history** (FailedAssumption nodes from KuzuDB with taxonomy) + re-run button.

**Implementation:** two separate full pages with no overlap and large missing pieces:
- `/ticker/{ticker}` — fundamentals only (valuation, FCF yield, SaaS KPIs, analyst consensus, technical, raw).
- `/memo/{ticker}` — the final memo (business model, financials, thesis, bull/bear debate, reverse-DCF, scenario price, falsification triggers, catalyst calendar).
- **Per-persona memos, position lineage, and failure history are surfaced nowhere.** Grep for `drawer|lineage|failure|per-persona|historical decision` across `pipeline.html`, `ticker_detail.html`, `memo.html` returns nothing.

**Why it matters (information flow):** the operator's core loop is "one name → everything I know about it." Today that requires two pages and still omits the two most decision-relevant views: *why did each persona say what it said* (per-persona memos) and *what has happened to this position over time* (lineage + failure taxonomy). The KuzuDB graph and FailedAssumption data exist but have no operator surface.

**Proposed design:**
- One **ticker workspace** at `/ticker/{ticker}` with a sticky context header (ticker, company, verdict chip, conviction, entry/price, current re-eval status, days held, next re-eval) and **anchored tabs**:
  `Memo · Personas · Lineage · Failures · Fundamentals · Debate`.
- `Personas` = the §16.6 accordion (one collapsible row per persona, with that persona's last-10 outputs and rolling Brier from `persona_ticker_affinity` — the same data the Agents persona-card drawer shows in §15.4, now reachable from the ticker context).
- `Lineage` = the holding event timeline from SQLite/KuzuDB (entry → re-evals → stop arming → trailing-stop → MtM), and the §16.6 "historical decisions (last 10 cycles)" table.
- `Failures` = FailedAssumption nodes with the 18-outcome / 5-reasoning-flaw taxonomy, shown when previously stopped out.
- `Fundamentals` = the existing `/ticker` content. `Debate` = the bull/bear + reverse-DCF + scenario-price + falsification sections currently on the memo.
- `/memo/{ticker}` becomes a redirect or a deep-link anchor (`/ticker/{ticker}#memo`) so existing links keep working.
- **Every ticker chip/link across the app targets this one workspace.** Today Dashboard and Agents link to `/memo/{ticker}` while Pipeline cards don't open a drawer at all — inconsistent destinations (see H).

**Spec authority:** Source.md §16.6 (drawer contents), §15.4 (per-persona track record), §7.3 (re-eval cadence), Agents.md (FailedAssumption taxonomy).

---

### B. Dashboard is only half-live — extend SSE to patch regions  *(high)*

**Spec (§14):** the dashboard is the operator's "home base" — positions, recent decisions, risk metrics, system health, and mutation summary should reflect the latest cycle without a manual reload.

**Implementation:** SSE live-updates **only** the portfolio sparkline (`sparkline_update` stream) and the toast/notification layer. The active-positions table, risk-metrics row, system-health card, mutation-summary card, and recent-decisions feed refresh only on a full page load or HTMX navigation. Verified: the only `data-sse`-style live targets in `dashboard.html` are `portfolio-value-display`, `portfolio-sparkline-container`, and `decisions-feed`; `app.js` SSE handlers almost entirely call `handleNotification(...)` (toasts), not region repatches.

**Why it matters:** a "live dashboard" that silently goes stale undercuts the whole point of SSE. After a cycle completes, the operator sees a toast but the positions/decisions/health panels still show the pre-cycle state until they manually navigate.

**Proposed design:** use **SSE-triggered HTMX partials** instead of bespoke JS per region:
- Wrap each dashboard region (positions, risk, health, mutation, decisions) in a container with `hx-get="/api/dashboard/partials/<region>"` + `hx-trigger="sse:cycle_complete, sse:trade_filled_paper, sse:stop_loss_triggered, sse:audit_chain_failure"`.
- The server emits those SSE events already (notification policy proves it); add small partial-render endpoints that return just that region's HTML.
- No new JS per region, no polling, no full reload. Same pattern the sparkline already uses, generalized.
- Guard with the same `is defined` / context-var safety noted in memory (`jinja_context_var_desync`) so partial renders don't 500 a running uvicorn.

**Spec authority:** Source.md §14.4–§14.7; Architecture.md §4.4 (dashboard data via SSE, not DB polling).

---

### C. Settings is missing most operator-action surfaces  *(high)*

**Spec (§20):** 13 subsections — General, Brokers, Inference, Universe, Risk, Crucible, Mutation Engine, Agent Personas, Queue, Audit & Debug, Operator, "what is NOT in Settings", plus notification levels.

**Implementation:** `settings.html` has only — Appearance, AI Provider, Budget, Notifications, Reset Progress, Mutations. (Cost lives partly in a separate `cost_settings.html`/`cost_widget.html`.)

**Missing operator-confirmed decision surfaces** that the spec assigns to Settings and the decision-rights matrix (§6) marks "operator-confirmed":
- **Risk thresholds** (§20.6 / §6: risk thresholds operator-confirmed) — `risk.toml` has no UI.
- **Crucible config** (§20.7) — `crucible.toml` (CPS budget, 90s/2-cycles) has no UI.
- **Agent Personas enable/disable** (§20.9 / §6: persona enable/disable operator-confirmed) — no UI.
- **Broker credentials** (§20.3 / §6: API credentials operator-confirmed) — no UI.
- **Per-trade approval toggle** (§6, autonomy vs manual-approval mode) — no UI toggle.
- **Audit replication target** (§6 operator-confirmed) — no UI.
- **Queue defaults / priority schemes** (§20.10) — queue is managed on Pipeline, but saved schemes' admin has no Settings home.
- **Operator profile / audit & debug preferences** (§20.11/§20.12).

**Why it matters:** these are the "money or trust" decisions §6 says the operator owns. With no UI surface, the only way to change them is editing TOML by hand — which bypasses the operator-confirmation friction layer the spec mandates, and means there's no audit trail for the change. This is a decision-rights/auditability gap, not just a missing-form gap.

**Proposed design:**
- Bring Settings up to the §20 subsection list. Each operator-confirmed change routes through the existing `blocking-modal` typed-confirm (so a risk-threshold change is logged like a kill-switch disengagement).
- Collapse the separate `cost_settings` into Settings → Budget so there's one config surface.
- For items that already have a contextual home (queue on Pipeline, universe on Universe, kill-switch on Cortex), Settings links to them rather than duplicating, but still lists them in the §20 index so the operator can find every tunable from one place.

**Spec authority:** Source.md §20.1–§20.13, §6 decision-rights matrix.

---

### D. Operator-confirmation friction is uneven — audit every money/trust POST  *(high)*

**Spec (§13.2 modal pattern + §6):** destructive/operator-confirmed actions carry action description, consequences, "Type SYMBOL to confirm," confirm field, cancel. Kill-switch and audit-chain-failure modals are non-disableable.

**Implementation:** the `blocking-modal` primitive exists and is wired to kill-switch disengage (cortex). But:
- **Force-exit is not surfaced as a button at all.** Spec §16.4 wants a "Force exit — for active positions only, operator-confirmed" action on each pipeline card. The route `/api/pipeline/force-exit` exists, but no visible button was found in `pipeline.html`. A money/trust action that the spec puts on the operator has no entry point.
- It's unclear whether **universe-remove-with-active-position** (§6: "operator-confirmed, force-exit ack") and **mutation approval** (§6 operator-confirmed) route through the typed-confirm modal vs. a plain confirm. These are exactly the actions the friction layer is for.

**Proposed design:**
- Add an explicit **Force exit** button to active-position cards (Pipeline §16.4 + Dashboard positions), wired to `/api/pipeline/force-exit` *through* `blocking-modal` with "Type {SYMBOL} to confirm force-exit."
- Add a confirmation audit in the review: grep every POST route against §6's operator-confirmed rows and assert each has a typed-confirm gate. Make the absence of a gate a failing check (this is enforceable in CI like the §16 anti-patterns).

**Spec authority:** Source.md §13.2, §6, §16.4.

---

### E. Wave-2 memo data-flow is split — the memo template shows sections that arrive empty  *(high, known)*

**Known from project memory (`dual_memo_paths_gap`):** `orchestrator.py` runs wave-2 (bull/bear advocates, cross-persona auditor, reverse-DCF, scenario-price) but does **not** persist memos; `pipeline.py` persists memos but has **no** wave-2 data. The memo *template* already renders all the wave-2 sections (Bull/Bear Debate, Reverse-DCF Anchor, Scenario-Weighted Expected Price, What Would Change My Mind, Catalyst Calendar — verified in `memo.html`). So live, those sections can render empty.

**Why it matters (information flow):** the most decision-relevant content PMACS produces — the adversarial debate and the falsification triggers — is the part most likely to be blank when the operator opens a memo. That's the worst place for a silent data-flow gap.

**Proposed design:** unify the two memo paths so wave-2 output is persisted into the same memo record pipeline writes. This is a plumbing fix, but it's an *information-flow* architecture issue, so it belongs in a design review: pick one owner for memo persistence (pipeline.py) and have the orchestrator hand wave-2 results to it, or have the orchestrator persist and pipeline read. Until unified, the memo page should **fail closed with a labeled "wave-2 not yet computed" state** rather than rendering empty sections — never let a blank section look like "the system found nothing."

**Spec authority:** Source.md §16.9, Agents.md §11b/§11c (advocates + auditor), Architecture.md §9.4b.

---

### F. State-design completeness (§13.4) is uneven — enforce one wrapper  *(medium)*

**Spec §13.4:**
- **Loading:** no spinners; show *what* is loading + ETA from rolling averages; cancel if ETA > 30s; skeleton same shape as content.
- **Error:** error code + one-line description + "What this means" expander + "What to try" + "Copy for Claude Code" button + spec link. No stack traces unless raw mode.
- **Empty:** meaningful per-page empty state, never generic "No data."

**Implementation:** `empty_state`, `error_state`, `loading_state` components exist and `data_layer.build_error_context` populates errors. But coverage is per-page ad-hoc, not enforced. Async regions that fetch client-side (e.g., Agents sankey fetch, ticker fundamentals lazy-fetch) likely show a plain "loading…" or a reload button (`ticker_detail.html` has a "reload now" link) rather than a skeleton+ETA per §13.4.

**Proposed design:**
- Wrap **every** data region (server- and client-rendered) in a single `<StateRegion>` contract: `loading` (skeleton + "Fetching X for N tickers, ~ETA" + cancel) → `ready` → `empty` (page-specific message + CTA) → `error` (code + what-this-means + what-to-try + copy-for-Claude + spec link).
- The `loading_state` should accept a "what" and an ETA so it's never a bare spinner. Add a CI lint that flags `Spinner`/bare "Loading…" strings outside the shared component.
- The "Copy for Claude Code" prompt is a genuinely good PMACS-specific touch (Source.md §13.4) — make sure every error surface includes it, not just some.

**Spec authority:** Source.md §13.4.

---

### G. Agents communication-layer tab names diverge from spec  *(low)*

**Spec §15.5:** toggle is **Process / Network / Math** (Network = Sankey of evidence→personas→arbitrated; Math = arbitration + conviction formulas computed step-by-step).

**Implementation:** tabs are **Process / Signals / Conviction**. The *intent* is similar but the spec's "Math" view (the full formula worked out line-by-line, the §15.6 decision-summary as an auditable derivation) may have been replaced by "Conviction" (a formula *breakdown*). If the step-by-step arithmetic transparency of "Math" was dropped, that's a real loss of auditability — the §15 design intent is "answer *why* without reading code."

**Proposed design:** either (a) restore a "Math" tab that shows the arbitration and conviction formulas computed line-by-line from the real numbers, or (b) update spec §15.5 to rename to Signals/Conviction and explicitly fold the Math content into the Conviction tab. Pick one; don't let spec and implementation drift (there's prior drift history — `spec_drift_jun16`). The Sankey "Network" view already exists (`/agents/sankey-data`, `sankey.js`) so the substance is there; it's the labeling/derivation-view that needs reconciling.

**Spec authority:** Source.md §15.5.

---

### H. Ticker click destinations are inconsistent  *(low, follows A)*

Today: Dashboard positions link to `/memo/{ticker}`; Agents results link to `/memo/{ticker}`; Universe rows don't link to a drill-down; Pipeline cards don't open the §16.6 drawer. Spec §13.3 says `TickerChip` click → Pipeline filtered to ticker; §16.6 says card click → drawer.

**Proposed design:** once the unified ticker workspace (A) exists, make **every** ticker reference across all pages link to `/ticker/{ticker}` (or `/ticker/{ticker}#tab` when the link is context-specific — e.g., a "stopped out" link goes to `#failures`). One destination, context-aware anchor. Delete the `/memo` vs `/ticker` split from the operator's mental model.

**Spec authority:** Source.md §13.3, §16.4, §16.6.

---

### I. Cost surface is split between two templates  *(low)*

`cost_widget.html` (dashboard) and `cost_settings.html` (separate page/route) duplicate cost UI. Spec puts cost under Settings → General/Budget. Fold `cost_settings` into Settings → Budget so the dashboard widget is a read-only summary of the same data, and editing happens in one place.

---

## 3. Suggested priority / sequencing

1. **A + H** — unify the ticker workspace. Highest information-flow value; unblocks lineage/failure/per-persona surfaces that have no home today.
2. **E** — fix the wave-2 memo data-flow split so the unified workspace's Debate/Reverse-DCF/Scenario sections actually render.
3. **B** — SSE-driven dashboard region patches (generalize the sparkline pattern).
4. **D** — surface Force-exit + audit confirmation friction across every §6 operator-confirmed POST.
5. **C** — bring Settings up to §20's 13 subsections; collapse cost_settings in.
6. **F** — enforce the shared `StateRegion` (loading/empty/error) wrapper with a lint.
7. **G + I** — reconcile tab naming; fold cost template.

## 4. Non-goals (explicitly out of scope)

- CSS, color, typography, animation, visual identity (Source.md §13.1) — not reviewed.
- Backend engine correctness, arbitration math, kill-switch logic — assumed correct per spec.
- Wizard onboarding flow — structurally separate, not part of the day-to-day operator loop.

## 5. Spec authority index

| Finding | Spec section |
|---|---|
| A unified ticker workspace | §16.6, §15.4, §7.3; Agents.md (taxonomy) |
| B live dashboard | §14.4–§14.7; Architecture.md §4.4 |
| C Settings completeness | §20.1–§20.13; §6 |
| D confirmation friction | §13.2, §6, §16.4 |
| E wave-2 memo flow | §16.9; Agents.md §11b/§11c; Architecture.md §9.4b |
| F state design | §13.4 |
| G comm-layer tabs | §15.5 |
| H ticker destinations | §13.3, §16.4, §16.6 |
| I cost surface | §20.2/§20 General/Budget |
