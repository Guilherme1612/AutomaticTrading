# GSD Phase 1: Foundation + Data

**Implements PMACS Build Phases 1-2** (spec/Phases.md §2)

## Milestone

Schemas compile, audit chain works, data fetches.

---

## PMACS Phase 1: Foundation — schemas, config, storage, audit

**Goal:** The skeleton exists. Every Pydantic model compiles. Every database initializes. The audit chain works end-to-end. Nothing runs yet, but the foundation is sound.

**What gets built:**
- `pmacs/schemas/*.py` — ALL Pydantic models (complete, even for engines not yet implemented)
- `pmacs/data/canonical.py` — canonical JSON serialization
- `pmacs/storage/sqlite.py` — SQLite initialization with all tables from `Architecture.md §8.5`
- `pmacs/storage/audit.py` — hash-chained audit writer + verifier
- `pmacs/storage/keychain.py` — macOS Keychain wrapper
- `pmacs/config.py` — config loader for `config/*.toml` + `config/model_registry.json`
- `config/` — all config files with production defaults
- `pmacs/constants.py` — anti-pattern thresholds
- `pmacs/logsys/` — debug log writer, error classifier
- `pmacs/engines/state_machine.py` — Holding state transitions with full transition table
- `tests/unit/test_schemas.py` — validates all schemas compile and cross-field validators work
- `tests/unit/test_audit_chain.py` — validates genesis, append, verify, break-detection
- `tests/unit/test_state_machine.py` — validates every valid transition and rejects every invalid one
- `.pre-commit-config.yaml` — anti-pattern grep hooks

**Exit test:**
1. `pytest tests/unit/test_schemas.py` — ALL pass
2. `pytest tests/unit/test_audit_chain.py` — chain genesis → 100 appends → verify passes; tamper one line → verify catches it
3. `pytest tests/unit/test_state_machine.py` — every valid transition succeeds; every invalid transition raises `InvalidStateTransition`
4. `python -c "from pmacs.config import load_config; load_config()"` — succeeds on a fresh repo
5. All anti-pattern grep checks pass

**Dependencies:** None (first phase).

---

## PMACS Phase 2: Data layer — sources, staleness, rate limiting, FX

**Goal:** PMACS can fetch real market data from every source, enforce staleness budgets, and produce well-typed EvidencePackets.

**What gets built:**
- `pmacs/data/gateway.py` — rate-limited HTTP wrapper with TokenBucket per source
- `pmacs/data/staleness.py` — FreshnessResult-returning staleness checker (no packet mutation; `Architecture.md §16.4`)
- `pmacs/data/fx.py` — ECB EUR/USD with `usd_per_eur` convention
- `pmacs/data/corp_actions.py` — splits, dividends, mergers
- `pmacs/data/universe.py` — operator-curated universe CRUD
- `pmacs/data/sources/*.py` — one module per source (edgar, polygon, finnhub, alpaca_data, openfda, finra, form4, ir_pages, press, fomc, fred, ecb, fundamentals)
- `pmacs/schemas/data.py` — EvidencePacket
- `pmacs/schemas/freshness.py` — FreshnessResult
- `pmacs/schemas/currency.py` — FxRate, FxSnapshot
- `config/source_criticality.toml`
- `tests/unit/test_staleness.py`
- `tests/unit/test_fx.py`
- `tests/integration/test_data_sources.py` — each source fetches one real data point and returns a valid EvidencePacket

**Exit test:**
1. `pytest tests/unit/test_staleness.py` — all budgets enforced; CRITICAL raises; IMPORTANT degrades; NICE_TO_HAVE degrades
2. `pytest tests/unit/test_fx.py` — `usd_to_eur(eur_to_usd(100, snap), snap) ≈ 100`
3. `pytest tests/integration/test_data_sources.py` — at least 10 of 13 sources return a valid EvidencePacket (3 can be NICE_TO_HAVE failures)
4. Rate limiting demonstrated: 20 rapid calls to Polygon complete without 429 errors

**Dependencies:** Phase 1 (schemas, config, Keychain for API keys).

---

## Next-phase dependency

GSD Phase 2 requires:
- All PMACS Phase 1 exit tests pass
- All PMACS Phase 2 exit tests pass
- Config loads cleanly
- Data sources fetch real data
