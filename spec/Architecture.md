# PMACS — Architecture

**File 2 of 4. Build-level specification: processes, IPC, storage, engines, cycle orchestration, kill switch, ops.**

> Companion files: `Source.md` (vision and operator surface), `Agents.md` (LLM personas, prompts, structured-output contracts, FDE taxonomy, Crucible loop, Mutation Engine reasoning), `Phases.md` (build sequence and mode promotion gates).
>
> **Reading order for Claude Code:** Read `Source.md` first to understand *what* and *why*. Read this file second to understand *how*. Read `Agents.md` when you touch any LLM-producing code path. Read `Phases.md` to know what to build next.
>
> **If anything contradicts:** This file wins for *implementation specifics.* `Source.md` wins for *vision and operator-facing behavior.* `Agents.md` wins for *LLM contracts.* `Phases.md` wins for *build sequence.*
>
> **Section anchors are stable.** Other files cite this file as `Architecture.md §<n>`.

---

## Table of contents

```
0.   Cross-reference index
1.   Critical preconditions for Claude Code (immutable rules)
2.   The 7-layer architecture
3.   Repo tree
4.   Process topology and IPC
5.   Logging — two parallel streams (audit + debug)
6.   Data layer
7.   Catalyst resolution subsystem
8.   Storage (5 stores)
9.   Deterministic engines
10.  Mutation Engine (process)
11.  StopLossMonitor (process)
12.  Cycle orchestration (canonical sequence)
13.  Kill switch
14.  Cross-DB consistency and dead letter
15.  Memory hierarchy
16.  Anti-patterns
17.  Configuration files
18.  Security model
19.  Testing strategy
20.  Performance budget
21.  Architectural Decision Records (ADRs)
22.  Connection to companion files
```

---

## 0. Cross-reference index

When this file references something defined elsewhere, the pointer is explicit.

| Concept | Lives in | Section |
|---|---|---|
| Vision and operator surface | `Source.md` | §1-§22 |
| Trust contract and non-negotiables | `Source.md` | §4, §5 |
| Decision rights matrix | `Source.md` | §6 |
| Mode ladder (semantics) | `Source.md` | §9 |
| Operator workflows | `Source.md` | §21 |
| UI page specifications | `Source.md` | §14-§20 |
| Conviction formula | `Source.md` | §7.2 (operator), §9.2 (impl) |
| Per-persona prompts and contracts | `Agents.md` | §4-§13 |
| The 18 outcome + 5 reasoning-flaw FDE taxonomy types | `Agents.md` | §15 |
| Crucible adversarial loop (inner state machine) | `Agents.md` | §16 |
| Mutation candidate generation rules (deterministic) | `Agents.md` | §17 |
| Episodic context injection (prompt-level mechanics) | `Agents.md` | §18 |
| Build phases (numbered, with exit tests) | `Phases.md` | §2 |
| Mode promotion and demotion gates (numerical) | `Phases.md` | §3 |
| File-by-file build dependency graph | `Phases.md` | §4 |

---

## 1. Critical preconditions for Claude Code

Non-negotiable. Every PR verifies these. CI fails if any is violated.

### 1.1 Pydantic v2 only

```python
from pydantic import BaseModel, ConfigDict, model_validator, field_validator
```

- `model_config = ConfigDict(...)`, NOT `class Config:`
- Cross-field validation: `@model_validator(mode="after")`, NOT field-by-field with `info.data`
- Use `model_validate()` / `model_dump()`, NOT `parse_obj()` / `dict()`
- Forbid `from pydantic.v1 import` anywhere in the codebase

### 1.2 ALL Pydantic models in `pmacs/schemas/`

Including engine-internal models. Engines import: `from pmacs.schemas.arbitration import DirectionalProbability, Arbitrated`. Schemas never import from engines. This rule prevents circular imports and centralizes validation.

### 1.3 macOS Keychain for ALL secrets

Service names: `pmacs.<category>.<key>`. Read via `pmacs/storage/keychain.py`. Never environment variables. Never config files. Never the repository. Never logged. Never serialized.

Implements `Source.md §4` promise 2 (local-only) and `Source.md §6` decision rights for credentials.

### 1.4 Process isolation is structural, not procedural

- `pmacs-execution` is the ONLY process that imports broker SDK code.
- `pmacs-inference` (llama-server) has zero internet egress (enforced by `pf` rules in `ops/install_pf_rules.sh`).
- `pmacs-dashboard` has read-only DB access; writes go through `pmacs-nervous` via authenticated POST.
- `pmacs-mutation` reads from storage; writes only to its own scoped tables (`mutation_*`).
- Verified at runtime by `pmacs cortex audit-isolation` (cron'd hourly).

This implements `Source.md §5` non-negotiable 1 (LLMs never sign trades).

### 1.5 Audit log: append-only, hash-chained, deterministic JSON

```
this_sha256 = sha256(iso_ts || prev_sha256 || event_type || canonical_json(payload))
```

- **Timezone convention:** All timestamps in PMACS are **UTC internally.** Display conversion to operator's timezone (configurable, default US/Eastern) happens only in the web layer. Audit log, debug log, SQLite, DuckDB, KuzuDB — all UTC. FX snapshots store `business_date` which is the ECB publication date (CET-based) but the `fetched_at` is UTC.

Genesis: `prev_sha256 = "0" * 64`
- Canonical JSON per §5.1 with explicit float rounding (10 decimal places)
- `fsync` after every write
- Cortex verifies chain on startup (full scan) and hourly (incremental: last 1000 entries + random 100 from history). Full scan weekly. Break → kill switch immediately.

Implements `Source.md §4` promise 1 (every decision auditable in deterministic detail).

### 1.6 LLMs never decide, never math, never sign

- LLMs produce structured outputs (GBNF for llama-server, JSON Schema for Ollama).
- Deterministic Python computes EV, sizing, calibration, arbitration, conviction.
- Trade signals are Ed25519-signed by the math process (`pmacs-execution`).
- An LLM cannot directly cause a trade.

Implements `Source.md §5` non-negotiables 1 and 2.

### 1.7 Structured output + sanity validator always paired

Every LLM-producing persona ships with three layers in this exact order:

1. **Grammar layer** — GBNF (llama-server) or JSON Schema (Ollama)
2. **Pydantic layer** — model_validate enforces shape
3. **Sanity validator** — `pmacs/agents/<persona>/sanity.py` enforces semantics (e.g., probabilities sum to 1.0 ± 1e-6, evidence_ids reference real evidence, citations resolve)

Structure catches form. Sanity catches semantics. Both must pass. See `Agents.md §3` for the contract.

### 1.8 Every cycle event hits BOTH audit log AND debug stream

- Audit: immutable, hash-chained, regulatory record.
- Debug: structured `DebugEvent` JSONL, Claude-Code-readable, 30-day retention.

The two streams have different consumers. Audit is for forensics and regulatory. Debug is for development and operator inspection.

### 1.9 No backtesting against historical LLM outputs

The model's training data already contains the future of any backtest period. SHADOW Mode is the only valid forward-test. CI fails on PRs that introduce backtest-against-historical-LLM-output code.

### 1.10 Flywheel health gates capacity expansion

- Empirical gates (Brier, Sharpe, n) gate mode promotions (`Phases.md §3`).
- FlywheelHealth gates LIVE *capacity* expansion within a mode.
- Mutation Engine gates its own promotions via stat-sig + effect size.

### 1.11 Two-process safety on every state mutation

Every Holding state transition AND every cycle operation uses `(cycle_id, op_seq)` idempotency keys. `cycle_id` is REQUIRED on every audit-emitting operation. Replays are no-ops.

```python
def with_idempotency(cycle_id: str, op_seq: int, op_type: str):
    """Decorator pattern. If (cycle_id, op_seq, op_type) is already in op_idempotency, return cached result."""
```

### 1.12 State transitions go through the validator

Holding state transitions use `pmacs.engines.state_machine.transition(holding, new_state, reason, cycle_id, op_seq)`. **Direct mutation of `holding.state` is forbidden.** Invalid transitions raise `InvalidStateTransition`. CI grep-fails on `holding.state =` outside `state_machine.py`.

### 1.13 Mutation Engine never writes production state directly

`pmacs-mutation` proposes only. Promotions are explicit writes by `pmacs-nervous` triggered by:
- Operator approval (all mutations require explicit operator confirmation)
- Operator action via Settings → Mutation Engine

This is structural, not procedural. The mutation process has no write access to `model_registry.json`, prompts, or thresholds. It writes only to `mutation_proposals` and `mutation_outcomes`.

### 1.14 Configuration is two-tier: code-versioned and runtime-editable

- **Code-versioned** (requires git commit + restart): arbitration formula, conviction formula, audit log format, DB schemas, anti-pattern thresholds, the 18+5 failure taxonomy types, cycle order sequence.
- **Runtime-editable** (Settings page, often operator-confirmed; writes use flock-based file locking to prevent concurrent write corruption): risk thresholds, Crucible time budget, mutation enable/disable, persona enable/disable, queue priorities, broker credentials.

CI grep-fails on attempts to load code-versioned values from `Settings.read()`.

### 1.15 SSE event stream is the only realtime UI channel

`pmacs-dashboard` does NOT poll DBs at fast intervals. It opens an SSE connection to `pmacs-nervous` `/events` and renders updates as they stream. This is structural to keep the dashboard read-only and the nervous-system the single writer. See §4.3.

---

## 2. The 7-layer architecture

```
+----------------------------------------------------------+
| L7  Web App (FastAPI + HTMX + Tailwind + SSE)            |
|     Implements Source.md §13-§20 (UI pages)              |
+----------------------------------------------------------+
| L6  Cortex (deterministic monitor + kill switch +        |
|             FlywheelHealth + StopLossMonitor + boot      |
|             detector + crash-loop detector)              |
+----------------------------------------------------------+
| L5  Nervous System (orchestrator + opportunity-cost +    |
|             override-learning + reconciliation +         |
|             SSE publisher)                               |
+----------------------------------------------------------+
| L4  Agents (LLM personas, siloed, structured-output)     |
|     Specified in Agents.md §4-§13                        |
+----------------------------------------------------------+
| L3  Engines (Arb / Pricing / Sizing / Calibration /      |
|             Memory / Queue / Lessons / OpportunityCost / |
|             FundamentalRouting / PortfolioRiskGate /     |
|             Reconciliation / Causal / Override /         |
|             CrucibleCalibration / FlywheelHealth /       |
|             StateMachine / FailureDiagnostic /           |
|             Conviction / Mutation)                        |
+----------------------------------------------------------+
| L2  Storage (KuzuDB + Qdrant + DuckDB + SQLite + Audit)  |
+----------------------------------------------------------+
| L1  Data + Runtime (data feeds, llama-server, paper sim) |
+----------------------------------------------------------+

      +---------------------------------------------+
      |  Execution Service (isolated process)        |
      |  Routes: Sim | Alpaca paper | IBKR live      |
      +---------------------------------------------+

      +---------------------------------------------+
      |  Mutation Service (isolated process)         |
      |  Reads storage, writes mutation_* tables     |
      +---------------------------------------------+
```

### 2.1 Data flow rules (allowed paths)

- **L7 → L2 storage** (read-only via `pmacs-dashboard`)
- **L7 → L5 nervous** (write actions, operator-confirmed POST)
- **L7 ← L5 nervous** via SSE (real-time event stream, see §4.3)
- **L5 → L4 agents** (LLM call orchestration)
- **L5 → L3 engines** (deterministic computation)
- **L5 → L2 storage** (read/write app tables)
- **L5 → execution service** (signed TradePlan via UDS)
- **L6 → all** (monitoring; kill-switch state writes)
- **L4 → L1** (LLM calls to llama-server)
- **L4 → L2 storage** (read evidence, write outputs)
- **L3 → L2 storage** (read/write engine-specific tables)
- **mutation service → L2 storage** (read; write only to `mutation_*` tables)
- **execution service → broker** (only path)

Any other data flow is forbidden. Verified by `pmacs cortex audit-isolation`.

### 2.2 ADR: Why dashboard process stays separate

Reviewers periodically ask why `pmacs-dashboard` isn't merged into `pmacs-nervous` since both are FastAPI servers. The answer: **attack surface isolation**. The dashboard runs HTMX/SSE endpoints the operator's browser hits constantly. Nervous has the orchestration logic and SQLite write access. A vulnerability in dashboard HTTP handling (XSS, CSRF, header injection, debugger leak) cannot escalate to write access because the dashboard process literally cannot write — its SQLite connection is opened with `mode=ro` and its filesystem permissions deny write. This defense-in-depth is intentional. Cost: one extra launchd plist.

See ADR-001 in §21.

---

## 3. Repo tree

```
pmacs/
├── pyproject.toml                  # uv-managed; Python 3.11; pydantic>=2.5
├── README.md
├── CHANGELOG.md
├── LICENSE
├── .gitignore
├── .pre-commit-config.yaml
├── pmacs/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py                      # `pmacs <command>` entry point
│   ├── config.py                   # loads config/*.toml + Keychain
│   ├── constants.py                # CI-tested anti-pattern thresholds (do not edit casually)
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── arbitration.py
│   │   ├── pricing.py
│   │   ├── sizing.py
│   │   ├── calibration.py
│   │   ├── opportunity_cost.py
│   │   ├── fundamental.py
│   │   ├── data.py                 # Evidence, EvidencePacket
│   │   ├── catalysts.py
│   │   ├── contracts.py            # Holding (with state machine), Thesis
│   │   ├── agents.py               # PersonaOutput base classes
│   │   ├── trade.py                # TradePlan (signed)
│   │   ├── memory.py
│   │   ├── queue.py
│   │   ├── lessons.py
│   │   ├── attribution.py
│   │   ├── overrides.py
│   │   ├── flywheel.py
│   │   ├── sim.py                  # paper ledger
│   │   ├── portfolio.py
│   │   ├── reconciliation.py
│   │   ├── stop_loss.py            # StopTrigger
│   │   ├── currency.py             # FxRate (with usd_per_eur convention)
│   │   ├── freshness.py            # FreshnessResult (no packet mutation)
│   │   ├── failure.py              # FailedAssumption, FailureClassification (taxonomy in Agents.md §15)
│   │   ├── mutation.py             # MutationCandidate, MutationOutcome, MutationProposal
│   │   ├── conviction.py           # NEW: Conviction (operator-facing scalar from Source.md §7.2)
│   │   └── system.py               # Mode, KillSwitchState, etc.
│   ├── data/
│   │   ├── __init__.py
│   │   ├── gateway.py              # rate-limited HTTP wrapper
│   │   ├── staleness.py            # FreshnessResult-returning, no mutation
│   │   ├── corp_actions.py         # splits, dividends, mergers
│   │   ├── universe.py             # operator-curated list management
│   │   ├── fx.py                   # ECB EUR/USD, usd_per_eur convention
│   │   ├── canonical.py            # canonical_json + helpers
│   │   ├── sources/
│   │   │   ├── edgar.py
│   │   │   ├── polygon.py
│   │   │   ├── finnhub.py
│   │   │   ├── alpaca_data.py
│   │   │   ├── openfda.py
│   │   │   ├── finra.py
│   │   │   ├── form4.py
│   │   │   ├── ir_pages.py
│   │   │   ├── press.py
│   │   │   ├── fomc.py
│   │   │   ├── fred.py
│   │   │   ├── ecb.py
│   │   │   └── fundamentals.py
│   │   └── resolution/
│   │       ├── catalyst_detector.py
│   │       ├── earnings_resolver.py
│   │       ├── fda_resolver.py
│   │       └── corroboration.py
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── kuzu.py
│   │   ├── qdrant.py
│   │   ├── duckdb.py
│   │   ├── sqlite.py
│   │   ├── audit.py                # hash-chained writer + verifier
│   │   ├── keychain.py             # macOS Keychain wrapper
│   │   ├── indexes.py
│   │   └── consistency.py          # cross-DB reconciler
│   ├── logsys/
│   │   ├── __init__.py
│   │   ├── logger.py
│   │   ├── debug_log.py            # structured DebugEvent JSONL
│   │   ├── error_classifier.py     # canonical error_code mapper
│   │   ├── replay.py               # replay events from audit
│   │   └── dead_letter.py
│   ├── engines/
│   │   ├── __init__.py
│   │   ├── arbitration.py
│   │   ├── pricing.py
│   │   ├── sizing.py
│   │   ├── calibration.py
│   │   ├── memory.py
│   │   ├── queue.py
│   │   ├── lessons.py
│   │   ├── opportunity_cost.py
│   │   ├── fundamental_routing.py
│   │   ├── portfolio_risk_gate.py
│   │   ├── reconciliation.py
│   │   ├── stop_loss_monitor.py    # logic; daemon is in cortex/
│   │   ├── state_machine.py        # Holding state transitions (see §8.2)
│   │   ├── causal_attribution.py
│   │   ├── override_learning.py
│   │   ├── crucible_calibration.py
│   │   ├── flywheel_health.py
│   │   ├── failure_diagnostic.py   # FDE w/ 18-taxonomy classifier (Agents.md §15)
│   │   ├── conviction.py           # conviction scoring (Source.md §7.2)
│   │   ├── mutation.py             # MutationEngine logic; daemon in mutation/
│   │   └── scaling.py              # [STUB] until v2
│   ├── sim/
│   │   ├── __init__.py
│   │   ├── ledger.py               # paper portfolio ledger
│   │   └── alpaca_paper_adapter.py
│   ├── agents/                     # specified in Agents.md
│   │   ├── __init__.py
│   │   ├── base.py                 # PersonaRunner
│   │   ├── gatekeeper.py           # deterministic, not LLM
│   │   ├── macro_regime.py
│   │   ├── catalyst_summarizer.py
│   │   ├── moat_analyst.py
│   │   ├── growth_hunter.py
│   │   ├── insider_activity.py
│   │   ├── short_interest.py
│   │   ├── forensics.py
│   │   ├── crucible.py
│   │   ├── memo_writer.py
│   │   ├── episodic_context.py     # episodic context injection (Agents.md §18)
│   │   ├── prompts/                # versioned, immutable in production
│   │   │   ├── macro_regime.md
│   │   │   ├── catalyst_summarizer.md
│   │   │   ├── moat_analyst.md
│   │   │   ├── growth_hunter.md
│   │   │   ├── insider_activity.md
│   │   │   ├── short_interest.md
│   │   │   ├── forensics.md
│   │   │   ├── crucible.md
│   │   │   └── memo_writer.md
│   │   ├── grammars/               # GBNF files for llama-server
│   │   │   └── *.gbnf
│   │   ├── schemas_json/           # JSON Schema files for Ollama
│   │   │   └── *.json
│   │   └── sanity/                 # per-persona sanity validators
│   │       ├── macro_regime.py
│   │       └── ...
│   ├── nervous/
│   │   ├── __init__.py
│   │   ├── orchestrator.py
│   │   ├── api.py                  # FastAPI app for write actions + SSE
│   │   ├── sse_publisher.py
│   │   ├── checkpoint.py           # cycle resume from idempotency log
│   │   └── auth.py                 # session token verification
│   ├── cortex/
│   │   ├── __init__.py
│   │   ├── daemon.py               # main loop
│   │   ├── drift.py                # cross-cycle drift monitoring
│   │   ├── health.py               # process heartbeat checks
│   │   ├── kill_switch.py
│   │   ├── sleep_watch.py          # macOS sleep/wake detection
│   │   ├── flywheel_monitor.py
│   │   ├── disk_monitor.py
│   │   ├── clock_monitor.py        # NTP drift detection
│   │   ├── crash_loop_detector.py
│   │   ├── model_integrity.py     # GGUF SHA256 verification
│   │   ├── boot_detector.py       # detects gap-since-last-cycle, triggers cycle
│   │   ├── self_check.py          # meta-monitor: pings cortex itself
│   │   └── stop_loss_daemon.py    # the pmacs-stoploss process body
│   ├── mutation/                   # pmacs-mutation process
│   │   ├── __init__.py
│   │   ├── daemon.py               # main loop (§10.4)
│   │   ├── candidate_generator.py  # rules in Agents.md §17
│   │   ├── ab_runner.py            # SHADOW-only A/B execution
│   │   ├── stat_test.py            # Welch's t-test, Cohen's d
│   │   ├── promotion.py
│   │   └── rollback.py
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── service.py              # the pmacs-execution process body
│   │   ├── alpaca_adapter.py
│   │   ├── ibkr_adapter.py         # [STUB] until LIVE_EARLY
│   │   ├── catastrophe_net.py      # broker-side wide stop placement
│   │   └── signing.py              # Ed25519
│   ├── web/                        # pmacs-dashboard process
│   │   ├── __init__.py
│   │   ├── app.py                  # dashboard FastAPI app
│   │   ├── sse_client.py           # subscribes to nervous /events
│   │   ├── routes/
│   │   │   ├── dashboard.py
│   │   │   ├── agents.py
│   │   │   ├── pipeline.py
│   │   │   ├── universe.py
│   │   │   ├── cortex.py
│   │   │   ├── debug.py
│   │   │   └── settings.py
│   │   ├── templates/              # Jinja2 + HTMX
│   │   ├── components/             # reusable HTMX partials
│   │   │   ├── card.html
│   │   │   ├── statblock.html
│   │   │   ├── persona_card.html
│   │   │   ├── ticker_chip.html
│   │   │   └── ...
│   │   └── static/                 # Tailwind CSS, minimal JS, D3 for sankey
│   └── installer/
│       ├── __init__.py
│       ├── wizard.py               # the 10-step run wizard (Source.md §12)
│       └── steps/
│           ├── step_01_welcome.py
│           ├── step_02_backend.py
│           ├── step_03_model.py
│           ├── step_04_keychain.py
│           ├── step_05_databases.py
│           ├── step_06_connectivity.py
│           ├── step_07_universe.py
│           ├── step_08_preferences.py
│           ├── step_09_smoketest.py
│           └── step_10_promote.py
├── gbnf/                           # GBNF grammar files (mirrored in pmacs/agents/grammars/)
├── prompts/                        # canonical persona prompts (mirrored)
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── fixtures/                   # synthetic data for smoke-test cycle
│   ├── mutation_eval/              # mutation A/B test harness
│   ├── property/                   # hypothesis-based property tests
│   └── e2e/                        # end-to-end cycle replays
├── ops/
│   ├── migrate.py                  # DB schema migrations
│   ├── backup_verify.py
│   ├── audit_chain_verify.py
│   ├── install_pf_rules.sh         # pf rules to block llama-server egress
│   ├── install_launchd.sh          # writes per-process plists
│   ├── spec_consistency.py         # CI: Source.md ↔ Architecture.md ↔ Agents.md ↔ Phases.md cross-ref check
│   └── verify_isolation.py         # runtime process isolation audit
├── config/
│   ├── resources.toml              # hardware budgets
│   ├── risk.toml                   # risk thresholds
│   ├── crucible.toml               # CPS budgets
│   ├── mutation.toml               # recommendation thresholds, A/B sample sizes
│   ├── model_registry.json         # llama-server / Ollama backend selection
│   ├── model_hashes.toml           # SHA256 of GGUF + mmproj files
│   ├── notification.toml           # operator notification levels (Source.md §13.5)
│   └── source_criticality.toml     # CRITICAL / IMPORTANT / NICE_TO_HAVE per data source
├── docs/
│   ├── operator_runbook.md         # this is for the operator, not Claude Code
│   └── adr/                        # Architecture Decision Records (see §21)
└── launchd/                        # plists for each pmacs-* process
    ├── com.pmacs.cortex.plist
    ├── com.pmacs.nervous.plist
    ├── com.pmacs.execution.plist
    ├── com.pmacs.dashboard.plist
    ├── com.pmacs.stoploss.plist
    ├── com.pmacs.mutation.plist
    ├── com.pmacs.inference.plist
    └── com.pmacs.cortex_self_check.plist
```

---

## 4. Process topology and IPC

PMACS runs as **eight launchd processes** in dependency order. Each has a single responsibility and minimal privileges.

### 4.1 Process inventory

| Process | Port/Socket | Broker creds | Egress | DB access | Boot order |
|---|---|---|---|---|---|
| `pmacs-inference` | :8080 localhost | none | NONE (`pf`-blocked) | none | 1 |
| `pmacs-cortex` | daemon | none | NONE | r/w meta tables | 2 |
| `pmacs-cortex-self-check` | daemon | none | NONE | none | 2.5 |
| `pmacs-execution` | UDS `/var/db/pmacs/exec.sock` | YES | broker only | w fills only | 3 |
| `pmacs-nervous` | :8000 localhost | none | data API allowlist | r/w app tables | 4 |
| `pmacs-stoploss` | daemon (RTH only) | none | quote API only | r/w stop_events | 5 |
| `pmacs-mutation` | daemon | none | NONE | r/w `mutation_*` tables | 6 |
| `pmacs-dashboard` | :8000 localhost (loopback), served in-process by `pmacs-nervous` | none | NONE | r/o all DBs | 7 |

### 4.2 launchd configuration

Each plist sets:
- `KeepAlive = {Crashed=true, SuccessfulExit=false}` — restart on crash and on early exit
- `ThrottleInterval = 10` — minimum 10s between restart attempts
- `StandardOutPath` and `StandardErrorPath` — `/var/log/pmacs/<process>-stdout.log`, `-stderr.log`
- `UserName` — process-specific user (`_pmacs_cortex`, `_pmacs_exec`, `_pmacs_nervous`, etc.)
- `WorkingDirectory` — `/usr/local/var/pmacs`
- `EnvironmentVariables` — `PMACS_HOME`, no secrets

Heartbeats: each process writes `/var/db/pmacs/heartbeat/<proc>.ts` (Unix epoch seconds) every 5 seconds. Cortex monitors and restarts processes >30s stale.

### 4.3 Inter-process communication

| From → To | Protocol | Auth | Purpose |
|---|---|---|---|
| Web → Nervous (write) | HTTP/JSON on :8000 | operator confirmation per write request (high-impact); session token (low-impact) | Operator actions |
| Web ← Nervous (live events) | SSE on :8000/events | Session token; bound to operator session | Real-time UI updates |
| Nervous → Inference | HTTP/JSON on :8080 | Local API key (Keychain) | LLM calls |
| Nervous → Execution | UDS `/var/db/pmacs/exec.sock` | Ed25519 signature on TradePlan | Trade submission |
| StopLoss → Nervous | SQLite `stop_events` table + filesystem notification | OS UID isolation | Stop-loss execution |
| Mutation → Nervous | SQLite `mutation_proposals` table (proposals only) | OS UID isolation | Candidate proposals |
| Cortex → All | File-based heartbeats + SQLite `kill_switch` | OS UID isolation | Health monitoring |
| Web → Storage | SQLite read-only connection | OS UID isolation | Read-only display |

### 4.5.1 Session management

Dashboard sessions use a random 256-bit session token stored in an HttpOnly, SameSite=Strict cookie. Sessions expire after 24 hours of inactivity. Only one active session is permitted at a time (subsequent logins invalidate the prior session). Session tokens are generated by `pmacs-nervous` on the first SSE connection and verified on every subsequent request.

Concurrent tabs: both tabs share the same session cookie and SSE connection. State is consistent because the dashboard is read-only — both tabs see the same data via SSE.

UDS ACL: socket file owned by `_pmacs_exec`, group `_pmacs_math`, mode 0660.

### 4.4 SSE event channel (Source.md §13.2 chrome → real-time)

`pmacs-nervous` exposes `GET /events` (SSE) with the following streams (filterable by query param):

| Stream | Event types |
|---|---|
| `cycle` | cycle.open, cycle.symbol_start, cycle.symbol_complete, cycle.close, cycle.aborted |
| `agent` | agent.queued, agent.running, agent.token (throttled to 1/sec), agent.complete, agent.failed |
| `decision` | decision.arbitrated, decision.crucible_complete, decision.sized, decision.risk_gate_passed, decision.final |
| `trade` | trade.signed, trade.submitted, trade.filled, trade.rejected |
| `mutation` | mutation.proposed, mutation.ab_started, mutation.ab_progress, mutation.ab_complete, mutation.ready_for_review, mutation.promoted, mutation.rejected, mutation.rolled_back |
| `system` | system.heartbeat, system.kill_switch_engaged, system.kill_switch_disengaged, system.mode_changed |

Events are JSON, one per SSE frame. Dashboard subscribes on session start; reconnects on disconnect with `Last-Event-ID` for resume from last delivered event.

### 4.5 Boot-driven cycle initiation (Source.md §22)

`cortex/boot_detector.py` runs on every Cortex startup:

```python
def maybe_initiate_cycle():
    last_cycle = sqlite.query_scalar(
        "SELECT MAX(closed_at) FROM cycles WHERE state = 'CLOSED'"
    )
    gap_hours = (datetime.utcnow() - last_cycle).total_seconds() / 3600 if last_cycle else float("inf")

    # Skip if we already cycled within 24h
    if gap_hours < 24:
        log_debug("BOOT_CYCLE_SKIPPED", payload={"gap_hours": gap_hours, "reason": "RECENT_CYCLE"})
        return

    # Skip if today is a US market holiday or weekend (cycle still works on data
    # from last trading day, but no new EOD data exists). The system uses the
    # NYSE trading calendar (pandas_market_calendars).
    today = date.today()
    if not is_us_trading_day(today):
        log_debug("BOOT_CYCLE_SKIPPED", payload={"reason": "MARKET_CLOSED", "date": str(today)})
        return

    # Skip if it's before the EOD data window (need EOD data, available ~16:30 ET)
    if datetime.now(US_EASTERN) < eod_data_available_time(today):
        log_debug("BOOT_CYCLE_SKIPPED", payload={"reason": "PRE_EOD_DATA", "date": str(today)})
        return

    if gap_hours > 168:  # 7+ days
        log_debug("RESUME_GAP", level="WARN", payload={"gap_hours": gap_hours})

    # Refresh data (no catch-up cycles for missed days; Source.md §22)
    data_gateway.refresh_all_sources()

    # Initiate cycle
    cycle_id = nervous.initiate_cycle(trigger="BOOT_DETECTED")
    log_audit("cycle_initiated_by_boot", {"cycle_id": cycle_id, "gap_hours": gap_hours})
```

This implements `Source.md §1` cadence row ("boot-driven") and `Source.md §22` day-in-the-life.

### 4.6 Graceful shutdown (sleep/wake and lid-close)

`cortex/sleep_watch.py` monitors macOS sleep/wake notifications via `IOKit`:

```python
def on_sleep_notification():
    log_debug("SLEEP_WAKE_DETECTED", level="INFO", msg="System entering sleep")
    # 1. If cycle is running: checkpoint current progress (op_seq) to SQLite
    # 2. StopLossMonitor: no action needed (broker catastrophe-net covers offline)
    # 3. SSE connections: will break naturally; dashboard reconnects on wake

def on_wake_notification():
    log_debug("SLEEP_WAKE_DETECTED", level="INFO", msg="System woke from sleep")
    # 1. Cortex verifies all process heartbeats (restarts stale ones)
    # 2. boot_detector.maybe_initiate_cycle() checks gap since last cycle
    # 3. StopLossMonitor resumes if RTH
    # 4. Dashboard SSE reconnects automatically via Last-Event-ID
```

If the operator closes the lid mid-cycle, the cycle resumes from the last checkpoint on wake (idempotency via `op_seq`). No data is lost; no duplicate operations occur.

### 4.7 Crash loop detection

`cortex/crash_loop_detector.py` watches restart counts. If a process restarts >= 5 times in 60s (matching config/resources.toml max_restarts_per_minute=5):
1. Mark it as `BROKEN_CRASH_LOOP` in `process_state` SQLite table
2. Halt restart attempts
3. Write to audit log
4. Emit `BROKEN_CRASH_LOOP` debug event
5. Engage kill switch

### 4.8 Cortex meta-monitor

`cortex/self_check.py` runs as `pmacs-cortex-self-check`, a separate launchd job. Pings Cortex every 60s via local HTTP. > 120s unresponsive → engages full system kill switch.

---

## 5. Logging — two parallel streams

### 5.1 Audit log

**Format** per line:
```
<iso_ts>\t<prev_sha256>\t<event_type>\t<canonical_json>\t<this_sha256>
```

**Hash chain:**
```python
this_sha256 = sha256(
    iso_ts.encode("utf-8") + b"\0" +
    prev_sha256.encode("utf-8") + b"\0" +
    event_type.encode("utf-8") + b"\0" +
    canonical_json(payload).encode("utf-8")
).hexdigest()
```

**Genesis:** the very first audit entry has `prev_sha256 = "0" * 64` (64 hex zeros).

**Canonical JSON** (deterministic across platforms):

```python
# pmacs/data/canonical.py
import json, math
from datetime import datetime, date
from enum import Enum

def canonical_json(payload: dict) -> str:
    """Deterministic serialization for hash-chaining."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_default,
    )

def _default(obj):
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            raise ValueError("NaN/Inf not allowed in canonical JSON")
        return round(obj, 10)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    raise TypeError(f"Unsupported type: {type(obj)}")
```

`fsync` required after every line. Cortex verifies on startup AND hourly. Break → KILL SWITCH IMMEDIATELY.

### 5.2 Audit event types (full registry)

Every event REQUIRES `cycle_id` except cross-cycle system events (kill switch, mode changes, audit chain verifications).

**Cycle lifecycle:**
- `cycle_initiated_by_boot`, `cycle_initiated_by_operator`, `cycle_initiated_by_resume`
- `cycle_open`, `cycle_close`, `cycle_aborted`

**Decision:**
- `holding_state_transition` (validated by state machine)
- `llm_call` (prompt + output + seed + temperature + model_hash + grammar_version)
- `arbitration_result`, `crucible_result`, `sizing_result`, `risk_gate_result`, `conviction_computed`

**Trade:**
- `trade_plan_emitted`, `trade_submitted`, `trade_filled`, `trade_rejected`
- `stop_loss_triggered`, `stop_loss_filled`
- `catastrophe_net_placed`, `catastrophe_net_triggered`

**System:**
- `kill_switch_engaged`, `kill_switch_disengaged`
- `config_changed`
- `audit_chain_verified`, `audit_replication_verified`
- `cross_db_reconciliation_report`
- `mode_changed` (auto-promotion, operator-promotion, demotion)

**Calibration:**
- `calibration_refit` (before/after Brier)
- `crucible_calibration_update`
- `causal_attribution_recorded`

**Universe:**
- `universe_diff` (add/remove/flag changes)
- `operator_override`

**Data:**
- `fx_rate_snapshot`

**Mutation:**
- `mutation_hypothesis_generated`
- `mutation_ab_started`, `mutation_ab_progress` (every 5 cycles), `mutation_ab_complete`
- `mutation_ready_for_review`, `mutation_operator_promoted`
- `mutation_rejected`, `mutation_rolled_back`

**Failure:**
- `failed_assumption_recorded`, `failure_classified`

**Memory:**
- `episodic_context_injected` (per persona run; payload: source ids and recency window)

**Memo:**
- `memo_emitted` (aggregated cycle memo for operator-facing display)

### 5.3 Audit retention and replication

**Retention:** 1-year hot. First 7 days uncompressed; days 8-365 gzipped daily. After 1 year: archived to operator-configured offsite location via operator-confirmed rsync.

**Replication:** rsync to operator-configured offsite location hourly. Post-rsync, the destination's chain is verified by computing `this_sha256` from each line's components and comparing to stored value. Mismatch → emit `AUDIT_REPLICATION_CORRUPTION` and engage kill switch.

**Replication failure escalation:**
- 1st failure: log `AUDIT_REPLICATION_FAILED` (WARN), retry next hour
- 6 consecutive failures (~6 hours): log `AUDIT_REPLICATION_DEGRADED` (ERROR), surface dashboard alert
- 24 consecutive failures (~24 hours): engage kill switch with trigger `AUDIT_REPLICATION_FAILED_PERSISTENT`. The audit log is the trust foundation; running >24h without offsite copy violates the trust contract.

**Daily rotation:** at midnight UTC, the current `audit-YYYY-MM-DD.log` rolls over. The new file's first entry's `prev_sha256` is the last entry of the previous file (chain spans rotations).

### 5.4 Debug log

```python
# pmacs/schemas/debug.py
from pydantic import BaseModel
from datetime import datetime
from typing import Literal

class DebugEvent(BaseModel):
    ts: datetime
    level: Literal["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"]
    proc: str                        # "cortex" / "nervous" / "execution" / etc.
    component: str                   # "engines.arbitration" / "agents.crucible" / etc.
    cycle_id: str | None             # only None for pre-cycle bootstrap events
    symbol: str | None
    error_code: str | None           # canonical from §5.5
    msg: str
    payload: dict
    traceback: str | None
    spec_ref: str | None             # cross-reference: "Source.md §7.2" or "Architecture.md §9.2"
    suggested_fix_keywords: list[str]
```

**Storage:** `/var/log/pmacs/debug-YYYY-MM-DD.jsonl`. 30-day retention.

### 5.5 Canonical error codes (full registry)

Every debug event with `level >= WARN` MUST carry an `error_code` from this registry. Adding a new code requires a PR that updates this section.

**Data:**
- `STALE_DATA`, `MISSING_FUNDAMENTAL_DATA`, `FX_RATE_UNAVAILABLE`
- `CORP_ACTION_UNHANDLED`, `UNIVERSE_ROTATION`, `SECTOR_ETF_MISSING`
- `RATE_LIMIT_BLOCKED`, `LIMITED_HISTORY_TICKER`
- `CALIBRATION_GAP_NEW_SYMBOL`

**LLM:**
- `GBNF_PARSE_FAILURE`, `JSON_SCHEMA_PARSE_FAILURE`
- `SCHEMA_VALIDATION`, `OUT_OF_RANGE_PROBABILITY`
- `LLM_TIMEOUT`, `LLM_CACHE_HASH_MISMATCH`
- `THINKING_MODE_LEAKED`, `PROMPT_INJECTION_DETECTED`
- `MODEL_INTEGRITY_FAILED`, `INFERENCE_BACKEND_UNREACHABLE`

**Decision:**
- `ANTIPATTERN_HIT`, `MOAT_GROWTH_DEEP_DISAGREE`
- `OPPORTUNITY_COST_EXIT`, `MISSED_OPPORTUNITY`
- `CRUCIBLE_INVALID_EVIDENCE_REF`, `CRUCIBLE_SEVERITY_RECALIBRATED`, `CRUCIBLE_BUDGET_EXCEEDED`
- `MACRO_REGIME_SHIFT`
- `FORENSICS_FLAG_RAISED`, `INSIDER_CLUSTER_DETECTED`, `SHORT_INTEREST_ANOMALY`
- `BOOTSTRAP_LOW_CONFIDENCE_TRADE`, `THESIS_AGING_REVIEW_TRIGGERED`

**Risk/Portfolio:**
- `PORTFOLIO_LIMIT_HIT`, `SECTOR_LIMIT_HIT`, `CONCENTRATION_LIMIT_HIT`
- `STOP_LOSS_TRIGGERED`, `STOP_LOSS_GAP_DOWN`
- `DAILY_LOSS_LIMIT_HIT`, `ROLLING_LOSS_LIMIT_HIT`
- `CATASTROPHE_NET_TRIGGERED`

**Execution:**
- `BROKER_REJECT`, `BROKER_TIMEOUT`, `BROKER_AUTH_FAILURE`
- `RECONCILIATION_MISMATCH`, `EXECUTION_FAILED`, `ORDER_PARTIAL_FILL`

**System:**
- `KILL_SWITCH_ENGAGED`, `AUDIT_CHAIN_BREAK`, `AUDIT_REPLICATION_CORRUPTION`
- `RESOLUTION_TIMEOUT`, `RESUME_GAP`, `SLEEP_WAKE_DETECTED`
- `BOOT_CYCLE_SKIPPED`, `INTERNAL_ASSERTION`
- `SIM_LEDGER_MISMATCH`, `PAPER_DIVERGENCE`
- `DISK_LOW`, `CLOCK_DRIFT`, `DB_CORRUPTION`, `CROSS_DB_INCONSISTENCY`
- `IDEMPOTENCY_VIOLATION`, `DEAD_LETTER_QUEUED`
- `INVALID_STATE_TRANSITION`, `BROKEN_CRASH_LOOP`

**Calibration / Mutation:**
- `CAUSAL_ATTRIBUTION_LOW_CONFIDENCE`, `OVERRIDE_LEARNED`
- `FLYWHEEL_HEALTH_DEGRADED`, `CALIBRATION_REFIT_REJECTED`
- `MUTATION_AB_LOW_POWER`, `MUTATION_REJECTED_NO_EFFECT`, `MUTATION_ROLLED_BACK_REGRESSION`, `MUTATION_READY_FOR_REVIEW`

**Operator:**
- `REQUEUE_JUSTIFICATION`, `MANUAL_OVERRIDE`, `OPERATOR_TRADED_OUTSIDE_PMACS`

### 5.6 Web `/debug` page

Implements `Source.md §19`. Live SSE stream of debug events. Filters by level/proc/error_code/cycle_id/symbol. "Copy for Claude Code" button per event with paste-ready prompt + spec reference.

---

## 6. Data layer

### 6.1 Sources

| Source | Role | Cost | Rate limit | Criticality |
|---|---|---|---|---|
| SEC EDGAR | 10-K/10-Q/8-K filings | Free | 10 req/sec | CRITICAL |
| SEC Form 4 | Insider transactions | Free | 10 req/sec (shared) | IMPORTANT |
| FINRA | Short interest | Free | 60 req/min | NICE_TO_HAVE |
| Finnhub | Earnings calendar | Free tier | 60 req/min | IMPORTANT |
| Polygon.io | EOD OHLCV, corp actions | Starter $29/mo | 5 req/sec | CRITICAL |
| openFDA | FDA decisions | Free | 240 req/min | IMPORTANT |
| Alpaca data | Quote sanity, intraday quotes for stop-loss | Free | 200 req/min | CRITICAL |
| Stock Analysis API / SimFin | Fundamentals | Free tier | 60 req/min | CRITICAL |
| Company IR pages | Guidance via HTML delta | Free | 1 req/30s/symbol | NICE_TO_HAVE |
| Tier 1-3 press | NYT, WSJ, Reuters | Free tier | varies | NICE_TO_HAVE |
| FOMC calendar/minutes | Macro regime | Free | 10 req/min | NICE_TO_HAVE |
| FRED | Treasury yield curve | Free | 120 req/min | IMPORTANT |
| ECB | EUR/USD daily reference rate | Free | 60 req/day | IMPORTANT |

**No Tier 4** (Reddit/X/social) — noise + injection surface (`Source.md §4`).

### 6.2 Universe (operator-curated, see `Source.md §8`)

```sql
CREATE TABLE universe (
    ticker TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    exchange TEXT NOT NULL,
    sector_gics TEXT,
    sub_sector_operator TEXT,           -- operator-tagged free-form
    market_cap_usd REAL,
    adv_usd REAL,
    days_of_history INTEGER,
    admitted_at TIMESTAMP NOT NULL,
    flags TEXT,                         -- JSON array: ['limited_history', 'adv_below_threshold', 'halted']
    is_active INTEGER NOT NULL DEFAULT 1
);
```

Daily ADV/halt check via `data/universe.py`. Flags surface in UI; never auto-removes (`Source.md §8.3`).

### 6.3 Staleness with EXHAUSTIVE criticality

```python
# pmacs/data/staleness.py
from datetime import datetime, timedelta
from pmacs.schemas.freshness import FreshnessResult

STALENESS_BUDGET = {
    "polygon.ohlcv.eod":    timedelta(hours=20),
    "alpaca.bar.intraday":  timedelta(minutes=5),
    "alpaca.quote":         timedelta(minutes=15),
    "edgar.filing":         timedelta(days=1),
    "finnhub.earnings_cal": timedelta(hours=24),
    "openfda.decisions":    timedelta(days=1),
    "ir_page.delta":        timedelta(hours=24),
    "fomc.calendar":        timedelta(days=14),
    "form4.insider":        timedelta(days=2),
    "finra.short_interest": timedelta(days=16),
    "fundamentals.api":     timedelta(days=7),
    "fred.yield_curve":     timedelta(days=2),
    "ecb.fx_rate":          timedelta(days=2),
    "press.tier1":          timedelta(hours=24),
    "press.tier2":          timedelta(hours=48),
    "press.tier3":          timedelta(days=7),
}

# Loaded from config/source_criticality.toml at startup
SOURCE_CRITICALITY = {
    "polygon.ohlcv.eod":    "CRITICAL",
    "alpaca.bar.intraday":  "CRITICAL",
    "alpaca.quote":         "IMPORTANT",
    "edgar.filing":         "CRITICAL",
    "fundamentals.api":     "CRITICAL",
    "finnhub.earnings_cal": "IMPORTANT",
    "openfda.decisions":    "IMPORTANT",
    "ir_page.delta":        "NICE_TO_HAVE",
    "fomc.calendar":        "NICE_TO_HAVE",
    "form4.insider":        "IMPORTANT",
    "finra.short_interest": "NICE_TO_HAVE",
    "fred.yield_curve":     "IMPORTANT",
    "ecb.fx_rate":          "IMPORTANT",
    "press.tier1":          "NICE_TO_HAVE",
    "press.tier2":          "NICE_TO_HAVE",
    "press.tier3":          "NICE_TO_HAVE",
}

class StaleDataError(Exception):
    pass

def assert_fresh(packet) -> FreshnessResult:
    """Returns result, NEVER mutates packet. See anti-pattern §16.4."""
    crit = SOURCE_CRITICALITY.get(packet.source)
    if crit is None:
        log_debug("INTERNAL_ASSERTION", msg=f"unmapped source criticality: {packet.source}")
        crit = "IMPORTANT"

    budget = STALENESS_BUDGET.get(packet.source)
    if budget is None:
        log_debug("INTERNAL_ASSERTION", msg=f"unmapped staleness budget: {packet.source}")
        budget = timedelta(hours=24)

    age = datetime.utcnow() - packet.fetched_at
    if age <= budget:
        return FreshnessResult(fresh=True, degraded=False, source=packet.source, age=age)

    if crit == "CRITICAL":
        raise StaleDataError(packet.source, age, budget)
    elif crit == "IMPORTANT":
        log_debug("STALE_DATA", level="WARN", source=packet.source)
        return FreshnessResult(fresh=False, degraded=True, source=packet.source, age=age)
    else:
        return FreshnessResult(fresh=False, degraded=True, source=packet.source, age=age)
```

### 6.4 Rate limiting (thread-safe token bucket)

```python
# pmacs/data/gateway.py
import threading
import time

class TokenBucket:
    def __init__(self, capacity: int, refill_rate_per_sec: float):
        self._capacity = capacity
        self._refill_rate = refill_rate_per_sec
        self._tokens = float(capacity)
        self._lock = threading.Lock()
        self._last_refill = time.monotonic()

    def acquire(self, blocking: bool = True) -> bool:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(
                self._capacity,
                self._tokens + (now - self._last_refill) * self._refill_rate,
            )
            self._last_refill = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            if not blocking:
                return False
            wait = (1.0 - self._tokens) / self._refill_rate
        time.sleep(wait)
        return self.acquire(blocking=True)

BUCKETS = {
    "polygon":      TokenBucket(capacity=5,   refill_rate_per_sec=5.0),
    "finnhub":      TokenBucket(capacity=60,  refill_rate_per_sec=1.0),
    "edgar":        TokenBucket(capacity=10,  refill_rate_per_sec=10.0),
    "openfda":      TokenBucket(capacity=240, refill_rate_per_sec=4.0),
    "alpaca":       TokenBucket(capacity=200, refill_rate_per_sec=200/60),
    "fundamentals": TokenBucket(capacity=60,  refill_rate_per_sec=1.0),
    "fred":         TokenBucket(capacity=120, refill_rate_per_sec=2.0),
    "ecb":          TokenBucket(capacity=10,  refill_rate_per_sec=60/86400),
}
```

All HTTP calls go through `data/gateway.py` which acquires from the appropriate bucket before sending.

### 6.5 FX (ECB convention)

```python
# pmacs/schemas/currency.py
from datetime import datetime, date
from pydantic import BaseModel, Field
from typing import Literal

class FxRate(BaseModel):
    """ECB convention: 1 EUR = X USD."""
    pair: Literal["EURUSD"] = "EURUSD"
    usd_per_eur: float = Field(gt=0.5, lt=2.0)
    fetched_at: datetime
    business_date: date
    source: Literal["ECB"] = "ECB"

class FxSnapshot(BaseModel):
    cycle_id: str
    rate: FxRate

# pmacs/data/fx.py
def usd_to_eur(usd_amount: float, snapshot: FxSnapshot) -> float:
    return usd_amount / snapshot.rate.usd_per_eur

def eur_to_usd(eur_amount: float, snapshot: FxSnapshot) -> float:
    return eur_amount * snapshot.rate.usd_per_eur
```

ECB unavailable: cached snapshot used. >3 days stale: `FX_RATE_UNAVAILABLE` warning; display in USD only.

---

## 7. Catalyst resolution subsystem

### 7.1 Catalyst types (7)

1. Earnings release
2. FDA decision
3. Product launch
4. Regulatory ruling (SEC, antitrust, sector regulator)
5. M&A close (announced merger reaches close/abandon)
6. Partnership announcement (binding contract or LOI)
7. Guidance update (forward-looking statement change)

### 7.2 Multi-source corroboration tiers

- **Tier A:** primary source (filing, press release on company wire, official announcement)
- **Tier B:** Tier 1 press (Reuters, WSJ, Bloomberg, FT, AP)
- **Tier C:** Tier 2/3 press, IR page, secondary sources

**Resolution rule:** A catalyst is RESOLVED only when corroboration reaches at least one of:
- Tier A (single source sufficient)
- Tier B + price-action consistency (movement aligned with claimed direction)

3σ outlier guard: a Tier B claim contradicting price action is held in PENDING for re-corroboration.

### 7.3 Resolution status enum

```python
class CatalystStatus(str, Enum):
    PENDING = "PENDING"
    RESOLVED_UP = "RESOLVED_UP"
    RESOLVED_DOWN = "RESOLVED_DOWN"
    RESOLVED_FLAT = "RESOLVED_FLAT"
    RESOLVED_MIXED = "RESOLVED_MIXED"
    TIMEOUT = "TIMEOUT"        # 48h after expected resolution time, no corroboration
```

Timeout: 48 hours after expected resolution timestamp (e.g., earnings call end). On TIMEOUT, the holding is exited via `EXIT_FAILED` and a FailedAssumption with `CATALYST_TIMEOUT` taxonomy is recorded.

---

## 8. Storage

### 8.1 Components and rationale

| Store | Purpose | Why this DB |
|---|---|---|
| KuzuDB | Graph: Holding-Evidence-Resolution-Lesson-FailedAssumption-MutationOutcome lineage with variable-depth traversal | Cypher-native; recursive CTEs over hundreds of edges become slow + error-prone in SQLite |
| Qdrant | Vector RAG | Production HNSW; sqlite-vss less battle-tested |
| DuckDB | Columnar analytics on accumulating resolution history + episodic memory rolling windows | Will exceed SQLite practical limits within 18 months |
| SQLite | Config/state/queue/checkpoints/paper account/mutation_proposals | Battle-tested OLTP at this scale |
| `audit.log` | Hash-chained immutable record | Append-only file with integrity proof |

### 8.2 Holding state machine

This is the single most important code in PMACS. Direct mutation of `holding.state` is forbidden (anti-pattern §16.1).

```python
# pmacs/schemas/contracts.py
from enum import Enum
from pydantic import BaseModel, Field, model_validator
from datetime import datetime, date
from typing import Literal

class HoldingState(str, Enum):
    # Pre-decision pipeline
    CANDIDATE = "CANDIDATE"
    PHASE1_RESEARCH = "PHASE1_RESEARCH"
    PHASE2_CRUCIBLE = "PHASE2_CRUCIBLE"
    APPROVED_PENDING = "APPROVED_PENDING"

    # Active position
    ACTIVE = "ACTIVE"

    # Aborts
    ABORTED_PRE_LLM = "ABORTED_PRE_LLM"     # antipattern, gatekeeper, stale data
    ABORTED_LLM = "ABORTED_LLM"              # disagreement, crucible, EV, calibration gap
    ABORTED_RISK = "ABORTED_RISK"            # portfolio limit, correlation, regime
    PHASE1_TIMEOUT = "PHASE1_TIMEOUT"

    # Resolutions (terminal)
    RESOLVED_UP = "RESOLVED_UP"
    RESOLVED_FLAT = "RESOLVED_FLAT"
    RESOLVED_DOWN = "RESOLVED_DOWN"
    RESOLVED_MIXED = "RESOLVED_MIXED"

    # Exits (terminal)
    STOPPED_OUT = "STOPPED_OUT"
    EXIT_THESIS_INVALIDATED = "EXIT_THESIS_INVALIDATED"   # thesis-bound exit (Source.md §7.1)
    EXIT_OPPORTUNITY_COST = "EXIT_OPPORTUNITY_COST"
    EXIT_TRAILING_STOP = "EXIT_TRAILING_STOP"
    EXIT_FAILED = "EXIT_FAILED"

    # Operational
    HALTED = "HALTED"
    DELISTED = "DELISTED"
    RESOLUTION_TIMEOUT = "RESOLUTION_TIMEOUT"
    PANIC_EXIT = "PANIC_EXIT"
    INTERRUPTED = "INTERRUPTED"

    # Thesis-aging review trigger (non-terminal)
    THESIS_AGING_REVIEW = "THESIS_AGING_REVIEW"

# pmacs/engines/state_machine.py
VALID_TRANSITIONS: dict[HoldingState, set[HoldingState]] = {
    HoldingState.CANDIDATE: {
        HoldingState.PHASE1_RESEARCH,
        HoldingState.ABORTED_PRE_LLM,
    },
    HoldingState.PHASE1_RESEARCH: {
        HoldingState.PHASE2_CRUCIBLE,
        HoldingState.ABORTED_LLM,
        HoldingState.PHASE1_TIMEOUT,
    },
    HoldingState.PHASE1_TIMEOUT: {
        HoldingState.ABORTED_LLM,
    },
    HoldingState.PHASE2_CRUCIBLE: {
        HoldingState.APPROVED_PENDING,
        HoldingState.ABORTED_LLM,
    },
    HoldingState.APPROVED_PENDING: {
        HoldingState.ACTIVE,
        HoldingState.ABORTED_RISK,
        HoldingState.ABORTED_LLM,
        HoldingState.ABORTED_PRE_LLM,   # conviction < 0.3 after sizing/risk gate
    },
    HoldingState.ACTIVE: {
        HoldingState.RESOLVED_UP, HoldingState.RESOLVED_FLAT,
        HoldingState.RESOLVED_DOWN, HoldingState.RESOLVED_MIXED,
        HoldingState.STOPPED_OUT, HoldingState.EXIT_THESIS_INVALIDATED,
        HoldingState.EXIT_OPPORTUNITY_COST, HoldingState.EXIT_TRAILING_STOP,
        HoldingState.EXIT_FAILED,
        HoldingState.HALTED, HoldingState.DELISTED,
        HoldingState.RESOLUTION_TIMEOUT, HoldingState.PANIC_EXIT,
        HoldingState.INTERRUPTED, HoldingState.THESIS_AGING_REVIEW,
    },
    HoldingState.THESIS_AGING_REVIEW: {
        HoldingState.ACTIVE,                    # thesis re-validated
        HoldingState.EXIT_THESIS_INVALIDATED,   # thesis broken
    },
    HoldingState.HALTED: {
        HoldingState.ACTIVE,
        HoldingState.DELISTED,
        HoldingState.PANIC_EXIT,
    },
    HoldingState.INTERRUPTED: {
        HoldingState.ACTIVE,
        HoldingState.PANIC_EXIT,
        HoldingState.DELISTED,      # ticker delisted while cycle was interrupted
    },
}

TERMINAL_STATES = {
    HoldingState.ABORTED_PRE_LLM, HoldingState.ABORTED_LLM, HoldingState.ABORTED_RISK,
    HoldingState.RESOLVED_UP, HoldingState.RESOLVED_FLAT,
    HoldingState.RESOLVED_DOWN, HoldingState.RESOLVED_MIXED,
    HoldingState.STOPPED_OUT, HoldingState.EXIT_THESIS_INVALIDATED,
    HoldingState.EXIT_OPPORTUNITY_COST, HoldingState.EXIT_TRAILING_STOP,
    HoldingState.EXIT_FAILED,
    HoldingState.DELISTED, HoldingState.RESOLUTION_TIMEOUT,
    HoldingState.PANIC_EXIT,
}

class InvalidStateTransition(Exception):
    pass

def transition(holding: Holding, new_state: HoldingState, reason: str,
               cycle_id: str, op_seq: int) -> Holding:
    """The ONE place Holding.state changes. Direct mutation forbidden (anti-pattern §16.1)."""
    current = holding.state

    if current in TERMINAL_STATES:
        raise InvalidStateTransition(f"holding {holding.id} is terminal at {current}")

    valid = VALID_TRANSITIONS.get(current, set())
    if new_state not in valid:
        log_debug("INVALID_STATE_TRANSITION", payload={
            "holding_id": holding.id,
            "from": current.value,
            "to": new_state.value,
            "reason": reason,
            "valid": [s.value for s in valid],
        })
        raise InvalidStateTransition(f"{current.value} -> {new_state.value} not valid")

    # Idempotency: if (cycle_id, op_seq, "state_transition") already in op_idempotency, no-op
    if op_already_completed(cycle_id, op_seq, "state_transition"):
        return holding

    holding.state = new_state
    if new_state in {HoldingState.ABORTED_PRE_LLM, HoldingState.ABORTED_LLM,
                     HoldingState.ABORTED_RISK}:
        holding.abort_reason = reason
    if new_state in TERMINAL_STATES and not holding.exit_date:
        holding.exit_date = datetime.utcnow()

    audit_write("holding_state_transition", {
        "cycle_id": cycle_id,
        "op_seq": op_seq,
        "holding_id": holding.id,
        "from": current.value,
        "to": new_state.value,
        "reason": reason,
    })

    record_op_completed(cycle_id, op_seq, "state_transition")

    # Trigger Failure Diagnostic Engine on terminal transitions
    if new_state in TERMINAL_STATES:
        from pmacs.engines.failure_diagnostic import classify_and_record
        classify_and_record(holding, cycle_id)

    return holding
```

### 8.3 Holding schema

```python
# pmacs/schemas/contracts.py
class Holding(BaseModel):
    id: str
    ticker: str
    catalyst_id: str

    state: HoldingState
    mode: Literal["SHADOW", "PAPER", "PAPER_VALIDATED",
                  "LIVE_EARLY", "LIVE_STANDARD", "LIVE_EXPANDED"]
    abort_reason: str | None = None

    # Entry
    signal_price: float
    entry_date: datetime | None = None
    entry_price: float | None = None
    position_size_usd: float
    position_size_shares: float

    # Decision context (snapshot at entry)
    original_p_up: float
    original_p_flat: float
    original_p_down: float
    original_ev_net: float
    original_conviction: float           # snapshot of conviction at entry
    thesis_hash: str
    thesis_version: int = 1
    thesis_embedding_id: str | None = None
    fundamental_weights_moat: float
    fundamental_weights_growth: float
    matured_sources_at_entry: int
    crucible_severity_at_entry: float

    # Risk
    stop_loss_price: float
    catastrophe_net_price: float          # broker-side wide stop
    trailing_stop_price: float | None = None
    trailing_stop_armed: bool = False
    thesis_review_due_date: date          # 90d ahead at entry; updates on each weekly re-eval

    # Exit
    exit_date: datetime | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    realized_pnl_usd: float | None = None
    realized_pnl_pct: float | None = None

    # Audit linkage
    cycle_id_opened: str
    cycle_id_closed: str | None = None

    @model_validator(mode="after")
    def _check_probabilities(self):
        total = self.original_p_up + self.original_p_flat + self.original_p_down
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"original probabilities sum to {total}, expected 1.0")
        return self
```

### 8.4 KuzuDB graph schema

```cypher
// Nodes — IMPORTANT: KuzuDB stores sparse projections (key fields + graph edges only).
// Full Holding data lives in SQLite; full evidence content in DuckDB evidence_archive.
// KuzuDB is for lineage traversal, not as a source of truth for field values.
CREATE NODE TABLE Holding (
  id STRING, ticker STRING, state STRING,
  cycle_id_opened STRING, cycle_id_closed STRING,
  PRIMARY KEY (id)
);

CREATE NODE TABLE Evidence (
  id STRING, source STRING, type STRING,
  fetched_at TIMESTAMP, content_hash STRING,
  PRIMARY KEY (id)
);

CREATE NODE TABLE Resolution (
  id STRING, holding_id STRING, kind STRING, ts TIMESTAMP,
  pnl_pct DOUBLE,
  PRIMARY KEY (id)
);

CREATE NODE TABLE Thesis (
  id STRING, hash STRING, version INT, text STRING,
  embedding_id STRING,
  PRIMARY KEY (id)
);

CREATE NODE TABLE Lesson (
  id STRING, kind STRING, weight DOUBLE, text STRING,
  PRIMARY KEY (id)
);

CREATE NODE TABLE FailedAssumption (
  id STRING, taxonomy STRING, severity DOUBLE, ts TIMESTAMP,
  summary STRING, holding_id STRING, cycle_id STRING,
  PRIMARY KEY (id)
);

CREATE NODE TABLE MutationOutcome (
  id STRING, dimension STRING, candidate_hash STRING,
  result STRING, effect_size DOUBLE, p_value DOUBLE,
  PRIMARY KEY (id)
);

// Edges
CREATE REL TABLE BACKED_BY (FROM Holding TO Evidence, weight DOUBLE);
CREATE REL TABLE RESOLVES_TO (FROM Holding TO Resolution);
CREATE REL TABLE GROUNDED_IN (FROM Thesis TO Evidence);
CREATE REL TABLE HAS_THESIS (FROM Holding TO Thesis);
CREATE REL TABLE PRODUCED_LESSON (FROM Resolution TO Lesson);
CREATE REL TABLE SIMILAR_TO (FROM Lesson TO Lesson, similarity DOUBLE);
CREATE REL TABLE FAILED_ASSUMPTION (FROM Holding TO FailedAssumption);
CREATE REL TABLE INFORMS_MUTATION (FROM FailedAssumption TO MutationOutcome);
CREATE REL TABLE PROMOTED_FROM (FROM Holding TO MutationOutcome);
```

### 8.5 SQLite tables (key set)

```sql
-- Cycles
CREATE TABLE cycles (
    cycle_id TEXT PRIMARY KEY,
    opened_at TIMESTAMP NOT NULL,
    closed_at TIMESTAMP,
    state TEXT NOT NULL,           -- OPEN / CLOSED / ABORTED
    trigger TEXT NOT NULL,         -- BOOT_DETECTED / OPERATOR / RESUME
    mode TEXT NOT NULL
);
CREATE INDEX idx_cycles_state ON cycles(state);
CREATE INDEX idx_cycles_closed_at ON cycles(closed_at DESC);

-- Mode history
CREATE TABLE mode_history (
    id INTEGER PRIMARY KEY,
    from_mode TEXT NOT NULL,
    to_mode TEXT NOT NULL,
    changed_at TIMESTAMP NOT NULL,
    reason TEXT,
    operator_confirmed INTEGER NOT NULL DEFAULT 0,
    triggered_by TEXT NOT NULL    -- 'OPERATOR' / 'AUTO_DEMOTION'
);

-- Queue (per-cycle)
CREATE TABLE queue (
    cycle_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    priority_band INTEGER NOT NULL,   -- 1=highest, 4=lowest
    pinned INTEGER NOT NULL DEFAULT 0,
    enqueued_at TIMESTAMP NOT NULL,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    operator_initiated INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (cycle_id, ticker)
);

-- Persistent priority pins (across cycles)
CREATE TABLE persistent_pins (
    ticker TEXT PRIMARY KEY,
    priority_band INTEGER NOT NULL,
    pinned_at TIMESTAMP NOT NULL,
    pinned_by_operator INTEGER NOT NULL DEFAULT 1
);

-- Stop events (StopLossMonitor → Nervous handoff)
CREATE TABLE stop_events (
    id INTEGER PRIMARY KEY,
    holding_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    triggered_at TIMESTAMP NOT NULL,
    quote_price REAL NOT NULL,
    stop_price REAL NOT NULL,
    market_state TEXT NOT NULL,      -- PRE_OPEN / RTH / AFTER_HOURS / CLOSED
    status TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING / SUBMITTED / FILLED / FAILED
    trade_plan_id TEXT,
    fill_price REAL,
    fill_at TIMESTAMP
);
CREATE INDEX idx_stop_events_status ON stop_events(status);

-- Mutation proposals (created by pmacs-mutation, consumed by pmacs-nervous on promotion)
CREATE TABLE mutation_proposals (
    id TEXT PRIMARY KEY,
    dimension TEXT NOT NULL,         -- prompts / source_weights / thresholds / persona_affinity / universe_flags
    target TEXT NOT NULL,            -- e.g., 'moat_analyst.system_prompt'
    candidate_payload TEXT NOT NULL, -- JSON: full candidate config diff
    baseline_hash TEXT NOT NULL,
    candidate_hash TEXT NOT NULL,
    proposed_at TIMESTAMP NOT NULL,
    proposer TEXT NOT NULL,          -- 'mutation_engine' / 'operator'
    status TEXT NOT NULL,            -- PROPOSED / AB_RUNNING / AB_COMPLETE_PROMOTE / AB_COMPLETE_REJECT / OPERATOR_PENDING / PROMOTED / REJECTED / ROLLED_BACK
    ab_started_at TIMESTAMP,
    ab_completed_at TIMESTAMP,
    sample_size INTEGER,
    effect_size REAL,
    p_value REAL,
    cohens_d REAL,
    promotion_at TIMESTAMP,
    promotion_audit_event_sha TEXT,
    rollback_at TIMESTAMP,
    rollback_reason TEXT,
    UNIQUE(candidate_hash, target)
);
CREATE INDEX idx_mutation_proposals_status ON mutation_proposals(status);

-- Mutation outcomes (per cycle, per active candidate)
CREATE TABLE mutation_outcomes (
    cycle_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    arm TEXT NOT NULL,               -- 'control' / 'candidate'
    metric_name TEXT NOT NULL,       -- 'brier' / 'sharpe_contribution' / 'pnl_pct' / etc.
    metric_value REAL NOT NULL,
    PRIMARY KEY (cycle_id, proposal_id, arm, metric_name)
);

-- Kill switch state
CREATE TABLE kill_switch (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton row
    state TEXT NOT NULL,             -- ARMED / ENGAGED
    engaged_at TIMESTAMP,
    engaged_reason TEXT,
    engaged_trigger TEXT,            -- one of the 10 triggers
    disengaged_at TIMESTAMP,
    disengaged_by_operator INTEGER DEFAULT 0,
    disengaged_reason TEXT
);

-- Idempotency
CREATE TABLE op_idempotency (
    cycle_id TEXT NOT NULL,
    op_seq INTEGER NOT NULL,
    op_type TEXT NOT NULL,
    completed_at TIMESTAMP NOT NULL,
    result_hash TEXT,                 -- for cached return value
    PRIMARY KEY (cycle_id, op_seq)
);

-- Process state (for crash loop detector)
CREATE TABLE process_state (
    proc TEXT PRIMARY KEY,
    last_started_at TIMESTAMP NOT NULL,
    restart_count_60s INTEGER NOT NULL DEFAULT 0,
    is_broken_crash_loop INTEGER NOT NULL DEFAULT 0
);

-- Paper account ledger (sim mode only)
CREATE TABLE paper_account (
    id INTEGER PRIMARY KEY,
    snapshot_at TIMESTAMP NOT NULL,
    cash_usd REAL NOT NULL,
    positions_value_usd REAL NOT NULL,
    total_value_usd REAL NOT NULL
);

-- FX snapshot history
CREATE TABLE fx_snapshots (
    cycle_id TEXT PRIMARY KEY,
    fetched_at TIMESTAMP NOT NULL,
    business_date DATE NOT NULL,
    usd_per_eur REAL NOT NULL
);

-- Consistency drift log (populated by cross-DB reconciler)
CREATE TABLE consistency_drift (
    id INTEGER PRIMARY KEY,
    detected_at TIMESTAMP NOT NULL,
    cycle_id TEXT NOT NULL,
    source_db TEXT NOT NULL,        -- 'kuzu' / 'qdrant' / 'duckdb' / 'sqlite'
    target_db TEXT NOT NULL,
    entity_type TEXT NOT NULL,      -- 'holding' / 'thesis' / 'resolution' / 'lesson'
    entity_id TEXT NOT NULL,
    drift_type TEXT NOT NULL,       -- 'MISSING_IN_TARGET' / 'FIELD_MISMATCH' / 'ORPHAN'
    details TEXT,
    resolved_at TIMESTAMP,
    resolution TEXT                  -- 'AUTO_REPAIRED' / 'MANUAL' / 'DEFERRED'
);
CREATE INDEX idx_consistency_drift_resolved ON consistency_drift(resolved_at);

-- Operator override log (Override Learning input)
CREATE TABLE operator_overrides (
    id INTEGER PRIMARY KEY,
    cycle_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    override_type TEXT NOT NULL,    -- 'force_skip_to_buy' / 'force_buy_to_skip' / 'force_exit' / 'force_rerun' / etc.
    operator_reason TEXT,
    occurred_at TIMESTAMP NOT NULL,
    outcome TEXT,                    -- evaluated post-resolution: 'CORRECT' / 'INCORRECT' / 'NEUTRAL'
    evaluated_at TIMESTAMP
);
```

### 8.6 DuckDB analytics tables (rolling windows = episodic memory)

```sql
CREATE TABLE resolutions_history (
    holding_id VARCHAR,
    ticker VARCHAR,
    cycle_id VARCHAR,
    mode VARCHAR,
    resolved_at TIMESTAMP,
    state VARCHAR,
    pnl_pct DOUBLE,
    pnl_usd DOUBLE,
    days_held INTEGER,
    original_conviction DOUBLE,
    matured_sources_at_entry INTEGER,
    failure_taxonomy VARCHAR  -- nullable; populated by FDE
);

CREATE TABLE rolling_metrics (
    cycle_id VARCHAR PRIMARY KEY,
    window_5d_brier DOUBLE,
    window_30d_brier DOUBLE,
    window_90d_brier DOUBLE,
    window_5d_sharpe DOUBLE,
    window_30d_sharpe DOUBLE,
    window_90d_sharpe DOUBLE,
    window_5d_drawdown DOUBLE,
    window_30d_drawdown DOUBLE,
    window_90d_drawdown DOUBLE,
    win_rate_30d DOUBLE,
    avg_rr_30d DOUBLE
);

CREATE TABLE persona_performance (
    persona VARCHAR,
    cycle_id VARCHAR,
    ticker VARCHAR,
    p_up_predicted DOUBLE,
    actual_resolution VARCHAR,
    brier_contribution DOUBLE,
    weight_used DOUBLE
);

CREATE TABLE persona_ticker_affinity (
    persona VARCHAR,
    ticker VARCHAR,
    cycle_count INTEGER,
    avg_brier DOUBLE,
    last_updated TIMESTAMP,
    PRIMARY KEY (persona, ticker)
);

CREATE TABLE persona_subsector_affinity (
    persona VARCHAR,
    sub_sector VARCHAR,
    cycle_count INTEGER,
    avg_brier DOUBLE,
    last_updated TIMESTAMP,
    PRIMARY KEY (persona, sub_sector)
);

CREATE TABLE evidence_archive (
    cycle_id VARCHAR NOT NULL,
    evidence_id VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    ticker VARCHAR NOT NULL,
    content TEXT NOT NULL,           -- full JSON content of the evidence packet
    content_hash VARCHAR NOT NULL,
    fetched_at TIMESTAMP NOT NULL,
    PRIMARY KEY (cycle_id, evidence_id)
);

CREATE TABLE scan_records (
    cycle_id VARCHAR NOT NULL,
    ticker VARCHAR NOT NULL,
    scanned_at TIMESTAMP NOT NULL,
    verdict VARCHAR NOT NULL,          -- STRONG_BUY / BUY / HOLD / SKIP
    conviction DOUBLE,
    p_up DOUBLE,
    p_flat DOUBLE,
    p_down DOUBLE,
    crucible_severity DOUBLE,
    matured_sources_used INTEGER,
    sizing_usd DOUBLE,
    abort_reason VARCHAR,
    PRIMARY KEY (cycle_id, ticker)
);

CREATE TABLE failure_taxonomy_counts (
    taxonomy VARCHAR,
    cycle_id VARCHAR,
    count INTEGER,
    PRIMARY KEY (taxonomy, cycle_id)
);
```

### 8.7 Qdrant collections

| Collection | Vector dim | Payload |
|---|---|---|
| `theses` | 768 | holding_id, ticker, thesis_text, embedding_at |
| `memos_persona` | 768 | persona, ticker, cycle_id, memo_text |
| `memos_aggregated` | 768 | ticker, cycle_id, final_memo_text, verdict |
| `evidence_chunks` | 768 | source, source_id, ticker, chunk_text, fetched_at |
| `lessons` | 768 | lesson_id, kind, lesson_text |

Embedding model: `BAAI/bge-base-en-v1.5` (768-dim, ~420MB on disk, ~1.2GB in RAM during inference). Runs on CPU via `sentence-transformers` library. Called per-cycle for new evidence chunks, thesis embeddings, and lesson embeddings. Not called during stop-loss monitoring. Memory impact included in Architecture.md §20.2 budget.

### 8.8 Keychain naming

All API keys, tokens, and secrets in macOS Keychain (`security` framework). Service names follow `pmacs.<category>.<key>`:

```
pmacs.broker.alpaca_paper_key
pmacs.broker.alpaca_paper_secret
pmacs.broker.ibkr_*                    # when LIVE
pmacs.data.polygon
pmacs.data.finnhub
pmacs.data.fred
pmacs.data.edgar_user_agent
pmacs.system.audit_replication_target  # rsync destination URL
pmacs.system.signing_key_ed25519       # private key for trade signing
pmacs.system.signing_key_pub_ed25519   # public key (also stored at ~/.pmacs/signing.pub for verification)
```

Read via `pmacs/storage/keychain.py`. Never logged. Never serialized. CI grep-fails on log lines or audit payloads containing values from these keys.

---

## 9. Deterministic engines

### 9.1 ArbitrationEngine

```python
# pmacs/schemas/arbitration.py
UNINFORMED_3STATE_BRIER = 0.667
    # Convention: Brier = sum((f_j - o_j)^2) for j in {up, flat, down}
    # Uninformed (1/3, 1/3, 1/3) vs truth (1, 0, 0): (2/3)^2 + (1/3)^2 + (1/3)^2 = 6/9 ≈ 0.667
    # Lower is better. Perfect = 0.0. All computations in PMACS use this convention.
WEIGHT_EPSILON = 0.05
MIN_HISTORICAL_N_FOR_MATURE = 30

class DirectionalProbability(BaseModel):
    source_name: str
    p_up: float = Field(ge=0.0, le=1.0)
    p_flat: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    historical_n: int = Field(ge=0)
    rolling_brier: float = Field(ge=0.0, le=2.0)

    @model_validator(mode="after")
    def _check_sum(self):
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"probabilities sum to {total}")
        return self

class Arbitrated(BaseModel):
    p_up: float = Field(ge=0.0, le=1.0)
    p_flat: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    weights: dict[str, float]
    decision: Literal["PROCEED", "ABORT_DISAGREEMENT", "ABORT_NO_MATURE_SOURCES",
                      "PROCEED_BOOTSTRAP_LOW_CONFIDENCE"]
    abort_reason: str | None
    matured_sources_used: int

    @model_validator(mode="after")
    def _check_sum(self):
        if self.decision in ("PROCEED", "PROCEED_BOOTSTRAP_LOW_CONFIDENCE"):
            total = self.p_up + self.p_flat + self.p_down
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"probabilities sum to {total}")
        return self
```

**MacroRegime weight multiplier:** MacroRegime contributes class-level (not ticker-specific) signal. Its weight in per-ticker arbitration is multiplied by 0.5x before Brier-inverse weighting. See `Agents.md §5.6`.

**Extreme-probability dampening (anti-injection defense):** Any persona producing `p_up > 0.9` or `p_down > 0.9` has its arbitration weight capped at 0.5x the Brier-inverse weight. This prevents a single hallucinated or injected extreme signal from dominating the consensus. See `Agents.md §19.2`.

**Bootstrap policy:** PAPER-mode resolutions count toward `historical_n`. When all sources are immature but agree on direction → `PROCEED_BOOTSTRAP_LOW_CONFIDENCE` with size haircut from SizingEngine. When immature and disagree → `ABORT_NO_MATURE_SOURCES`.

**Combination rule:** weighted average of probability vectors, weights = `1 / (rolling_brier + WEIGHT_EPSILON)` for mature sources. Renormalize to sum 1.0.

**Wave-2 roster extension (debate + audit):** Two advocate personas (BullAdvocate, BearAdvocate — `Agents.md §11b/§11c`) enter the pool as normal `DirectionalProbability` sources. They start **immature** (`historical_n=0`, `rolling_brier=0.667`) and are Brier-inverse-dampened until calibrated — no special multiplier is applied to them. The CrossPersonaAuditor (`Agents.md §11d`) does **not** emit a `DirectionalProbability` and never enters the pool; instead the orchestrator applies each `AuditorFlag` as a per-cycle `weight_multiplier` cap of `(1 - flag.severity)` on the offending persona's `ArbitrationSignal` *before* calling `arbitrate()`. The arbitration engine itself is unchanged — it already consumes `ArbitrationSignal.weight_multiplier` (default 1.0). When no auditor flags are present, arbitration results are identical to the pre-wave-2 baseline (the auditor is strictly additive).

### 9.2 ConvictionEngine

Implements `Source.md §7.2` operator-facing conviction.

```python
# pmacs/engines/conviction.py
def compute_conviction(arb: Arbitrated, crucible_severity: float, ev_multiple: float,
                       is_bootstrap: bool = False) -> float:
    """
    Maps internal probability + Crucible severity + EV to operator-facing scalar.
    See Source.md §7.2.

    Bootstrap floor: when is_bootstrap=True (PROCEED_BOOTSTRAP_LOW_CONFIDENCE),
    maturity_factor is floored at 0.25 so conviction can reach ~0.5 max,
    never exactly 0. This matches Source.md §7.2 promise.
    """
    direction = arb.p_up - arb.p_down

    if is_bootstrap:
        # Bootstrap floor: maturity_factor floored at 0.50 so conviction
        # can reach ~0.5 max (Source.md §7.2 promise). With perfect
        # direction=1.0, crucible=1.0, ev=1.0: 1.0 * 0.5 * 1.0 * 1.0 = 0.5
        maturity_factor = max(0.50, min(arb.matured_sources_used / 4.0, 1.0))
    else:
        maturity_factor = min(arb.matured_sources_used / 4.0, 1.0)

    crucible_factor = max(0.0, 1.0 - crucible_severity)
    ev_factor = min(ev_multiple / 1.5, 1.0)
    return direction * maturity_factor * crucible_factor * ev_factor

def verdict_tier(conviction: float, is_active_holding: bool, thesis_valid: bool) -> str:
    """
    Maps conviction (range: -1.0 to 1.0) to operator-facing verdict.

    Negative conviction (when p_down > p_up) always returns SKIP. The system
    does not short positions in v1 (Source.md §10), so a negative-direction
    signal is interpreted as 'avoid' for non-held names.

    For active holdings, negative conviction triggers EXIT_THESIS_INVALIDATED
    on weekly re-evaluation (handled by OpportunityCostEngine, not this function).
    """
    if is_active_holding and thesis_valid:
        return "HOLD"
    if conviction >= 0.6:
        return "STRONG_BUY"
    if conviction >= 0.3:
        return "BUY"
    # conviction < 0.3 OR negative
    return "SKIP"
```

**Unchanged by the wave-2 debate/audit layer.** The BullAdvocate/BearAdvocate personas and the CrossPersonaAuditor (`Agents.md §11b-§11d`) do **not** add a multiplier to this formula. Debate pressure enters as two extra arbitration voters (dampened until calibrated); auditor pressure enters as arbitration weight caps and Crucible-brief enrichment (`§9.1`, `Agents.md §16.4`). `compute_conviction`'s signature and output for any given `(Arbitrated, crucible_severity, ev_multiple, is_bootstrap)` are identical before and after wave-2 — the new layer only changes the *inputs* (which personas are in `Arbitrated`, what `crucible_severity` the enriched brief produces), never the function. This preserves Five Non-Negotiable #2 (LLMs never math) and #3 (deterministic arbitration).

### 9.3 SizingEngine

```python
# pmacs/engines/sizing.py
BOOTSTRAP_HAIRCUT = {
    0: 0.50, 1: 0.65, 2: 0.80, 3: 0.90,
    # 4+ : full size (1.0)
}

LIMITED_HISTORY_HAIRCUT = 0.50  # stacked on top of bootstrap

def size_position(x: SizingInputs) -> SizingResult:
    # Half-Kelly with safety margin
    kelly_fraction = compute_kelly(x.p_up, x.p_down, x.target_gain_pct, x.stop_loss_pct)

    # Negative Kelly = no edge; refuse to trade
    if kelly_fraction <= 0:
        return SizingResult(
            target_usd=0.0,
            target_shares=0.0,
            applied_haircuts={},
            abort_reason="NEGATIVE_KELLY_NO_EDGE",
        )

    safety_kelly = kelly_fraction * 0.5

    # Correlation factor (floor 0.3)
    correlation_factor = max(0.3, 1.0 - max(x.portfolio_correlations))

    # Bootstrap haircut
    n_mature = x.matured_sources_used
    bootstrap_factor = BOOTSTRAP_HAIRCUT.get(min(n_mature, 4), 1.0)

    # Limited-history haircut (stacked) — Source.md §8.2
    limited_factor = LIMITED_HISTORY_HAIRCUT if x.is_limited_history else 1.0

    # Composite
    target_pct = safety_kelly * correlation_factor * bootstrap_factor * limited_factor
    target_pct = min(target_pct, x.max_position_pct)
    target_usd = target_pct * x.portfolio_value_usd

    # Convert to shares (Alpaca paper supports fractional)
    target_shares = target_usd / x.current_price

    return SizingResult(
        target_usd=target_usd,
        target_shares=target_shares,
        applied_haircuts={
            "bootstrap": bootstrap_factor,
            "limited_history": limited_factor,
            "correlation": correlation_factor,
        },
    )
```

### 9.4 PricingEngine, CalibrationEngine, OpportunityCostEngine, FundamentalRoutingEngine, PortfolioRiskGate, CausalAttribution, OverrideLearning, CrucibleCalibration, FlywheelHealth

Standard implementations in `pmacs/engines/*.py`. Cross-field validation always via `@model_validator(mode="after")`. Each engine has a counterpart Pydantic schema in `pmacs/schemas/`. Each engine logs both audit events and debug events (preconditions §1.8).

Detailed signatures and invariants are documented in their module docstrings.

### 9.4b ReverseDcfEngine, ScenarioPriceEngine (deterministic valuation)

Two pure-Python, LLM-free valuation engines — peers of `ticker_metrics.py` (`Source.md §16.8`). Neither enters Arbitration (neither is a persona); neither amends the conviction formula. They provide the deterministic valuation anchor for the bull/bear debate and the memo.

**ReverseDcfEngine** (`pmacs/engines/reverse_dcf.py`): solves the growth rate the market is *implying* from the current price, then compares it to the GrowthHunter's estimated growth. Inputs are stored `EvidencePacket` primitives (price, shares, `annual_freeCashFlow` + `fcf_ttm_usd`, growth assumption from `GrowthHunterOutput.revenue_yoy_pct` or yfinance `revenueGrowthTTMYoy`) via the same `_extract` pattern as `pmacs/web/routes/ticker_data.py`. Gordon-style: `price = fcf_ttm * (1 + g) / (discount - g)` → solve for `g` = implied growth. Output `ReverseDcfResult`: `implied_growth_pct`, `assumed_growth_pct`, `growth_gap_pct`, `fair_value_usd`, `current_price_usd`, `valuation_lean` (BULLISH if implied < assumed — market is under-pricing growth; BEARISH if implied > assumed; NEUTRAL otherwise), `sensitivity`. No LLM, no network (Five Non-Negotiable #2/#4). When primitives are missing, returns a `NEUTRAL` result with a notes field — never fabricates.

**ScenarioPriceEngine** (`pmacs/engines/scenario_price.py`): consumes the `Arbitrated` probability vector plus the reverse-DCF fair value / valuation range and produces a probability-weighted expected price: `E[price] = p_up * bull_price + p_flat * base_price + p_down * bear_price`. Output `ScenarioPriceResult`: `bull_price`, `base_price`, `bear_price`, `expected_price_usd`. Feeds `MemoWriterOutput` only — it does **not** replace `compute_ev`'s `ev_multiple` (which is a trade-expectancy ratio, `§9.4 PricingEngine`, not a valuation multiple). The two are kept distinct and both surface in the memo.

Both engines require `cycle_id` and log to both audit and debug streams (`§1.8`, `§1.11`).

### 9.5 FailureDiagnosticEngine

The FDE is critical to the flywheel. Runs on every terminal-state Holding. Classifies into 1 of 18 outcome taxonomy types (specified in `Agents.md §15`). The 5 auditor-only reasoning-flaw types (`§15.4`) are emitted by the CrossPersonaAuditor at cycle time, not by `classify()`. Both write `FailedAssumption` nodes to KuzuDB. The Mutation Engine consumes these to target prompt/threshold mutations.

```python
# pmacs/engines/failure_diagnostic.py
from pmacs.schemas.failure import FailureClassification, FailedAssumption
from pmacs.schemas.contracts import Holding

def classify_and_record(holding: Holding, cycle_id: str) -> FailureClassification:
    """
    Runs on every terminal-state Holding (called from state_machine.transition).
    Classifies into 1 of 18 outcome taxonomy types (Agents.md §15). Auditor-only
    reasoning-flaw types (§15.4) are written by the CrossPersonaAuditor, not here.
    Writes FailedAssumption node to KuzuDB.
    """
    classification = _classify(holding)

    # Write to Kuzu
    fa_id = f"fa_{holding.id}_{cycle_id}"
    kuzu.execute("""
        CREATE (fa:FailedAssumption {
            id: $id, taxonomy: $tax, severity: $sev, ts: $ts,
            holding_id: $hid, cycle_id: $cid, summary: $summary
        })
        WITH fa
        MATCH (h:Holding {id: $hid})
        CREATE (h)-[:FAILED_ASSUMPTION]->(fa)
    """, {
        "id": fa_id,
        "tax": classification.taxonomy.value,
        "sev": classification.severity,
        "ts": datetime.utcnow(),
        "hid": holding.id,
        "cid": cycle_id,
        "summary": classification.summary,
    })

    log_audit("failed_assumption_recorded", {
        "cycle_id": cycle_id,
        "holding_id": holding.id,
        "taxonomy": classification.taxonomy.value,
        "severity": classification.severity,
    })

    return classification

def _classify(holding: Holding) -> FailureClassification:
    """The 18-type classifier. Logic specified in Agents.md §15."""
    # Implementation: deterministic rules based on holding.exit_reason,
    # price action analysis, evidence inspection, etc.
    # See Agents.md §15 for the rule definitions.
    ...
```

### 9.6 StateMachineEngine

See §8.2.

---

## 10. Mutation Engine (`pmacs-mutation` process)

### 10.1 Purpose

Active flywheel (`Source.md §10`). Continuously hunts for underperforming components, generates candidate variants, runs them as shadow A/B tests, and promotes the winners. **The single component that distinguishes PMACS's flywheel from passive learning systems.**

### 10.2 The five mutation dimensions

| Dimension | What mutates | Approval required |
|---|---|---|
| `prompts` | Persona system prompts (variants tested in SHADOW alongside production) | **No — operator confirmation** |
| `source_weights` | Per-source Brier-derived adjustments to arbitration weights | **No — operator confirmation** |
| `thresholds` | Conviction cutoffs, Crucible severity multipliers, bootstrap haircuts | **No — operator confirmation** |
| `persona_affinity` | Per-persona-per-ticker weight adjustment based on track record | **No — operator confirmation** |
| `universe_flags` | Tickers with chronic uncertainty get flagged for review | **No — operator review (no code change, just flag)** |

**All mutations require operator confirmation to apply.** The Mutation Engine is an advisor: it detects, hypothesizes, A/B tests, and recommends. It never auto-applies. This prevents the flywheel from degrading the base system. Auto-rollback on regression remains as a safety net for operator-approved mutations that underperform.

### 10.3 Lifecycle

```
Detection -> Hypothesis -> Shadow A/B (>= 20 cycles) -> Statistical test -> Classification ->
  \-- ALL mutations -> Settings page -> operator reviews evidence -> confirm -> applied
       |
       v
       30-cycle probation (locked from further mutation)
       |
       v
       Post-probation: eligible for new mutations, plus auto-rollback if 50-cycle
       performance regresses below pre-mutation baseline
```

### 10.4 Daemon loop

```python
# pmacs/mutation/daemon.py
def main_loop():
    while True:
        if mode_too_early_for_mutation():    # < 50 PAPER cycles
            sleep(3600)
            continue

        # 1. Detect: read FDE failures, persona Brier drift, regression in rolling metrics
        candidates = candidate_generator.generate(
            failure_clusters=read_recent_fde_clusters(),
            persona_drift=read_persona_brier_drift(),
            metric_regression=read_rolling_metric_regression(),
        )

        # 2. Stage proposals (idempotent — dedupe by candidate_hash)
        for c in candidates:
            sqlite.execute_or_skip("""
                INSERT OR IGNORE INTO mutation_proposals
                (id, dimension, target, candidate_payload, baseline_hash,
                 candidate_hash, proposed_at, proposer, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'mutation_engine', 'PROPOSED')
            """, c.as_row())

        # 3. Activate AB for any PROPOSED candidates
        for p in sqlite.query("SELECT * FROM mutation_proposals WHERE status = 'PROPOSED'"):
            ab_runner.start(p)
            sqlite.execute(
                "UPDATE mutation_proposals SET status='AB_RUNNING', ab_started_at=? WHERE id=?",
                (datetime.utcnow(), p.id),
            )

        # 4. Drive A/B for any AB_RUNNING proposals (collect outcomes per cycle)
        for p in sqlite.query("SELECT * FROM mutation_proposals WHERE status = 'AB_RUNNING'"):
            ab_runner.collect_outcomes_for_cycle(p, current_cycle_id())

        # 5. On A/B completion (>= sample_size_min cycles), run stat test
        for p in sqlite.query("""
            SELECT * FROM mutation_proposals
            WHERE status = 'AB_RUNNING' AND sample_size >= 20
        """):
            result = stat_test.run_welch_t_test(p)
            update_proposal_with_result(p, result)
            classify_outcome(p, result)

        # 6. Stage ALL significant results for operator review (no auto-promote)
        for p in sqlite.query("""
            SELECT * FROM mutation_proposals
            WHERE status = 'AB_COMPLETE_PROMOTE'
        """):
            sqlite.execute("UPDATE mutation_proposals SET status='OPERATOR_PENDING' WHERE id=?", (p.id,))
            sse.publish("mutation", "mutation.ready_for_review", {
                "proposal_id": p.id, "dimension": p.dimension,
                "target": p.target, "effect_size": p.effect_size, "p_value": p.p_value,
            })

        # 7. Rollback regressions (any promoted mutation post-probation showing regression)
        for promoted in sqlite.query("""
            SELECT mp.* FROM mutation_proposals mp
            WHERE mp.status = 'PROMOTED'
            AND (SELECT COUNT(*) FROM cycles c
                 WHERE c.closed_at > mp.promotion_at AND c.state = 'CLOSED') >= ?
        """, (config.mutation.probation_cycles,)):
            if rollback.regression_detected(promoted):
                rollback.execute(promoted, reason="50-cycle regression below baseline")

        sleep(60)
```

### 10.5 Candidate generation rules

Detailed in `Agents.md §17`. Brief summary:

- If `MOAT_DRIFT_OVERESTIMATE` cluster appears N≥5 times in 30 cycles → propose MoatAnalyst prompt mutation adding "consider competitive entry" directive.
- If `STOP_HUNTED` cluster appears N≥3 → propose stop-loss tightening or loosening thresholds.
- If a persona's rolling Brier degrades >0.05 over 30 cycles → propose prompt mutation introducing more evidence-citation requirements.
- If a source's weight drops below a threshold over time → propose source weight adjustment.

Generation is **rule-based, not LLM-generated, in v1**. (LLM-as-mutation-author deferred to v2 to avoid unconstrained self-modification.)

### 10.6 Statistical test

Welch's t-test on the metric distribution between control and candidate arms. Auto-promote requires ALL of:

```
p_value < 0.05
cohens_d >= 0.20
sample_size >= 20
effect_magnitude < 0.10  # mutation magnitude — avoid promoting drastic auto changes
```

Recommendations meeting these thresholds are surfaced to the operator in Settings → Mutation Engine. **The operator must confirm before any mutation is applied to production.**

### 10.7 Promotion

```python
# pmacs/mutation/promotion.py
def operator_promote(proposal_id: str):
    """Called from Settings page; requires explicit operator action."""
    proposal = load(proposal_id)
    apply_candidate_to_registry(proposal)
    log_audit("mutation_operator_promoted", {
        "proposal_id": proposal.id,
        "dimension": proposal.dimension,
        "target": proposal.target,
        "operator": True,
    })
    sqlite.execute("""
        UPDATE mutation_proposals
        SET status='PROMOTED', promotion_at=?
        WHERE id=?
    """, (datetime.utcnow(), proposal_id))
```

**Critical:** `apply_candidate_to_registry` is the only function that writes to `model_registry.json`. It is in `pmacs-nervous`, not `pmacs-mutation`. The mutation process never directly modifies production config (precondition §1.13).

### 10.8 Rollback

Triggered automatically when a promoted mutation post-probation (cycles > probation_cycles after promotion, default 30) shows the controlling metric (Brier or Sharpe) below the **baseline window** (last 50 cycles before promotion). Manual rollback button in Settings (operator-confirmed).

**Rollback window expiration:** Auto-rollback monitors a promoted mutation for 50 cycles after probation ends (cycles 30-80 post-promotion). After cycle 80, auto-monitoring stops and the mutation is considered 'naturalized' — it has accumulated enough evidence to be a baseline component. Manual rollback via Settings remains available indefinitely (operator-confirmed). The rollback_config remains stored in the mutation_proposals table forever; the operator can always manually revert any promoted mutation.

### 10.9 Activation timing

`pmacs-mutation` process starts on day 1 but its `main_loop` returns early until `mode_too_early_for_mutation()` is False. That gate clears at PAPER cycle count >= 50 (configurable in `config/mutation.toml` → `activation_after_paper_cycles`).

This ensures a stable baseline before mutation experiments begin (`Source.md §10` design choice Q-M2).

### 10.10 SHADOW-only execution

Candidate arms run in SHADOW only, never PAPER or LIVE. The same evidence and gatekeeper output feeds both arms; the candidate arm uses the candidate config; the control arm uses production config. Outcomes (Brier, would-have-PnL) are computed against actual price evolution post-cycle.

**Compute budget:** 5 tickers/cycle (rotated) × 7 analysis personas × ~90s per slot path × 2 arms = ~2,700s (~45min) added to cycle. Tight on a 1.8h base cycle but fits within the 18,000s daily budget (`config/resources.toml`).

### 10.11 Visibility

Mutation Engine UI (Settings → Mutation Engine, `Source.md §20.8`):
- Hidden during the first 5 cycles of A/B (per Q-M3 design)
- After cycle 5: visible with progress bar (sample_size / sample_size_min) and trending direction (current candidate-control delta with confidence interval)
- After stat-sig: full results displayed with promote / reject buttons (operator confirmation for substantive mutations)

---

## 11. StopLossMonitor (`pmacs-stoploss` process)

### 11.1 Two-layer architecture

- **Primary (PMACS-managed):** StopLossMonitor process. Tight stops based on thesis/ATR. Triggered via SQLite handoff to Nervous → math sign → execution submit.
- **Catastrophe net (broker-managed):** Wide stop (15% below entry) submitted at position open. Fires only if PMACS offline.

This implements `Source.md §3` exit priority and `Source.md §4` promise 1 (every decision auditable).

### 11.2 Execution path

```
1. StopLossMonitor (pmacs-stoploss process)
   - Fetches Alpaca quote every 30 minutes during RTH (cached 60s per config)
   - Compares to holding.stop_loss_price
   - If breached: writes StopTrigger to SQLite stop_event with status=PENDING, fsync
   - log_debug STOP_LOSS_TRIGGERED
   - Continue monitoring (don't block)

2. Nervous System poller (every 10s during RTH)
   - Polls SQLite for status=PENDING StopTriggers
   - For each: constructs TradePlan
     side=SELL, order_type=MARKET (intraday breach)
     OR MARKET_ON_OPEN (overnight gap detected at open)
   - Sends unsigned TradePlan to Execution via UDS
   - Execution signs with Ed25519 and submits to broker
   - Updates StopTrigger to status=SUBMITTED

3. Execution Service
   - Verifies signature
   - Submits to Alpaca paper / live
   - Returns fill report
   - Nervous updates StopTrigger to status=FILLED
   - State machine transitions Holding to STOPPED_OUT
```

### 11.3 Gap-down execution

```python
# pmacs/engines/stop_loss_monitor.py
def determine_order_type(stop_trigger: StopTrigger, market_state: str) -> str:
    if market_state == "RTH":
        return "MARKET"
    if market_state in ("PRE_OPEN", "AFTER_HOURS", "CLOSED"):
        return "MARKET_ON_OPEN"
    raise ValueError(f"unknown market_state: {market_state}")
```

When fill price is more than 2% below stop level, log `STOP_LOSS_GAP_DOWN`; aggregated as a quality metric in DuckDB.

### 11.4 Trailing stop arming

```python
def maybe_arm_trailing(holding: Holding, current_price: float, atr_20: float):
    profit_r = (current_price - holding.entry_price) / (holding.entry_price - holding.stop_loss_price)
    if not holding.trailing_stop_armed and profit_r > 1.5:
        holding.trailing_stop_armed = True
        holding.trailing_stop_price = current_price - 1.0 * atr_20
        log_audit("trailing_stop_armed", {
            "holding_id": holding.id,
            "trailing_at": holding.trailing_stop_price,
            "profit_r": profit_r,
        })

def maybe_ratchet_trailing(holding: Holding, current_price: float, atr_20: float):
    if not holding.trailing_stop_armed:
        return
    new_trailing = current_price - 1.0 * atr_20
    if new_trailing > holding.trailing_stop_price:
        holding.trailing_stop_price = new_trailing
        # No audit on every ratchet; logged on exit
```

Trailing only ratchets up; never down.

### 11.5 Catastrophe-net cancellation

When ANY primary exit path fires (PMACS-managed stop-loss, trailing stop, thesis-invalidation exit, opportunity-cost exit, manual operator exit), `pmacs-execution` MUST cancel the broker-side catastrophe-net stop BEFORE submitting the new SELL order. This prevents duplicate fills.

```python
def execute_exit(holding: Holding, exit_reason: str):
    # 1. Cancel broker-side catastrophe-net stop
    if holding.catastrophe_net_order_id:
        try:
            broker.cancel_order(holding.catastrophe_net_order_id)
        except BrokerError as e:
            # Cancel failed — engage kill switch (potential duplicate-fill risk)
            log_debug("CATASTROPHE_CANCEL_FAILED", level="CRITICAL", payload={...})
            kill_switch.engage(trigger="CATASTROPHE_CANCEL_FAILED")
            raise

    # 2. Submit primary exit order
    submit_market_sell(holding)

    # 3. Audit
    log_audit("catastrophe_net_cancelled", {"holding_id": holding.id, "reason": exit_reason})
```

If broker cancel fails (network issue, broker downtime), the system engages the kill switch immediately — risking duplicate fills is worse than halting trading.

### 11.6 Trailing stop execution path

When a trailing stop is breached, the path is identical to §11.2 (primary stop-loss path) with these differences:
- `stop_events.stop_price` = `holding.trailing_stop_price` (not `holding.stop_loss_price`)
- State machine transitions to `EXIT_TRAILING_STOP` (not `STOPPED_OUT`)
- FDE classifies as a distinct terminal state (may produce STOP_HUNTED or STOP_LOSS_CORRECT depending on post-exit price action)

---

## 12. Cycle orchestration (canonical sequence)

Boot-driven (§4.5). Each cycle is one full pass. The sequence below is the **single canonical order**; deviating breaks invariants.

```
0.   Acquire cycle lock (flock /var/db/pmacs/cycle.lock); fail-fast if held
0.5. ClockMonitor.check_drift()
1.   Resume checkpoint if cycle_id == today (see §12.3 for resume protocol)
2.   FxSnapshot.capture() (ECB rate)
3.   CorporateActions.process_all_active_holdings()
4.   KillSwitch.check()
5.   FlywheelHealth.snapshot()
6.   MacroRegime.run()
7.   CatalystResolutionDetector.run_all()
8.   UniverseSyncer.maybe_check_admittance()  (no auto-rotate; flag only)
9.   Gatekeeper.scan_universe()
10.  LessonsEngine.run_daily_flagger()
11.  OverrideLearning.cluster_recent_overrides()
12.  Queue.compose(...)
       # Queue scoring: priority_score = (catalyst_imminence * 3.0)
       #                                + (thesis_strength * 2.0)
       #                                + (source_brier_avg * 1.5)
       #                                + (portfolio_fit * 1.0)
       # Operator pins override score (pinned tickers sort first within their band).
       # Priority bands (P1-P4) override score across bands.
       # Active holdings always in P1 for re-evaluation.
13.  foreach symbol in queue:
       state_machine.transition(holding, PHASE1_RESEARCH, "phase1 start", cycle_id, op_seq++)
       MemoryEngine.check_antipattern()
       EpisodicContext.inject()                     # load 5/30/90-day rolling brief
       Phase1.run()                                 # 7 analysis personas in parallel (3 slots)
       Arbitration.combine()                        # cycle_id required
       state_machine.transition(holding, PHASE2_CRUCIBLE, ...)
       Phase2.crucible(self-calibrated severity, CPS budget)
       Pricing.compute_ev()
       state_machine.transition(holding, APPROVED_PENDING, ...)
       Sizing.size()                                # reads matured_sources_used + limited_history
       Conviction.compute()                         # Source.md §7.2
       PortfolioRiskGate.evaluate()
       MemoWriter.emit()                            # MUST run last in per-symbol pipeline; reads ALL persona outputs + Arbitrated + Crucible + Conviction + Verdict; writes operator-facing memo
       ScanRecord.write()                            # per-ticker cycle result snapshot to DuckDB
       if approve and not blocked:
         state_machine.transition(holding, ACTIVE, "approved", cycle_id, op_seq++)
         TradePlan.sign_and_send()                  # Alpaca paper in PAPER mode
         brokers.submit_catastrophe_net_stop()      # 15% wide failsafe
14.  WeeklyReeval.run_if_due()                       # active holdings with weekly cadence
15.  ThesisAging.run_if_90d()                        # mandatory 90d re-eval
16.  Execution.process_fills()
17.  ReconciliationEngine.reconcile()
18.  foreach active holding:
       OpportunityCostEngine.decide_hold_or_exit()
19.  Calibration.evaluate_and_maybe_refit()
20.  CrucibleCalibration.update_multipliers()
21.  CausalAttribution.attribute_resolutions()
22.  Memory.record_resolutions()
23.  LessonsEngine.run_lesson_writer_queue()
24.  OverrideLearning.evaluate_recent_outcomes()
25.  FailureDiagnostic.classify_pending_terminal_states()  # also fires inline in state_machine.transition
26.  Cortex.snapshot_drift_stats()
27.  ConsistencyReconciler.cross_db_audit()
28.  DeadLetter.process_queue()
29.  Audit.close_cycle()
30.  Release cycle lock
```

### 12.3 Cycle resume protocol

When `pmacs-nervous` starts a cycle, it first checks for an existing OPEN cycle for today's date:

```python
# pmacs/nervous/checkpoint.py
def maybe_resume_cycle() -> str | None:
    open_cycle = sqlite.query_one(
        "SELECT cycle_id FROM cycles WHERE state='OPEN' AND DATE(opened_at)=?",
        (date.today(),),
    )
    if not open_cycle:
        return None

    cycle_id = open_cycle.cycle_id
    log_audit("cycle_initiated_by_resume", {"cycle_id": cycle_id})

    # Replay completed operations (idempotent via op_idempotency)
    completed_ops = sqlite.query(
        "SELECT op_seq, op_type FROM op_idempotency WHERE cycle_id=? ORDER BY op_seq",
        (cycle_id,),
    )
    last_op_seq = max((o.op_seq for o in completed_ops), default=0)

    # Resume from last_op_seq + 1
    return cycle_id, last_op_seq

# Each step in the cycle sequence checks op_idempotency before executing.
# If (cycle_id, op_seq, op_type) exists in op_idempotency, the step is a no-op.
# Otherwise, it runs and writes to op_idempotency on success.
```

**State preserved across crashes:**
- Cycle state in `cycles` table (OPEN/CLOSED)
- Per-symbol completion state in `queue` table (`started_at`, `completed_at`)
- Per-operation idempotency in `op_idempotency` table
- Persona outputs in DuckDB `scan_records`
- Holding state transitions in audit log + `holdings` SQLite table

**State NOT preserved:** in-flight LLM calls (>30s old). On resume, in-flight calls are aborted; the affected ticker re-runs from the beginning of its Phase 1.

### 12.1 Exit priority (highest first)

1. Kill switch
2. Stop-loss (PMACS-managed via StopLossMonitor)
3. Catastrophe-net stop (broker-side, only fires if PMACS offline)
4. Corporate action (halt, delisting)
5. Catalyst resolution
6. Catalyst invalidation (maps to EXIT_THESIS_INVALIDATED when catalyst evidence contradicts thesis)
7. Thesis invalidation (90d review or weekly re-eval)
8. Opportunity-cost exit
9. Trailing stop (armed > 1.5R)

**No time-decay forced exit** — replaced with thesis-aging review trigger (`Source.md §7.1`).

### 12.2 Per-symbol Phase 1 sub-sequence

For each symbol in queue (step 13):

```
a. State transition to PHASE1_RESEARCH (audited)
b. EpisodicContext.inject() — pull 5/30/90-day rolling brief
c. Parallel persona dispatch via llama-server's 3-slot pool:
   slots[0]: MacroRegime + CatalystSummarizer (sequential within slot)
   slots[1]: MoatAnalyst + GrowthHunter (sequential within slot)
   slots[2]: InsiderActivity + ShortInterest + Forensics (sequential)
d. Each persona output goes through:
   - GBNF parse (or JSON Schema parse for Ollama)
   - Pydantic validation
   - Sanity validator
   - On any failure: log debug, retry up to 2x, then ABORT_LLM
e. Wait for all personas to complete or timeout (Phase 1 budget: 270s)
f. If timeout: state transition to PHASE1_TIMEOUT then ABORTED_LLM
g. Wave-2 dispatch (BullAdvocate + BearAdvocate + CrossPersonaAuditor) in parallel,
   each receiving the FROZEN wave-1 persona outputs as peer context (§14.4):
   - advocates go through the same 3-layer contract (GBNF → Pydantic → sanity) and emit
     a DirectionalProbability each
   - the auditor emits AuditorFlags (no probabilities)
   - wave-2 budget: `debate_wave_seconds_per_symbol` (default 180s); on timeout the
     wave-2 agents are skipped and the cycle proceeds with wave-1 only (graceful degrade)
h. Auditor flags applied: per-cycle `weight_multiplier` cap on flagged personas' signals;
   flags stashed for Crucible-brief injection (§16.4) and FDE write (§15.4)
i. Else: proceed to Arbitration (wave-1 7 DPs + 2 advocate DPs, with auditor weight caps)
```

The persona dispatch is parallel within slots and concurrent across slots. Wave-1 wall-clock ~30s for the longest-running persona path × 3 slots = ~90s typical, ~270s worst-case. Wave-2 adds one sequential stage (~90-180s) because the advocates/auditor depend on wave-1 outputs; it cannot overlap wave-1. Total per-symbol Phase 1 budget is bumped accordingly in `config/resources.toml` (`debate_wave_seconds_per_symbol` + `daily_llm_seconds_total`). Wave-2 is strictly additive: if it times out or all three agents abort, the cycle falls back to the pre-wave-2 path (7 personas → arbitrate → crucible) with no behavior change.

---

## 13. Kill switch

### 13.1 Triggers (Cortex engages on ANY of)

1. Audit chain integrity failure (immediate)
2. Rolling 5-day loss exceeds 10% of equity
3. Single-day MtM loss exceeds 5% of equity (24h pause first; if persists, kill)
4. Reconciliation mismatch in LIVE_* mode > $100 absolute or 5% of position
5. Broker authentication failure (suggests credential compromise)
6. Disk free < 2GB
7. NTP clock drift > 60s
8. Cortex meta-monitor unresponsive > 120s
9. Process crash loop detected (>5 restarts in 60s)
10. Model file integrity check failed on startup

### 13.2 Disengagement

Requires:
- Explicit operator action from the Cortex page
- Explicit acknowledgment of trigger reason (typed reason field)
- Cortex confirms underlying condition resolved
- Audit log entry with operator identity (operator-set)

While engaged: no new positions; no normal exits (only stop-loss and catastrophe-net fire); StopLossMonitor continues running.

### 13.3 Mutation rollback on kill

When kill switch engages, the most recent N=3 mutation promotions are flagged for review. Operator can rollback any/all on disengage from the kill switch panel (Cortex page).

### 13.4 Kill switch state machine

```python
# pmacs/cortex/kill_switch.py
class KillSwitchState(str, Enum):
    ARMED = "ARMED"
    ENGAGED = "ENGAGED"

def engage(trigger: str, reason: str):
    """Engagement does NOT require operator confirmation; engagement is the safer option."""
    sqlite.execute("""
        UPDATE kill_switch SET state='ENGAGED', engaged_at=?, engaged_trigger=?, engaged_reason=?
    """, (datetime.utcnow(), trigger, reason))
    log_audit("kill_switch_engaged", {"trigger": trigger, "reason": reason})
    # Halt nervous orchestrator
    nervous.halt()
    # SSE broadcast
    sse.publish("system", "system.kill_switch_engaged", {"trigger": trigger, "reason": reason})

def disengage(operator_reason: str):
    """Disengagement requires explicit operator action and an operator-typed reason."""
    if not condition_resolved():
        raise RuntimeError("kill switch condition not yet resolved")
    sqlite.execute("""
        UPDATE kill_switch SET state='ARMED', disengaged_at=?, disengaged_by_operator=1, disengaged_reason=?
    """, (datetime.utcnow(), operator_reason))
    log_audit("kill_switch_disengaged", {"operator_reason": operator_reason})
    nervous.resume()
    sse.publish("system", "system.kill_switch_disengaged", {"operator_reason": operator_reason})
```

---

## 14. Cross-DB consistency and dead letter

**Consistency model:** Best-effort with end-of-cycle reconciliation.

Order of writes within a transaction-spanning operation:
1. KuzuDB (graph source of truth)
2. Qdrant (vector index)
3. DuckDB (analytics)
4. SQLite (OLTP)

On Qdrant write failure: KuzuDB is committed; Qdrant write queued in dead-letter for retry. End of cycle, `ConsistencyReconciler.cross_db_audit()` validates all foreign-key-like references.

### 14.1 Dead-letter queue

```sql
CREATE TABLE dead_letter (
    id INTEGER PRIMARY KEY,
    op_type TEXT NOT NULL,
    target_db TEXT NOT NULL,           -- 'qdrant' / 'duckdb' / 'kuzu' / 'sqlite'
    payload TEXT NOT NULL,             -- JSON
    queued_at TIMESTAMP NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMP,
    last_error TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING'  -- PENDING / RETRYING / FAILED / RESOLVED
);
CREATE INDEX idx_dead_letter_status ON dead_letter(status);
```

**Backoff:** 1s, 5s, 30s, 5min, 1h, 1d. After 6 attempts: status=FAILED, alert (`DEAD_LETTER_QUEUED` debug + audit).

### 14.2 Cross-DB audit

End of every cycle, `ConsistencyReconciler.cross_db_audit()` runs:
- For every Holding in Kuzu, verify SQLite has corresponding row (and vice versa)
- For every thesis_embedding_id in Holdings, verify Qdrant has corresponding vector
- For every Resolution in DuckDB, verify Kuzu has corresponding node
- Mismatches → `CROSS_DB_INCONSISTENCY` debug, written to `consistency_drift` table for operator review

---

## 15. Memory hierarchy

Implements `Source.md §10.1` flywheel architecture. Four-tier model.

| Tier | Horizon | Stores | Purpose |
|---|---|---|---|
| **Working** | Current cycle | SQLite scratch tables (`queue`, `op_idempotency`, in-flight Pydantic objects) | Per-cycle pipeline state |
| **Episodic** | Rolling 5/30/90-day | DuckDB `rolling_metrics`, `persona_performance`, `persona_ticker_affinity`, `persona_subsector_affinity` | Recent regime, recent overrides, recent failures, mutation candidate performance |
| **Semantic** | Full history | KuzuDB graph + Qdrant vectors + DuckDB `resolutions_history` | Lineage, embeddings, calibration corpus |
| **Immutable** | Forever | `audit.log` | Hash-chained record of every decision |

### 15.1 Episodic context injection

The short-term memory enhancement for the flywheel. Each persona run, `EpisodicContext.inject()` reads:

- Recent regime shift events (last 30 cycles)
- Recent FailedAssumption nodes for this ticker or sector
- Recent operator overrides on similar setups (Hamming-distance match against feature vector)
- Persona's own Brier on this ticker (from `persona_ticker_affinity`)
- Persona's own Brier on this sub-sector (from `persona_subsector_affinity`)

Compresses to a 200-word "context brief" prepended to the persona prompt as a system-message append. This is the **short-term memory enhancement** that powers the flywheel (`Source.md §10`).

Logged via audit `episodic_context_injected` event with content hash (not the full content — that goes to debug log only).

The detailed mechanics (template, retrieval logic, ranking) are in `Agents.md §18`.

---

## 16. Anti-patterns

Things Claude Code must NOT do. CI grep-fails or runtime-asserts on each. Adding to this list requires PR review.

### 16.1 Direct state mutation
```python
# ❌ FORBIDDEN
holding.state = "ABORTED_LLM"

# ✅ REQUIRED
state_machine.transition(holding, HoldingState.ABORTED_LLM, reason, cycle_id, op_seq)
```

### 16.2 Float serialization without canonical_json
```python
# ❌ FORBIDDEN — all audit writes go through audit_write() wrapper
json.dumps(payload)  # in any audit-path code

# ✅ REQUIRED
canonical_json(payload)  # with explicit float rounding (10 dp)
```

### 16.3 Custom token bucket logic per source
```python
# ❌ FORBIDDEN
if last_request_time + 0.2 < now: ...

# ✅ REQUIRED
BUCKETS["polygon"].acquire()
```

### 16.4 Packet mutation in staleness check
```python
# ❌ FORBIDDEN
def assert_fresh(packet):
    packet.is_fresh = age <= budget  # mutating the packet
    return None

# ✅ REQUIRED
def assert_fresh(packet) -> FreshnessResult:
    return FreshnessResult(fresh=..., degraded=..., source=..., age=...)
```

### 16.5 cycle_id optionality
```python
# ❌ FORBIDDEN
def arbitrate(probs, cycle_id=None): ...

# ✅ REQUIRED
def arbitrate(probs, cycle_id: str): ...   # cycle_id REQUIRED on every audit-emitting op
```

### 16.6 Bootstrap mishandling (day 1 abort cascade)
```python
# ❌ FORBIDDEN
if all_sources_immature: return ABORT_NO_MATURE_SOURCES

# ✅ REQUIRED
if all_sources_immature and all_agree_direction:
    return PROCEED_BOOTSTRAP_LOW_CONFIDENCE  # with size haircut
elif all_sources_immature and disagreement:
    return ABORT_NO_MATURE_SOURCES
```

### 16.7 Tight broker-side stops (stop-hunting visibility)
```python
# ❌ FORBIDDEN
brokers.submit_stop(symbol=ticker, stop_price=tight_price)  # visible to stop-hunters

# ✅ REQUIRED
# Primary stop monitored by pmacs-stoploss (off-broker)
# Broker-side stop is only the catastrophe-net (15% wide failsafe)
```

### 16.8 Ambiguous FX direction
```python
# ❌ FORBIDDEN (ambiguous)
class FxRate(BaseModel):
    eur_per_usd: float

# ✅ REQUIRED (matches ECB convention)
class FxRate(BaseModel):
    usd_per_eur: float  # 1 EUR = X USD
```

### 16.9 Mutation Engine writing production state directly
```python
# ❌ FORBIDDEN — pmacs-mutation has no write access to production config
# pmacs/mutation/promotion.py
def promote(p):
    open("config/model_registry.json", "w").write(...)

# ✅ REQUIRED — pmacs-nervous performs the write triggered by mutation engine signal
# The mutation engine writes a row to mutation_proposals with status='AB_COMPLETE_PROMOTE';
# pmacs-nervous reads that row and applies the change.
```

### 16.10 Mutation A/B running in PAPER alongside production trades
```python
# ❌ FORBIDDEN
ab_runner.execute_in_mode("PAPER")  # candidate arm submitting paper trades

# ✅ REQUIRED
ab_runner.execute_in_mode("SHADOW")  # candidate arm runs SHADOW-only
# Outcomes computed against actual price evolution post-cycle
```

### 16.11 Code-versioned prompts edited at runtime
```python
# ❌ FORBIDDEN — operator UI directly edits prompt file
@app.post("/settings/persona/{name}/prompt")
def update_prompt(name, new_prompt):
    open(f"prompts/{name}.md", "w").write(new_prompt)

# ✅ REQUIRED — operator submission stages a candidate
@app.post("/settings/persona/{name}/propose_mutation")
def propose_mutation(name, new_prompt):
    sqlite.execute("INSERT INTO mutation_proposals ... status='PROPOSED'", ...)
```

### 16.12 Backtesting against historical LLM outputs
```python
# ❌ FORBIDDEN — model's training data contains the test period
def backtest(start_date="2023-01-01"):
    for cycle_date in date_range(start_date, today):
        run_full_pipeline_with_current_llm(cycle_date)
        ...

# ✅ REQUIRED — only forward-test in SHADOW
# SHADOW mode runs the system in real-time on current market data with no execution.
```

### 16.13 Logging secrets
```python
# ❌ FORBIDDEN
log.info(f"connecting to broker with key={api_key}")

# ✅ REQUIRED — secrets never logged
log.info("connecting to broker")
```

### 16.14 Uncategorized error
```python
# ❌ FORBIDDEN — error_code missing on WARN+
log_debug(level="ERROR", msg="something failed")

# ✅ REQUIRED — every WARN+ has a canonical error_code from §5.5
log_debug(level="ERROR", error_code="LLM_TIMEOUT", msg="...")
```

---

## 17. Configuration files

### 17.1 `config/resources.toml`

```toml
[hardware]
ram_gb = 64
cpu_cores = 10
gpu = "apple_m1_max"

[runtime]
backend = "llama_server"
parallel_slots = 3
ctx_size = 32768
quantization = "UD-Q4_K_XL"

[budgets]
phase1_seconds_per_symbol = 270
daily_llm_seconds_total = 18000

[stop_loss]
intraday_check_interval_seconds = 1800
quote_freshness_max_seconds = 60

[crash_loop]
max_restarts_per_minute = 5
broken_state_requires_manual_clear = true

[catastrophe_net]
catastrophe_stop_pct = 0.15
```

### 17.2 `config/risk.toml`

```toml
[position]
max_single_position_pct = 0.20
max_concurrent_positions = 5

[kill_switch]
daily_loss_pct = 0.05
rolling_5d_loss_pct = 0.10
reconciliation_tolerance_usd = 100
reconciliation_tolerance_pct = 0.05

[ev]
minimum_ev_pct = 0.01

[sizing]
half_kelly = true
correlation_floor = 0.30
```

### 17.3 `config/crucible.toml`

```toml
[budget]
seconds_per_attack = 90
max_rewrite_cycles = 2
default_on_budget_exhaust = "NO_TRADE"

[severity]
flip_threshold = 0.60     # severity above which Crucible's verdict overrides
```

### 17.4 `config/mutation.toml`

```toml
[activation]
activation_after_paper_cycles = 50

[ab]
sample_size_min = 20
shadow_tickers_per_cycle = 5

[recommendation]
# All mutations require operator confirmation. No auto-promote.
stat_sig_p_threshold = 0.05
stat_sig_cohens_d_min = 0.20
stat_sig_sample_min = 20

[probation]
cycles = 30
auto_rollback_window = 50

[dimensions]
prompts = "operator_only"
source_weights = "auto_eligible"
thresholds = "operator_only"
persona_affinity = "auto_eligible"
universe_flags = "flag_only"
```

### 17.5 `config/model_registry.json`

```json
{
  "backends": {
    "llama_server": {
      "url": "http://127.0.0.1:8080",
      "default_model": "unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_XL",
      "structured_output": "gbnf"
    },
    "ollama": {
      "url": "http://127.0.0.1:11434",
      "default_model": "qwen3.6:35b-a3b-coding-mxfp8",
      "structured_output": "json_schema"
    }
  },
  "active": "llama_server",
  "personas": {
    "gatekeeper": null,
    "macro_regime": "default",
    "catalyst_summarizer": "default",
    "moat_analyst": "default",
    "growth_hunter": "default",
    "insider_activity": "default",
    "short_interest": "default",
    "forensics": "default",
    "crucible": "default"
  },
  "candidates": {}
}
```

The `candidates` field is populated by the Mutation Engine for in-flight A/B tests.

### 17.6 `config/model_hashes.toml`

```toml
[gguf]
"Qwen3.6-35B-A3B-Q4_K_XL" = "<sha256>"

[mmproj]
# only if multimodal model is added
```

Mismatch on startup → kill switch (precondition §1, trigger #10).

### 17.7 `config/notification.toml`

Maps the events in `Source.md §13.5` to surface/sound levels. Editable via Settings → General.

### 17.8 `config/source_criticality.toml`

```toml
"polygon.ohlcv.eod"    = "CRITICAL"
"alpaca.bar.intraday"  = "CRITICAL"
"alpaca.quote"         = "IMPORTANT"
# ... (full list in §6.3)
```

---

## 18. Security model

### 18.1 Network egress control

Enforced via macOS `pf` (Packet Filter):

```
# /etc/pf.anchors/com.pmacs
block out quick from any to any group _pmacs_inference
block out quick from any to any group _pmacs_cortex
block out quick from any to any group _pmacs_dashboard
block out quick from any to any group _pmacs_mutation

# Nervous: data API allowlist
pass out from any to {
  api.polygon.io, finnhub.io, www.sec.gov, data.sec.gov,
  api.openfda.gov, paper-api.alpaca.markets, data.alpaca.markets,
  api.stlouisfed.org, www.ecb.europa.eu
} group _pmacs_nervous

# Execution: broker-only
pass out from any to {
  paper-api.alpaca.markets, api.alpaca.markets,
  ndcdyn.interactivebrokers.com   # IBKR (when LIVE)
} group _pmacs_exec

# StopLoss: quote API only
pass out from any to {
  data.alpaca.markets
} group _pmacs_stoploss
```

Verified at runtime by `ops/verify_isolation.py` (cron'd).

### 18.2 Process user/group isolation

Each process runs as a dedicated macOS user (`_pmacs_<role>`). UDS sockets, file system permissions, and `pf` rules all key off these UIDs/GIDs. No process can write outside its scoped paths.

### 18.3 Secret management

All secrets in macOS Keychain with `pmacs.<category>.<key>` naming (§8.8). Read via `pmacs/storage/keychain.py` which:
- Calls `security find-generic-password` under the process's own UID
- Caches in-process for single use, never written to disk
- Wipes on process exit (best-effort; macOS handles process memory cleanup)

### 18.4 Operator action authorization

This is a single-operator, loopback-only system; there is no second-factor authentication gate. Sensitive writes still require an explicit operator action through the dashboard (a confirmation step, and a typed reason where noted) and every such action is hash-chain audited. The actions requiring explicit operator confirmation (exhaustive list matching Source.md §6 decision rights matrix):
- Mode promotion (PAPER → PAPER_VALIDATED → LIVE_*)
- Kill switch disengage
- Universe ticker add
- Persona enable/disable
- Broker credential change
- Risk threshold change
- Mutation operator-promotion
- Audit log replication target change

### 18.5 Ed25519 trade signing

Keypair in Keychain. Private key only readable by `_pmacs_exec` (math process) UID. Every TradePlan signed before sent to broker adapter; signature verified at submission. Tamper-detected TradePlans are rejected with `INTERNAL_ASSERTION` audit event.

### 18.6 Dashboard CSRF and XSS

- CSRF: every write endpoint requires a same-origin token for sensitive ops. Origin checked against `localhost:8000`.
- XSS: HTMX templates use Jinja2 autoescape. JSON responses use `Content-Type: application/json` with `X-Content-Type-Options: nosniff`.
- CSP: strict policy disallowing inline scripts and external resources.

---

## 19. Testing strategy

### 19.1 Layers

| Layer | Tool | Coverage target |
|---|---|---|
| Unit | pytest + hypothesis (property-based for math) | 90% line, 100% on engines/state_machine.py and engines/conviction.py |
| Integration | pytest + Docker fixtures (Kuzu, Qdrant, Postgres-as-DuckDB-substitute for CI speed) | All cycle stages |
| Property | hypothesis | Probability invariants, FX symmetry, idempotency keys |
| End-to-end | pytest + synthetic fixture cycle | One full cycle on synthetic data (matches wizard step 10) |
| Mutation eval | custom harness (`tests/mutation_eval/`) | Mutation A/B tests on offline replays |

### 19.2 Critical test invariants

The following invariants are CI-verified on every PR:

- Probability vectors sum to 1.0 ± 1e-6 everywhere
- `holding.state` only changes via `state_machine.transition`
- Every `audit_write(...)` call passes a `cycle_id` (except whitelisted system events)
- Every `log_debug(level >= "WARN", ...)` call passes a canonical `error_code`
- No imports of broker SDK code outside `pmacs/execution/`
- No imports from `pydantic.v1`
- `canonical_json` produces byte-identical output across multiple runs of the same input
- Audit chain genesis-to-head verifies cleanly on smoke test fixtures
- All staleness budgets and source criticalities have entries (no `KeyError` paths)

### 19.3 Performance regression tests

A subset of integration tests run with timing assertions:
- Phase 1 per symbol: ≤ 270s on M1 Max (declared; tests record actual)
- Audit chain verify of 100K entries: ≤ 5s
- Cross-DB reconciliation on 10K holdings: ≤ 30s

Regressions fail CI.

### 19.4 Smoke-test fixtures

Synthetic ticker (`SMKT`) with deterministic OHLCV, fake earnings, fake insider transactions. Used in:
- Wizard step 10 (operator-facing smoke test)
- Pre-merge CI (every PR runs the fixture cycle)
- Post-deploy validation (CLI command `pmacs smoke-test`)

---

## 20. Performance budget

### 20.1 Per-cycle budget (M1 Max 64GB)

| Phase | Time | Notes |
|---|---|---|
| Phase 0 gatekeeper (deterministic, full universe) | ~5s | |
| Phase 1 per symbol (7 analysis personas, 3 parallel slots) | ≤ 270s | |
| Phase 1 total (assume 20 admitted symbols) | ≤ 5,400s (1.5h) | |
| Crucible (15 active) | ~900s (15min) | |
| MacroRegime + supporting | ~120s | |
| Resolution / calibration / engines | ~60s | |
| Mutation A/B (5 ticker rotation) | ~2,700s (45min) | when active |
| **Total typical** | ~9,200s (~2.5h) | well within 18,000s daily budget |

### 20.2 Memory budget

| Component | RAM |
|---|---|
| Qwen3.6-35B-A3B Q4_K_XL | ~21GB |
| KV cache (3 slots × 32K ctx) | ~8GB |
| llama-server overhead | ~2GB |
| All pmacs-* Python processes combined | ~3GB |
| Embedding model (bge-base-en-v1.5, CPU) | ~1.2GB |
| KuzuDB / Qdrant / DuckDB / SQLite buffers | ~6GB |
| macOS reserved | ~8GB |
| **Used** | ~49GB |
| **Headroom** | ~15GB |

### 20.3 Disk budget

| Item | Size at 1 year |
|---|---|
| Audit log (uncompressed first 7 days, gzipped after) | ~8GB |
| Debug log (30 day rotation) | ~2GB |
| KuzuDB | ~5GB |
| Qdrant | ~3GB |
| DuckDB | ~4GB |
| SQLite | ~1GB |
| Model GGUF | ~21GB |
| **Total** | ~44GB |

Disk-low kill-switch threshold: 2GB free. Operator should monitor.

---

## 21. Architectural Decision Records (ADRs)

ADRs capture decisions with their context and rationale. Stored in `docs/adr/`.

### ADR-001: Dashboard process is separate from Nervous

**Context:** Both run FastAPI on localhost. Merging would simplify deployment.
**Decision:** Keep separate.
**Rationale:** Attack surface isolation. Dashboard handles browser traffic constantly; Nervous holds orchestration logic and write access. A vulnerability in dashboard cannot escalate to write. Cost: one extra plist.
**Status:** Accepted.

### ADR-002: llama-server primary, Ollama secondary

**Context:** Both can serve Qwen3.6. Operator initially preferred Ollama for ease; llama-server has stronger primitives.
**Decision:** llama-server primary, Ollama secondary, both supported via `model_registry.json`.
**Rationale:** llama-server provides native GBNF (richer than JSON Schema), true 3-slot parallelism on 64GB, direct `enable_thinking` control, no third-party repackaging required, supports standard upstream Qwen GGUF.
**Status:** Accepted.

### ADR-003: 9 LLM personas + Crucible, all using one base model

**Context:** v6 had ~9 personas; v3.5 had 3. Operator wants the depth of 9.
**Decision:** Same base model (Qwen3.6-35B-A3B), 9 personas via system-prompt + GBNF + sanity-validator differentiation.
**Rationale:** Per-persona models would multiply RAM and cycle time. Same base with different prompts produces meaningfully different outputs at much lower cost. Persona-affinity dimension in Mutation Engine compensates for any base-model uniformity bias.
**Status:** Accepted.

### ADR-004: Five storage backends, not one

**Context:** Could PMACS run on SQLite alone?
**Decision:** No. KuzuDB + Qdrant + DuckDB + SQLite + audit.log.
**Rationale:** Each store has a clear specialization. Graph traversal in SQLite's recursive CTEs is brittle at depth. Vector search in sqlite-vss is less battle-tested than Qdrant. Columnar analytics on a year of resolutions in SQLite would degrade. Each store handles ~50-200MB of write traffic over a year — none are misused.
**Status:** Accepted.

### ADR-005: Hash-chained audit log, not just append-only

**Context:** Append-only file with `fsync` is simpler.
**Decision:** Add hash chain (`prev_sha256`).
**Rationale:** Append-only catches truncation; hash chain catches mid-file tampering. The cost is ~32 bytes per entry and one SHA256 per write. Worth it for the trust contract guarantee (`Source.md §4.1`).
**Status:** Accepted.

### ADR-006: Mutation Engine activates after 50 PAPER cycles, not day 1

**Context:** Could mutate from cycle 1.
**Decision:** Wait for 50 PAPER cycles before activating.
**Rationale:** Mutation needs a stable baseline to mutate against. Pre-baseline mutation experiments are noise on noise. 50 cycles establish a meaningful baseline.
**Status:** Accepted.

### ADR-007: SHADOW + PAPER concurrent from day 1

**Context:** v3.5 had separate TRAINING and SHADOW phases.
**Decision:** Merge — SHADOW (audit-only) and PAPER ($5K simulated) run concurrently from day 1.
**Rationale:** Operator wants paper from start. SHADOW provides math-gate audit with zero capital risk. PAPER provides resolution data for source maturation. They don't conflict; both add value from day 1.
**Status:** Accepted.

### ADR-008: Boot-driven cycles, not scheduled

**Context:** v6 had EOD auto at 16:30 ET.
**Decision:** Boot-driven. Cycle runs when operator opens the laptop.
**Rationale:** Operator is single-user and not always at the machine. Scheduled cycles when operator absent imply background daemon reliability that's overkill for this scale. Boot-driven matches operator's natural rhythm.
**Status:** Accepted.

### ADR-009: Operator-curated universe, no screener-driven expansion

**Context:** Easy to add a screener that pulls in growth-tech names by criteria.
**Decision:** Reject. Operator curates.
**Rationale:** Operator's edge is thesis quality on names they understand. A screener would dilute that into "growth at this market cap" averaging. The compromise (Settings → Index Overlay = on) is opt-in.
**Status:** Accepted.

### ADR-010: No backtesting

**Context:** Backtesting is industry-standard.
**Decision:** PMACS does not backtest against historical LLM outputs.
**Rationale:** The model's training data already contains the test period's future. A backtest "shows" the system would have predicted outcomes the model already knows. SHADOW is the only valid forward-test.
**Status:** Accepted.

---

## 22. Connection to companion files

### 22.1 → Source.md

Read `Source.md` to understand:
- Vision and operator persona (§1-§3)
- Trust contract and non-negotiables (§4-§5)
- Decision rights matrix (§6)
- Holding philosophy and conviction tiers (§7)
- Universe philosophy (§8)
- Mode ladder semantics (§9)
- The flywheel narrative (§10)
- Failure modes the operator accepts (§11)
- Run wizard sequence (§12)
- UI page specifications (§14-§20)
- Operator workflows (§21)

This file (`Architecture.md`) implements those concepts.

### 22.2 → Agents.md

When you touch any LLM-producing code path:
- Per-persona prompts and structured-output contracts (§4-§13)
- The 18 outcome + 5 reasoning-flaw FDE taxonomy types (§15)
- Crucible adversarial loop (§16)
- Mutation Engine candidate generation rules (§17)
- Episodic context injection mechanics (§18)
- Per-persona sanity validators (§4-§13 sanity sections)

`Agents.md` is the LLM contract. This file (`Architecture.md`) tells you *where* the LLM call happens; `Agents.md` tells you *what to send and what to expect*.

### 22.3 → Phases.md

Before you start a ticket:
- Build phases and their order (§2)
- Per-phase exit tests (§2.x)
- File-by-file build dependencies (§4)
- Mode promotion gates (numerical) (§3)
- Mode demotion triggers (§3.5)

`Phases.md` enforces dependency order. If you find yourself implementing something whose dependencies aren't built yet, stop and read `Phases.md`.

### 22.4 What this file does NOT contain

- **No persona prompts.** Live in `Agents.md §4-§13`.
- **No build sequence.** Lives in `Phases.md §2`.
- **No vision-level rationale.** Lives in `Source.md`.

If you find yourself wanting to put a prompt in this file, that signals it belongs in `Agents.md`.

### 22.5 The four-file invariant

Every operator-facing behavior in `Source.md` has at least one implementation pointer in this file. Every persona behavior has at least one contract specification in `Agents.md`. Every build-time dependency has at least one entry in `Phases.md`.

Verified by `ops/spec_consistency.py` in CI.

---

*End of Architecture.md. v1. Pair with Source.md, Agents.md, Phases.md.*
