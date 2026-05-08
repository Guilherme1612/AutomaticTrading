# Phase 6 Context — Calibration + FDE

## PMACS Phases Covered
- Phase 11: Calibration + lessons + causal attribution + override learning
- Phase 12: Failure Diagnostic Engine (18 taxonomy types) + cross-DB + reconciliation

## Spec References
- Agents.md §15 (18 FDE taxonomy types with exact trigger conditions)
- Agents.md §18 (Episodic context injection — 200-word context brief)
- Architecture.md §9.4 (CalibrationEngine), §9.5 (FDE), §15 (Memory hierarchy)

## Exit Tests
1. Calibration refit adjusts persona weights after 20 synthetic resolutions
2. Lessons engine extracts + writes to vector store + retrieval returns it
3. CausalAttribution attributes resolution to contributing personas
4. FlywheelHealth snapshot records rolling metrics
5. All 18 FDE taxonomy types classify correctly
6. STOP_HUNTED vs STOP_LOSS_CORRECT differentiation
7. Cross-DB reconciler detects mismatches
8. Dead-letter queue: fail → queue → retry → succeed
9. FailedAssumption nodes traversable in graph DB
