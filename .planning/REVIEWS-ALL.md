# PMACS Cross-Phase Review — All Phases

**Reviewer:** Claude Code (sole available CLI)
**Date:** 2026-05-26
**Scope:** 11 GSD phases (Phase 1–16), 11 PLAN.md files, spec compliance, implementation quality
**Per-phase details:** See `CROSS-REVIEW.md` in each phase directory

---

## Executive Summary

| Phase | PMACS Scope | Score | Verdict |
|-------|-------------|-------|---------|
| Phase 1 | Foundation + Data | **4/5** | Solid, but state machine diverges from spec in 7 places |
| Phase 2 | Inference + Processes | **4/5** | Strong, ROLLING_5D_LOSS stub + disengage gap |
| Phase 5 | Monitoring + Dashboard | **4/5** | Engines excellent, SSE proxy is a stub |
| Phase 6 | Calibration + FDE | **3/5** plan / **5/5** impl | Plan stale — all gaps already closed |
| Phase 8 | Polish (LIVE-READY) | **3.5/5** | Anti-patterns enforced, empirical validation deferred |
| Phase 9 | Core Orchestration | **3.5/5** | Step ordering violates spec, crash resume untested |
| Phase 10 | Broker Integration + Ops | **3/5** | Wizard spec drift (12 vs 11 steps), embedding mismatch |
| Phase 11 | Polish + Operator Exp | **3/5** | 5 dashboard spec gaps, axe-core CI missing |
| Phase 12 | Spec Gap Closure | **3.5/5** | Impl strong (4/5), plan severely stale (2/5) |
| Phase 13 | UI Polish | **3.5/5** | Focus traps partial, exit tests unverified |
| Phase 16 | Token-Cost Accounting | **3/5** | 2 critical bugs — monthly budget never fires |

**Weighted average: 3.5/5**

---

## Critical Issues (Must Fix Before Live)

### CR-01: Phase 16 — Monthly Budget Kill Switch Never Fires
- **File:** `pmacs/cortex/kill_switch.py:638`
- **Bug:** `_get_period_total(conn, "month")` passes `"month"` but the SQL expects `"this_month"`. Monthly budget threshold is never checked.
- **Impact:** Operator has no monthly spend protection. Token costs could spiral unchecked.

### CR-02: Phase 16 — Settings Cost API Queries Wrong Table/Column
- **File:** `pmacs/web/routes/settings.py:715, 623-670`
- **Bug:** Queries nonexistent `actual_costs` table instead of DuckDB `api_usage`. Uses `created_at` column but schema has `called_at`. Every settings cost endpoint fails.
- **Impact:** Cost dashboard shows nothing. Operator cannot monitor spend.

### CR-03: Phase 1 — State Machine INTERRUPTED Deadlock
- **File:** `pmacs/schemas/state_machine.py`
- **Bug:** `INTERRUPTED` is in `TERMINAL_STATES` but spec defines outgoing transitions (→ ACTIVE, PANIC_EXIT, DELISTED). Those transitions are also missing from `VALID_TRANSITIONS`. Creates an unrecoverable state.
- **Impact:** If a holding is interrupted (e.g., news halt), it can never resume. Requires manual DB surgery.

### CR-04: Phase 1 — VALID_TRANSITIONS Diverges from Spec (7 places)
- **File:** `pmacs/schemas/state_machine.py`
- **Bug:** `APPROVED_PENDING` missing `ABORTED_LLM`/`ABORTED_PRE_LLM` aborts. `HALTED` has `CANDIDATE` (not in spec) but missing `DELISTED`/`PANIC_EXIT`. 5 other transitions don't match spec Architecture.md §8.2.
- **Impact:** Spec-required state transitions are blocked. Conviction < 0.3 aborts can't fire.

### CR-05: Phase 10 — Wizard Smoke-Test Cycle Missing
- **File:** `pmacs/web/routes/wizard.py`
- **Bug:** Implementation has 12 steps; spec (Source.md §12) requires 11. The smoke-test cycle (Step 10) that validates the full pipeline before PAPER promotion is absent.
- **Impact:** System promotes to PAPER without end-to-end verification. Could run broken pipeline with real paper money.

### CR-06: Phase 10 — Embedding Model Mismatch
- **File:** Wizard verification step
- **Bug:** Checks for `all-MiniLM-L6-v2` (384-dim) but spec requires `BAAI/bge-base-en-v1.5` (768-dim).
- **Impact:** Qdrant dimension mismatch at runtime. Vector search silently fails.

---

## High-Priority Warnings

### H-01: Phase 9 — Step Ordering Violates Architecture.md §12
- The orchestrator runs kill switch (step 4) and flywheel health (step 5) BEFORE FX snapshot (step 2) and corporate actions (step 3).
- Spec explicitly orders: FX → corp actions → kill switch → flywheel.
- Flywheel health snapshot may use stale FX data.

### H-02: Phase 2 — ROLLING_5D_LOSS Kill Switch Is a Stub
- `pmacs/cortex/kill_switch.py:440-472` reads `total_value_usd` but never compares to 5-day-prior value. Always returns `triggered=False`.
- One of 10 spec-required kill switch triggers is effectively disabled.

### H-03: Phase 2 — Kill Switch Disengage Doesn't Verify Condition Resolution
- `disengage()` only checks TOTP validity. Architecture.md §13.2 requires "Cortex confirms underlying condition resolved."
- Allows premature resumption under unsafe conditions.

### H-04: Phase 9 — Crash Resume Untested End-to-End
- Idempotency/checkpoint is the primary defense against crash corruption.
- No test simulates a mid-cycle crash and verifies resume.
- Existing tests only verify checkpoints exist after a full cycle.

### H-05: Phase 11 — axe-core CI Integration Missing
- `spec/Source.md §13.7` explicitly requires "verified in CI via axe-core."
- `test_a11y.py` uses TestClient structural checks only. No automated WCAG validation in CI.

### H-06: Phase 13 — Focus Traps Only Wired for Cmd-K
- TOTP modal and blocking modal have `aria-modal="true"` but no programmatic Tab containment.
- 2 WCAG compliance gaps.

---

## Cross-Cutting Patterns

### 1. Stale Plans (Phases 6, 12, 8)
Three phases have plans that significantly lag implementation. Phase 6 plan is at 0% remaining but marked REPLAN. Phase 12 plan targets ~80% already-completed work. This creates confusion about what's actually left.

**Recommendation:** Audit all PLAN.md files against current codebase. Mark completed items. Update status to reflect reality.

### 2. Spec Divergence Accumulating
Multiple phases show implementation drifting from spec (state machine transitions, wizard step count, dashboard metrics, step ordering). The 7,100-line spec is the source of truth but isn't being checked systematically.

**Recommendation:** Add a pre-commit or CI check that validates key spec contracts (state machine transitions, step counts, required fields).

### 3. Untested Critical Paths
- Crash resume (Phase 9)
- Monthly budget enforcement (Phase 16)
- Kill switch condition resolution (Phase 2)
- Smoke-test cycle (Phase 10)
- SSE real-time events (Phase 5)

**Recommendation:** Prioritize E2E tests for these 5 paths. Each represents a safety-critical feature.

### 4. SSE Still a Stub
Architecture.md §4.4 defines SSE as the central UI communication mechanism. Phase 5's SSE proxy sends pings only. Phase 16 notes no SSE cost events. Dashboard cannot receive real-time updates.

**Recommendation:** Wire SSE proxy as a cross-cutting priority. Blocks real-time dashboard functionality across multiple phases.

---

## Score Distribution

```
5/5 ████████                  Phase 6 (implementation)
4/5 ██████████████            Phase 1, Phase 2, Phase 5
3.5/5██████████████           Phase 8, Phase 9, Phase 12, Phase 13
3/5 ████████████████████████  Phase 10, Phase 11, Phase 16
```

---

## Priority Fix List (Ranked)

| # | Phase | Issue | Effort | Impact |
|---|-------|-------|--------|--------|
| 1 | 16 | Monthly budget kill switch period name bug | S | Critical safety |
| 2 | 16 | Settings cost queries wrong table/column | S | Cost visibility |
| 3 | 1 | INTERRUPTED deadlock + transition table reconciliation | M | Holding lifecycle |
| 4 | 10 | Embedding model mismatch (MiniLM vs bge-base) | S | Vector search |
| 5 | 10 | Add smoke-test wizard step (12→11 alignment) | M | Pre-PAPER validation |
| 6 | 2 | Wire ROLLING_5D_LOSS trigger logic | M | Risk management |
| 7 | 9 | Fix step ordering to match Architecture.md §12 | M | Cycle correctness |
| 9 | 5 | Wire SSE proxy (currently ping-only) | L | Dashboard real-time |
| 8 | 2 | Add condition-resolution check to disengage | S | Kill switch safety |
| 10 | 9 | Add crash-resume E2E test | M | Crash recovery |
| 11 | 11 | Add axe-core CI integration | M | WCAG compliance |
| 12 | 13 | Wire focus traps for TOTP + blocking modals | S | WCAG compliance |

**S** = < 1 hour, **M** = 1–4 hours, **L** = 4+ hours

---

## Per-Phase Review Files

| Phase | File |
|-------|------|
| Phase 1 | `.planning/phases/phase-1/CROSS-REVIEW.md` |
| Phase 2 | `.planning/phases/phase-2/CROSS-REVIEW.md` |
| Phase 5 | `.planning/phases/phase-5/CROSS-REVIEW.md` |
| Phase 6 | `.planning/phases/phase-6/CROSS-REVIEW.md` |
| Phase 8 | `.planning/phases/phase-8/CROSS-REVIEW.md` |
| Phase 9 | `.planning/phases/phase-9/CROSS-REVIEW.md` |
| Phase 10 | `.planning/phases/phase-10/CROSS-REVIEW.md` |
| Phase 11 | `.planning/phases/phase-11/CROSS-REVIEW.md` |
| Phase 12 | `.planning/phases/phase-12/CROSS-REVIEW.md` |
| Phase 13 | `.planning/phases/phase-13/CROSS-REVIEW.md` |
| Phase 16 | `.planning/phases/phase-16/CROSS-REVIEW.md` |
