# Phase 10: Broker Integration + Operational Gaps — Context

## Goal
Replace mock fills with real Alpaca paper API integration, complete the wizard, fill Ollama JSON schema gaps, and close remaining operational tooling gaps. After this phase, the system can actually submit and fill paper trades.

## Origin
Spec review (2026-05-12/13) identified broker integration as the critical blocker for paper trading. Phase 9 wired the full 30-step cycle but uses mock fills (D2).

## Key Decisions

### D1: Alpaca paper first, real later
Paper adapter is the primary deliverable. Real adapter interface is defined but paper-only for now. Mode gating ensures real adapter is only callable in LIVE modes.

### D2: BrokerAdapter ABC pattern
Define abstract `BrokerAdapter` base class. `AlpacaPaperAdapter` implements it. `MockAdapter` preserves backward compat for tests. The execution service selects adapter based on current mode.

### D3: Quantity stays int (no fractional shares)
Alpaca supports fractional but PMACS spec uses whole shares. Keep `TradePlan.quantity: int`.

### D4: Wizard runs on pmacs-dashboard (:8001)
Wizard is part of the dashboard, not the nervous API. This keeps nervous focused on cycle orchestration.

### D5: Fill polling: 30s timeout for paper LIMIT orders
Paper fills are near-instant but use 30s to handle latency spikes.

### D6: Phase 9 review fixes are NOT in scope
C1-C3 and H1-H5 were already fixed in commit `52ac02a`. This phase starts clean.

## Scope
1. **BrokerAdapter ABC + AlpacaPaperAdapter** — paper order submission, fill polling, cancellation
2. **Catastrophe net wiring** — 15% broker-side stop via adapter
3. **Wizard UI** — 11-step HTML templates + 3 missing backend steps
4. **Ollama JSON Schemas** — 9 schema files for secondary backend
5. **Dead-letter persistence** — SQLite-backed dead letter queue
6. **SSE Last-Event-ID resume** — Client reconnect support

## Out of Scope
- Real (live) Alpaca adapter — paper only
- Cmd-K command palette — deferred to Phase 11
- Sankey/D3 visualization — deferred to Phase 11
- Keyboard shortcuts — deferred to Phase 11
- Performance profiling — covered by Phase 9

## Exit Test
1. AlpacaPaperAdapter submits a LIMIT BUY order via paper API
2. Fill is received and paper ledger updated
3. Catastrophe-net stop placed at 15% below entry
4. Wizard step 1-11 can render in browser
5. Dead-letter entries persist to SQLite
6. SSE client reconnects with Last-Event-ID without missing events
7. All 9 Ollama JSON schemas validate against their GBNF grammars
