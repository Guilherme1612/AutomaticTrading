# Phase 2 Context — Inference + Processes

## PMACS Phases Covered
- Phase 3: Inference backend (llama-server integration)
- Phase 4: Core processes (Cortex, Nervous, Execution, kill switch)

## Spec References
- Architecture.md §4 (process topology, IPC, heartbeats, crash loop detection)
- Architecture.md §9 (deterministic engines — Arbitration, Conviction, Sizing)
- Architecture.md §11 (kill switch)
- Architecture.md §13 (inference server)
- Agents.md §1-3 (agent philosophy, roster, three-layer contract)
- Phases.md §2 Phase 3 & Phase 4

## Key Design Decisions (from spec)
- LLM backend: llama-server on :8080, pf-blocked from internet
- Model: unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_XL
- Three-layer output contract: GBNF → Pydantic → Sanity validator
- 8 launchd processes with heartbeats every 5s, stale after 30s
- Kill switch: ARMED/ENGAGED state machine, engage without TOTP, disengage requires TOTP
- Execution: Ed25519 signing via UDS, no LLM can sign trades
- Nervous: FastAPI on :8000, SSE on /events, stub cycle
- Crash loop: ≥5 restarts in 60s → BROKEN_CRASH_LOOP → kill switch

## Exit Tests
1. llama-server starts with GGUF, responds on :8080
2. Integration test: prompt → GBNF-constrained JSON → Pydantic validates → audit logged
3. Model integrity: GGUF SHA256 matches model_hashes.toml
4. GBNF violation (no grammar) → Pydantic failure
5. All 8 processes start, heartbeat within 10s, Cortex monitors all
6. Kill switch: engage → no new cycles → disengage with TOTP → cycles resume
7. Stub cycle: open → close, audit + SSE events emitted
8. Ed25519: sign → verify → tamper → verification fails
9. Crash loop: 5 restarts/60s → BROKEN_CRASH_LOOP → kill switch engages
10. pf rules: llama-server cannot reach external IP

## Dependencies (from Phase 1)
- pmacs/schemas/ — all schema models
- pmacs/storage/audit.py — audit writer
- pmacs/storage/sqlite.py — SQLite init
- pmacs/data/gateway.py — HTTP gateway
- pmacs/engines/state_machine.py — holding state machine
- pmacs/logsys/ — logging system
- pmacs/config.py — config loader
