# PMACS Roadmap

## Overview

8 GSD phases implementing 15 PMACS build phases. Sequential execution with risk checkpoints at critical boundaries.

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

## Dependencies (linear chain) — ALL COMPLETE

```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7 → Phase 8 ✓
                     ↑ Checkpoint A    ↑ Checkpoint B              ↑ Checkpoint C
```

All 8 phases complete. LIVE-READY.

## Operator Milestones

- **After Phase 4 (GSD):** System boots, runs stub cycles, kill switch works. Not usable yet.
- **After Phase 8 (PMACS) / GSD Phase 4:** System is operationally usable. Paper trading works. Wizard complete. SHADOW + PAPER mode. **The operator can start using the system here.**
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
