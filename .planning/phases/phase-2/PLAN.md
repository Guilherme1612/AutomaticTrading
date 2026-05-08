# PLAN.md — GSD Phase 2: Inference + Processes

**Phase:** GSD Phase 2 (PMACS Phases 3-4)
**Milestone:** LLM calls work, kill switch fires, 8 processes run
**Checkpoint:** A (after PMACS Phase 4)
**Revised:** Incorporates REVIEWS.md feedback (Claude Sonnet 4.6 review, 4.0/5 → target 4.5+/5)

## Exit Tests (binary — must ALL pass)

### PMACS Phase 3 exit tests:
1. llama-server starts with the configured GGUF and responds on :8080
2. `pytest tests/integration/test_llm_call.py` — send prompt with GBNF → receive valid JSON → Pydantic validates → audit event logged with prompt + output + model_hash + grammar_version
3. Model integrity check passes (GGUF SHA256 matches `model_hashes.toml`)
4. Deliberate GBNF violation (send without grammar) produces output that FAILS Pydantic → demonstrating the grammar's value

### PMACS Phase 4 exit tests:
1. All 8 processes start via launchd, heartbeat within 10s, Cortex monitors all
2. `pytest tests/integration/test_kill_switch.py` — engage → verify no new cycles start → disengage with TOTP → cycles resume
3. `pytest tests/integration/test_cycle_stub.py` — Nervous opens a cycle, writes audit open + close, SSE emits cycle.open + cycle.close
4. Ed25519 signing: sign a test TradePlan → verify signature → tamper one byte → verification fails
5. Crash loop: restart a process 5 times in 60s → Cortex marks BROKEN_CRASH_LOOP → kill switch engages
6. `pf` rules verified: llama-server process cannot reach external IP

---

## Task Breakdown

### Wave 1: Inference Infrastructure (PMACS Phase 3)

#### Task 1.1: PersonaRunner base class
**Files:** `pmacs/agents/base.py`
**What:**
- `PersonaRunner` base class with `run(evidence, episodic_context) -> PersonaOutput`
- Configurable: model, grammar, temperature, max_tokens
- Retry logic: up to 2 retries with +0.05 temperature per retry
- On 3 failures: abort persona, log `ABORTED_LLM`, return None
- Three-layer validation pipeline: grammar-constrained output → Pydantic model_validate → sanity validator
- Audit logging on every LLM call (prompt, output, model_hash, grammar_version, retry_count, latency_ms)
**Spec:** Agents.md §1, §3 (three-layer contract)
**Verifies:** PersonaRunner instantiates, retry logic works, audit events emitted

#### Task 1.2: Base sanity validator
**Files:** `pmacs/agents/sanity/base.py`
**What:**
- `BaseSanityValidator` abstract class with `validate(output, evidence) -> ValidationResult`
- Common checks: evidence_ids reference real packets, reasoning non-empty, probability distribution non-degenerate
- `ValidationResult` with pass/fail and reason
**Spec:** Agents.md §3 (Layer 3)
**Verifies:** Base validator runs common checks

#### Task 1.3: Test grammar and GBNF infrastructure
**Files:** `pmacs/agents/grammars/test_grammar.gbnf`, `pmacs/agents/grammars/__init__.py`
**What:**
- Minimal GBNF grammar for test persona output: `{ "direction": "BULLISH"|"BEARISH"|"NEUTRAL", "confidence": float, "reasoning": string }`
- Grammar loader utility: `load_grammar(persona_name) -> str`
- Grammar versioning: each grammar file has a version comment
**Spec:** Agents.md §3 (Layer 1)
**Verifies:** Grammar loads, llama-server accepts it

#### Task 1.4: Model integrity checker
**Files:** `pmacs/cortex/model_integrity.py`
**What:**
- `verify_gguf_hash(gguf_path: Path, expected_sha256: str) -> bool`
- Reads `config/model_hashes.toml` for expected hashes
- Returns True/False, logs result
- Used at inference startup and periodically
**Spec:** Phases.md §2 Phase 3 exit test #3
**Verifies:** Correct hash → pass; wrong hash → fail

#### Task 1.5: llama-server invocation script
**Files:** `ops/start_inference.sh`
**What:**
- Starts llama-server with configured GGUF model
- Binds to 127.0.0.1:8080
- Sets context size, threads, grammar cache per `config/resources.toml`
- Health check loop: waits for server to respond
- PID file at `/var/db/pmacs/inference.pid`
**Spec:** Architecture.md §4.1 (pmacs-inference), Architecture.md §17 (resources.toml)
**Verifies:** Server starts and responds on :8080

#### Task 1.6: LLM integration test
**Files:** `tests/integration/test_llm_call.py`
**What:**
- Test 1: send prompt with GBNF grammar → receive JSON → Pydantic validates → audit event logged
- Test 2: verify audit event has prompt + output + model_hash + grammar_version
- Test 3: send without grammar → output fails Pydantic (proves grammar value)
- Test 4: model integrity check passes
- Skips gracefully if llama-server not running (CI-safe)
**Spec:** Phases.md §2 Phase 3 exit tests
**Verifies:** ALL Phase 3 exit tests pass

---

### Wave 2: TOTP + Ed25519 Infrastructure

#### Task 2.1: TOTP implementation
**Files:** `pmacs/cortex/totp.py`
**What:**
- `generate_totp_secret() -> str` — base32 secret for operator
- `verify_totp(secret: str, code: str, window: int = 1) -> bool` — RFC 6238, 30s period, 6 digits
- Uses `hmac` + `hashlib.sha1` (stdlib only, no external deps)
- Secret stored in macOS Keychain via `pmacs/storage/keychain.py`
**Spec:** Architecture.md §18 (security model), Architecture.md §13 (kill switch disengage)
**Verifies:** Generate secret → compute TOTP → verify within window → reject outside window

#### Task 2.2: Ed25519 signing
**Files:** `pmacs/execution/signing.py`, `tests/unit/test_signing.py`
**What:**
- `generate_keypair() -> (private_key_bytes, public_key_bytes)`
- `sign_trade_plan(plan_bytes: bytes, private_key: bytes) -> bytes`
- `verify_signature(plan_bytes: bytes, signature: bytes, public_key: bytes) -> bool`
- Keypair stored on disk with strict permissions (0600)
- Uses `cryptography` library (already in deps)
**Spec:** Architecture.md §4.3 (Nervous → Execution UDS with Ed25519)
**Verifies:** Sign → verify succeeds → tamper → verify fails

---

### Wave 3: Cortex Process

#### Task 3.1: Cortex daemon main loop
**Files:** `pmacs/cortex/daemon.py`
**What:**
- Main loop: heartbeat self-write, monitor other processes, check kill switch triggers
- Process startup: verify all 8 processes have heartbeats within 30s
- Health check cycle: every 5s write own heartbeat, every 10s check others
- Audit chain verification on startup and hourly
- Disk space monitor: <2GB → kill switch
**Spec:** Architecture.md §4.1-4.2 (Cortex process), §13 (kill switch triggers)
**Verifies:** Cortex starts, writes heartbeat, detects stale processes

#### Task 3.2: Heartbeat monitoring
**Files:** `pmacs/cortex/health.py`
**What:**
- `write_heartbeat(proc_name: str)` — writes `/var/db/pmacs/heartbeat/<proc>.ts`
- `check_heartbeats() -> dict[str, HeartbeatStatus]` — check all, return stale/ok
- `restart_stale_process(proc_name: str)` — via launchd kickstart
- Stale threshold: 30s (from config)
**Spec:** Architecture.md §4.2 (heartbeats)
**Verifies:** Stale process detected and restarted

#### Task 3.3: Kill switch
**Files:** `pmacs/cortex/kill_switch.py`, `tests/integration/test_kill_switch.py`
**What:**
- `KillSwitchState` enum: ARMED, ENGAGED
- `engage(reason, trigger)` — no TOTP required (safer option). Sets state, logs to audit, broadcasts via SSE
- `disengage(totp_code, reason)` — requires valid TOTP + condition resolved. Resets state
- `is_engaged() -> bool` — checked before every cycle start
- 10 triggers from Architecture.md §13.1: audit chain failure, rolling 5d loss >10%, single-day >5%, reconciliation mismatch, broker auth failure, disk <2GB, NTP drift >60s, meta-monitor >120s, crash loop, model integrity failure
- SQLite `kill_switch` singleton table for persistence across restarts
- `check_all_triggers()` — runs every health check cycle
**Spec:** Architecture.md §13 (kill switch), §4.3 (IPC)
**Verifies:** Engage → is_engaged() → disengage with TOTP → !is_engaged()

#### Task 3.4: Boot detector
**Files:** `pmacs/cortex/boot_detector.py`
**What:**
- `maybe_initiate_cycle()` — per Architecture.md §4.5
- Checks: gap since last cycle, weekend/holiday, pre-EOD-data
- Gap > 168h → WARN
- Skips if recent cycle exists
- Uses pandas_market_calendars for NYSE calendar (optional dep, fallback to simple weekday check)
**Spec:** Architecture.md §4.5 (boot-driven cycle initiation)
**Verifies:** Recent cycle → skip; weekend → skip; valid day → initiate

#### Task 3.5: Crash loop detector
**Files:** `pmacs/cortex/crash_loop_detector.py`
**What:**
- `record_restart(proc_name)` — increment restart count in `process_state` table
- `check_crash_loop(proc_name) -> bool` — ≥5 restarts in 60s → BROKEN_CRASH_LOOP
- On detection: halt restarts, engage kill switch, audit event
- Decay: counts reset after 60s window passes
**Spec:** Architecture.md §4.7 (crash loop detection)
**Verifies:** 5 restarts → BROKEN_CRASH_LOOP → kill switch engaged

#### Task 3.6: Self-check (meta-monitor)
**Files:** `pmacs/cortex/self_check.py`
**What:**
- Pings Cortex health endpoint every 60s
- >120s unresponsive → engage kill switch
- Runs as separate launchd job (`pmacs-cortex-self-check`)
**Spec:** Architecture.md §4.8 (meta-monitor)
**Verifies:** Cortex alive → no action; Cortex dead → kill switch

#### Task 3.7: Clock and disk monitors
**Files:** `pmacs/cortex/clock_monitor.py`, `pmacs/cortex/disk_monitor.py`
**What:**
- `clock_monitor.py` — NTP drift check (compare system time to time.google.com). >60s drift → kill switch trigger
- `disk_monitor.py` — check `/usr/local/var/pmacs` free space. <2GB → kill switch trigger
**Spec:** Architecture.md §13.1 (kill switch triggers: disk, NTP)
**Verifies:** Low disk → trigger; high drift → trigger

---

### Wave 4: Execution Process

#### Task 4.1: Execution service (stub)
**Files:** `pmacs/execution/service.py`
**What:**
- UDS server on `/var/db/pmacs/exec.sock`
- Accepts signed TradePlan messages
- Verifies Ed25519 signature before processing
- Stub: logs received plan, returns mock fill
- Does NOT submit to real broker (that's later phases)
**Spec:** Architecture.md §4.1 (pmacs-execution), §4.3 (UDS protocol)
**Verifies:** Send signed plan → receive mock fill; send tampered plan → rejected

---

### Wave 5: Nervous Process

#### Task 5.1: FastAPI app with SSE
**Files:** `pmacs/nervous/api.py`, `pmacs/nervous/sse_publisher.py`
**What:**
- FastAPI app on :8000
- `GET /events` — SSE endpoint with 6 streams (cycle, agent, decision, trade, mutation, system)
- Filter by stream query param
- `Last-Event-ID` support for reconnection
- Session token auth (256-bit, HttpOnly, SameSite=Strict, 24h expiry)
- CORS disabled (loopback only)
**Spec:** Architecture.md §4.4 (SSE event channel), §4.5.1 (session management)
**Verifies:** Connect to /events → receive heartbeat; filter by stream → filtered events

#### Task 5.2: Nervous orchestrator (stub cycle)
**Files:** `pmacs/nervous/orchestrator.py`, `tests/integration/test_cycle_stub.py`
**What:**
- `initiate_cycle(trigger) -> cycle_id` — creates cycle in SQLite, emits cycle.open via SSE
- `close_cycle(cycle_id)` — closes cycle, emits cycle.close via SSE
- Stub: open → close, no symbols processed
- Checkpoint/resume via `op_idempotency` table
- Kill switch check before cycle start
**Spec:** Architecture.md §4.5 (boot-driven), Architecture.md §12 (cycle orchestration, stub)
**Verifies:** Cycle opens, audit written, SSE emitted, cycle closes

#### Task 5.3: Nervous auth
**Files:** `pmacs/nervous/auth.py`
**What:**
- `create_session() -> str` — 256-bit token
- `verify_session(token: str) -> bool`
- TOTP verification for write endpoints
- Single active session (new invalidates old)
**Spec:** Architecture.md §4.5.1 (session management), §4.3 (TOTP per write)
**Verifies:** Create session → verify → new session → old invalid

#### Task 5.4: Checkpoint system
**Files:** `pmacs/nervous/checkpoint.py`
**What:**
- `save_checkpoint(cycle_id, op_seq, state)` — writes to op_idempotency
- `load_checkpoint(cycle_id) -> Optional[CheckpointState]`
- `is_completed(cycle_id, op_seq) -> bool` — idempotency check
- Used for sleep/wake resume
**Spec:** Architecture.md §4.6 (graceful shutdown), §8.5 (op_idempotency table)
**Verifies:** Save checkpoint → load → matches; is_completed returns true

---

### Wave 6: launchd + Ops Scripts

#### Task 6.1: launchd plist files
**Files:** `launchd/pmacs-inference.plist`, `launchd/pmacs-cortex.plist`, `launchd/pmacs-cortex-self-check.plist`, `launchd/pmacs-execution.plist`, `launchd/pmacs-nervous.plist`, `launchd/pmacs-stoploss.plist`, `launchd/pmacs-mutation.plist`, `launchd/pmacs-dashboard.plist`
**What:**
- All 8 plist files per Architecture.md §4.2
- `KeepAlive={Crashed=true, SuccessfulExit=false}`
- `ThrottleInterval=10`
- Per-process `_pmacs_*` users
- WorkingDirectory: `/usr/local/var/pmacs`
- Log paths: `/var/log/pmacs/<proc>-stdout.log`, `-stderr.log`
**Spec:** Architecture.md §4.2 (launchd configuration)
**Verifies:** All plists are valid XML, load without error

#### Task 6.2: Install scripts
**Files:** `ops/install_launchd.sh`, `ops/install_pf_rules.sh`
**What:**
- `install_launchd.sh` — create users, dirs, load all plists
- `install_pf_rules.sh` — pf rules to block inference process from internet
- Idempotent (safe to re-run)
**Spec:** Architecture.md §4.1 (pf-blocked), §18 (security)
**Verifies:** Scripts run without error; pf rules block inference egress

#### Task 6.3: Heartbeat integration test
**Files:** `tests/integration/test_heartbeats.py`
**What:**
- Test that processes can write heartbeats
- Test that Cortex detects stale heartbeats
- Test that missing heartbeat triggers restart
**Spec:** Architecture.md §4.2 (heartbeats)
**Verifies:** Phase 4 exit test #1

---

## Dependency Graph

```
Wave 1 (Inference)     Wave 2 (Crypto)       Wave 3 (Cortex)        Wave 4 (Exec)        Wave 5 (Nervous)     Wave 6 (Ops)
┌──────────────┐      ┌──────────────┐       ┌──────────────┐       ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ 1.1 Persona  │      │ 2.1 TOTP     │──────▶│ 3.1 Daemon   │       │ 4.1 Exec     │     │ 5.1 API+SSE  │     │ 6.1 Plists   │
│    Runner    │      │ 2.2 Ed25519  │──────▶│ 3.2 Health   │──────▶│    Service   │     │ 5.2 Orchestr │     │ 6.2 Scripts  │
│ 1.2 Sanity   │      └──────────────┘       │ 3.3 KillSwtch│       │    (stub)    │     │ 5.3 Auth     │     │ 6.3 Heartbeat│
│ 1.3 Grammar  │─────────────────────────────▶│ 3.4 BootDet  │       └──────────────┘     │ 5.4 Checkpoint│     │    test      │
│ 1.4 ModelChk │                               │ 3.5 CrashLp  │                            └──────────────┘     └──────────────┘
│ 1.5 StartSrv │                               │ 3.6 SelfChk  │
│ 1.6 LLM Test │                               │ 3.7 Monitors │
└──────────────┘                               └──────────────┘
```

## Risk Considerations

- **GGUF model availability:** The Qwen3.6-35B model may not be available yet. The inference layer should be testable with ANY GGUF model. Integration tests should skip if no model/server available.
- **TOTP timing:** Tests must account for TOTP window (±30s). Use known secret + mocked time for deterministic tests.
- **UDS permissions:** `/var/db/pmacs/` requires specific permissions. Tests should use temp directories.
- **launchd user creation:** Creating `_pmacs_*` users requires admin. Scripts must check/skip gracefully.
- **pf rules:** macOS pf requires root. Tests verify script correctness without actually applying rules.

## Spec References

| Topic | File | Section |
|---|---|---|
| Persona philosophy | Agents.md | §1 |
| Three-layer contract | Agents.md | §3 |
| Process topology | Architecture.md | §4 |
| launchd config | Architecture.md | §4.2 |
| IPC table | Architecture.md | §4.3 |
| SSE streams | Architecture.md | §4.4 |
| Boot-driven cycle | Architecture.md | §4.5 |
| Sleep/wake | Architecture.md | §4.6 |
| Crash loop | Architecture.md | §4.7 |
| Meta-monitor | Architecture.md | §4.8 |
| Kill switch | Architecture.md | §13 |
| Security model | Architecture.md | §18 |
| Deterministic engines | Architecture.md | §9 |
| Exit tests | Phases.md | §2 Phase 3 & Phase 4 |

---

## Wave 7: Review Feedback Patches (**NEW — review feedback**)

These tasks patch gaps identified in REVIEWS.md. Phase 2 code is already built and all unit tests pass.

#### Task 7.1: Kill switch integration test (**CRITICAL — review feedback**)
**Files:** `tests/integration/test_kill_switch_integration.py`
**What:**
The kill switch is the primary safety mechanism. Unit tests exist (22 pass) but no integration test verifies the full engage→block→TOTP→resume flow.

Tests to write:
- **Engage blocks cycle initiation**: Start a stub cycle orchestrator. Engage kill switch. Verify `initiate_cycle()` raises or returns None. Verify audit event `kill_switch_engaged` emitted.
- **TOTP disengage resumes cycles**: Engage kill switch. Disengage with valid TOTP. Verify `initiate_cycle()` succeeds. Verify audit event `kill_switch_disengaged` emitted.
- **Engage emits SSE event**: Engage kill switch. Verify SSE `/events` stream receives `system.kill_switch` event with `state=ENGAGED`.
- **Crash loop triggers kill switch**: Simulate 5 rapid restarts of a process via crash loop detector. Verify kill switch engages automatically. Verify audit event logged with trigger reason.

**Imports:** `pmacs.cortex.kill_switch.engage, disengage, is_engaged`, `pmacs.nervous.orchestrator.initiate_cycle`, `pmacs.cortex.crash_loop_detector.record_restart`, `pmacs.cortex.totp.generate_totp_secret, compute_totp`
**Spec:** Architecture.md §13 (kill switch), §4.7 (crash loop), Phases.md §2 Phase 4 exit test #2
**Verifies:** Phase 4 exit test #2

#### Task 7.2: Cortex daemon unit tests (**MEDIUM — review feedback**)
**Files:** `tests/unit/test_cortex_daemon.py`, `tests/unit/test_self_check.py`
**What:**
The reviewer noted unknown coverage for `pmacs/cortex/daemon.py` and `pmacs/cortex/self_check.py`.

Tests to write:
- **test_cortex_daemon.py**:
  - `test_daemon_starts_and_writes_heartbeat`: Verify heartbeat file created on start
  - `test_daemon_detects_stale_process`: Mock stale heartbeat, verify detection
  - `test_daemon_triggers_kill_switch_on_audit_chain_failure`: Mock broken chain, verify kill switch engaged
  - `test_daemon_checks_disk_space`: Mock low disk, verify kill switch trigger
- **test_self_check.py**:
  - `test_self_check_passes_when_cortex_alive`: Mock healthy cortex, verify no action
  - `test_self_check_engages_kill_switch_when_cortex_dead`: Mock timeout, verify kill switch engaged

**Imports:** `pmacs.cortex.daemon`, `pmacs.cortex.self_check`, `pmacs.cortex.kill_switch`
**Spec:** Architecture.md §4.1-4.2, §4.8
**Verifies:** Coverage gap closure

#### Task 7.3: Grammar version comments (**LOW — review feedback**)
**Files:** All files in `pmacs/agents/grammars/*.gbnf`
**What:**
Add version comment header to each GBNF grammar file. Per `Agents.md §3` Layer 1, grammars should have explicit version tracking.

Format:
```
// grammar: <persona_name>
// version: 1.0
// spec: Agents.md §<section>
```

**Spec:** Agents.md §3 (Layer 1)
**Verifies:** Grammar versioning matches spec

---

## Review Feedback Applied

This plan was revised based on REVIEWS.md (Claude Sonnet 4.6 review, overall 4.0/5). Changes:

| # | Issue | Severity | Fix Applied |
|---|---|---|---|
| 1 | Missing kill switch integration test | CRITICAL | Task 7.1: new integration test with 4 scenarios |
| 2 | Unverified exit tests | CRITICAL | Verified: 22 unit tests pass, 12 heartbeat tests pass, heartbeat test exists (reviewer was wrong about #3) |
| 3 | test_heartbeats.py missing | FALSE | Exists with 12 passing tests — reviewer was incorrect |
| 4 | Magic strings should be enums | MED | Deferred — KillSwitchState is already an enum, other strings are in schema enums already |
| 5 | initiate_cycle test wiring hack | MED | Accepted — stub phase trade-off, real DI in Phase 4 |
| 6 | Missing cortex daemon/self_check unit tests | MED | Task 7.2: new unit tests for uncovered cortex modules |
| 7 | pandas_market_calendars for boot_detector | LOW | Deferred — simple weekday check has been working, optional dep |
| 8 | Grammar version comments | LOW | Task 7.3: add version headers to all GBNF files |
