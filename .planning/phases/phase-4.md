# GSD Phase 4: Pipeline + Paper

**Implements PMACS Build Phases 7-8** (spec/Phases.md §2)

## Milestone

Full pipeline, paper trading, wizard — **PAPER-READY**.

---

## PMACS Phase 7: Crucible + conviction + sizing + risk gate

**Goal:** The full decision pipeline runs: Phase 0 → Phase 1 (7 personas) → Arbitration → Phase 2 (Crucible) → EV → Sizing → Conviction → Risk Gate → Verdict. The system can produce STRONG_BUY / BUY / SKIP verdicts.

**What gets built:**
- `pmacs/agents/crucible.py` + prompts + grammar + sanity (`Agents.md §12`)
- Crucible inner loop (`Agents.md §16`) — 2-cycle max, 90s budget, NO_TRADE default
- `pmacs/engines/conviction.py` — conviction scoring (`Architecture.md §9.2`, `Source.md §7.2`)
- `pmacs/engines/pricing.py` — EV computation
- `pmacs/engines/sizing.py` — half-Kelly + bootstrap haircut + limited-history haircut (`Architecture.md §9.3`)
- `pmacs/engines/portfolio_risk_gate.py` — max positions, sector limits, concentration
- `pmacs/agents/memo_writer.py` + prompts + grammar + sanity (`Agents.md §13`)
- Nervous orchestrator updated: full step 13 (all sub-steps through TradePlan)
- `tests/integration/test_full_pipeline.py`
- `tests/unit/test_conviction.py`
- `tests/unit/test_sizing.py`
- `tests/unit/test_crucible_budget.py`

**Exit test:**
1. `pytest tests/integration/test_full_pipeline.py` — one ticker goes through the complete pipeline: Gatekeeper → 7 personas → Arbitration → Crucible (with attack) → EV → Sizing → Conviction → Risk Gate → Verdict → MemoWriter. Audit trail shows every step.
2. `pytest tests/unit/test_conviction.py` — conviction formula produces expected outputs for known inputs; STRONG_BUY ≥ 0.6, BUY ≥ 0.3, SKIP < 0.3
3. `pytest tests/unit/test_sizing.py` — bootstrap haircuts apply correctly; limited-history haircut stacks; half-Kelly produces sane sizes; max-position-% caps
4. `pytest tests/unit/test_crucible_budget.py` — Crucible times out at 90s → NO_TRADE; Crucible exceeds 2 cycles → NO_TRADE; severity > 0.6 cycle 1 → NO_TRADE without cycle 2
5. A ticker with Crucible severity > 0.6 produces SKIP. A ticker with low Crucible severity and high arbitrated p_up produces STRONG_BUY or BUY.

**Dependencies:** Phase 6 (all 7 personas).

---

## PMACS Phase 8: Paper trading — Alpaca paper + sim ledger + wizard

**Goal:** The system trades paper money. Alpaca paper API integration. The wizard works end-to-end. SHADOW + PAPER mode concurrent from first boot.

**What gets built:**
- `pmacs/sim/ledger.py` — paper portfolio ledger ($5K start)
- `pmacs/sim/alpaca_paper_adapter.py` — Alpaca paper order submission + fill polling
- `pmacs/execution/alpaca_adapter.py` — real adapter (not stub)
- `pmacs/execution/catastrophe_net.py` — broker-side wide stop placement at entry
- `pmacs/installer/wizard.py` + `steps/*.py` — the 11-step wizard (`Source.md §12`)
- Mode management: `INSTALLING → SHADOW + PAPER` transition
- `pmacs/schemas/system.py` — Mode enum, mode transition logic
- SQLite `mode_history`, `paper_account` tables
- Nervous orchestrator updated: step 13 concludes with TradePlan.sign_and_send() + catastrophe-net stop for PAPER mode
- `tests/integration/test_paper_trade.py` — submit order → receive fill → update ledger → update holding → audit
- `tests/integration/test_wizard.py` — run all 11 steps with mocked APIs
- `tests/e2e/test_smoke_cycle.py` — the smoke-test cycle from wizard step 10

**Exit test:**
1. Wizard completes all 11 steps on a fresh machine (prerequisite: `ops/install_system_users.sh` has been run with sudo to create _pmacs_* system users) (with mocked API keys in test mode)
2. `pytest tests/integration/test_paper_trade.py` — a STRONG_BUY ticker → TradePlan signed → submitted to Alpaca paper → fill received → ledger updated → holding transitions to ACTIVE → catastrophe-net stop placed → audit trail complete
3. `pytest tests/e2e/test_smoke_cycle.py` — full cycle on synthetic fixtures; audit chain verifies; all engines fire
4. SHADOW mode concurrently captures audit-only signals (no fake-trades in SHADOW)
5. The paper ledger balance starts at $5,000 and reflects the fill correctly

**Dependencies:** Phase 7 (full decision pipeline to produce TradePlans).

---

## Risk Checkpoint B (after Phase 8)

Before proceeding to GSD Phase 5, verify:
- [ ] Paper trades execute end-to-end with correct fills
- [ ] Catastrophe-net stops are placed for every new position
- [ ] Ledger balance is accurate after 10+ trades
- [ ] Wizard completes without error
- [ ] The operator can engage and disengage the kill switch from the UI (Cortex page)
- [ ] The operator can force-exit a position from the Pipeline page (TOTP required)
- [ ] Mode is SHADOW + PAPER after wizard completes

**If any fails:** Do not proceed. The system is trading (paper) money.

---

## Next-phase dependency

GSD Phase 5 requires:
- All PMACS Phase 7-8 exit tests pass
- Risk Checkpoint B fully verified
- Paper trading works end-to-end
- Wizard completes on fresh machine
- System in SHADOW + PAPER mode
