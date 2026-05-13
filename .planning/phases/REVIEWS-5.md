# REVIEWS.md — GSD Phase 5 (PMACS Phases 9-10)

**Review Date:** 2026-05-09
**Reviewers:** 1 (Claude Sonnet — independent session)

---

## Reviewer 1: Claude Sonnet (independent session)

### Overall Assessment: **REQUEST_CHANGES**

The Phase 5 plan has good structural alignment with the spec but contains several critical gaps, missing dependencies, and incomplete exit tests that must be addressed before execution. The dashboard section is particularly underspecified compared to the 150+ lines of detailed requirements in Source.md §13-20.

---

### Critical Issues (must fix before execution)

#### C1. Catastrophe-net cancellation missing from Phase 9 plan
**Spec:** Architecture.md §11.5 — ANY primary exit path MUST cancel broker-side catastrophe-net stop BEFORE submitting SELL. If cancel fails → engage kill switch immediately.
**Plan gap:** Plan mentions stop-loss execution but does NOT include catastrophe-net cancellation logic or kill-switch engagement on cancel failure. Safety-critical omission.

#### C2. Missing OpportunityCostEngine implementation details
**Spec:** Architecture.md §12 step 18 — per-holding iteration, hold vs. EXIT_OPPORTUNITY_COST decision.
**Plan gap:** Lists `pmacs/engines/opportunity_cost.py` but doesn't specify per-holding iteration logic or exit paths.

#### C3. Dashboard TOTP modal implementation incomplete
**Spec:** Source.md §13.3 requires reusable TOTP modal for ALL TOTP-gated actions across the app.
**Plan gap:** "TOTP modal" is a single bullet. Must be a reusable component for: Settings writes, Universe add ticker, Pipeline force exit, Cortex kill switch disengage, Mutation Engine promotions.

#### C4. No SSE endpoint specification
**Spec:** Source.md §13.1 and §15.7 require SSE from Nervous `/events`.
**Plan gap:** Lists `pmacs/web/sse_client.py` but doesn't mention server-side SSE endpoint in Nervous.

#### C5. Missing visual identity tokens implementation
**Spec:** Source.md §13.1 specifies Notion-like aesthetic with specific Tailwind tokens.
**Plan gap:** "Visual identity tokens from Source.md §13.1" is a reference, not an implementation task. Needs explicit Tailwind config task.

---

### Significant Gaps (should address)

#### S1. Stop-loss polling interval unspecified
**Spec:** Nervous polls SQLite every 10s during RTH. Plan doesn't specify this interval.

#### S2. Trailing stop state machine transitions wrong
**Spec:** Trailing stop breaches → `EXIT_TRAILING_STOP` (distinct from `STOPPED_OUT`).
**Plan gap:** Exit test mentions trailing math but doesn't verify distinct state transition or FDE classification.

#### S3. Cmd-K command palette underspecified
**Spec:** Source.md §13.3 lists 8 specific shortcuts (Cmd-1 through Cmd-7, Cmd-Shift-K, Cmd-K, Cmd-T).
**Plan gap:** One bullet — should specify all shortcuts and search/navigation interface.

#### S4. Agents page Sankey diagram implementation missing
**Spec:** Source.md §15.5 — D3-based Sankey with evidence → personas → arbitration flow, animated transitions, hover interactions.
**Plan gap:** "D3 for Sankey" mentioned but not specific requirements.

#### S5. Pipeline page queue management incomplete
**Spec:** Source.md §16.5 — P1/P2/P3/P4 multi-band priority queue with drag-and-drop, pin/unpin, saved schemes.
**Plan gap:** "Reorder queue from Pipeline right rail" doesn't capture full P1-P4 system.

#### S6. Missing cycle resume protocol
**Spec:** Architecture.md §12.3 — cycle resume using `op_idempotency` table, checkpoint recovery.
**Plan gap:** Neither phase mentions cycle resume for crash recovery during stop-loss monitoring.

#### S7. Dashboard page components severely underspecified
**Spec:** Source.md §14 — 8 major components per page (portfolio summary, mode status, risk metrics, positions table, decisions feed, system health, mutation engine, empty states).
**Plan gap:** "All 7 pages render" is one exit test item — needs 7 separate items with specific component verification.

---

### Minor Observations

- M1: Thesis aging exit path unclear (back to ACTIVE if validated, EXIT_* if invalidated)
- M2: Weekly re-eval cadence tracking not specified (how weekly timer is persisted)
- M3: Dashboard responsiveness requirements omitted (1024px minimum, 3x3 grid below 1280px)
- M4: Debug page "Copy for Claude Code" button missing (Source.md §19.2)
- M5: Settings mutation candidate display incomplete (Source.md §20.8)

---

### Spec Coverage Matrix

| Spec Requirement | Covered? | Gap |
|---|---|---|
| **Phase 9: StopLossMonitor** | | |
| Two-layer architecture (PMACS + catastrophe-net) | Partial | Catastrophe-net cancel logic missing |
| 30-min RTH monitoring | Yes | |
| SQLite stop_events table | Yes | |
| Nervous 10s polling for PENDING triggers | No | Polling interval not specified |
| Gap-down MARKET_ON_OPEN handling | Yes | |
| Trailing stop arm at 1.5R | Yes | |
| Trailing stop ratchet (up only) | Yes | |
| Trailing stop → EXIT_TRAILING_STOP state | No | Wrong state in exit test |
| Weekly re-evaluation (step 14) | Yes | Weekly cadence tracking unspecified |
| Thesis aging 90-day review (step 15) | Yes | Exit path unclear |
| Opportunity cost per-holding (step 18) | Partial | Per-holding iteration not explicit |
| Catastrophe-net cancellation before exit | No | Safety-critical omission |
| **Phase 10: Dashboard** | | |
| FastAPI + SSE subscription | Yes | Server-side SSE endpoint missing |
| 7 pages with routes + templates | Yes | |
| Visual identity tokens (Tailwind config) | No | Implementation task missing |
| Cmd-K command palette (all shortcuts) | Partial | All shortcuts not specified |
| TOTP modal (reusable component) | Partial | Not explicitly reusable |
| Toast notifications | Yes | |
| Empty + loading states | Yes | |
| WCAG AA accessibility | No | Not mentioned |
| Dashboard page — 8 components | No | Components not listed |
| Agents page — persona cards + viz | Partial | Severely underspecified |
| Pipeline page — kanban + P1-P4 queue | No | Only "reorder" mentioned |
| Universe page — badges + add modal | Partial | Add ticker modal not explicit |
| Cortex page — 6-panel grid | No | |
| Debug page — Copy for Claude Code | No | |
| Settings page — 12 sections | No | Only "renders all sections" |

---

### Recommended Changes (ordered by priority)

#### Phase 9 (StopLossMonitor):

1. **Add catastrophe-net cancellation task** — Add to "What gets built": `pmacs/engines/catastrophe_net.py` implementing cancel-before-exit logic with kill-switch fallback. Add exit test for cancel + kill-switch on failure.
2. **Add Nervous polling interval** — Add to "What gets built": "Nervous SQLite poller every 10s during RTH for PENDING stop events"
3. **Fix trailing stop state transition** — Update exit test: verify EXIT_TRAILING_STOP (not STOPPED_OUT) + FDE classification
4. **Specify opportunity cost per-holding** — Add to "What gets built": per-holding iteration, hold vs EXIT_OPPORTUNITY_COST decision with audit
5. **Add cycle resume protocol** — Add `op_idempotency` table and checkpoint recovery for crash resilience

#### Phase 10 (Dashboard):

6. **Replace single "All 7 pages render" exit test with page-by-page verification** — 7 specific tests with component-level detail
7. **Add explicit visual identity implementation** — Tailwind config task with all tokens from Source.md §13.1
8. **Add Cmd-K specification** — All shortcuts from Source.md §13.6
9. **Add Nervous SSE endpoint** — Server-side `/events` endpoint for real-time dashboard updates
10. **Add TOTP modal reusability** — Single component used across all TOTP-gated actions
11. **Add accessibility requirements** — WCAG AA, prefers-reduced-motion, keyboard nav, 1024px minimum

#### Ordering:

- Phase 9 correctly depends on Phase 8 ✅
- Phase 10's dependency on Phase 9 is questionable — dashboard can develop without stop-loss events; consider making optional for early dev

#### Risk Assessment (highest-risk items):

1. Catastrophe-net cancellation failure → duplicate fills
2. Stop-loss during market close → MARKET_ON_OPEN correctness
3. SSE implementation → UX-critical, connection failure handling
4. TOTP modal consistency → security-critical
5. State proliferation → EXIT_TRAILING_STOP vs STOPPED_OUT vs EXIT_OPPORTUNITY_COST confusion

---

## Summary

| | Critical | Significant | Minor |
|---|---|---|---|
| Claude Sonnet | 5 | 7 | 5 |

**Top 3 actions before execution:**
1. Add catastrophe-net cancellation logic (safety-critical)
2. Expand dashboard exit tests to page-by-page with component verification
3. Add Nervous SSE endpoint + visual identity Tailwind config as explicit tasks
