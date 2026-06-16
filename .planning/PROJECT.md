# PMACS — Portfolio Management and Catalyst Automation System

## Summary

Single-operator, local-only, catalyst-driven, LLM-assisted decision engine with deterministic arbitration. Runs local LLMs (Qwen3.6-35B-A3B) for narrative analysis and Python for math, sizing, arbitration, and execution. LLMs never decide, never math, never sign. Trades paper money ($5K) from day 1, graduates to live capital only after empirical performance gates pass.

## Spec (pre-specified, canonical)

The complete specification lives in `spec/` as four files totaling 7,242 lines:

| File | Lines | Purpose | Wins for |
|---|---|---|---|
| `Source.md` | 1,536 | Vision, philosophy, operator surface, UI specs | Vision and operator-facing behavior |
| `Architecture.md` | 3,092 | Processes, IPC, storage, engines, cycle orchestration | Implementation specifics |
| `Agents.md` | 1,652 | Persona prompts, GBNF grammars, sanity validators, FDE | LLM contracts |
| `Phases.md` | 962 | 15 build phases, exit tests, mode gates | Build sequence and what ships when |

Reading order: Source.md → Architecture.md → Agents.md → Phases.md.

## Five Non-Negotiables

1. LLMs never sign trades (Ed25519 only in pmacs-execution)
2. LLMs never math (Python computes probabilities, sizing, arbitration, conviction)
3. Every state transition is hash-chained (prev_sha256)
4. Local-only execution (no cloud LLM, no telemetry, pf-blocked)
5. Operator owns the kill switch (engagement automatic; disengagement requires TOTP)

## Key Constraints

- Paper capital: $5,000
- Max single position: 20% ($1,000)
- Max concurrent positions: 5
- Catastrophe-net stop: 15% below entry (broker-side)
- Local model: Qwen3.6-35B-A3B via llama-server on :8080
- 8 launchd processes, 5 storage backends
- Mutation Engine: advisor-only, ALL mutations require operator TOTP

## Process Topology

| Process | Port | Responsibility |
|---|---|---|
| pmacs-inference | :8080 | llama-server (pf-blocked from internet) |
| pmacs-cortex | daemon | Health monitoring, kill switch, boot detection |
| pmacs-cortex-self-check | daemon | Meta-monitor |
| pmacs-execution | UDS | Trade signing (Ed25519) + broker submission |
| pmacs-nervous | :8000 | Orchestration, SSE, write API |
| pmacs-stoploss | daemon | RTH position monitoring every 30 min |
| pmacs-mutation | daemon | Active flywheel (dormant first 50 cycles) |
| pmacs-dashboard | :8000 | Read-only web UI, served by pmacs-nervous (loopback only) |

## GSD Phase Structure

| GSD Phase | PMACS Phases | Milestone |
|---|---|---|
| 1 | 1-2 | Foundation + Data |
| 2 | 3-4 | Inference + Processes |
| 3 | 5-6 | Personas |
| 4 | 7-8 | Pipeline + Paper (PAPER-READY) |
| 5 | 9-10 | Monitoring + Dashboard |
| 6 | 11-12 | Calibration + FDE |
| 7 | 13-14 | Episodic + Mutation (FLYWHEEL-READY) |
| 8 | 15 | Polish (LIVE-READY) |

## Risk Checkpoints

- **Checkpoint A** (after Phase 4): kill switch, audit chain, pf rules, Ed25519
- **Checkpoint B** (after Phase 8): paper trades, catastrophe-net, wizard, mode SHADOW+PAPER
- **Checkpoint C** (after Phase 14): Mutation Engine isolation, auto-rollback, TOTP on all mutations
