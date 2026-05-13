# Phase 10: Broker Integration - Research

**Researched:** 2026-05-13
**Domain:** Alpaca paper trading API integration, broker adapter architecture, wizard frontend, operational tooling
**Confidence:** MEDIUM-HIGH

## Summary

Phase 10 replaces the orchestrator's mock fills (step 13o) with real Alpaca paper API integration, and fills the remaining operational gaps left from earlier phases. The current execution service (`pmacs/execution/service.py`) is a UDS server that verifies Ed25519 signatures and returns hardcoded mock fills with `price: 0.0, qty: 0`. The paper ledger (`pmacs/sim/ledger.py`) is functional with $5K capital, position limits, and catastrophe-net stop defaults. The catastrophe_net module exists but has no broker to submit orders to. The wizard has 11 steps defined in enum but only 8 backend step files (no `verify_llm.py`, `verify_data.py`, or `TOTP enrollment` step). The schemas_json directory for Ollama backend is empty. The phase 9 review found a critical bug (INTERRUPTED state unreachable) and several high-severity issues that must be fixed before broker integration work begins.

**Primary recommendation:** Build the broker adapter as a protocol-based abstraction (`BrokerAdapter` ABC) with `AlpacaPaperAdapter` and `AlpacaLiveAdapter` implementations. Wire it into the existing ExecutionService UDS handler so that signature verification still guards every submission. Use `alpaca-py` SDK v0.43.4 for paper trading with httpx-based async calls. Fix phase 9 review findings (C1-C3, H1-H5) as Wave 0 before any broker work.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Order submission + fill polling | API / Backend (pmacs-execution) | -- | Only pmacs-execution imports broker SDK (Architecture.md §4.1) |
| Catastrophe-net stop placement | API / Backend (pmacs-execution) | -- | Broker-side stop at 15% below entry, placed at position open |
| Fill processing (step 16) | API / Backend (pmacs-nervous) | -- | Orchestrator polls fills and updates ledger + holdings |
| Paper ledger management | API / Backend (pmacs-nervous) | -- | In-memory ledger for PAPER mode tracking |
| Broker credential storage | OS (macOS Keychain) | -- | Architecture.md §1.3: Keychain for ALL secrets |
| Wizard frontend | Browser / Client (HTML) | Frontend Server (Jinja2) | 11-step HTML templates served by pmacs-nervous |
| Ollama JSON schemas | Static files | -- | .json files consumed by Ollama backend |
| SSE Last-Event-ID | Frontend Server (pmacs-nervous) | -- | Already implemented in api.py but needs verification |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| alpaca-py | 0.43.4 | Alpaca paper/live trading SDK | Official Python SDK; covers trading + data APIs; supports paper endpoint [VERIFIED: pip index] |
| httpx | 0.28.1 | Async HTTP client (already installed) | Already in project dependencies; alpaca-py uses it internally |
| pydantic | >=2.5 | Schema validation (already installed) | Project standard; TradePlan/TradeResult already use it |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| cryptography | >=42.0 | Ed25519 signing (already installed) | Already used in pmacs/execution/signing.py |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| alpaca-py | Raw httpx calls to Alpaca REST | More control but must handle auth, retries, rate limits, order status polling manually. alpaca-py wraps these correctly. |
| alpaca-py | alpaca-trade-api (deprecated) | alpaca-trade-api is the old SDK name; alpaca-py is the unified replacement [ASSUMED] |

**Installation:**
```bash
pip install alpaca-py==0.43.4
```

Add to `pyproject.toml` dependencies:
```
"alpaca-py>=0.43.0",
```

**Version verification:**
```
alpaca-py 0.43.4 (latest) — verified via pip index versions
```

## Architecture Patterns

### System Architecture Diagram

```
Orchestrator (pmacs-nervous)
    |
    | Step 13o: verdict == BUY/STRONG_BUY
    |
    v
ExecutionService (UDS server)
    |
    | 1. Verify Ed25519 signature on TradePlan
    | 2. Select adapter (paper vs live) via BrokerAdapter protocol
    |
    +---> AlpacaPaperAdapter
    |       |  submit_order() → Alpaca paper API
    |       |  poll_fill() → check order status
    |       |  place_stop() → catastrophe-net GTC stop
    |       |  cancel_order() → cancel stop on exit
    |
    +---> AlpacaLiveAdapter (future)
            |  Same interface, live endpoint
            |
    v
TradeResult (filled_price, filled_qty, broker_order_id)
    |
    v
PaperLedger.open_position() / close_position()
    |
    v
Audit log (trade_executed event)
```

### Recommended Project Structure
```
pmacs/
├── execution/
│   ├── __init__.py
│   ├── service.py          # UDS server (exists, extend)
│   ├── signing.py          # Ed25519 signing (exists, no changes)
│   ├── catastrophe_net.py  # Stop order builder (exists, wire to broker)
│   ├── adapter.py          # NEW: BrokerAdapter ABC + factory
│   ├── alpaca_paper.py     # NEW: AlpacaPaperAdapter
│   └── alpaca_live.py      # NEW: AlpacaLiveAdapter (stub for future)
├── sim/
│   ├── ledger.py           # Paper ledger (exists, no changes)
│   └── alpaca_paper_adapter.py  # NOT NEEDED (merged into execution/alpaca_paper.py)
├── installer/
│   ├── wizard.py           # Wizard enum (exists, update step names)
│   └── steps/              # Backend step handlers
│       ├── verify_llm.py   # NEW: step 6 backend
│       ├── verify_data.py  # NEW: step 8 backend
│       └── totp_enroll.py  # NEW: step 9 backend
├── agents/
│   └── schemas_json/       # Ollama JSON schemas (9 files needed)
│       ├── macro_regime.json
│       ├── catalyst_summarizer.json
│       ├── moat_analyst.json
│       ├── growth_hunter.json
│       ├── insider_activity.json
│       ├── short_interest.json
│       ├── forensics.json
│       ├── crucible.json
│       └── memo_writer.json
└── web/
    └── templates/
        └── wizard/         # NEW: 11 HTML wizard templates
            ├── step01_welcome.html
            ├── step02_inference.html
            ├── ...
            └── step11_complete.html
```

### Pattern 1: BrokerAdapter Protocol
**What:** Abstract base class defining the broker interface. Paper and live adapters implement the same protocol.
**When to use:** All execution paths in pmacs-execution.
**Example:**
```python
# pmacs/execution/adapter.py
from abc import ABC, abstractmethod
from pmacs.schemas.trade import TradePlan, TradeResult

class BrokerAdapter(ABC):
    """Protocol for broker communication (Architecture.md §4.5, §9)."""

    @abstractmethod
    async def submit_order(self, plan: TradePlan) -> str:
        """Submit order, return broker_order_id."""

    @abstractmethod
    async def poll_fill(self, broker_order_id: str) -> TradeResult:
        """Poll until fill received or timeout."""

    @abstractmethod
    async def place_stop_order(self, ticker: str, stop_price: float, qty: float) -> str:
        """Place catastrophe-net stop, return broker_order_id."""

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an existing order. Returns True on success."""

    @abstractmethod
    async def get_position(self, ticker: str) -> dict | None:
        """Get current position for a ticker."""

def create_adapter(mode: str, api_key: str, api_secret: str) -> BrokerAdapter:
    """Factory: select adapter based on current mode."""
    if mode in ("INSTALLING", "SHADOW", "PAPER", "PAPER_VALIDATED"):
        from pmacs.execution.alpaca_paper import AlpacaPaperAdapter
        return AlpacaPaperAdapter(api_key, api_secret)
    else:
        from pmacs.execution.alpaca_live import AlpacaLiveAdapter
        return AlpacaLiveAdapter(api_key, api_secret)
```

### Pattern 2: Alpaca Paper Adapter
**What:** Concrete implementation using alpaca-py SDK against paper endpoint.
**When to use:** All PAPER mode trading.
**Example:**
```python
# pmacs/execution/alpaca_paper.py
import alpaca
from pmacs.execution.adapter import BrokerAdapter

class AlpacaPaperAdapter(BrokerAdapter):
    PAPER_URL = "https://paper-api.alpaca.markets"

    def __init__(self, api_key: str, api_secret: str):
        # alpaca-py TradingClient for paper
        self._client = alpaca.TradingClient(
            api_key=api_key,
            secret_key=api_secret,
            paper=True,  # routes to paper-api.alpaca.markets
        )

    async def submit_order(self, plan: TradePlan) -> str:
        order = self._client.submit_order(
            symbol=plan.ticker,
            qty=plan.quantity,
            side=alpaca.OrderSide.BUY if plan.direction == "BUY" else alpaca.OrderSide.SELL,
            type=alpaca.OrderType.LIMIT if plan.order_type == "LIMIT" else alpaca.OrderType.MARKET,
            limit_price=plan.price_usd if plan.order_type == "LIMIT" else None,
            time_in_force=alpaca.TimeInForce.DAY,
        )
        return order.id

    async def poll_fill(self, broker_order_id: str) -> TradeResult:
        # Poll until filled or timeout (30s default)
        order = self._client.get_order_by_id(broker_order_id)
        # Map order status to TradeResult
        ...
```

### Anti-Patterns to Avoid
- **Placing tight stops on broker side:** PMACS manages tight stops internally. Broker gets only 15% catastrophe-net (Architecture.md §16.7, §11.1).
- **Auto-promoting mutations:** All mutations require operator TOTP. Broker integration must not change this.
- **alpaca-trade-api import:** Deprecated. Use `alpaca-py` only (package name is `alpaca` after install).
- **Hardcoding broker URLs:** Use adapter factory with mode-based URL selection.
- **Skipping signature verification:** Ed25519 signature check must happen before any broker submission, even in paper mode.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Order submission + status tracking | Custom REST calls to Alpaca | alpaca-py `TradingClient` | Handles auth headers, retries, rate limiting, order lifecycle |
| Fill polling with timeout | Manual polling loop | alpaca-py order status + asyncio.wait_for | Edge cases around partial fills, rejections, cancellations |
| Broker auth header construction | Manual HTTP header signing | alpaca-py client constructor | SDK handles `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY` headers |
| Fractional share handling | Custom rounding logic | Alpaca's fractional share support | Alpaca paper supports fractional shares; SizingEngine already produces float target_shares |
| TOTP verification | Custom TOTP implementation | `pmacs/cortex/totp.py` (exists) | Already implemented and tested |

**Key insight:** The broker adapter is the ONLY place that imports `alpaca` (Architecture.md §4.1: "pmacs-execution is the ONLY process that imports broker SDK code"). All other code talks to the adapter via the ABC.

## Current State Inventory

### What Exists
| Component | File | State | What Needs Doing |
|-----------|------|-------|------------------|
| ExecutionService (UDS) | `pmacs/execution/service.py` | Returns mock fills (price=0, qty=0) | Wire to BrokerAdapter after signature check |
| Ed25519 signing | `pmacs/execution/signing.py` | Complete, works | No changes needed |
| Catastrophe net stop builder | `pmacs/execution/catastrophe_net.py` | Builds order dict, but never submits | Wire to adapter.place_stop_order() |
| Paper ledger | `pmacs/sim/ledger.py` | Complete with $5K, constraints, PnL | No changes needed |
| TradePlan/TradeResult schemas | `pmacs/schemas/trade.py` | Complete with frozen Pydantic models | Add `broker_order_id` field to TradeResult (already exists) |
| Wizard enum | `pmacs/installer/wizard.py` | 11 steps defined | Backend step files for verify_llm, verify_data, TOTP enrollment missing |
| Wizard step files | `pmacs/installer/steps/` | 8 of ~11 backend files exist | Missing: verify_llm, verify_data, totp_enroll |
| Alpaca data source | `pmacs/data/sources/alpaca_data.py` | Fetches bars via DataGateway | No changes (separate from trading API) |
| Broker config step | `pmacs/installer/steps/configure_broker.py` | Stores API key/secret flags | Needs Keychain storage integration |
| SSE endpoint | `pmacs/nervous/api.py` | Has Last-Event-ID support | Verify reconnection works end-to-end |
| Dead-letter queue | `pmacs/logsys/dead_letter.py` | In-memory with exponential backoff | Operational but not persisted to SQLite |

### What Does NOT Exist
| Component | Spec Reference | What Needs Building |
|-----------|---------------|-------------------|
| `pmacs/execution/adapter.py` | Architecture.md §4.5, §9 | BrokerAdapter ABC + factory |
| `pmacs/execution/alpaca_paper.py` | Phases.md Phase 8 | Alpaca paper adapter implementation |
| `pmacs/execution/alpaca_live.py` | Architecture.md §4.1 | Stub for future LIVE mode |
| `pmacs/sim/alpaca_paper_adapter.py` | Phases.md Phase 8 | NOT NEEDED (merged into execution/) |
| Wizard HTML templates | Source.md §12 | 11-step Jinja2 templates with Notion aesthetic |
| Ollama JSON schemas (9 files) | Agents.md §3 | JSON Schema equivalents of GBNF grammars |
| Dead-letter SQLite persistence | Architecture.md §14.1 | Write dead_letter entries to SQLite table |

## Phase 9 Review Findings (MUST FIX as Wave 0)

These findings from `.planning/phases/phase-9/09-REVIEW.md` must be addressed before broker work begins.

### Critical
| ID | Issue | Impact on Phase 10 | Fix |
|----|-------|-------------------|-----|
| C1 | INTERRUPTED state unreachable -- no VALID_TRANSITIONS entry | Mid-cycle abort silently fails; broker orders may be in-flight with no tracking | Add INTERRUPTED to all non-terminal state transitions in contracts.py |
| C2 | Direct Holding field mutation outside state_machine | Execution fields bypass validation; broker fills need to go through a proper method | Add `update_execution_fields()` helper or annotate the existing pattern |
| C3 | SQL injection surface in `_column_exists` | f-string PRAGMA with unsanitized input | Add regex validation guard |

### High
| ID | Issue | Impact on Phase 10 | Fix |
|----|-------|-------------------|-----|
| H1 | 6 unregistered error codes | Broker error codes won't be canonical | Add BROKER_SUBMISSION_FAILED, FILL_TIMEOUT, etc. to registry |
| H2 | `WHERE state = 'OPEN'` should be 'ACTIVE' | Corporate actions never applied | Fix SQL query |
| H4 | `_run_symbol` is 800+ lines | Hard to add broker fill handling in step 13o | Extract sub-steps into methods |
| H5 | `_symbol_holdings.pop` missing on 3 abort paths | Holdings leak in tracker | Add pop() calls |

### Medium
| ID | Issue | Notes |
|----|-------|-------|
| M1 | Hardcoded dummy signing key | Must add mode assertion before real broker work |
| M2 | Connection-per-query pattern | Performance issue with many holdings |
| M6 | CREATE TABLE in step methods | Move to SCHEMA_SQL |

## Common Pitfalls

### Pitfall 1: Alpaca paper fills are NOT instant
**What goes wrong:** Assuming paper API returns fills synchronously like mock fills do.
**Why it happens:** Mock fills return immediately. Real Alpaca paper orders (especially LIMIT) may take time to fill, or may never fill if the limit price is unrealistic.
**How to avoid:** Implement async fill polling with timeout (30s default). Fall back to MARKET orders for paper mode to ensure fills. Set `time_in_force=DAY` for limit orders.
**Warning signs:** Orders stuck in PENDING status; TradeResult with `filled_quantity=0`.

### Pitfall 2: Dummy signing key used in live mode
**What goes wrong:** The hardcoded `hashlib.sha256(b"pmacs_paper_mode").digest()` key from orchestrator line 1610 could be used accidentally in live mode.
**Why it happens:** No mode guard on the signing path.
**How to avoid:** Add assertion: `assert current_mode in (Mode.INSTALLING.value, Mode.SHADOW.value, Mode.PAPER.value, Mode.PAPER_VALIDATED.value)`.
**Warning signs:** All trade signatures are forgeable with a publicly known key.

### Pitfall 3: Catastrophe-net stop not placed before position open
**What goes wrong:** If the order submission succeeds but the stop placement fails, the position is unprotected.
**Why it happens:** Treating stop placement as optional or non-blocking.
**How to avoid:** Place catastrophe-net stop IMMEDIATELY after order fill confirmation. If stop placement fails, engage kill switch (Architecture.md §11.5).
**Warning signs:** Position exists in ledger but no corresponding stop_events row.

### Pitfall 4: Duplicate catastrophe-net fill on exit
**What goes wrong:** When exiting a position, the SELL order fires BEFORE the catastrophe-net stop is cancelled, causing both to execute.
**Why it happens:** Race condition between cancel and sell submission.
**How to avoid:** Architecture.md §11.5 is explicit: cancel catastrophe-net FIRST, THEN submit SELL. If cancel fails, engage kill switch.
**Warning signs:** Two SELL fills for the same ticker on the same day.

### Pitfall 5: Alpaca paper account reset during testing
**What goes wrong:** Alpaca paper accounts can be reset, wiping all positions and history. Tests that assume pre-existing positions break.
**Why it happens:** Paper accounts are ephemeral by design.
**How to avoid:** Never depend on pre-existing paper account state. Always create positions programmatically in tests. Use mock adapter for unit tests, real adapter only for integration tests.
**Warning signs:** Integration tests fail with "position not found" errors.

### Pitfall 6: Fractional shares with integer-only broker APIs
**What goes wrong:** SizingEngine produces float `target_shares` but some code paths use `int(shares)`.
**Why it happens:** Inconsistent handling of share quantities.
**How to avoid:** Alpaca paper supports fractional shares. Pass float directly. The `TradePlan.quantity` field is `int = Field(ge=1)` -- consider updating to `float` for Alpaca compatibility.
**Warning signs:** Position sizes rounded down, leaving uninvested cash.

## Code Examples

### Alpaca Paper Order Submission
```python
# Source: alpaca-py SDK (pip install alpaca-py==0.43.4)
import alpaca

# Paper client -- routes to paper-api.alpaca.markets automatically
client = alpaca.TradingClient(
    api_key="YOUR_KEY",
    secret_key="YOUR_SECRET",
    paper=True,
)

# Submit a limit buy order
order = client.submit_order(
    symbol="AAPL",
    qty=10,
    side=alpaca.OrderSide.BUY,
    type=alpaca.OrderType.LIMIT,
    limit_price=150.00,
    time_in_force=alpaca.TimeInForce.DAY,
)

# Check order status
order = client.get_order_by_id(order.id)
# order.status: "new", "partially_filled", "filled", "canceled", "rejected"

# Place a stop (catastrophe-net)
stop_order = client.submit_order(
    symbol="AAPL",
    qty=10,
    side=alpaca.OrderSide.SELL,
    type=alpaca.OrderType.STOP,
    stop_price=127.50,  # 15% below $150
    time_in_force=alpaca.TimeInForce.GTC,
)

# Cancel an order
client.cancel_order_by_id(stop_order.id)
```

### BrokerAdapter Integration into ExecutionService
```python
# How to wire the adapter into the existing UDS handler:
# pmacs/execution/service.py _handle_client()

if valid:
    # Instead of mock fill:
    # response = {"status": "ACCEPTED", "fill": {"price": 0.0, "qty": 0, ...}}

    # Use the broker adapter:
    try:
        trade_plan = TradePlan.model_validate_json(payload_bytes)
        broker_order_id = await self._adapter.submit_order(trade_plan)
        fill_result = await self._adapter.poll_fill(broker_order_id)

        # Place catastrophe-net stop after fill
        if trade_plan.stop_price_usd:
            stop_id = await self._adapter.place_stop_order(
                ticker=trade_plan.ticker,
                stop_price=trade_plan.stop_price_usd,
                qty=fill_result.filled_quantity,
            )

        response = {
            "status": "ACCEPTED",
            "fill": {
                "price": fill_result.filled_price_usd,
                "qty": fill_result.filled_quantity,
                "timestamp": fill_result.filled_at.isoformat(),
            },
            "broker_order_id": broker_order_id,
            "stop_order_id": stop_id,
        }
    except Exception as exc:
        response = {"status": "REJECTED", "reason": str(exc)}
```

### Ollama JSON Schema Pattern
```json
// pmacs/agents/schemas_json/macro_regime.json
// Source: Agents.md §3 — JSON Schema equivalent of grammars/macro_regime.gbnf
{
    "type": "object",
    "properties": {
        "regime": {
            "type": "string",
            "enum": ["BULL", "BEAR", "NEUTRAL", "TRANSITION", "UNCERTAIN"]
        },
        "regime_confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0
        },
        "p_up": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "p_flat": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "p_down": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "evidence_ids": {
            "type": "array",
            "items": {"type": "string"}
        },
        "reasoning": {"type": "string"}
    },
    "required": ["regime", "regime_confidence", "p_up", "p_flat", "p_down", "reasoning"]
}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| alpaca-trade-api | alpaca-py | 2023 | Unified SDK for trading + data; package name `alpaca` after install |
| REST-only broker interaction | alpaca-py TradingClient | alpaca-py 0.20+ | Synchronous and async clients available |
| Manual fill polling | WebSocket streaming fills | alpaca-py 0.30+ | Can use streaming for real-time fills, but polling is simpler for PMACS cycle model |

**Deprecated/outdated:**
- `alpaca-trade-api`: Old package name, replaced by `alpaca-py`. Do not use.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | alpaca-py 0.43.4 TradingClient supports paper=True flag for paper endpoint routing | Standard Stack | Must use separate URL if flag not supported |
| A2 | Alpaca paper API supports GTC (Good-Till-Cancelled) stop orders | Catastrophe Net | Must use DAY + re-place daily if GTC not available on paper |
| A3 | Alpaca paper fills are typically fast (< 5s for MARKET, < 30s for LIMIT during market hours) | Pitfalls | Must increase polling timeout if fills take longer |
| A4 | GBNF grammar files are the authoritative source for JSON Schema derivation | Ollama Schemas | Must cross-reference with Pydantic schemas if grammars are incomplete |
| A5 | The wizard frontend templates should be served by pmacs-nervous (port 8000), not pmacs-dashboard (port 8001) | Wizard Frontend | Dashboard is read-only per Architecture.md §4.1 |
| A6 | `pmacs/agents/schemas_json/` directory exists but is empty -- no Ollama schema files started | Current State | If schemas exist elsewhere, would need different approach |

## Open Questions

1. **TradePlan.quantity is `int` -- should it be `float` for fractional shares?**
   - What we know: Alpaca paper supports fractional shares. SizingEngine produces float target_shares.
   - What's unclear: Whether PMACS should use fractional shares or round to whole shares.
   - Recommendation: Change to `float` in TradePlan for Alpaca compatibility. Architecture.md §9.3 SizingEngine already computes `target_shares` as float.

2. **Should the wizard run on pmacs-nervous (:8000) or pmacs-dashboard (:8001)?**
   - What we know: Dashboard is read-only per spec. Wizard needs to write config and create DBs.
   - What's unclear: Source.md §12 doesn't specify which process serves the wizard.
   - Recommendation: pmacs-nervous serves wizard pages (it has write access). After wizard completes, dashboard opens at :8001.

3. **Fill polling timeout for paper orders?**
   - What we know: Paper MARKET orders fill almost instantly. LIMIT orders may never fill.
   - What's unclear: What timeout PMACS should use before giving up on a fill.
   - Recommendation: 30s polling interval, 120s total timeout for LIMIT orders. Fall back to MARKET if timeout exceeds.

4. **Should dead-letter entries be persisted to SQLite?**
   - What we know: Current implementation is in-memory only (survives process lifetime only). Architecture.md §14.1 defines a `dead_letter` SQLite table.
   - What's unclear: Whether persistence is needed for Phase 10 scope.
   - Recommendation: Persist to SQLite as part of operational gaps. In-memory entries are lost on process restart.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| alpaca-py | Broker adapter | Not installed (dry-run verified) | 0.43.4 | -- |
| httpx | Async HTTP (already installed) | Yes | 0.28.1 | -- |
| cryptography | Ed25519 signing (already installed) | Yes | >=42.0 | -- |
| fastapi | API + wizard routes (already installed) | Yes | >=0.110 | -- |
| jinja2 | Wizard templates (already installed) | Yes | >=3.1.6 | -- |
| Alpaca paper API credentials | Paper trading | Needs Keychain | -- | Mock adapter for tests |
| macOS Keychain | Credential storage | Yes | -- | -- |

**Missing dependencies with no fallback:**
- `alpaca-py`: Must install before adapter implementation. Verified available at v0.43.4.

**Missing dependencies with fallback:**
- Alpaca API credentials: Not needed for development (mock adapter for tests). Operator configures during wizard step 4.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest >=8.0 |
| Config file | pyproject.toml `[tool.pytest.ini_options]` |
| Quick run command | `pytest tests/unit/ -x -q` |
| Full suite command | `pytest tests/ -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| BROKER-01 | BrokerAdapter ABC defines submit/poll/stop/cancel | unit | `pytest tests/unit/test_broker_adapter.py -x` | No (Wave 0) |
| BROKER-02 | AlpacaPaperAdapter submits limit buy and receives fill | integration | `pytest tests/integration/test_paper_trade.py::test_alpaca_paper_fill -x` | Partial (existing test file, new test) |
| BROKER-03 | Catastrophe-net stop placed after position open | unit | `pytest tests/unit/test_catastrophe_cancel.py -x` | Yes (exists) |
| BROKER-04 | Catastrophe-net cancel before exit SELL | unit | `pytest tests/unit/test_catastrophe_cancel.py -x` | Yes (exists) |
| BROKER-05 | Dummy signing key rejected in non-paper modes | unit | `pytest tests/unit/test_execution_service.py -x` | Yes (exists) |
| WIZARD-01 | All 11 wizard steps complete with mocked APIs | integration | `pytest tests/integration/test_wizard.py -x` | Partial |
| SCHEMA-01 | All 9 Ollama JSON schemas validate against Pydantic models | unit | `pytest tests/unit/test_ollama_schemas.py -x` | No (Wave 0) |
| FIX-C1 | INTERRUPTED state reachable from all non-terminal states | unit | `pytest tests/unit/test_state_machine.py -x` | Yes |
| FIX-H1 | All error codes registered in VALID_ERROR_CODES | unit | `pytest tests/unit/test_error_codes.py -x` | No |

### Sampling Rate
- **Per task commit:** `pytest tests/unit/ -x -q`
- **Per wave merge:** `pytest tests/ -x`
- **Phase gate:** Full suite green before verifying work

### Wave 0 Gaps
- [ ] `tests/unit/test_broker_adapter.py` -- covers BROKER-01 (adapter ABC + factory)
- [ ] `tests/unit/test_ollama_schemas.py` -- covers SCHEMA-01 (JSON schema validation)
- [ ] `tests/unit/test_error_codes.py` -- covers FIX-H1 (error code registry)
- [ ] Framework install: `pip install alpaca-py==0.43.4` -- must be added to pyproject.toml

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | TOTP for all write operations; Ed25519 for trade signing |
| V3 Session Management | yes | HttpOnly cookies, 24h expiry, single session |
| V4 Access Control | yes | Loopback-only binding; process isolation via launchd users |
| V5 Input Validation | yes | Pydantic v2 validation on all TradePlan inputs |
| V6 Cryptography | yes | Ed25519 signing (cryptography library); SHA256 audit chain |
| V9 Communications | yes | HTTPS to Alpaca API; UDS for local IPC |

### Known Threat Patterns for Broker Integration

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| API key exposure in logs | Information Disclosure | Architecture.md §16: never log secrets; Keychain storage only |
| Unauthorized order submission | Tampering | Ed25519 signature verification before broker submission |
| Man-in-the-middle on broker API | Tampering | HTTPS to Alpaca endpoints; pf rules prevent inference egress |
| Replay attack on TradePlan | Tampering | TradePlan includes timestamp + cycle_id; idempotency via op_idempotency |
| Kill switch bypass during broker failure | Elevation of Privilege | Architecture.md §11.5: broker cancel failure engages kill switch automatically |
| Paper/live mode confusion | Tampering | Mode assertion on signing key; adapter factory checks mode |
| Rate limit exceeded on Alpaca API | Denial of Service | BUCKETS["alpaca_trading"].acquire() before submission |

## Sources

### Primary (HIGH confidence)
- `spec/Architecture.md` §4 (process topology), §9 (engines), §11 (stop-loss), §13 (kill switch) -- read in full
- `spec/Source.md` §12 (wizard), §21 (operator workflows) -- read in full
- `spec/Phases.md` Phase 8 (paper trading) -- read in full
- `spec/Agents.md` §3 (three-layer contract, JSON Schema for Ollama) -- read in full
- `pmacs/execution/service.py` -- read in full (178 lines)
- `pmacs/execution/catastrophe_net.py` -- read in full (225 lines)
- `pmacs/sim/ledger.py` -- read in full (156 lines)
- `pmacs/schemas/trade.py` -- read in full (57 lines)
- `pmacs/nervous/orchestrator.py` -- read in full (3172 lines)
- `pip index versions alpaca-py` -- verified 0.43.4 is latest
- `.planning/phases/phase-9/09-REVIEW.md` -- read in full (336 lines)

### Secondary (MEDIUM confidence)
- `pmacs/installer/steps/configure_broker.py` -- read (45 lines)
- `pmacs/installer/wizard.py` -- read (85 lines)
- `pmacs/nervous/api.py` -- read (255 lines)
- `pmacs/web/templates/settings.html` -- read (300 lines)
- `pmacs/web/app.py` -- read (34 lines)
- `pmacs/data/sources/alpaca_data.py` -- read (25 lines)
- `tests/integration/test_paper_trade.py` -- read first 60 lines

### Tertiary (LOW confidence)
- alpaca-py API surface (submit_order, poll_fill, place_stop) -- [ASSUMED] based on SDK naming conventions and Alpaca REST API documentation
- Ollama JSON Schema format compatibility -- [ASSUMED] based on Agents.md §3 description

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - alpaca-py version verified via pip index; httpx and cryptography already installed
- Architecture: HIGH - BrokerAdapter pattern follows Architecture.md §4.1 isolation rule; all spec sections read
- Pitfalls: MEDIUM-HIGH - based on real Alpaca paper API behavior and Architecture.md anti-patterns
- Ollama schemas: MEDIUM - format is well-specified in Agents.md §3, but exact GBNF-to-JSON-Schema mapping needs verification
- Wizard frontend: MEDIUM - Source.md §12 specifies 11 steps clearly, but HTML template implementation is new territory

**Research date:** 2026-05-13
**Valid until:** 2026-06-13 (stable APIs; alpaca-py version may update)
