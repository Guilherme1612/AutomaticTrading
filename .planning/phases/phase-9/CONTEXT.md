# Phase 9: Core Orchestration — Context

## Goal
Wire all 30 canonical cycle steps into `pmacs/nervous/orchestrator.py`, connecting 24+ existing but disconnected engines, agents, and storage modules into a working end-to-end decision pipeline.

## Origin
Full spec review (2026-05-12/13) identified that while all components exist as files, the orchestrator is a stub with only `initiate_cycle()`/`close_cycle()`. The system has all parts but they're not assembled.

## Key Decisions

### D1: Scope — Orchestration only, no new features
All engines and agents exist. This phase is purely wiring them together. No new business logic.

### D2: Alpaca integration deferred to Phase 10
Broker integration (paper + real adapters) is a separate concern. Phase 9 uses mock fills. This keeps scope focused on orchestration correctness.

### D3: 6-wave build order (from RESEARCH.md)
- Wave 1: Core skeleton (lock, checkpoint, kill switch, close)
- Wave 2: Pre-cycle data + queue composition
- Wave 3: Per-symbol pipeline (the heart — personas, arbitration, crucible, sizing, conviction, execution)
- Wave 4: Post-cycle flywheel (calibration, lessons, FDE, reconciliation)
- Wave 5: Hardening (timeouts, memory, graceful shutdown, kill switch mid-cycle)
- Wave 6: Integration tests + performance validation

### D4: Step-dispatch table pattern
Each canonical step maps to a callable with (cycle_id, op_seq, db, audit) signature. Enables idempotency, crash resume, and per-step testing.

## Constraints
- Every state transition via `state_machine.transition()` — NEVER direct mutation
- Every audit event requires `cycle_id` — NEVER `cycle_id=None`
- `canonical_json()` for all audit serialization — NEVER `json.dumps()`
- `BUCKETS["source"].acquire()` for rate limiting — NEVER custom rate-limit logic
- All mutations require operator TOTP — no auto-promote
- Evidence scoped per-symbol — no cross-ticker leakage
- Crucible hard-limited to 2 cycles, 90s per cycle
- Persona dispatch across 3 parallel slots (not sequential)

## Dependencies
- All engines from Phases 1-8 (arbitration, conviction, sizing, queue, calibration, etc.)
- All 10 persona runners (7 analysis + gatekeeper + crucible + memo_writer)
- All 5 storage backends (SQLite, KuzuDB, Qdrant, DuckDB, audit.log)
- All 14 data sources (edgar, polygon, finnhub, etc.)

## Exit Test
Full synthetic cycle runs end-to-end:
1. Cycle opens, acquires lock
2. Pre-cycle steps execute (FX, universe, gatekeeper, queue)
3. 3 synthetic tickers pass through per-symbol pipeline
4. At least 1 produces STRONG_BUY with mock fill
5. Post-cycle flywheel engines fire
6. Cycle closes with complete audit trail
7. Audit chain verifies (no tampering)
8. Resume from checkpoint after simulated crash at step 13g
