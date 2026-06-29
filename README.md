# PMACS — Portfolio Management and Catalyst Automation System

A single-operator, local-only, catalyst-driven, LLM-assisted decision engine with deterministic arbitration.

## What It Does

PMACS monitors a curated universe of equities for catalyst events (earnings, FDA decisions, product launches, etc.), runs 7 analysis personas through local LLMs, combines their probabilities with deterministic arbitration, and produces actionable verdicts — all on paper money until empirical performance gates pass.

## Principles

- **LLMs never decide, never math, never sign.** Probabilities are combined, sized, and arbitrated by Python. Trades are Ed25519-signed by the execution process.
- **Every state transition is hash-chained.** The audit log uses `prev_sha256` — tamper with one line and the chain breaks.
- **Local-only execution.** No cloud LLM calls. No telemetry. The inference process is pf-blocked from the internet.
- **Operator owns the kill switch.** Disengagement requires an explicit operator action. Only the operator can lift it.

## Prerequisites

- macOS (Keychain integration, launchd process management)
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- Local LLM: llama.cpp with Qwen3.6-35B-A3B (or compatible GGUF)
- Alpaca paper trading account (API keys)

## Quickstart

```bash
# Install dependencies
uv sync

# Run the setup wizard
python -m pmacs wizard

# Start all 8 processes
bash ops/install_launchd.sh

# Open dashboard
open http://localhost:8000
```

## Architecture

PMACS runs 8 processes managed by launchd:

| Process | Port | Role |
|---|---|---|
| pmacs-cortex | daemon | Health monitoring, kill switch, boot detection |
| pmacs-cortex-self-check | daemon | Meta-monitor: pings cortex every 60s |
| pmacs-execution | UDS | Trade signing (Ed25519) + broker submission |
| pmacs-nervous | :8000 | Orchestration, SSE, write API |
| pmacs-stoploss | daemon | RTH position monitoring every 30 min |
| pmacs-mutation | daemon | Active flywheel (dormant first 50 cycles) |
| pmacs-dashboard | :8000 | Read-only web UI, served by pmacs-nervous (loopback only) |

5 storage backends: SQLite (OLTP), KuzuDB (graph lineage), Qdrant (vector embeddings), DuckDB (analytics), audit.log (hash-chained).

## Spec-Driven Development

The canonical specification lives in `spec/` (7,100+ lines across 4 files):

1. **`spec/Source.md`** — Vision, operator persona, UI specs, workflows
2. **`spec/Architecture.md`** — 7-layer architecture, storage schemas, engine specs
3. **`spec/Agents.md`** — Per-persona prompts, GBNF grammars, sanity validators
4. **`spec/Phases.md`** — 15 build phases with exit tests

When in doubt, read the spec. If the spec is silent, ask before inventing behavior.

## Testing

```bash
# Unit tests (90% line coverage target)
pytest tests/unit/ -v

# Integration tests (all cycle stages)
pytest tests/integration/ -v

# Property tests (probability invariants, FX symmetry)
pytest tests/property/ -v

# E2E tests (full cycle on synthetic fixtures)
pytest tests/e2e/ -v
```

## Daily tooling — slash commands + skills

PMACS ships three PMACS-specific slash commands (and matching skills) that automate the spec-vs-code audit discipline. They live in `~/.claude/skills/pmacs-*/SKILL.md` (user-scoped, persistent) and are invoked as thin wrappers at `.claude/commands/{gap-audit,design-scorecard,trace-cycle}.md` (gitignored).

| Command | What it does | When to run |
|---|---|---|
| `/gap-audit [base]` | 5-dimension sweep against `base` (default `origin/main`): untested code paths, contradicting tests, invalidated spec, stale memory, anti-pattern regressions | Before opening a PR |
| `/design-scorecard <path>` | 6-pillar scorecard vs `Source.md §13.1` visual identity (color, typography, spacing, components, anti-patterns, a11y). PASS at 14, FLAG at 11, BLOCK at 10 | Before merging a UI change |
| `/trace-cycle [id-prefix]` | Post-mortem a failing cycle via SQLite + audit log + `orchestrator.py`. Step reference table with line numbers + spec citations. **Check `memory/pmacs_skills_and_hook_jun29.md` first** — many prior post-mortems live there | After a cycle aborts unexpectedly |

All three commands fall back to their inline procedure if the skill is not loaded — they never silently skip.

### Live anti-pattern enforcement (complementary layers)

Two enforcement layers protect `Architecture.md §16` anti-patterns. Both are wired by default on this repo. A clean working tree passes both.

- **pre-commit** (`.pre-commit-config.yaml`) — 6 patterns, fires on `git commit` from any tool.
- **hookify** (`.claude/hookify.pmacs-anti-patterns.local.md`) — 5 patterns, fires on live `Edit`/`Write`/`MultiEdit` from Claude Code against `pmacs/*.py`.

Together they cover 11 distinct anti-patterns. Pre-commit wins on coverage (file-level grep over `pmacs/schemas/`, secrets-in-logs); hookify wins on real-time feedback (fires before the operator stages the change). The hookify rule MUST live directly in `.claude/` (not a subdirectory) and MUST use `field: content` (not `field: new_text` — that one does not resolve for Edit operations). Both gotchas are pinned by `tests/unit/test_hookify_rule.py`.

See `spec/Source.md §27` for the operator-facing summary and `spec/Architecture.md §16.15` for the implementation rationale.

## License

MIT
