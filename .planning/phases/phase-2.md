# GSD Phase 2: Inference + Processes

**Implements PMACS Build Phases 3-4** (spec/Phases.md §2)

## Milestone

LLM calls work, kill switch fires, 8 processes run.

---

## PMACS Phase 3: Inference backend — llama-server integration

**Goal:** PMACS can send a prompt to llama-server, receive structured output constrained by GBNF, parse it through Pydantic, and log the call to audit.

**What gets built:**
- `pmacs/agents/base.py` — `PersonaRunner` base class
- `pmacs/agents/grammars/test_grammar.gbnf` — a minimal test grammar
- `pmacs/agents/sanity/base.py` — base sanity validator
- llama-server invocation script in `ops/start_inference.sh`
- `config/model_hashes.toml` — SHA256 of the GGUF
- `pmacs/cortex/model_integrity.py` — GGUF SHA256 verification
- `tests/integration/test_llm_call.py` — send a simple prompt, receive GBNF-constrained JSON, validate through Pydantic

**Exit test:**
1. llama-server starts with the configured GGUF and responds on :8080
2. `pytest tests/integration/test_llm_call.py` — send prompt with GBNF → receive valid JSON → Pydantic validates → audit event logged with prompt + output + model_hash + grammar_version
3. Model integrity check passes (GGUF SHA256 matches `model_hashes.toml`)
4. Deliberate GBNF violation (send without grammar) produces output that FAILS Pydantic → demonstrating the grammar's value

**Dependencies:** Phase 1 (schemas, audit for logging the LLM call).

---

## PMACS Phase 4: Core processes — Cortex, Nervous, Execution, kill switch

**Goal:** The process topology exists. All 8 launchd processes can start, heartbeat, and be monitored. The kill switch works end-to-end. The nervous system can orchestrate a stub cycle.

**What gets built:**
- `pmacs/cortex/daemon.py` — main loop
- `pmacs/cortex/health.py` — heartbeat monitoring
- `pmacs/cortex/kill_switch.py` — engage/disengage with TOTP
- `pmacs/cortex/boot_detector.py` — gap detection
- `pmacs/cortex/crash_loop_detector.py`
- `pmacs/cortex/self_check.py` — meta-monitor
- `pmacs/cortex/clock_monitor.py`
- `pmacs/cortex/disk_monitor.py`
- `pmacs/cortex/totp.py` — TOTP verification
- `pmacs/nervous/orchestrator.py` — stub cycle (open → close, no symbols)
- `pmacs/nervous/api.py` — FastAPI app with `/events` SSE
- `pmacs/nervous/sse_publisher.py`
- `pmacs/nervous/checkpoint.py` — cycle resume
- `pmacs/nervous/auth.py` — session token + TOTP verification
- `pmacs/execution/service.py` — stub (accepts TradePlan via UDS, logs, returns mock fill)
- `pmacs/execution/signing.py` — Ed25519 keypair generation and signing
- `launchd/*.plist` — all 8 plists
- `ops/install_launchd.sh`
- `ops/install_pf_rules.sh` — network egress rules
- `tests/integration/test_kill_switch.py`
- `tests/integration/test_heartbeats.py`
- `tests/integration/test_cycle_stub.py`

**Exit test:**
1. All 8 processes start via launchd, heartbeat within 10s, Cortex monitors all
2. `pytest tests/integration/test_kill_switch.py` — engage → verify no new cycles start → disengage with TOTP → cycles resume
3. `pytest tests/integration/test_cycle_stub.py` — Nervous opens a cycle, writes audit open + close, SSE emits cycle.open + cycle.close
4. Ed25519 signing: sign a test TradePlan → verify signature → tamper one byte → verification fails
5. Crash loop: restart a process 5 times in 60s → Cortex marks BROKEN_CRASH_LOOP → kill switch engages
6. `pf` rules verified: llama-server process cannot reach external IP

**Dependencies:** Phase 1 (schemas, audit, SQLite), Phase 2 (data gateway for boot detector's refresh).

---

## Risk Checkpoint A (after Phase 4)

Before proceeding to GSD Phase 3, verify:
- [ ] Kill switch engages on all 10 triggers
- [ ] Kill switch disengagement requires TOTP
- [ ] Audit chain break → immediate kill switch
- [ ] llama-server process cannot reach external IP (pf verified)
- [ ] Execution process is the only one with broker imports
- [ ] Ed25519 signing works and tamper-detection works
- [ ] Crash loop detection works

**If any fails:** Do not proceed. Fix the risk property first.

---

## Next-phase dependency

GSD Phase 3 requires:
- All PMACS Phase 3-4 exit tests pass
- Risk Checkpoint A fully verified
- llama-server operational with GBNF
- Kill switch works end-to-end
