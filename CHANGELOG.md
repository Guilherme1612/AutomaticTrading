# Changelog

All notable changes to PMACS are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] — 2025-05-18

### Added

Spec-driven implementation of Phases 1–13:

- **Phase 1:** Foundation — all Pydantic schemas, SQLite/DuckDB/KuzuDB/Qdrant storage, hash-chained audit log, state machine
- **Phase 2:** Data layer — 13 data sources, staleness detection, FX (ECB), rate limiting, evidence router
- **Phase 3:** Inference backend — llama-server integration, GBNF grammars, JSON Schema fallbacks
- **Phase 4:** Core processes — 8 launchd services, kill switch (TOTP-gated), boot detection, crash loop detector
- **Phase 5:** Gatekeeper + first 3 personas (MacroRegime, CatalystSummarizer, MoatAnalyst)
- **Phase 6:** Remaining 4 personas (GrowthHunter, InsiderActivity, ShortInterest, Forensics)
- **Phase 7:** Crucible adversarial loop, conviction scoring, sizing (half-Kelly), portfolio risk gate
- **Phase 8:** Paper trading — Alpaca paper adapter, paper ledger, setup wizard
- **Phase 9:** Stop-loss monitor, trailing stops, thesis re-evaluation
- **Phase 10:** Dashboard — all 7 pages (dashboard, agents, pipeline, universe, cortex, debug, settings)
- **Phase 11:** Calibration, lessons engine, causal attribution, override learning
- **Phase 12:** Failure Diagnostic Engine (18-taxonomy), cross-DB consistency, reconciliation
- **Phase 13:** Episodic context injection, memo embeddings

### Infrastructure

- 8 launchd plists for process management
- pf rules blocking inference process from internet
- macOS Keychain integration for secrets
- Pre-commit hooks enforcing spec anti-patterns
- Operator runbook and 10 architecture decision records
