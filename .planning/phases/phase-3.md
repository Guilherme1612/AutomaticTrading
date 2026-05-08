# GSD Phase 3: Personas

**Implements PMACS Build Phases 5-6** (spec/Phases.md §2)

## Milestone

All 7 analysis personas operational.

---

## PMACS Phase 5: Gatekeeper + first 3 personas (MacroRegime, CatalystSummarizer, MoatAnalyst)

**Goal:** Phase 0 (Gatekeeper) and the first three LLM personas run end-to-end on real data. Arbitration combines their signals. The cycle processes real tickers.

**What gets built:**
- `pmacs/agents/gatekeeper.py` — deterministic admittance filter (`Agents.md §4`)
- `pmacs/agents/macro_regime.py` + `prompts/macro_regime.md` + `grammars/macro_regime.gbnf` + `sanity/macro_regime.py` (`Agents.md §5`)
- `pmacs/agents/catalyst_summarizer.py` + prompts + grammar + sanity (`Agents.md §6`)
- `pmacs/agents/moat_analyst.py` + prompts + grammar + sanity (`Agents.md §7`)
- `pmacs/engines/arbitration.py` — full ArbitrationEngine (`Architecture.md §9.1`)
- `pmacs/engines/queue.py` — queue composition from universe + priority bands
- `pmacs/engines/memory.py` — antipattern checker (stub; no patterns yet)
- Nervous orchestrator updated: steps 0-13 (partial: 3 personas, no Crucible)
- `tests/integration/test_gatekeeper.py`
- `tests/integration/test_3persona_cycle.py` — run a full cycle with 3 personas on 3 test tickers

**Exit test:**
1. Gatekeeper filters: a halted ticker is rejected; a stale-data ticker is rejected; a valid ticker is admitted
2. `pytest tests/integration/test_3persona_cycle.py` — 3 tickers processed; each produces 3 `DirectionalProbability` outputs; Arbitration combines them; audit log shows all events; cycle opens and closes cleanly
3. GBNF violations are caught: feed MacroRegime a prompt that produces invalid JSON without grammar → grammar enforces valid JSON
4. Sanity validators catch: manually inject `p_up=1.0, p_flat=0.0, p_down=0.0` → sanity rejects (degenerate distribution); retry fires; retry produces valid output

**Dependencies:** Phase 1 (schemas), Phase 2 (data sources), Phase 3 (inference), Phase 4 (processes).

---

## PMACS Phase 6: Remaining 4 personas (GrowthHunter, InsiderActivity, ShortInterest, Forensics)

**Goal:** All 7 analysis personas operational. Full Phase 1 pipeline runs on all admitted tickers.

**What gets built:**
- `pmacs/agents/growth_hunter.py` + prompts + grammar + sanity (`Agents.md §8`)
- `pmacs/agents/insider_activity.py` + prompts + grammar + sanity (`Agents.md §9`)
- `pmacs/agents/short_interest.py` + prompts + grammar + sanity (`Agents.md §10`)
- `pmacs/agents/forensics.py` + prompts + grammar + sanity (`Agents.md §11`)
- Nervous orchestrator updated: step 13 dispatches all 7 personas across 3 inference slots (`Architecture.md §12.2`)
- `tests/integration/test_7persona_cycle.py`

**Exit test:**
1. `pytest tests/integration/test_7persona_cycle.py` — full universe cycle; all 7 personas produce valid outputs on 5+ tickers; Arbitration combines 7 signals; audit trail complete
2. Parallel slot dispatch: 3 personas run concurrently (wall-clock < 3× sequential single-persona time for a 3-ticker cycle)
3. Each persona's sanity validator has at least 3 test cases (unit) covering pass, fail-retry-pass, and fail-all-retries-abort

**Dependencies:** Phase 5 (first 3 personas + arbitration working).

---

## Next-phase dependency

GSD Phase 4 requires:
- All PMACS Phase 5-6 exit tests pass
- All 7 personas produce valid outputs
- Arbitration combines all signals
- Parallel slot dispatch working
