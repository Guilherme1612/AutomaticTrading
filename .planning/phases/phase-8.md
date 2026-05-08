# GSD Phase 8: Polish

**Implements PMACS Build Phase 15** (spec/Phases.md §2)

## Milestone

Production-quality — **LIVE-READY**.

---

## PMACS Phase 15: Polish, performance, operator experience

**Goal:** The system is production-quality for paper trading. All operator workflows from `Source.md §21` work smoothly. Performance is within budget. The first-30-days experience from `Source.md §23` is pleasant.

**What gets built:**
- Agents page animations (persona progress bars, Sankey, Math view) — `Source.md §15.5`
- Pipeline page kanban refinement (smooth drag-drop, priority bands)
- Dashboard sparklines and time-window selector
- Cmd-K command palette (full: tickers, pages, quick actions, audit search)
- Keyboard shortcuts (`Source.md §13.6`)
- Accessibility audit (`Source.md §13.7`)
- Performance profiling: per-cycle throughput verified against `Architecture.md §20.1`
- Memory profiling: RAM usage verified against `Architecture.md §20.2`
- `ops/spec_consistency.py` — cross-file reference checker for CI
- `ops/backup_verify.py` — backup + restore tested
- `ops/audit_chain_verify.py` — standalone verification tool
- Documentation: `docs/operator_runbook.md`
- All empty states, loading states, error states per `Source.md §13.4`
- Notification policy implementation (`Source.md §13.5`)
- Cycle compare feature (`Source.md §15.9`)
- "Copy for Claude Code" button on every debug event

**Exit test:**
1. All 8 operator workflows from `Source.md §21` complete in ≤ 3 clicks (excluding TOTP input)
2. Full cycle on 16-ticker universe completes within 3 hours on M1 Max 64GB
3. RAM usage under 50GB during cycle peak
4. Audit chain verifies after 100+ cycles of accumulated data
5. `ops/spec_consistency.py` passes (every Source.md operator-promise has an Architecture.md implementation pointer)
6. Backup + restore: back up all 5 DBs → wipe → restore → audit chain verifies → system resumes cycling
7. Accessibility: axe-core scan on all 7 pages returns zero critical violations
8. All toast notifications, modal dialogs, and keyboard shortcuts function per spec

**Dependencies:** All previous phases.

---

## Final state

After GSD Phase 8, the system is:
- **LIVE-READY** — eligible for LIVE_EARLY evaluation
- All 8 operator workflows functional
- Performance within budget
- Full flywheel operational (calibration + FDE + episodic + mutation)
- All risk checkpoints verified (A, B, C)
- Production-quality for paper trading

**Mode promotion path:**
- Current: SHADOW + PAPER
- Next: PAPER_VALIDATED (requires ≥ 90 cycles, ≥ 200 trades, Brier ≤ 0.30, Sharpe ≥ 0.0, drawdown ≤ 15%, TOTP)
- Then: LIVE_EARLY (requires Phase 15 complete + performance gates)
