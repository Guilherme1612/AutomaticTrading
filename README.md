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
| pmacs-inference | :8080 | llama-server (pf-blocked from internet) |
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

## License

MIT
