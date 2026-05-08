# GSD Phase 7: Episodic + Mutation

**Implements PMACS Build Phases 13-14** (spec/Phases.md §2)

## Milestone

Active flywheel — **FLYWHEEL-READY**.

---

## PMACS Phase 13: Episodic context injection

**Goal:** Every persona receives its short-term memory brief. The flywheel feeds back into reasoning.

**What gets built:**
- `pmacs/agents/episodic_context.py` — `build_context_brief()` implementation (`Agents.md §18`)
- DuckDB `persona_ticker_affinity` and `persona_subsector_affinity` tables (populated by calibration)
- Qdrant `lessons` collection retrieval integration
- Update all persona prompts to include `{episodic_context}` placeholder
- Audit event `episodic_context_injected` with content_hash
- `tests/integration/test_episodic.py`

**Exit test:**
1. `pytest tests/integration/test_episodic.py` — a persona running on a ticker with 5+ past cycles receives a non-empty context brief containing persona-ticker affinity, recent failures, and macro context
2. Context brief is ≤ 200 words
3. Audit event `episodic_context_injected` is logged with content_hash
4. A persona running on a ticker with zero history receives a minimal brief (macro context only)
5. Before-and-after comparison: same ticker, same evidence, with vs without episodic context → outputs differ (demonstrating the context influences reasoning)

**Dependencies:** Phase 11 (calibration, Qdrant, DuckDB populated), Phase 12 (FDE for failure history).

---

## PMACS Phase 14: Mutation Engine

**Goal:** The active flywheel is operational. The system proposes, A/B tests, and promotes (or rejects) variants of its own components. All five rollback safety levels are functional.

**What gets built:**
- `pmacs/mutation/daemon.py` — main loop (`Architecture.md §10.4`)
- `pmacs/mutation/candidate_generator.py` — rule-based generation (`Agents.md §17`)
- `pmacs/mutation/ab_runner.py` — SHADOW-only A/B execution
- `pmacs/mutation/stat_test.py` — Welch's t-test + Cohen's d
- `pmacs/mutation/promotion.py` — auto-promote + operator-promote
- `pmacs/mutation/rollback.py` — auto-rollback + manual rollback (`Agents.md §17.4`)
- SQLite `mutation_proposals`, `mutation_outcomes` tables (already created in Phase 1 schema but now populated)
- Settings → Mutation Engine panel (connect to existing Settings page from Phase 10)
- SSE events: `mutation.*`
- `config/mutation.toml` — activation threshold, recommendation thresholds
- `pmacs-mutation` launchd plist activation
- `tests/unit/test_stat_test.py`
- `tests/integration/test_mutation_lifecycle.py`
- `tests/integration/test_rollback.py`
- `tests/mutation_eval/` — offline A/B test harness

**Exit test:**
1. `pytest tests/unit/test_stat_test.py` — Welch's t-test produces correct p-values on known distributions; Cohen's d correct
2. `pytest tests/integration/test_mutation_lifecycle.py` — synthetic FDE cluster (N=5 MOAT_DRIFT_OVERESTIMATE) → candidate generated → A/B started → 20 synthetic cycles → stat test → result classified → if significant: staged as recommendation for operator TOTP approval (ALL mutations require operator confirmation)
3. `pytest tests/integration/test_rollback.py`:
   - **Level 1:** mutation process cannot write to `model_registry.json` (filesystem permission denied)
   - **Level 2:** baseline_config and rollback_config are identical and immutable after proposal creation
   - **Level 3:** promotion is atomic (verified by killing process mid-write → old config persists)
   - **Level 4:** auto-rollback fires after 50-cycle regression (synthetic regression → rollback → config restored → audit logged)
   - **Level 5:** kill switch engagement → 3 most recent promotions flagged → rollback one → config restored
4. Maximum concurrent A/B test cap (3) enforced: 4th proposal queues in PROPOSED status
5. Mutation Engine dormant before 50 PAPER cycles (verified by checking no proposals generated in early test cycles)

**Dependencies:** Phase 12 (FDE operational — candidate generation reads FDE clusters), Phase 13 (episodic context — mutations to prompts affect context injection).

---

## Risk Checkpoint C (after Phase 14)

Before proceeding to GSD Phase 8, verify:
- [ ] Mutation Engine cannot write to production config directly (filesystem permission denied)
- [ ] Auto-rollback fires on synthetic regression (tested with injected bad mutation)
- [ ] Kill switch engagement flags last 3 promotions
- [ ] Maximum 3 concurrent A/B tests enforced
- [ ] No mutation is ever applied without operator TOTP (verify: attempt auto-apply → rejected)
- [ ] Mutation candidates cannot target excluded paths (arbitration formula, state machine, kill switch, etc.)
- [ ] `reversible=True` is enforced on every MutationCandidate
- [ ] Operator TOTP required for prompt and threshold mutations

**If any fails:** Do not proceed. The Mutation Engine is the highest-risk component — an unrestricted self-modifying system will destroy itself.

---

## Next-phase dependency

GSD Phase 8 requires:
- All PMACS Phase 13-14 exit tests pass
- Risk Checkpoint C fully verified
- Episodic context injection working
- Mutation Engine lifecycle + all 5 rollback levels working
- Active flywheel operational
