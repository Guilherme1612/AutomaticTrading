# PLAN.md — GSD Phase 1: Foundation + Data

**Phase:** GSD Phase 1 (PMACS Phases 1-2)
**Milestone:** Schemas compile, audit chain works, data fetches
**Revised:** Incorporates REVIEWS.md feedback (Claude Sonnet 4.6 review, 3.2/5 → target 4.0+/5)

## Exit Tests (binary — must ALL pass)

### PMACS Phase 1 exit tests:
1. `pytest tests/unit/test_schemas.py` — ALL pass
2. `pytest tests/unit/test_audit_chain.py` — genesis → 100 appends → verify passes; tamper one line → verify catches it
3. `pytest tests/unit/test_state_machine.py` — every valid transition succeeds; every invalid raises `InvalidStateTransition`
4. `python -c "from pmacs.config import load_config; load_config()"` — succeeds on fresh repo
5. All anti-pattern grep checks pass
6. `pytest tests/property/test_probabilities.py` — all probabilistic invariants hold (**NEW — review feedback**)
7. `pytest tests/integration/test_audit_writing.py` — state transitions emit audit events (**NEW — review feedback**)

### PMACS Phase 2 exit tests:
1. `pytest tests/unit/test_staleness.py` — all budgets enforced
2. `pytest tests/unit/test_fx.py` — round-trip `usd_to_eur(eur_to_usd(100, snap), snap) ≈ 100`; `eur_per_usd` field raises ValueError (**STRENGTHENED — review feedback**)
3. `pytest tests/integration/test_data_sources.py` — at least 10/13 sources return valid EvidencePacket
4. Rate limiting: 20 rapid calls to Polygon complete without 429

---

## Task Breakdown

### Wave 1: Project scaffolding + config (no dependencies)

#### Task 1.1: Project scaffolding
**Files:** `pyproject.toml`, `pmacs/__init__.py`, `pmacs/__main__.py`, `pmacs/cli.py`, `.gitignore`, `pmacs/schemas/__init__.py`, `pmacs/data/__init__.py`, `pmacs/storage/__init__.py`, `pmacs/logsys/__init__.py`, `pmacs/engines/__init__.py`
**What:**
- `pyproject.toml` — uv-managed, Python 3.11, pydantic>=2.5, all runtime deps (tomli/tomllib, httpx, cryptography, pydantic, sqlite3 stdlib). Include `hypothesis` for property tests.
- Directory structure per Architecture.md §3
- `pmacs/__init__.py` with version
- `pmacs/cli.py` stub entry point (`pmacs <command>`)
- `.gitignore` for Python + macOS + secrets
**Spec:** Architecture.md §3 (repo tree)
**Verifies:** `pip install -e .` succeeds; `import pmacs` works

#### Task 1.2: Configuration files
**Files:** `config/resources.toml`, `config/risk.toml`, `config/crucible.toml`, `config/mutation.toml`, `config/model_registry.json`, `config/model_hashes.toml`, `config/source_criticality.toml`
**What:**
Copy defaults from Architecture.md §17 verbatim. Each file must contain complete, loadable content (not just structure):

- `resources.toml` — hardware budgets: `max_concurrent_llm = 1`, `llm_timeout_s = 120`, `cycle_time_limit_s = 300`, `stop_loss_interval_s = 1800`, `crash_loop.max_restarts_per_minute = 5`, `catastrophe_net.pct_below_entry = 0.15`
- `risk.toml` — position limits: `max_position_pct = 0.20`, `max_concurrent_positions = 5`, `paper_capital_usd = 5000`, `kill_switch.max_drawdown_pct = 0.15`, `kill_switch.max_daily_loss_usd = 500`, `ev_minimum = 0.0`, `sizing.half_kelly_fraction = 0.5`
- `crucible.toml` — `time_budget_s = 90`, `max_cycles = 2`, `temperature = 0.1`
- `mutation.toml` — `activation_cycle_count = 50`, `stat_sig.p_threshold = 0.05`, `stat_sig.cohens_d_min = 0.20`, `stat_sig.n_min = 20`, `probation_cycles = 30`, `auto_rollback_window = 50`
- `model_registry.json` — `{"primary": {"backend": "llama_server", "port": 8080}, "secondary": {"backend": "ollama", "port": 11434}, "personas": {}, "candidates": {}}`
- `model_hashes.toml` — placeholder: `gguf_sha256 = "PLACEHOLDER_VERIFY_AFTER_DOWNLOAD"`
- `source_criticality.toml` — per-source criticality + staleness budget hours:
  - CRITICAL: `polygon = { criticality = "CRITICAL", budget_hours = 1 }`, `edgar = { criticality = "CRITICAL", budget_hours = 4 }`, `finnhub = { criticality = "CRITICAL", budget_hours = 1 }`, `alpaca_data = { criticality = "CRITICAL", budget_hours = 1 }`
  - IMPORTANT: `openfda = { criticality = "IMPORTANT", budget_hours = 24 }`, `finra = { criticality = "IMPORTANT", budget_hours = 24 }`, `form4 = { criticality = "IMPORTANT", budget_hours = 24 }`, `ir_pages = { criticality = "IMPORTANT", budget_hours = 48 }`, `ecb = { criticality = "IMPORTANT", budget_hours = 48 }`
  - NICE_TO_HAVE: `fomc = { criticality = "NICE_TO_HAVE", budget_hours = 168 }`, `fred = { criticality = "NICE_TO_HAVE", budget_hours = 168 }`, `fundamentals = { criticality = "NICE_TO_HAVE", budget_hours = 168 }`, `press = { criticality = "NICE_TO_HAVE", budget_hours = 168 }`

**Spec:** Architecture.md §17
**Verifies:** Each file loads as valid TOML/JSON; all values match CLAUDE.md key constants
**Review fix:** Enumerates all default values explicitly (was incomplete in v1)

#### Task 1.3: Config loader
**Files:** `pmacs/config.py`
**What:**
- `load_config()` — loads all config/*.toml + model_registry.json
- Returns typed config objects (Pydantic models or dataclasses)
- Resolves paths relative to project root
- Keychain integration for API keys (deferred to Task 3.4)
- Config reload: `load_config()` always re-reads from disk. No caching. Processes reload on restart (code-versioned) or via TOTP-gated Settings page write (runtime-editable) per Architecture.md §1.15.
**Spec:** Architecture.md §3 (pmacs/config.py), §17 (config files), §1.15 (code-versioned vs runtime-editable)
**Verifies:** Exit test #4 — `python -c "from pmacs.config import load_config; load_config()"` succeeds

#### Task 1.4: Constants
**Files:** `pmacs/constants.py`
**What:**
CI-tested values — do not edit casually. All values from Architecture.md and CLAUDE.md:
- `MAX_POSITION_PCT = 0.20` (20% of portfolio)
- `MAX_CONCURRENT_POSITIONS = 5`
- `CATASTROPHE_NET_PCT = 0.15` (15% below entry)
- `PAPER_CAPITAL_USD = 5000`
- `CRUCIBLE_TIME_BUDGET_S = 90`
- `CRUCIBLE_MAX_CYCLES = 2`
- `CRUCIBLE_TEMPERATURE = 0.1`
- `ANALYSIS_TEMPERATURE = 0.2`
- `MEMO_TEMPERATURE = 0.3`
- `MUTATION_ACTIVATION_CYCLES = 50`
- `MUTATION_STAT_SIG_P = 0.05`
- `MUTATION_STAT_SIG_D = 0.20`
- `MUTATION_STAT_SIG_N = 20`
- `MUTATION_PROBATION_CYCLES = 30`
- `MUTATION_ROLLBACK_WINDOW = 50`
- `MODE_PROMOTION_MIN_CYCLES = 90`
- `MODE_PROMOTION_MIN_TRADES = 200`
- `MODE_PROMOTION_MAX_BRIER = 0.30`
- `MODE_PROMOTION_MIN_SHARPE = 0.0`
- `MODE_PROMOTION_MAX_DRAWDOWN_PCT = 0.15`
- `PROB_SUM_TOLERANCE = 1e-6`
- `BOOTSTRAP_HAIRCUT_FACTOR = 0.5` (conviction tops at ~0.5 during bootstrap per Source.md §4.2)
- `FX_RATE_CONVENTION = "usd_per_eur"` (ECB convention per Architecture.md §16.8)
- Mode names enum (INSTALLING, SHADOW, PAPER, PAPER_VALIDATED, LIVE_EARLY, LIVE_STANDARD, LIVE_EXPANDED)
- `UNINFORMED_3STATE_BRIER = 0.667`
- `WEIGHT_EPSILON = 0.05`

**Spec:** Architecture.md §16, §17; CLAUDE.md (key constants); Architecture.md §9 (arbitration constants)
**Verifies:** `import pmacs.constants` works; values match spec
**Review fix:** Enumerates ALL constants explicitly (was incomplete in v1)

---

### Wave 2: Schemas (depends on Wave 1)

> **Boundary clarification (review fix):** Files in `pmacs/schemas/` contain ONLY Pydantic models (data shapes, validators, enums). Engine logic lives in `pmacs/engines/`. Per Architecture.md §1.2: "ALL Pydantic models in `pmacs/schemas/`. Including engine-internal models. Engines import from schemas. Schemas never import from engines." No circular imports. Schemas import from `pydantic` and stdlib only, never from other `pmacs.*` modules.

#### Task 2.1: Core schemas — contracts, agents, trade
**Files:** `pmacs/schemas/contracts.py`, `pmacs/schemas/agents.py`, `pmacs/schemas/trade.py`, `pmacs/schemas/system.py`
**What:**
- `contracts.py` — `HoldingState` enum (all 24 states from Architecture.md §8.2):
  - Pre-decision: CANDIDATE, PHASE1_RESEARCH, PHASE2_CRUCIBLE, APPROVED_PENDING
  - Active: ACTIVE
  - Aborts: ABORTED_PRE_LLM, ABORTED_LLM, ABORTED_RISK, PHASE1_TIMEOUT
  - Resolutions (terminal): RESOLVED_UP, RESOLVED_FLAT, RESOLVED_DOWN, RESOLVED_MIXED
  - Exits (terminal): STOPPED_OUT, EXIT_THESIS_INVALIDATED, EXIT_OPPORTUNITY_COST, EXIT_TRAILING_STOP, EXIT_FAILED
  - Operational: HALTED, DELISTED, RESOLUTION_TIMEOUT, PANIC_EXIT, INTERRUPTED
  - Non-terminal trigger: THESIS_AGING_REVIEW
  - `Holding` model with state, ticker, thesis, entry/exit dates, abort_reason
  - `Thesis` model
- `agents.py` — `PersonaOutput` base class, `DirectionalProbability` (p_up, p_flat, p_down with sum ≈ 1.0 ± 1e-6), persona name enum
- `trade.py` — `TradePlan` (signed), `TradeResult`, direction enum, order type enum
- `system.py` — `Mode` enum (INSTALLING, SHADOW, PAPER, PAPER_VALIDATED, LIVE_EARLY, LIVE_STANDARD, LIVE_EXPANDED), `KillSwitchState`, mode transition logic
- ALL models use `model_config = ConfigDict(...)`, NOT `class Config:`
- Cross-field validators via `@model_validator(mode="after")`
**Spec:** Architecture.md §8.2 (HoldingState — all 24 values enumerated), §9 (engines), Agents.md §3 (output contracts)
**Verifies:** All models instantiate, cross-field validators fire
**Review fix:** Explicitly enumerates all 24 HoldingState values (was "all 22 states" — wrong count, now verified against spec)

#### Task 2.2: Data schemas — evidence, freshness, currency, catalysts
**Files:** `pmacs/schemas/data.py`, `pmacs/schemas/freshness.py`, `pmacs/schemas/currency.py`, `pmacs/schemas/catalysts.py`
**What:**
- `data.py` — `Evidence`, `EvidencePacket` (with source, ticker, fetched_at, content_hash, data payload)
- `freshness.py` — `FreshnessResult` (fresh/stale/degraded with staleness budget, NO packet mutation — anti-pattern §16.4)
- `currency.py` — `FxRate`, `FxSnapshot` with `usd_per_eur` convention:
  - `@model_validator(mode="after")` that REJECTS any model with `eur_per_usd` field (raises `ValueError: "eur_per_usd field forbidden — use usd_per_eur (anti-pattern §16.8)"`)
  - `business_date: date` (ECB CET publication date)
  - `fetched_at: datetime` (UTC)
- `catalysts.py` — 7 catalyst types from Architecture.md §7.1
**Spec:** Architecture.md §7, §8, §16.4, §16.8
**Verifies:** Models compile; `usd_per_eur` convention enforced by validator; `eur_per_usd` field raises `ValueError`
**Review fix:** Added explicit FX convention rejection test in schema validator (was implicit before)

#### Task 2.3: Engine schemas — arbitration, pricing, sizing, conviction, portfolio, queue
**Files:** `pmacs/schemas/arbitration.py`, `pmacs/schemas/pricing.py`, `pmacs/schemas/sizing.py`, `pmacs/schemas/conviction.py`, `pmacs/schemas/portfolio.py`, `pmacs/schemas/queue.py`
**What:**
These are Pydantic data models ONLY (no engine logic). Engine implementations go in `pmacs/engines/` in later phases.
- `arbitration.py` — `Arbitrated` (combined directional probs), persona weight mappings. Includes constants: `UNINFORMED_3STATE_BRIER = 0.667`, `WEIGHT_EPSILON = 0.05`
- `pricing.py` — EV computation input/output models
- `sizing.py` — position size model with half-Kelly, bootstrap haircut, limited-history haircut. Reference: `BOOTSTRAP_HAIRCUT` from Architecture.md §9 (sizing engine)
- `conviction.py` — Conviction scalar model, verdict tiers (STRONG_BUY ≥ 0.6, BUY ≥ 0.3, SKIP < 0.3). **Note:** The formula implementation is in `pmacs/engines/conviction.py` (Architecture.md §9, `compute_conviction()`). This schema file is the data shape only.
- `portfolio.py` — portfolio state, position tracking, sector limits
- `queue.py` — queue item, priority band enum
**Spec:** Architecture.md §9 (engine schemas), §1.2 (all Pydantic models in schemas/), Source.md §7.2 (conviction tiers)
**Verifies:** Models compile; conviction thresholds match spec
**Review fix:** Clarifies schema/engine boundary explicitly. Cites Architecture.md §9 for conviction formula source.

#### Task 2.4: Remaining schemas — calibration, lessons, attribution, overrides, flywheel, failure, mutation, memory, stop_loss, reconciliation, sim, fundamental
**Files:** `pmacs/schemas/calibration.py`, `pmacs/schemas/lessons.py`, `pmacs/schemas/attribution.py`, `pmacs/schemas/overrides.py`, `pmacs/schemas/flywheel.py`, `pmacs/schemas/failure.py`, `pmacs/schemas/mutation.py`, `pmacs/schemas/memory.py`, `pmacs/schemas/stop_loss.py`, `pmacs/schemas/reconciliation.py`, `pmacs/schemas/sim.py`, `pmacs/schemas/fundamental.py`
**What:**
- All remaining Pydantic models from Architecture.md §8, §9
- Models are complete even though engines don't exist yet (spec requirement)
- `failure.py` — FailedAssumption, FailureClassification (taxonomy from Agents.md §15)
- `mutation.py` — MutationCandidate, MutationOutcome, MutationProposal
- `sim.py` — paper ledger models
- `stop_loss.py` — StopTrigger
- `flywheel.py` — FlywheelHealth snapshot
**Spec:** Architecture.md §8, §9, §10, Agents.md §15, §17
**Verifies:** ALL models in pmacs/schemas/ compile; `pytest tests/unit/test_schemas.py` passes

#### Task 2.5: Schema compilation test
**Files:** `tests/unit/test_schemas.py`
**What:**
- Import every model from pmacs/schemas/
- Instantiate each with valid data
- Test cross-field validators:
  - DirectionalProbability sum ≈ 1.0 ± 1e-6 (valid and invalid)
  - FxRate convention: `usd_per_eur` accepted, `eur_per_usd` field raises `ValueError` (**NEW — review feedback**)
  - HoldingState transitions tested via state machine (Task 4.2)
  - Conviction thresholds: STRONG_BUY ≥ 0.6, BUY ≥ 0.3, SKIP < 0.3
- Verify no `pydantic.v1` imports anywhere in schemas/
- Verify no schema imports from `pmacs.engines` or other `pmacs.*` modules (**NEW — review feedback**)
**Spec:** Architecture.md §1.1 (Pydantic v2 only), §1.2 (schemas never import from engines)
**Verifies:** Phase 1 exit test #1
**Review fix:** Added FX rejection test, import boundary test

#### Task 2.6: Property tests for probabilistic invariants (**NEW — review feedback**)
**Files:** `tests/property/test_probabilities.py`
**What:**
Property-based tests using Hypothesis for core math invariants:
- **DirectionalProbability**: For any valid (p_up, p_flat, p_down) triple, sum is in [0, 1]. Models reject p < 0 or p > 1. Models reject sum outside 1.0 ± 1e-6.
- **Arbitration weight normalization**: For any positive weight assignments, normalized weights sum to 1.0 and each is in [0, 1].
- **Conviction monotonicity**: Higher directional probability → conviction ≥ lower directional probability (all else equal). Verdict tiers match thresholds.
- **FX round-trip**: For any positive amount and valid rate, `usd_to_eur(eur_to_usd(amount, snap), snap) ≈ amount` within float tolerance.
- **Staleness budget**: For any EvidencePacket with any fetched_at timestamp, freshness check produces a valid FreshnessResult (fresh/stale/degraded) and never mutates the packet.
**Spec:** Architecture.md §9 (arbitration, conviction, sizing), §16.4 (no packet mutation), §16.8 (FX convention)
**Verifies:** Phase 1 exit test #6 (**NEW**)
**Review fix:** Addresses critical gap — trading system core math was untested

---

### Wave 3: Canonical JSON + Storage (depends on Wave 2)

#### Task 3.1: Canonical JSON serialization
**Files:** `pmacs/data/canonical.py`
**What:**
- `canonical_json(payload: dict) -> str` — deterministic serialization
- `sort_keys=True`, `separators=(",",":")`, `allow_nan=False`
- Float rounding to 10 decimal places
- datetime/date → ISO format
- Enum → value
- NaN/Inf → ValueError
- Exact implementation from Architecture.md §5.1
**Spec:** Architecture.md §5.1
**Verifies:** Same dict always produces same string; floats rounded; NaN rejected

#### Task 3.2: Audit log — hash-chained writer + verifier
**Files:** `pmacs/storage/audit.py`, `tests/unit/test_audit_chain.py`
**What:**
- `AuditWriter` — append-only, hash-chained writer
- Genesis: `prev_sha256 = "0" * 64`
- Hash: `sha256(iso_ts || prev_sha256 || event_type || canonical_json(payload))`
- `fsync` after every write
- `AuditVerifier` — scan full chain or incremental (last N + random)
- Event types from Architecture.md §5.2 registry
- All events require `cycle_id` except system events (kill switch, mode changes, audit chain verifications). Per Architecture.md §5.2: `cycle_id: str | None # only None for pre-cycle bootstrap events`
- **Idempotency decorator** `with_idempotency(cycle_id, op_seq, op_type)`: If (cycle_id, op_seq, op_type) already in `op_idempotency`, return cached result. Per Architecture.md §1.11. (**NEW — review feedback**)
**Spec:** Architecture.md §5.1, §5.2, §5.3, §1.11 (idempotency)
**Verifies:** Phase 1 exit test #2 — genesis → 100 appends → verify; tamper → catch; idempotency dedup works
**Review fix:** Added idempotency decorator from spec §1.11 (was missing)

#### Task 3.3: SQLite initialization
**Files:** `pmacs/storage/sqlite.py`
**What:**
- Initialize all tables from Architecture.md §8.5:
  - `cycles`, `mode_history`, `queue`, `persistent_pins`
  - `stop_events`, `process_state`, `paper_account`, `fx_snapshots`
  - `consistency_drift`, `operator_overrides`, `dead_letter`
  - `mutation_proposals`, `mutation_outcomes` (schema exists, populated in Phase 14)
  - `op_idempotency` (for cycle resume)
  - `holdings` table (key fields matching KuzuDB projection)
- All indexes from spec
- Idempotent init (CREATE TABLE IF NOT EXISTS)
**Spec:** Architecture.md §8.5
**Verifies:** `init_db()` succeeds on fresh path; all tables exist; indexes present

#### Task 3.4: Keychain wrapper with error recovery (**STRENGTHENED — review feedback**)
**Files:** `pmacs/storage/keychain.py`, `tests/unit/test_keychain.py`
**What:**
- macOS Keychain access via `security` CLI or `keyring` library
- `get_api_key(service: str, account: str) -> str`
- `set_api_key(service: str, account: str, key: str)`
- Raise `KeychainError` on missing key (not return None)
- Never log API keys (anti-pattern §16)
- **Error recovery (NEW):**
  - On `KeychainError` at startup (missing/corrupted): log CRITICAL with canonical error code `KEYCHAIN_UNAVAILABLE`, return structured error to caller. Caller (config loader or process init) decides: boot with degraded data sources (no API-keyed sources) or halt.
  - On `KeychainError` at runtime: log CRITICAL + error code `KEYCHAIN_RUNTIME_FAILURE`. If in active cycle, abort cycle gracefully. Do NOT engage kill switch (Keychain is recoverable, not a trading emergency).
- **Secret scrubber (NEW):** `_scrub_secrets(message: str, secrets: list[str]) -> str` called before any log output from keychain module. Removes any substring matching a secret value. Applied to exception messages, debug output, and error payloads.
- **API key rotation:** `rotate_api_key(service, account, old_key, new_key) -> None`. Validates old_key matches current, then sets new. Logs audit event `key_rotated` (service name only, never key value). Per Architecture.md §1.3 service convention: `pmacs.<category>.<key>`.
**Spec:** Architecture.md §18 (security model), §1.3 (Keychain convention), §16 (no logging secrets)
**Verifies:** Round-trip set/get works; missing key raises `KeychainError`; secrets never appear in log output; rotation works
**Review fix:** Added error recovery spec, secret scrubber, rotation method. Was just a thin wrapper before.

---

### Wave 4: Logging + State Machine + Pre-commit + Integration (depends on Wave 3)

#### Task 4.1: Debug logging system
**Files:** `pmacs/logsys/logger.py`, `pmacs/logsys/debug_log.py`, `pmacs/logsys/error_classifier.py`
**What:**
- `log_debug(event_type, payload, level, error_code, cycle_id)` — structured JSONL debug log
- Levels: DEBUG, INFO, WARN, ERROR
- Every WARN+ requires `error_code` from Architecture.md §5.5 registry
- `error_classifier.py` — maps known error conditions to canonical codes
- Error code registry: STALE_DATA, FX_RATE_UNAVAILABLE, GBNF_PARSE_FAIL, LLM_TIMEOUT, KEYCHAIN_UNAVAILABLE, KEYCHAIN_RUNTIME_FAILURE (**NEW**), BOOT_CYCLE_SKIPPED, RATE_LIMIT_EXCEEDED, etc.
- `cycle_id` required on all cycle-scoped events. Only `None` for pre-cycle bootstrap events (per Architecture.md §5.2).
- **Runtime enforcement (NEW):** `log_debug` at WARN+ without `error_code` raises `ValueError`. `log_debug` for cycle-scoped events without `cycle_id` raises `ValueError` (unless event type is in `SYSTEM_EVENT_TYPES` whitelist).
**Spec:** Architecture.md §5, §5.5, §16.14
**Verifies:** log_debug with missing error_code at WARN+ raises; codes are valid; missing cycle_id for non-system events raises
**Review fix:** Added runtime enforcement for cycle_id and error_code (was heuristic grep only)

#### Task 4.2: Holding state machine
**Files:** `pmacs/engines/state_machine.py`, `tests/unit/test_state_machine.py`
**What:**
- `VALID_TRANSITIONS` dict mapping each state to its valid successors (from Architecture.md §8.2)
- `TERMINAL_STATES` frozenset (RESOLVED_UP, RESOLVED_FLAT, RESOLVED_DOWN, RESOLVED_MIXED, STOPPED_OUT, EXIT_THESIS_INVALIDATED, EXIT_OPPORTUNITY_COST, EXIT_TRAILING_STOP, EXIT_FAILED, DELISTED, PANIC_EXIT, RESOLUTION_TIMEOUT)
- `transition(holding, new_state, reason, cycle_id, op_seq)` — the ONE place state changes
- Raises `InvalidStateTransition` on invalid transitions
- Terminal state guard (no transitions from terminal)
- Idempotency via `op_idempotency` check (Architecture.md §1.11)
- Audit write on every transition
- Abort reason capture for ABORTED_* states
- Exit date auto-fill for terminal states
- **Test coverage expansion (NEW — review feedback):**
  - Every valid transition succeeds
  - Every invalid transition raises `InvalidStateTransition`
  - Terminal state guard: transition from any terminal state raises
  - `THESIS_AGING_REVIEW` non-terminal transition works (→ ACTIVE back)
  - Idempotency: same (cycle_id, op_seq, "transition") is a no-op on replay
  - Audit event emitted on transition (verified by test, not just assumed)
**Spec:** Architecture.md §8.2, §16.1 (no direct mutation), §1.11 (idempotency)
**Verifies:** Phase 1 exit test #3 — every valid/invalid transition; terminal guard; idempotency
**Review fix:** Expanded test coverage for terminal states, THESIS_AGING_REVIEW, idempotency, and audit emission verification

#### Task 4.3: Pre-commit hooks
**Files:** `.pre-commit-config.yaml`
**What:**
- Anti-pattern grep hooks from Architecture.md §16 and Phases.md §1.4:
  - No `holding.state =` outside `state_machine.py`
  - No `json.dumps` on audit payloads (must use `canonical_json`)
  - No secrets in log output
  - No `pydantic.v1` imports
  - `cycle_id` required on audit-emitting functions (heuristic grep)
  - Every `log_debug(level >= "WARN")` has `error_code`
  - No `eur_per_usd` field (must use `usd_per_eur`)
  - No `from pmacs.engines import` inside `pmacs/schemas/` (**NEW — review feedback**)
- Standard hooks: trailing-whitespace, end-of-file-fixer, check-yaml, check-json
**Spec:** Architecture.md §16, Phases.md §1.4, Architecture.md §1.2 (schema/engine boundary)
**Verifies:** Phase 1 exit test #5 — all anti-pattern checks pass
**Review fix:** Added import boundary check for schemas/

#### Task 4.4: Audit writing integration test (**NEW — review feedback**)
**Files:** `tests/integration/test_audit_writing.py`
**What:**
Integration test verifying the full audit pipeline:
- `state_machine.transition()` actually writes to audit log file
- Audit event has correct structure: `event_type = "holding_state_transition"`, valid `cycle_id`, `op_seq`, `canonical_json` payload
- Hash chain is valid after state transitions
- Idempotent replay does NOT produce duplicate audit entries
- Audit verifier catches tampered state transition records
**Spec:** Architecture.md §5.1, §5.2, §8.2, §1.11
**Verifies:** Phase 1 exit test #7 (**NEW**)
**Review fix:** Addresses critical gap — no test verified audit writes actually happened

---

### Wave 5: Data layer — core (depends on Wave 4)

#### Task 5.1: Rate-limited HTTP gateway
**Files:** `pmacs/data/gateway.py`
**What:**
- TokenBucket rate limiter per source
- `httpx.Client` with configurable timeouts
- Per-source rate limits from config
- Retry with backoff on 429/5xx
- User-Agent header
- Response validation (status code + content-type)
- **TokenBucket edge case tests (NEW — review feedback):**
  - Verify token count never goes negative
  - Verify burst behavior matches config
  - Verify refill rate over time is correct
**Spec:** Architecture.md §6 (data layer)
**Verifies:** 20 rapid calls don't hit 429; timeout raises gracefully; bucket math correct

#### Task 5.2: Staleness checker
**Files:** `pmacs/data/staleness.py`, `tests/unit/test_staleness.py`
**What:**
- `check_freshness(packet: EvidencePacket, budget: StalenessBudget) -> FreshnessResult`
- Returns FreshnessResult (fresh/stale/degraded), does NOT mutate the packet (anti-pattern §16.4)
- CRITICAL sources: stale → raise error (abort ticker)
- IMPORTANT sources: stale → degrade (proceed with warning)
- NICE_TO_HAVE sources: stale → proceed silently
- Budgets from `config/source_criticality.toml`
- **Degradation behavior tests (NEW — review feedback):**
  - CRITICAL source stale → raises with error code `STALE_DATA`
  - IMPORTANT source stale → returns degraded FreshnessResult
  - NICE_TO_HAVE source stale → returns stale FreshnessResult (no exception)
  - Fresh source always returns `fresh` regardless of criticality
**Spec:** Architecture.md §16.4 (no packet mutation), source_criticality.toml
**Verifies:** Phase 2 exit test #1
**Review fix:** Added per-criticality degradation behavior tests

#### Task 5.3: FX handler
**Files:** `pmacs/data/fx.py`, `tests/unit/test_fx.py`
**What:**
- ECB EUR/USD feed
- `usd_per_eur` convention (NOT `eur_per_usd` — anti-pattern §16.8)
- `FxSnapshot` with business_date (ECB CET) and fetched_at (UTC)
- `usd_to_eur(amount, snap)` and `eur_to_usd(amount, snap)` helpers
- Round-trip identity: `usd_to_eur(eur_to_usd(x, snap), snap) ≈ x`
- Store in SQLite `fx_snapshots` table
- **ECB staleness handling (NEW — review feedback):**
  - ECB publishes on CET business days only. Weekends and ECB holidays have no publication.
  - Staleness detection: compare `business_date` to last known ECB publication date (not raw `fetched_at`). A rate from Friday is still fresh on Saturday/Sunday.
  - If ECB feed is unreachable: log `FX_RATE_UNAVAILABLE` error code. Use last cached rate from SQLite. If no cached rate exists and system is in PAPER mode: abort cycle (FX is CRITICAL for EUR-denominated positions). If system has no EUR exposure: proceed with warning.
  - Weekend/holiday handling: if today is not an ECB business day, use most recent business day's rate. No staleness warning for weekend/holiday gaps.
**Spec:** Architecture.md §16.8, §8.5 (fx_snapshots table), §6 (ECB data tier)
**Verifies:** Phase 2 exit test #2 — round-trip + convention + staleness
**Review fix:** Addresses critical gap — ECB staleness detection and weekend/holiday handling was unspecified

#### Task 5.4: Corporate actions handler
**Files:** `pmacs/data/corp_actions.py`
**What:**
- Split, dividend, merger detection and adjustment
- Price adjustment for splits
- Cost basis adjustment for dividends
- Merger symbol mapping
**Spec:** Architecture.md §6
**Verifies:** Split adjustment produces correct adjusted price

#### Task 5.5: Universe management
**Files:** `pmacs/data/universe.py`
**What:**
- Operator-curated ticker universe CRUD
- Ticker flags: halted, delisted, sector
- Universe diff logging (audit event `universe_diff`)
- Persist to SQLite
**Spec:** Source.md §8 (universe philosophy)
**Verifies:** Add/remove ticker persists; diff audit event emitted

---

### Wave 6: Data sources (depends on Wave 5)

#### Task 6.1: Core data sources (CRITICAL priority)
**Files:** `pmacs/data/sources/edgar.py`, `pmacs/data/sources/polygon.py`, `pmacs/data/sources/finnhub.py`, `pmacs/data/sources/alpaca_data.py`
**What:**
- Each source: fetch method → returns `EvidencePacket`
- Rate-limited via gateway TokenBucket
- Source-specific parsing (SEC filings, market data, etc.)
- Error handling with canonical error codes
**Spec:** Architecture.md §6, source_criticality.toml (CRITICAL)
**Verifies:** Each returns valid EvidencePacket

#### Task 6.2: Important data sources
**Files:** `pmacs/data/sources/openfda.py`, `pmacs/data/sources/finra.py`, `pmacs/data/sources/form4.py`, `pmacs/data/sources/ir_pages.py`, `pmacs/data/sources/press.py`
**What:**
- Same pattern as Task 6.1
- IMPORTANT criticality → degrade gracefully on stale data
**Spec:** Architecture.md §6, source_criticality.toml (IMPORTANT)
**Verifies:** Each returns valid EvidencePacket or graceful degradation

#### Task 6.3: Supplementary data sources
**Files:** `pmacs/data/sources/fomc.py`, `pmacs/data/sources/fred.py`, `pmacs/data/sources/ecb.py`, `pmacs/data/sources/fundamentals.py`
**What:**
- Same pattern as Task 6.1
- NICE_TO_HAVE criticality → proceed silently on stale
**Spec:** Architecture.md §6, source_criticality.toml (NICE_TO_HAVE)
**Verifies:** Each returns valid EvidencePacket or silent degradation

#### Task 6.4: Data integration test
**Files:** `tests/integration/test_data_sources.py`
**What:**
- Each source fetches one real data point
- Validates EvidencePacket structure
- Counts valid vs degraded
- Requires ≥ 10/13 sources returning valid packets
**Spec:** Phases.md §2 (Phase 2 exit test #3)
**Verifies:** Phase 2 exit test #3

---

## Dependency Graph

```
Wave 1 (no deps)     Wave 2 (→ W1)       Wave 3 (→ W2)       Wave 4 (→ W3)       Wave 5 (→ W4)       Wave 6 (→ W5)
┌─────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ 1.1 Scaffold│────▶│ 2.1 Core     │────▶│ 3.1 Canonical│────▶│ 4.1 DebugLog │────▶│ 5.1 Gateway  │────▶│ 6.1 Critical │
│ 1.2 Config  │     │    schemas   │     │ 3.2 Audit    │     │ 4.2 StateMach│     │ 5.2 Staleness│     │    sources   │
│ 1.3 Loader  │     │ 2.2 Data     │     │ 3.3 SQLite   │     │ 4.3 Precommit│     │ 5.3 FX       │     │ 6.2 Important│
│ 1.4 Constan │     │    schemas   │     │ 3.4 Keychain │     │ 4.4 AuditInt │     │ 5.4 CorpActs │     │    sources   │
└─────────────┘     │ 2.3 Engine   │     └──────────────┘     └──────────────┘     │ 5.5 Universe │     │ 6.3 Supplem. │
                    │    schemas   │                                                └──────────────┘     │    sources   │
                    │ 2.4 Remaining│                                                                     │ 6.4 Integ    │
                    │ 2.5 Schema   │                                                                     │    test      │
                    │    test      │                                                                     └──────────────┘
                    │ 2.6 Property │
                    │    tests NEW │
                    └──────────────┘
```

## Risk Considerations

- **Canonical JSON determinism:** Must be verified cross-platform. Float rounding to 10 decimals is critical for hash chain integrity.
- **Schema completeness:** ALL schemas must compile even for engines not yet built. This is explicit in the spec.
- **Anti-pattern enforcement:** Pre-commit hooks + runtime enforcement from Day 1. No exceptions.
- **API key management:** Keychain integration must never log secrets. Secret scrubber applied to all outputs.
- **Data source reliability:** Integration tests need real API keys. 3/13 can fail (NICE_TO_HAVE).
- **ECB FX staleness:** Weekend/holiday handling must not trigger false staleness warnings. Use business_date, not fetched_at.
- **Keychain error recovery:** Startup failure is degraded mode (no API-keyed sources), not system halt. Runtime failure aborts cycle, not system.
- **Property test coverage:** Core probabilistic math (arbitration, conviction, FX) tested via Hypothesis invariants, not just unit tests.
- **Bootstrap haircut:** Conviction tops at ~0.5 during bootstrap (Source.md §4.2). `PROCEED_BOOTSTRAP_LOW_CONFIDENCE` path exists for Day 1. Not fully exercised until Phase 4 (pipeline) but constants and schemas are in place.

## Review Feedback Applied

This plan was revised based on REVIEWS.md (Claude Sonnet 4.6 review, overall 3.2/5). Changes:

| # | Issue | Severity | Fix Applied |
|---|---|---|---|
| 1 | Keychain error recovery unspecified | HIGH | Task 3.4: added error recovery spec, degraded mode, secret scrubber, rotation |
| 2 | Conviction formula source unclear | HIGH | Task 2.3: explicitly cites Architecture.md §9 `compute_conviction()` as engine, schema is data shape |
| 3 | No property tests for probabilistic invariants | HIGH | Task 2.6: new property test suite with Hypothesis |
| 4 | FX ECB staleness detection unspecified | HIGH | Task 5.3: added weekend/holiday/business_date handling, feed-down fallback |
| 5 | Anti-pattern runtime enforcement incomplete | HIGH | Task 4.1: runtime enforcement for cycle_id/error_code. Task 3.2: idempotency decorator |
| 6 | Schema vs engine boundary ambiguous | HIGH | Wave 2 header: explicit boundary clarification. Task 2.3: schema is models only |
| 7 | Config file contents not enumerated | HIGH | Task 1.2: full default values for all 7 config files. Task 1.4: all constants listed |
| 8 | Missing edge case tests | MED | Task 4.2: expanded test coverage for terminals, idempotency, THESIS_AGING_REVIEW |
| 9 | Bootstrap kill switch logic unspecified | MED | Risk Considerations: documented `PROCEED_BOOTSTRAP_LOW_CONFIDENCE` path |
| 10 | API key rotation workflow missing | MED | Task 3.4: added `rotate_api_key()` method |
| 11 | Audit writing integration test missing | MED | Task 4.4: new integration test verifying audit pipeline |
| 12 | Structured logging secret exposure | MED | Task 3.4: secret scrubber applied before all log output |
| 13 | Backup verification not in Phase 1 | MED | Deferred to Phase 2 (audit log backup requires nervous process for rotation) |
| — | HoldingState count wrong (22 vs 24) | FIX | Task 2.1: corrected to 24 states, all enumerated from spec |

## Spec References

| Topic | File | Section |
|---|---|---|
| Repo tree | Architecture.md | §3 |
| Storage schemas | Architecture.md | §8 |
| Audit log format | Architecture.md | §5.1, §5.2 |
| Canonical JSON | Architecture.md | §5.1 |
| Holding state machine | Architecture.md | §8.2 |
| Anti-patterns | Architecture.md | §16 |
| Config files | Architecture.md | §17 |
| Error codes | Architecture.md | §5.5 |
| Pydantic v2 rules | Architecture.md | §1.1 |
| Schema/engine boundary | Architecture.md | §1.2 |
| Idempotency | Architecture.md | §1.11 |
| Keychain convention | Architecture.md | §1.3, §18 |
| Conviction formula | Architecture.md | §9 (engines/conviction.py) |
| Bootstrap haircut | Source.md | §4.2 |
| FX convention | Architecture.md | §16.8 |
| Exit tests | Phases.md | §2 (Phase 1, Phase 2) |
