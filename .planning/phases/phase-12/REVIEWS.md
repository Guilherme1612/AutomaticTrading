# Phase 12: Spec Gap Closure — Cross-Review Summary

**Date:** 2026-05-19
**Reviewers:** 3 independent Claude subagents (Architect, Spec Compliance, Risk Auditor)
**Verdict:** REQUEST_CHANGES (unanimous)

---

## Executive Summary

All three reviewers agree: **the plan is severely stale**. It was authored against 85/158 DONE components, but current MISSING.md shows 118/158 DONE. Roughly 20 of 25 plan tasks target already-completed work. The plan must be regenerated from current state before execution.

Additionally, the reviewers identified **3 critical operational risks** (no storage rollback path, non-thread-safe audit writer, publicly forgeable signing key) and **1 critical data integrity risk** (price fallback to 1.0 silently corrupts sizing).

---

## Reviewer Verdicts

| Reviewer | Verdict | Critical | High | Medium | Low |
|----------|---------|----------|------|--------|-----|
| Architect | REQUEST_CHANGES | 1 | 2 | 3 | 2 |
| Spec Compliance | PARTIALLY_COMPLIANT | 0 | 2 | 3 | 3 |
| Risk Auditor | HIGH risk | 3 | 5 | 6 | 1 |

---

## Top 5 Issues (Cross-Reviewer Consensus)

### 1. [CRITICAL] Plan Targets Already-DONE Work
**All 3 reviewers agree.** ~40% of tasks describe implementing components that are DONE per current MISSING.md and exist on disk.

Already-DONE items in the plan:
- 1.1 Evidence Pipeline, 1.3 Catalyst Resolution, 3.1 Crucible Loop
- 3.2 Weekly Re-Eval, 3.3 Cash Ledger, 3.5 MARKET_ON_OPEN
- 4.3 Mutation SSE, 5.1 Ops Scripts, 5.2 Integration Tests

**Action:** Regenerate plan from current MISSING.md (20 MISSING items). Convert DONE items to "verify" tasks.

### 2. [CRITICAL] Storage Migration Has No Rollback Path
Once KuzuDB/Qdrant write real data, reverting to stubs is impossible. Split-brain state (some stores real, some stub) is the worst outcome.

**Action:** Add `_storage_health_check()` at orchestrator startup. Add `storage_migration_state` table in SQLite to track migration progress.

### 3. [CRITICAL] AuditWriter Is Not Thread-Safe
Concurrent audit writes can break the hash chain (Non-Negotiable #3 violation). No locking mechanism around `_prev_sha` state.

**Action:** Add file-level lock (`fcntl.flock`) or singleton pattern with internal locking to AuditWriter.

### 4. [HIGH] Price Fallback to 1.0 Silently Corrupts Sizing
`current_price = 1.0` fallback produces catastrophically wrong share counts (5000 instead of 100) and stop prices ($0.85 instead of $42.50).

**Action:** Add guard: `if current_price <= 1.0: abort symbol with DATA_UNAVAILABLE`.

### 5. [HIGH] Exit Test Does Not Match PMACS Phase 12 Spec
Plan's custom exit test omits 3 of 5 spec-mandated Phase 12 exit criteria (dead-letter retry, cross-DB mismatch, KuzuDB FailedAssumption traversal).

**Action:** Add the 5 PMACS Phase 12 exit criteria from Phases.md as explicit acceptance criteria.

---

## Real Remaining Work (Estimated 30-40% of Plan)

After removing DONE items, the actual work is:

1. **Storage Infrastructure Provisioning** (Wave 2)
   - Install kuzu, qdrant-client, duckdb packages
   - Download embedding model (wizard step, NOT runtime — pf-blocked from internet)
   - Verify existing adapter code paths work with live servers
   - Populate initial schemas/collections

2. **FDE STOP_HUNTED 48h Post-Exit Monitoring** (Wave 3.4)
   - Implement persistent check queue in SQLite
   - Wire stop-loss daemon to process pending checks

3. **Flywheel Closure** (Wave 4, depends on Wave 2)
   - Wire lessons engine to real DuckDB data
   - Wire episodic context to real Qdrant + DuckDB
   - Wire mode promotion gates to real metrics

4. **Spec Exit Test Verification** (Wave 5)
   - Verify existing ops scripts (not create)
   - Verify existing integration tests pass with real data
   - Add missing cross-store consistency tests

---

## Risk Register (Top Items)

| ID | Severity | Risk | Mitigation |
|----|----------|------|------------|
| RA-01 | CRITICAL | No storage rollback path | Startup health check + migration state tracking |
| RA-02 | CRITICAL | AuditWriter not thread-safe | File-level lock or singleton |
| RA-03 | CRITICAL | Deterministic signing key | Generate random key at wizard install |
| RA-04 | HIGH | Price=1.0 corrupts sizing | Guard: abort if price <= 1.0 |
| RA-05 | HIGH | Re-eval unbounded LLM calls | Cap re-eval per cycle, add budget check |
| RA-06 | HIGH | KuzuDB dict params may fail | Verify API parameter binding format |
| RA-07 | HIGH | DuckDB file lock contention | READ_ONLY mode for dashboard |
| RA-08 | HIGH | Embedding model download at runtime | Pre-download in wizard step |

---

## Anti-Pattern Concerns

| Anti-Pattern | Risk | Notes |
|--------------|------|-------|
| §16.5 cycle_id optionality | MEDIUM | New storage/evidence writes need cycle_id — plan doesn't mention |
| §16.14 missing error_code | MEDIUM | Error paths in new code need canonical codes from Arch §5.5 |

---

## Spec Reference Errors

| Plan Item | Wrong Ref | Correct Ref |
|-----------|-----------|-------------|
| 1.1 Evidence Pipeline | Arch §6.2 | Arch §6.1 + §6.4 |
| 1.2 Price Feed | Arch §9.3 | Arch §9.4 |
| 2.4 DuckDB | Arch §8.5 | Arch §8.6 |

---

## Recommended Next Steps

1. **Regenerate PLAN.md** from current MISSING.md, keeping only MISSING/STUB items
2. **Add pre-wave safeguards**: price guard, storage health check, audit writer locking
3. **Correct exit test** to include all 5 PMACS Phase 12 criteria from Phases.md
4. **Defer Wave 5.3** (Phase 15 polish) to a separate phase
5. **Add embedding model download** as wizard prerequisite (not runtime)

---

## Individual Reviews

- [REVIEW-ARCHITECT.md](REVIEW-ARCHITECT.md) — Plan structure, feasibility, dependency ordering
- [REVIEW-SPEC-COMPLIANCE.md](REVIEW-SPEC-COMPLIANCE.md) — Spec traceability, deviations, anti-patterns
- [REVIEW-RISK-AUDIT.md](REVIEW-RISK-AUDIT.md) — Security, operational risks, testing gaps
