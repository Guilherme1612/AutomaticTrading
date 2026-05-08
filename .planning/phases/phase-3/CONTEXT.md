# Phase 3 Context — Personas

## PMACS Phases Covered
- Phase 5: Gatekeeper + first 3 personas (MacroRegime, CatalystSummarizer, MoatAnalyst) + ArbitrationEngine
- Phase 6: Remaining 4 personas (GrowthHunter, InsiderActivity, ShortInterest, Forensics)

## Spec References
- Agents.md §4-11 (all 7 personas + Gatekeeper)
- Agents.md §14 (inter-persona communication model)
- Architecture.md §9.1 (ArbitrationEngine)
- Architecture.md §12 (cycle orchestration, steps 0-13)
- Phases.md §2 Phase 5 & Phase 6

## Key Design Decisions
- Gatekeeper is deterministic Python (no LLM)
- All 7 personas are LLM-based, share same base model
- Each persona has 4 files: runner, prompt (.md), grammar (.gbnf), sanity validator
- Personas are independent — no lateral communication
- ArbitrationEngine uses Brier-inverse weighting with MacroRegime 0.5x multiplier
- Extreme-probability dampening: p>0.9 → weight capped at 0.5x
- Bootstrap policy: immature but agree → PROCEED_BOOTSTRAP_LOW_CONFIDENCE

## Exit Tests
1. Gatekeeper: halted rejected, stale-data rejected, valid admitted
2. 3-persona cycle: 3 tickers, 3 DirectionalProbability each, Arbitration combines, audit complete
3. GBNF violation caught
4. Sanity validators catch degenerate distributions
5. 7-persona cycle: full universe, all 7 personas, Arbitration with 7 signals
6. Parallel slot dispatch: 3 concurrent personas
7. Each sanity validator has 3+ test cases (pass, fail-retry-pass, fail-all-retries-abort)
