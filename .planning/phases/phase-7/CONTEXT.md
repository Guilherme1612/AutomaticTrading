# Phase 7 Context — Episodic + Mutation

## PMACS Phases Covered
- Phase 13: Episodic context injection (connect flywheel to persona prompts)
- Phase 14: Mutation Engine (active flywheel, advisor-only, TOTP-gated)

## Milestone: FLYWHEEL-READY (Checkpoint C after PMACS Phase 14)

## Spec References
- Agents.md §16 (Crucible adversarial loop), §17 (Mutation candidate rules + 5 rollback safety levels)
- Architecture.md §10 (Mutation Engine full lifecycle, daemon loop, stat test, promotion, rollback)

## Key Design Decisions
- All mutations require operator TOTP — NO auto-promote (advisor-only)
- Candidate generation is rule-based, not LLM-generated (v1)
- Max 3 concurrent A/B tests
- Activation gate: 50 PAPER cycles minimum
- 5 rollback safety levels: structural separation, baseline snapshot, atomic promotion, auto-rollback, kill-switch review
- Statistical test: Welch's t-test, p<0.05, Cohen's d>=0.20, n>=20

## Exit Tests
1. Welch's t-test correct on known distributions
2. FDE cluster → candidate → A/B → 20 cycles → stat test → recommendation staged
3. 5 rollback safety levels all verified
4. Max 3 concurrent A/B tests enforced
5. Dormant before 50 PAPER cycles
6. Episodic context brief ≤200 words
7. Audit event logged with content_hash
