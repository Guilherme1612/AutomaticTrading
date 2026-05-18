# PMACS Roadmap

## Overview

9 GSD phases implementing 15 PMACS build phases + core orchestration. Sequential execution with risk checkpoints at critical boundaries.

## Phase Structure

| Phase | PMACS Phases | Milestone | Risk Checkpoint |
|---|---|---|---|
| [Phase 1](phases/phase-1.md) | 1-2: Foundation + Data | Schemas compile, audit chain works, data fetches | — |
| [Phase 2](phases/phase-2.md) | 3-4: Inference + Processes | LLM calls work, kill switch fires, 8 processes run | **Checkpoint A** (after PMACS Phase 4) |
| [Phase 3](phases/phase-3.md) | 5-6: Personas | All 7 analysis personas operational | — |
| [Phase 4](phases/phase-4.md) | 7-8: Pipeline + Paper | Full pipeline, paper trading, wizard — **PAPER-READY** | **Checkpoint B** (after PMACS Phase 8) |
| [Phase 5](phases/phase-5.md) | 9-10: Monitoring + Dashboard | Stop-loss, re-eval, all 7 UI pages | — |
| [Phase 6](phases/phase-6.md) | 11-12: Calibration + FDE | Flywheel passive components, 18 taxonomy types | — |
| [Phase 7](phases/phase-7.md) | 13-14: Episodic + Mutation | Active flywheel — **FLYWHEEL-READY** | **Checkpoint C** (after PMACS Phase 14) |
| [Phase 8](phases/phase-8.md) | 15: Polish | Production-quality — **LIVE-READY** | — |
| [Phase 9](phases/phase-9/PLAN.md) | Core Orchestration | Wire 30 canonical cycle steps into working end-to-end pipeline | — |
| [Phase 10](phases/phase-10/PLAN.md) | Broker Integration + Ops | Replace mock fills with Alpaca paper API, complete wizard, operational gaps | — |
| [Phase 11](phases/phase-11/PLAN.md) | Polish + Operator Experience | Dynamic sparklines, a11y audit, perf profiling, workflow validation — **LIVE-READY** | — |
| [Phase 12](phases/phase-12/PLAN.md) | Spec Gap Closure | Evidence pipeline, real prices, storage activation, engine completion, flywheel closure — **SPEC-COMPLIANT** | — |

## Dependencies (linear chain)

```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7 → Phase 8 → Phase 9 → Phase 10 → Phase 11 → Phase 12
                     ↑ Checkpoint A    ↑ Checkpoint B              ↑ Checkpoint C    ↑ SPEC-COMPLIANT
```

Phases 1-11 complete. System is LIVE-READY.

## Phase 9 Plans

- [ ] Wave 1: Core Skeleton -- flock lock, checkpoint resume, kill switch, close
- [ ] Wave 2: Pre-Cycle Data + Queue -- FX, universe, gatekeeper, queue composition
- [ ] Wave 3: Per-Symbol Pipeline -- personas, arbitration, crucible, sizing, mock execution
- [ ] Wave 4: Post-Cycle Flywheel -- calibration, lessons, FDE, reconciliation
- [ ] Wave 5: Hardening -- timeouts, graceful shutdown, kill switch mid-cycle
- [ ] Wave 6: Performance Validation -- timing instrumentation, edge cases, exit test

## Phase 10 Plans

- [ ] Wave 1: BrokerAdapter ABC + AlpacaPaperAdapter
- [ ] Wave 2: Catastrophe Net Wiring
- [ ] Wave 3: Wizard UI (11-step templates + backend steps)
- [ ] Wave 4: Operational Gaps (Ollama schemas, dead-letter, SSE resume)
- [ ] Wave 5: Integration + Exit Test

## Phase 11 Plans

- [x] Wave 1: Dynamic Sparklines + HTMX Navigation
- [x] Wave 2: Error State Integration + Notification Persistence
- [x] Wave 3: Accessibility Audit + Keyboard Shortcuts Validation
- [x] Wave 4: Performance Profiling + Ops Tool Validation
- [x] Wave 5: Operator Workflow Validation + Documentation Polish

## Operator Milestones

- **After Phase 4 (GSD):** System boots, runs stub cycles, kill switch works. Not usable yet.
- **After Phase 8 (PMACS) / GSD Phase 4:** System is operationally usable. Paper trading works. Wizard complete. SHADOW + PAPER mode. **The operator can start using the system here.**
- **After Phase 9 (GSD):** All 30 canonical cycle steps wired. Full end-to-end decision pipeline operational. System runs complete cycles from data fetch through flywheel post-processing.
- **After Phase 10 (GSD):** Real Alpaca paper trading. System submits and fills real paper orders. Wizard operational. Dead-letter persistence and SSE resume work.
- **After Phase 11 (GSD):** Production-quality polish. Dynamic sparklines, HTMX navigation, a11y verified, performance within budget, all 8 operator workflows ≤ 3 clicks. **LIVE-READY.**
- **After Phase 12 (PMACS) / GSD Phase 6:** Flywheel learning begins. Calibration adjusts weights. Failures classified.
- **After Phase 14 (PMACS) / GSD Phase 7:** Active flywheel. Mutation Engine proposes improvements. PAPER_VALIDATED eligible.
- **After Phase 15 (PMACS) / GSD Phase 8:** Production-quality. LIVE_EARLY eligible.

## Mode Progression

```
INSTALLING → SHADOW + PAPER → PAPER_VALIDATED → LIVE_EARLY → LIVE_STANDARD → LIVE_EXPANDED
              (after GSD 4)    (after GSD 7+)     (after GSD 8)
```

All mode transitions require numerical gate passage + operator TOTP.

## Spec Authority

| Question type | Authority |
|---|---|
| Vision, operator behavior | `spec/Source.md` |
| Implementation specifics | `spec/Architecture.md` |
| LLM contracts | `spec/Agents.md` |
| Build sequence, what ships when | `spec/Phases.md` |
