# Phase 4 Context — Pipeline + Paper

## PMACS Phases Covered
- Phase 7: Crucible + Conviction + Sizing + Risk Gate
- Phase 8: Paper Trading — Alpaca paper + sim ledger + wizard

## Milestone: PAPER-READY (Checkpoint B after PMACS Phase 8)

## Spec References
- Architecture.md §9.2 (ConvictionEngine), §9.3 (SizingEngine), §12 (cycle orchestration)
- Agents.md §12 (Crucible), §13 (MemoWriter), §16 (Crucible inner loop)
- Source.md §7.2 (conviction model), §12 (wizard)

## Exit Tests
1. Full pipeline: Gatekeeper → 7 personas → Arbitration → Crucible → EV → Sizing → Conviction → Risk Gate → Verdict → MemoWriter
2. Conviction formula: STRONG_BUY ≥ 0.6, BUY ≥ 0.3, SKIP < 0.3
3. Sizing: bootstrap haircuts, limited-history haircut stacks, half-Kelly, max-position cap
4. Crucible budget: 90s timeout → NO_TRADE, 2 cycle max → NO_TRADE, severity > 0.6 → SKIP
5. Paper trade: TradePlan → Alpaca paper → fill → ledger update → holding ACTIVE → catastrophe-net → audit
6. Wizard: 11 steps complete with mocked APIs
7. SHADOW mode captures audit-only signals
8. Paper ledger starts at $5,000

## Dependencies
- All Phase 3 deliverables (personas, arbitration, gatekeeper)
- Phase 2 execution service (UDS, Ed25519 signing)
