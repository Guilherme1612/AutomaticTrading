# PMACS — Portfolio Management and Catalyst Automation System

## Identity

PMACS is a single-operator, local-only, catalyst-driven, LLM-assisted decision engine with deterministic arbitration. It runs local LLMs (Qwen3.6-35B-A3B) for narrative analysis and Python for math, sizing, arbitration, and execution. LLMs never decide, never math, never sign. The system trades paper money ($5K) from day 1 and graduates to live capital only after empirical performance gates pass.

## The Four-File Spec

The canonical specification lives in `spec/`. **These files are the source of truth for everything.** If you're unsure about a design decision, the answer is in the spec. Do not invent behavior not specified.

### Reading order (always follow this)

1. **`spec/Source.md`** — Read first. Tells you WHAT is being built and WHY. Vision, philosophy, operator persona, holding philosophy, mode ladder, flywheel, UI page specifications, operator workflows, the day-in-the-life narrative. **If you're confused about what the system should do, the answer is here.**

2. **`spec/Architecture.md`** — Read second. Tells you HOW it's built. 7-layer architecture, process topology, IPC, storage schemas (5 stores), deterministic engines, cycle orchestration sequence, kill switch, Mutation Engine process, anti-patterns. **If you're writing code, the answer is here.**

3. **`spec/Agents.md`** — Read when touching LLM code. Per-persona prompts, GBNF grammars, Pydantic output schemas, sanity validators, the Crucible adversarial loop, Failure Diagnostic Engine (18 taxonomy types), Mutation Engine candidate generation rules, episodic context injection, prompt-injection defense, hallucination defense. **If you're working with LLM outputs, the answer is here.**

4. **`spec/Phases.md`** — Read to decide what to build next. 15 numbered build phases with explicit exit tests, file-by-file dependency graph, mode promotion/demotion gates with numerical thresholds, risk checkpoints. **If you're unsure whether to start a task, check here first.**

### Conflict resolution

If the four files disagree:
- `Source.md` wins for **vision and operator-facing behavior**
- `Architecture.md` wins for **implementation specifics**
- `Agents.md` wins for **LLM contracts**
- `Phases.md` wins for **build sequence and what ships when**

### Cross-references

All files cite each other by `File.md §N`. When a section references another file, **read the referenced section before implementing.** Don't guess.

### Section §0 in every file

Every spec file has a §0 cross-reference index. Use it as a lookup table.

## Five Non-Negotiables (enforce on every PR)

These are absolute. They are not guidelines. Violating any of them is a bug.

1. **LLMs never sign trades.** Trades are Ed25519-signed by `pmacs-execution`. An LLM cannot directly cause a trade.
2. **LLMs never math.** Probabilities are combined, sized, and arbitrated by Python. LLMs produce structured outputs only.
3. **Every state transition is hash-chained.** Audit log with `prev_sha256`. Tampering with one line breaks the chain.
4. **Local-only execution.** No cloud LLM calls. No telemetry. The inference process is pf-blocked from internet.
5. **Operator owns the kill switch.** TOTP-gated disengagement. The system can engage it. Only the operator can lift it.

## Anti-Patterns (enforce via pre-commit + CI)

These are from `spec/Architecture.md §16`. Every one of them is a specific code pattern that is FORBIDDEN. When writing code, check your output against these:

- ❌ `holding.state = "ABORTED_LLM"` — MUST use `state_machine.transition()`
- ❌ `json.dumps(payload)` for audit — MUST use `canonical_json(payload)`
- ❌ Custom rate-limit logic — MUST use `BUCKETS["source"].acquire()`
- ❌ Mutating evidence packets in staleness checks — MUST return `FreshnessResult`
- ❌ `cycle_id=None` on audit-emitting functions — cycle_id is REQUIRED
- ❌ Day 1 bootstrap aborting everything — MUST use `PROCEED_BOOTSTRAP_LOW_CONFIDENCE`
- ❌ Tight broker-side stops — PMACS manages tight stops; broker gets only catastrophe-net (15%)
- ❌ `eur_per_usd` field — MUST use `usd_per_eur` (ECB convention)
- ❌ Mutation Engine writing production state directly — proposes only; operator TOTP applies
- ❌ Mutation A/B running in PAPER — candidate arm runs SHADOW-only
- ❌ Any mutation auto-applying — ALL mutations require operator TOTP. No exceptions.
- ❌ Runtime prompt edits — operator proposes mutation → A/B test → TOTP promote
- ❌ Backtesting against historical LLM outputs — epistemically invalid
- ❌ Logging secrets — never log API keys, TOTP secrets, signing keys
- ❌ Missing `error_code` on WARN+ debug events — every WARN+ has a canonical code from Architecture.md §5.5

## Pydantic v2 Rules (enforce everywhere)

- `from pydantic import BaseModel, ConfigDict, model_validator, field_validator`
- `model_config = ConfigDict(...)` NOT `class Config:`
- `@model_validator(mode="after")` for cross-field validation
- `model_validate()` / `model_dump()` NOT `parse_obj()` / `dict()`
- ALL Pydantic models live in `pmacs/schemas/` — engines import from there
- Forbid `from pydantic.v1 import` anywhere

## Build Phases → GSD Mapping

PMACS has 15 build phases defined in `spec/Phases.md §2`. Map them to GSD phases as follows:

| GSD Phase | PMACS Build Phases | Milestone |
|---|---|---|
| Phase 1 | Phase 1-2 (Foundation + Data) | Schemas compile, audit chain works, data fetches |
| Phase 2 | Phase 3-4 (Inference + Processes) | LLM calls work, kill switch fires, 8 processes run |
| Phase 3 | Phase 5-6 (Personas) | All 7 analysis personas operational |
| Phase 4 | Phase 7-8 (Pipeline + Paper) | Full pipeline, paper trading, wizard — **PAPER-READY** |
| Phase 5 | Phase 9-10 (Monitoring + Dashboard) | Stop-loss, re-eval, all 7 UI pages |
| Phase 6 | Phase 11-12 (Calibration + FDE) | Flywheel passive components, 18 taxonomy types |
| Phase 7 | Phase 13-14 (Episodic + Mutation) | Active flywheel — **FLYWHEEL-READY** |
| Phase 8 | Phase 15 (Polish) | Production-quality — **LIVE-READY** |

### For each GSD phase:
1. Read `spec/Phases.md §2` for the PMACS phase(s) in scope
2. Check the **exit test** — it's your acceptance criteria
3. Check the **dependencies** — don't skip ahead
4. If a **risk checkpoint** applies (after Phases 4, 8, 14), verify ALL checkbox items
5. Run the previous phase's exit test first (regression check)

## How to use the spec during coding

### Starting a new feature
1. Check `spec/Phases.md §4` (file dependency graph) — are your dependencies built?
2. Read the relevant section in `spec/Architecture.md` for implementation details
3. If the feature touches LLM code, read the persona spec in `spec/Agents.md §4-13`
4. Check `spec/Architecture.md §16` anti-patterns before writing code

### Implementing a Pydantic model
1. The schema is ALREADY defined in `spec/Architecture.md §8` or `spec/Architecture.md §9`
2. Copy the schema from the spec — do not improvise fields
3. Put it in `pmacs/schemas/<appropriate_module>.py`
4. Add `@model_validator(mode="after")` for cross-field invariants from the spec

### Implementing an engine
1. Read the engine spec in `spec/Architecture.md §9`
2. Read the schema it consumes and produces from `pmacs/schemas/`
3. Implement in `pmacs/engines/<engine>.py`
4. ALWAYS log to both audit AND debug streams (spec/Architecture.md §1.8)
5. ALWAYS require `cycle_id` parameter (spec/Architecture.md §1.11)

### Implementing a persona
1. Read the persona spec in `spec/Agents.md §4-13` (the specific section for that persona)
2. Create four files:
   - `pmacs/agents/<persona>.py` — runner
   - `pmacs/agents/prompts/<persona>.md` — system prompt
   - `pmacs/agents/grammars/<persona>.gbnf` — GBNF grammar (llama-server)
   - `pmacs/agents/sanity/<persona>.py` — sanity validator
3. The three-layer contract is Grammar → Pydantic → Sanity (spec/Agents.md §3)
4. On any layer failure: retry 2x with +0.05 temp, then abort persona

### Implementing a UI page
1. Read the page spec in `spec/Source.md §14-20`
2. Use the visual identity tokens from `spec/Source.md §13.1`
3. Dashboard gets data via SSE from nervous (spec/Architecture.md §4.4) — NOT by polling DBs
4. Write actions go through `pmacs-nervous` POST (TOTP-gated) — dashboard is READ-ONLY

### Implementing the Mutation Engine
1. Read `spec/Architecture.md §10` for the process lifecycle
2. Read `spec/Agents.md §17` for candidate generation rules
3. Read `spec/Agents.md §17.4` for the FIVE rollback safety levels — ALL MUST WORK
4. **The Mutation Engine is an advisor, not an actor.** ALL mutations require operator TOTP. No auto-promote.
5. The mutation process CANNOT write to production config (structural, not procedural)
6. Auto-rollback on regression remains as a safety net for operator-approved mutations
7. Run `spec/Phases.md §6.3` Checkpoint C before marking Phase 14 complete

## Key configuration files

All in `config/`:
- `resources.toml` — hardware budgets, slot counts, cycle time limits
- `risk.toml` — position sizes, kill-switch thresholds, EV minimums
- `crucible.toml` — CPS budget (90s/attack, 2 cycles max)
- `mutation.toml` — activation threshold (50 cycles), auto-promote rules
- `model_registry.json` — backend selection (llama-server primary, Ollama secondary)
- `model_hashes.toml` — GGUF SHA256 for integrity verification
- `source_criticality.toml` — CRITICAL / IMPORTANT / NICE_TO_HAVE per data source

## Key constants

- Paper capital: $5,000
- Max single position: 20% ($1,000)
- Max concurrent positions: 5
- Catastrophe-net stop: 15% below entry (broker-side)
- Crucible time budget: 90s per attack cycle, 2 cycles max
- Mutation activation: after 50 PAPER cycles
- Mutation: ALL require operator TOTP (no auto-promote). Engine is advisor-only.
- Mutation stat-sig threshold for recommendations: p < 0.05, Cohen's d > 0.20, n >= 20
- Mutation probation: 30 cycles, auto-rollback window: 50 cycles (safety net for approved mutations that regress)
- Mode promotion to PAPER_VALIDATED: >= 90 cycles, >= 200 trades, Brier <= 0.30, Sharpe >= 0.0, drawdown <= 15%
- Analysis persona temperature: 0.2; Crucible: 0.1; MemoWriter: 0.3

## Process topology (8 processes)

| Process | Port | Responsibility |
|---|---|---|
| pmacs-inference | :8080 | llama-server (pf-blocked from internet) |
| pmacs-cortex | daemon | Health monitoring, kill switch, boot detection |
| pmacs-cortex-self-check | daemon | Meta-monitor: pings cortex every 60s |
| pmacs-execution | UDS | Trade signing (Ed25519) + broker submission |
| pmacs-nervous | :8000 | Orchestration, SSE, write API |
| pmacs-stoploss | daemon | RTH position monitoring every 30 min |
| pmacs-mutation | daemon | Active flywheel (dormant first 50 cycles) |
| pmacs-dashboard | :8001 | Read-only web UI (loopback only) |

## Storage (5 stores)

| Store | Purpose | Access |
|---|---|---|
| SQLite | OLTP: cycles, queue, holdings, stops, mutations, config | Read/write by nervous; read-only by dashboard |
| KuzuDB | Graph: Holding-Evidence-Resolution-Lesson-FailedAssumption lineage | Read/write by nervous + engines |
| Qdrant | Vector: thesis embeddings, memo embeddings, lesson embeddings | Read/write by nervous + engines |
| DuckDB | Analytics: resolution history, rolling metrics, persona affinity | Read/write by engines; read by dashboard |
| audit.log | Hash-chained immutable record | Append-only by nervous; verify by cortex |

## Testing strategy

- Unit tests in `tests/unit/` — 90% line coverage, 100% on state_machine + conviction
- Integration tests in `tests/integration/` — all cycle stages
- Property tests in `tests/property/` — probability invariants, FX symmetry
- E2E tests in `tests/e2e/` — full cycle on synthetic fixtures
- Every PR runs anti-pattern checks + previous phase's exit test (regression)

## When in doubt

1. Read the spec section referenced in the code's docstring (`spec_ref` field)
2. If no spec reference: search the four files for the concept
3. If the spec is silent: ask the operator before inventing behavior
4. If the spec is ambiguous: follow Source.md for vision, Architecture.md for implementation
5. Never assume. The spec is 7,100 lines for a reason.