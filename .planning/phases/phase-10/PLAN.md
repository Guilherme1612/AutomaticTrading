# Phase 10: Broker Integration + Operational Gaps

## Goal

Replace mock fills with real Alpaca paper API integration, complete the wizard, fill Ollama JSON schema gaps, and close remaining operational tooling gaps. After this phase, the system submits and fills paper trades on Alpaca.

## Context

- CONTEXT.md: D1-D6 decisions locked
- 10-RESEARCH.md: Full architecture, SDK info, code patterns, pitfalls
- ExecutionService (`pmacs/execution/service.py`): UDS server with mock fills (price=0, qty=0)
- CatastropheNet (`pmacs/execution/catastrophe_net.py`): Order builder, no broker wired
- PaperLedger (`pmacs/sim/ledger.py`): Functional with $5K capital
- TradePlan/TradeResult (`pmacs/schemas/trade.py`): Frozen Pydantic models, quantity is int (D3)
- Wizard (`pmacs/installer/wizard.py`): 11-step enum, 8 backend files, 3 missing
- SSEPublisher (`pmacs/nervous/sse_publisher.py`): Event IDs tracked, no replay on reconnect
- 9 GBNF grammars exist in `pmacs/agents/grammars/`, no JSON Schema equivalents for Ollama
- Spec refs: Architecture.md S4 (process topology), S9 (engines), S11 (stop-loss), S14 (dead-letter); Source.md S12 (wizard)

## Key Decisions (locked from CONTEXT.md)

- **D1:** Alpaca paper first, real later. Paper adapter is primary deliverable.
- **D2:** BrokerAdapter ABC pattern. AlpacaPaperAdapter implements it. MockAdapter preserved for tests.
- **D3:** Quantity stays int. No fractional shares. TradePlan.quantity: int stays.
- **D4:** Wizard runs on pmacs-dashboard (:8001), not nervous. RESEARCH.md noted nervous might be correct but D4 locks dashboard.
- **D5:** 30s fill polling timeout for paper LIMIT orders.
- **D6:** Phase 9 review fixes already done (commit 52ac02a). Start clean.

## Waves

---

### Wave 1: BrokerAdapter ABC + AlpacaPaperAdapter (S1)

**Goal:** Define the broker abstraction and implement paper trading against Alpaca.

**Depends on:** Nothing (clean start per D6)

---

#### Task 1.1: BrokerAdapter ABC + MockAdapter + factory

**What:** Create `pmacs/execution/adapter.py` with the abstract base class, a MockAdapter (wraps current mock behavior for test compat), and a `create_adapter()` factory that selects based on mode.

**Where:**
- `pmacs/execution/adapter.py` (NEW)
- `tests/unit/test_broker_adapter.py` (NEW)

**Action:**
- Define `BrokerAdapter` ABC with methods: `submit_order(plan: TradePlan) -> str`, `poll_fill(broker_order_id: str) -> TradeResult`, `place_stop_order(ticker, stop_price, qty) -> str`, `cancel_order(broker_order_id: str) -> bool`, `get_position(ticker) -> dict | None`.
- `MockAdapter` implements all methods returning deterministic results (mirrors current mock fill behavior: instant fill at plan.price_usd with plan.quantity).
- `create_adapter(mode, api_key, api_secret)` factory: returns MockAdapter for INSTALLING/SHADOW/test, AlpacaPaperAdapter for PAPER/PAPER_VALIDATED, raises NotImplementedError for LIVE modes.
- Import only `pmacs.schemas.trade` types. No broker SDK in this file.

**Test:**
```bash
pytest tests/unit/test_broker_adapter.py -x -q
```
- MockAdapter.submit_order returns deterministic order ID
- MockAdapter.poll_fill returns TradeResult with filled_quantity == plan.quantity
- create_adapter("PAPER", ...) returns AlpacaPaperAdapter instance
- create_adapter("INSTALLING", ...) returns MockAdapter instance

**Commit:** `feat(10): BrokerAdapter ABC + MockAdapter + factory`

**Done:** All adapter tests green. MockAdapter is a drop-in replacement for current mock fill logic. Factory routes correctly by mode.

---

#### Task 1.2: AlpacaPaperAdapter implementation

**What:** Implement the concrete Alpaca paper trading adapter using `alpaca-py` SDK. Install dependency. Handle order submission, fill polling with 30s timeout (D5), catastrophe-net stop placement, and cancellation.

**Where:**
- `pmacs/execution/alpaca_paper.py` (NEW)
- `pyproject.toml` (add `alpaca-py>=0.43.0`)
- `tests/unit/test_broker_adapter.py` (extend)

**Action:**
- Install `alpaca-py>=0.43.0` and add to pyproject.toml dependencies.
- `AlpacaPaperAdapter.__init__(api_key, api_secret)`: create `alpaca.TradingClient(api_key, secret_key, paper=True)`.
- `submit_order(plan)`: convert TradePlan to alpaca order params. Map `TradeDirection.BUY/SELL` to `alpaca.OrderSide`. Map `OrderType.LIMIT/MARKET` to alpaca types. Use `time_in_force=DAY`. Return `order.id`.
- `poll_fill(broker_order_id)`: poll with 30s timeout (D5). Loop every 2s checking `get_order_by_id`. Map alpaca order status to TradeResult: filled -> FILLED, partially_filled -> PARTIAL, rejected -> REJECTED. On timeout, return TradeResult with status="PENDING".
- `place_stop_order(ticker, stop_price, qty)`: submit STOP_MARKET SELL order with `time_in_force=GTC`. Return stop order ID.
- `cancel_order(broker_order_id)`: call `cancel_order_by_id`. Return True on success.
- `get_position(ticker)`: call `get_open_position`. Return dict or None.
- Quantity stays int per D3. Alpaca receives `qty=int(plan.quantity)`.
- This is the ONLY file that imports `alpaca` (Architecture.md S4.1).
- Wrap all alpaca calls in try/except, logging via `log_debug` with canonical error codes.

**Test:**
```bash
pytest tests/unit/test_broker_adapter.py::test_alpaca_paper_adapter_unit -x -q
```
- Unit tests use mocked TradingClient (no real API calls)
- submit_order builds correct alpaca params from TradePlan
- poll_fill maps order statuses correctly
- place_stop_order creates STOP_MARKET with GTC
- cancel_order returns bool
- Mode assertion: paper adapter rejects LIVE mode instantiation

**Commit:** `feat(10): AlpacaPaperAdapter with alpaca-py SDK`

**Done:** AlpacaPaperAdapter implements full BrokerAdapter protocol. Unit tests pass with mocked SDK. pyproject.toml updated. Only file importing alpaca.

---

#### Task 1.3: Wire adapter into ExecutionService

**What:** Replace mock fill logic in `pmacs/execution/service.py` with BrokerAdapter. Signature verification still guards every submission. Adapter selected by mode at construction time.

**Where:**
- `pmacs/execution/service.py` (MODIFY)
- `tests/unit/test_execution_service.py` (EXTEND)

**Action:**
- Add `adapter: BrokerAdapter` parameter to `ExecutionService.__init__`.
- In `_handle_client()`, after signature verification succeeds:
  - Parse `payload_bytes` as `TradePlan.model_validate_json()`.
  - Call `await self._adapter.submit_order(trade_plan)` to get `broker_order_id`.
  - Call `await self._adapter.poll_fill(broker_order_id)` to get `TradeResult`.
  - Build response with real fill data: `{"status": "ACCEPTED", "fill": {"price": fill.filled_price_usd, "qty": fill.filled_quantity, ...}, "broker_order_id": ...}`.
- On adapter exception: return `{"status": "REJECTED", "reason": str(exc)}`.
- Preserve existing audit logging on every submission.
- Backward compat: `ExecutionService(sock_path, public_key, audit_dir, adapter=None)`. If adapter is None, construct `MockAdapter()` (existing tests pass unchanged).

**Test:**
```bash
pytest tests/unit/test_execution_service.py -x -q
```
- Existing tests still pass (MockAdapter is default when adapter=None)
- New test: inject MockAdapter explicitly, verify fill data flows through
- New test: adapter raises -> REJECTED response

**Commit:** `feat(10): wire BrokerAdapter into ExecutionService`

**Done:** ExecutionService uses BrokerAdapter for all fills. MockAdapter preserves backward compat. Signature verification unchanged. Audit logging intact.

---

### Wave 2: Catastrophe Net Wiring (S2)

**Goal:** Wire catastrophe-net stop placement to broker adapter. Ensure the cancel-before-sell sequence works end-to-end.

**Depends on:** Wave 1 (adapter.py, alpaca_paper.py)

---

#### Task 2.1: Wire catastrophe-net stop to broker adapter

**What:** After a successful fill, automatically place the 15% catastrophe-net stop via the adapter. Store the stop order ID on the holding/ledger for later cancellation.

**Where:**
- `pmacs/execution/service.py` (MODIFY _handle_client)
- `pmacs/execution/catastrophe_net.py` (wire broker parameter)
- `tests/unit/test_catastrophe_cancel.py` (EXTEND)

**Action:**
- In `ExecutionService._handle_client()`, after `poll_fill()` returns a FILLED result:
  - Compute catastrophe stop price: `compute_catastrophe_stop(fill.filled_price_usd)`.
  - Call `await self._adapter.place_stop_order(ticker=plan.ticker, stop_price=stop_price, qty=fill.filled_quantity)`.
  - Include `stop_order_id` in response dict alongside `broker_order_id`.
  - If stop placement fails: log CRITICAL, include warning in response. Do NOT engage kill switch on placement failure (position is still valid; StopLossMonitor will cover).
- Update `catastrophe_net.py`'s `cancel_catastrophe_net()` to accept `BrokerAdapter` instead of generic `broker` with `.cancel_order()`. The existing duck-typed interface already matches.
- Audit: log `catastrophe_net_placed` event with holding_id, stop_price, stop_order_id.

**Test:**
```bash
pytest tests/unit/test_catastrophe_cancel.py -x -q
```
- Test: successful fill -> catastrophe-net stop placed automatically
- Test: stop placement failure -> position still recorded, warning logged
- Test: cancel_catastrophe_net with MockAdapter -> CancelResult(success=True)
- Test: cancel failure -> BrokerError raised (existing test)

**Commit:** `feat(10): wire catastrophe-net stop to broker adapter`

**Done:** Every filled position gets a 15% broker-side catastrophe-net stop. Stop order ID returned in execution response. Cancel path already works via existing cancel_catastrophe_net().

---

#### Task 2.2: Integration test -- full paper order lifecycle

**What:** End-to-end test covering: TradePlan signing -> UDS submission -> adapter submit -> fill polling -> catastrophe-net stop placement -> cancel stop -> submit exit SELL. Uses MockAdapter (no real API needed for CI).

**Where:**
- `tests/integration/test_paper_trade.py` (EXTEND)

**Action:**
- Create test function `test_full_order_lifecycle_with_mock_adapter`:
  1. Start ExecutionService with MockAdapter on temp UDS socket
  2. Create TradePlan, sign it with Ed25519
  3. Send via `sign_and_send()` client helper
  4. Verify response: status=ACCEPTED, fill with real price/qty, broker_order_id set
  5. Verify catastrophe-net stop_order_id in response
  6. Cancel the stop via adapter.cancel_order()
  7. Submit exit SELL order
  8. Verify second fill received
  9. Verify audit log entries for both submissions
- This test validates the full wiring without needing real Alpaca credentials.

**Test:**
```bash
pytest tests/integration/test_paper_trade.py::test_full_order_lifecycle_with_mock_adapter -x -q
```

**Commit:** `test(10): full paper order lifecycle integration test`

**Done:** Integration test passes. MockAdapter validates entire order lifecycle: submit -> fill -> catastrophe-net -> cancel -> exit.

---

### Wave 3: Wizard UI (S3)

**Goal:** Build all 11 wizard step HTML templates and the 3 missing backend step files. Wizard runs on pmacs-dashboard (:8001) per D4.

**Depends on:** Nothing (independent of broker work)

---

#### Task 3.1: Wizard HTML templates (11 steps)

**What:** Create 11 Jinja2 HTML templates for the wizard under `pmacs/web/templates/wizard/`. Follow the Notion aesthetic from base.html. Each step is a full-window panel with a progress strip showing 11 dots. Forward-only navigation. Source.md S12 specifies each step's content.

**Where:**
- `pmacs/web/templates/wizard/step01_welcome.html` (NEW)
- `pmacs/web/templates/wizard/step02_inference.html` (NEW)
- `pmacs/web/templates/wizard/step03_model.html` (NEW)
- `pmacs/web/templates/wizard/step04_keychain.html` (NEW)
- `pmacs/web/templates/wizard/step05_embedding.html` (NEW)
- `pmacs/web/templates/wizard/step06_db_init.html` (NEW)
- `pmacs/web/templates/wizard/step07_data_ping.html` (NEW)
- `pmacs/web/templates/wizard/step08_universe.html` (NEW)
- `pmacs/web/templates/wizard/step09_cycle_prefs.html` (NEW)
- `pmacs/web/templates/wizard/step10_totp.html` (NEW)
- `pmacs/web/templates/wizard/step11_complete.html` (NEW)
- `pmacs/web/templates/wizard/_progress.html` (NEW - shared progress strip partial)

**Action:**
- Create `_progress.html` partial: renders 11 dots, filled for completed steps, outlined for current, empty for remaining. CSS: `w-2.5 h-2.5 rounded-full` with accent/zinc colors.
- Each step template extends a minimal wizard layout (no sidebar, no top nav -- wizard is full-screen). Include progress strip partial.
- Step content follows Source.md S12.1 step descriptions:
  1. Welcome: system identity, hardware detection, M1/RAM confirmation
  2. Inference: llama-server detection, Ollama alternate, install instructions
  3. Model: download Qwen3.6-35B-A3B, SHA256 verification, progress bar
  4. Keychain: credential entry form (Alpaca, Polygon, Finnhub, FRED, EDGAR, optional)
  5. Embedding: BAAI/bge-base-en-v1.5 download, 768-dim verification
  6. DB init: KuzuDB/Qdrant/DuckDB/SQLite/audit log creation, genesis entry
  7. Data ping: green/red matrix per source, CRITICAL blocking, IMPORTANT/NICE_TO_HAVE warn
  8. Universe: 16-ticker seed display, deselect/add, OHLCV validation
  9. Cycle prefs: timezone, display currency, EOD semantics
  10. TOTP: QR code display, authenticator scan, verify one TOTP
  11. Complete: promote to SHADOW+PAPER, dashboard link to :8001
- Use HTMX for step navigation: `hx-post="/wizard/step/{N}"` triggers backend step execution.
- Error states: specific error code, spec section reference, "Copy for Claude Code" button per Source.md S12.3.
- 200ms cross-fade between steps. No unnecessary transitions.

**Test:**
```bash
pytest tests/e2e/test_wizard_renders.py -x -q
```
- Test: GET /wizard renders step 1
- Test: each step template renders without error (loop 1-11)
- Test: progress strip shows correct dot states
- Test: HTMX navigation to next step works

**Commit:** `feat(10): wizard HTML templates (11 steps)`

**Done:** All 11 wizard step templates render in browser. Progress strip shows correct state. HTMX navigation works between steps. Notion aesthetic applied.

---

#### Task 3.2: Missing backend wizard steps + wizard route

**What:** Create the 3 missing backend step files (`verify_llm.py`, `verify_data.py`, `totp_enroll.py`) and a wizard route in the dashboard FastAPI app to serve the wizard pages and handle step transitions.

**Where:**
- `pmacs/installer/steps/verify_llm.py` (NEW)
- `pmacs/installer/steps/verify_data.py` (NEW)
- `pmacs/installer/steps/totp_enroll.py` (NEW)
- `pmacs/web/routes/wizard.py` (NEW)
- `pmacs/web/app.py` (MODIFY - add wizard router)
- `tests/integration/test_wizard.py` (NEW)

**Action:**
- `verify_llm.py`: start llama-server (or detect running), send short test prompt, verify structured output. Return `{"ok": bool, "message": str, "model_path": str}`.
- `verify_data.py`: for each CRITICAL source (Alpaca, Polygon, EDGAR), make one read query. Return `{"results": {source_name: {ok, message}}, "all_ok": bool}`. IMPORTANT/NICE_TO_HAVE failures are warnings, not blockers.
- `totp_enroll.py`: generate TOTP secret via `pmacs/cortex/totp.py`, store in Keychain as `pmacs.system.totp_secret`, return QR code data URI for display. Verify one TOTP code from user input before marking complete.
- `pmacs/web/routes/wizard.py`: FastAPI router with:
  - `GET /wizard` -> render step01_welcome.html (or resume step if in progress)
  - `POST /wizard/step/{N}` -> run backend step N, render step N+1 on success, re-render N on failure
  - `GET /wizard/status` -> JSON with current step, completed steps
  - Wizard state stored in SQLite (checkpoint at every step per Source.md S12.1)
- Register wizard router in `pmacs/web/app.py`.

**Test:**
```bash
pytest tests/integration/test_wizard.py -x -q
```
- Test: verify_llm step with mocked llama-server returns ok
- Test: verify_data step with mocked sources returns per-source results
- Test: totp_enroll generates secret and validates code
- Test: wizard route GET /wizard renders step 1
- Test: wizard route POST /wizard/step/1 advances to step 2

**Commit:** `feat(10): wizard backend steps + route`

**Done:** All 11 wizard steps have backend handlers. Wizard route serves templates and handles transitions. TOTP enrollment works. State checkpoints to SQLite.

---

### Wave 4: Operational Gaps (S4)

**Goal:** Fill Ollama JSON schema gap, persist dead-letter queue to SQLite, implement SSE Last-Event-ID resume.

**Depends on:** Nothing (independent)

---

#### Task 4.1: Ollama JSON schemas (9 files)

**What:** Create JSON Schema equivalents of all 9 existing GBNF grammars for the Ollama backend. Place in `pmacs/agents/schemas_json/`.

**Where:**
- `pmacs/agents/schemas_json/__init__.py` (NEW)
- `pmacs/agents/schemas_json/macro_regime.json` (NEW)
- `pmacs/agents/schemas_json/catalyst_summarizer.json` (NEW)
- `pmacs/agents/schemas_json/moat_analyst.json` (NEW)
- `pmacs/agents/schemas_json/growth_hunter.json` (NEW)
- `pmacs/agents/schemas_json/insider_activity.json` (NEW)
- `pmacs/agents/schemas_json/short_interest.json` (NEW)
- `pmacs/agents/schemas_json/forensics.json` (NEW)
- `pmacs/agents/schemas_json/crucible.json` (NEW)
- `pmacs/agents/schemas_json/memo_writer.json` (NEW)
- `tests/unit/test_ollama_schemas.py` (NEW)

**Action:**
- Create `pmacs/agents/schemas_json/` directory.
- For each `.gbnf` in `pmacs/agents/grammars/`, create a corresponding `.json` JSON Schema:
  - Read the GBNF to extract fields, enum values, types.
  - Create JSON Schema `{"type": "object", "properties": {...}, "required": [...]}`.
  - GBNF enums map to `"enum": [...]`. Number fields get `"type": "number"` with min/max bounds. String arrays map to `"type": "array", "items": {"type": "string"}`.
  - Per Agents.md S3: GBNF is strictly more expressive. JSON Schema accepts slightly wider output space (sanity validators catch the difference).
- `__init__.py`: loader function `load_schema(persona: str) -> dict` that reads and returns the JSON Schema for a persona.
- Test: validate each schema is valid JSON Schema (use `jsonschema` validator if available, else parse and check structure). Cross-reference fields with GBNF source.

**Test:**
```bash
pytest tests/unit/test_ollama_schemas.py -x -q
```
- All 9 schemas are valid JSON Schema
- Each schema's fields match the corresponding GBNF grammar
- load_schema() returns correct dict per persona

**Commit:** `feat(10): Ollama JSON schemas (9 persona schemas)`

**Done:** All 9 JSON schemas exist. Each matches its GBNF grammar. Loader function works. Ollama backend can use these for structured output.

---

#### Task 4.2: Dead-letter SQLite persistence + SSE Last-Event-ID resume

**What:** Persist dead-letter queue entries to the SQLite `dead_letter` table (Architecture.md S14.1). Implement SSE replay from Last-Event-ID so reconnecting clients receive missed events.

**Where:**
- `pmacs/storage/dead_letter.py` (NEW or MODIFY if in-memory exists elsewhere)
- `pmacs/nervous/sse_publisher.py` (MODIFY)
- `pmacs/nervous/api.py` (MODIFY SSE endpoint)
- `tests/unit/test_dead_letter.py` (NEW)
- `tests/unit/test_sse_endpoint.py` (EXTEND)

**Action:**

**Dead-letter persistence:**
- Create `pmacs/storage/dead_letter.py` with `DeadLetterQueue` class.
- Schema per Architecture.md S14.1: `CREATE TABLE dead_letter (id INTEGER PRIMARY KEY, op_type TEXT NOT NULL, target_db TEXT NOT NULL, payload TEXT NOT NULL, queued_at TIMESTAMP NOT NULL, retry_count INTEGER NOT NULL DEFAULT 0, last_attempt_at TIMESTAMP, last_error TEXT, status TEXT NOT NULL DEFAULT 'PENDING')`.
- Index: `idx_dead_letter_status ON dead_letter(status)`.
- Methods: `enqueue(op_type, target_db, payload)`, `process_next()`, `mark_failed(id)`, `mark_resolved(id)`.
- Backoff: 1s, 5s, 30s, 5min, 1h, 1d. After 6 attempts: status=FAILED, emit `DEAD_LETTER_QUEUED` audit+debug.
- SQLite connection via existing storage pattern in `pmacs/storage/sqlite.py`.

**SSE Last-Event-ID resume:**
- In `SSEPublisher`: add `_event_log: list[str]` ring buffer (last 1000 events) to enable replay.
- On `publish()`: append `(event_id, frame)` to log.
- Add `get_events_since(last_id: int) -> list[str]` method that returns all events after the given ID.
- In SSE endpoint handler: read `Last-Event-ID` header from request. If present, call `get_events_since(int(last_id))` and send missed events before subscribing to live stream.
- Client receives continuous event stream without gaps on reconnect.

**Test:**
```bash
pytest tests/unit/test_dead_letter.py tests/unit/test_sse_endpoint.py -x -q
```
- Dead-letter: enqueue -> persisted to SQLite, retry_count increments, FAILED after 6 attempts
- SSE: publisher stores events in ring buffer
- SSE: get_events_since returns correct subset
- SSE: endpoint with Last-Event-ID header replays missed events

**Commit:** `feat(10): dead-letter SQLite persistence + SSE Last-Event-ID resume`

**Done:** Dead-letter entries survive process restart in SQLite. SSE clients reconnect without missing events. Both features match Architecture.md S14.1 spec.

---

### Wave 5: Integration + Exit Test (S5)

**Goal:** Validate everything works together. Run the phase exit test.

**Depends on:** Wave 1, Wave 2, Wave 3, Wave 4

---

#### Task 5.1: Exit test validation + final integration

**What:** Run all exit test items from CONTEXT.md. Fix any issues found. Ensure full test suite passes.

**Where:**
- `tests/integration/test_phase10_exit.py` (NEW)
- All files from prior waves (fixes if needed)

**Action:**
- Create exit test file with 7 test functions matching exit test items from CONTEXT.md:
  1. `test_alpaca_paper_limit_buy`: AlpacaPaperAdapter (or MockAdapter in CI) submits LIMIT BUY, receives fill, verifies TradeResult fields.
  2. `test_paper_ledger_updated`: After fill, paper ledger has correct position with entry price and quantity.
  3. `test_catastrophe_net_placed`: After position open, catastrophe-net stop exists at 15% below entry.
  4. `test_wizard_all_steps_render`: All 11 wizard step templates render without error.
  5. `test_dead_letter_persistence`: Enqueue dead-letter entry, verify SQLite row exists, process and resolve.
  6. `test_sse_reconnect_resume`: Connect SSE client, emit events, disconnect, reconnect with Last-Event-ID, verify all events received.
  7. `test_ollama_schemas_validate`: All 9 JSON schemas pass jsonschema validation and match GBNF grammar fields.
- Run `pytest tests/ -x` to verify no regressions.
- Fix any issues found.

**Test:**
```bash
pytest tests/integration/test_phase10_exit.py -x -v
```

**Commit:** `test(10): phase 10 exit test validation`

**Done:** All 7 exit test items pass. Full test suite green. Phase 10 complete.

---

## Verification

- [ ] AlpacaPaperAdapter submits a LIMIT BUY order via paper API (or MockAdapter in CI)
- [ ] Fill is received and paper ledger updated
- [ ] Catastrophe-net stop placed at 15% below entry
- [ ] Wizard steps 1-11 render in browser
- [ ] Dead-letter entries persist to SQLite
- [ ] SSE client reconnects with Last-Event-ID without missing events
- [ ] All 9 Ollama JSON schemas validate against their GBNF grammars
- [ ] Full test suite green: `pytest tests/ -x`

## Dependencies

- **alpaca-py>=0.43.0** -- new dependency, install in Wave 1 Task 1.2
- **Phase 9 complete** -- orchestrator mock fills are the starting point (confirmed done per D6)
- **pmacs/execution/signing.py** -- Ed25519 signing (exists, no changes)
- **pmacs/cortex/totp.py** -- TOTP verification for wizard step 10 (exists, no changes)

## Out of Scope

- Real (live) Alpaca adapter -- paper only per D1
- Cmd-K command palette -- deferred to Phase 11
- Sankey/D3 visualization -- deferred to Phase 11
- Keyboard shortcuts -- deferred to Phase 11
- Performance profiling -- covered by Phase 9
- TradePlan.quantity type change -- stays int per D3
