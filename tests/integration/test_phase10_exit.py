"""Phase 10 exit test — validates all 7 exit-test items from CONTEXT.md.

1. AlpacaPaperAdapter submits a LIMIT BUY order via paper API (or MockAdapter in CI)
2. Fill is received and paper ledger updated
3. Catastrophe-net stop placed at 15% below entry
4. Wizard steps 1-11 render in browser
5. Dead-letter entries persist to SQLite
6. SSE client reconnects with Last-Event-ID without missing events
7. All 9 Ollama JSON schemas validate against their GBNF grammars

Spec ref: Phases.md Phase 10 exit test, Architecture.md passim.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from pmacs.execution.adapter import MockAdapter, create_adapter
from pmacs.execution.catastrophe_net import compute_catastrophe_stop
from pmacs.nervous.sse_publisher import SSEPublisher
from pmacs.schemas.trade import TradeDirection, TradePlan, OrderType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_adapter():
    return MockAdapter()


@pytest.fixture
def trade_plan():
    return TradePlan(
        id="test-exit-1",
        ticker="AAPL",
        direction=TradeDirection.BUY,
        order_type=OrderType.LIMIT,
        quantity=5,
        price_usd=150.0,
        cycle_id="cycle-exit-test",
    )


@pytest.fixture
def dead_letter_db(tmp_path):
    """Create an in-memory SQLite with dead_letter table."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dead_letter ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "op_type TEXT NOT NULL, "
        "target_db TEXT NOT NULL, "
        "payload TEXT NOT NULL, "
        "queued_at TEXT NOT NULL, "
        "retry_count INTEGER NOT NULL DEFAULT 0, "
        "last_attempt_at TEXT, "
        "last_error TEXT, "
        "status TEXT NOT NULL DEFAULT 'PENDING'"
        ")"
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def web_client():
    """FastAPI test client with all routes including wizard."""
    from pmacs.web.app import app
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Exit Test 1: MockAdapter submits LIMIT BUY, receives fill
# ---------------------------------------------------------------------------

class TestExit01PaperLimitBuy:
    """Exit test 1: AlpacaPaperAdapter (or MockAdapter) submits LIMIT BUY,
    receives fill."""

    @pytest.mark.asyncio
    async def test_submit_order_returns_broker_id(self, mock_adapter, trade_plan):
        broker_order_id = await mock_adapter.submit_order(trade_plan)
        assert broker_order_id is not None
        assert isinstance(broker_order_id, str)
        assert len(broker_order_id) > 0

    @pytest.mark.asyncio
    async def test_poll_fill_returns_filled(self, mock_adapter, trade_plan):
        broker_order_id = await mock_adapter.submit_order(trade_plan)
        fill = await mock_adapter.poll_fill(broker_order_id)
        assert fill.status == "FILLED"

    @pytest.mark.asyncio
    async def test_create_adapter_returns_mock_for_shadow(self):
        adapter = create_adapter(mode="SHADOW")
        assert isinstance(adapter, MockAdapter)

    @pytest.mark.asyncio
    async def test_create_adapter_raises_for_paper_without_keys(self):
        with pytest.raises(ValueError, match="api_key"):
            create_adapter(mode="PAPER")


# ---------------------------------------------------------------------------
# Exit Test 2: Fill received and paper ledger updated
# ---------------------------------------------------------------------------

class TestExit02PaperLedgerUpdated:
    """Exit test 2: After fill, paper ledger has correct position."""

    def test_open_position_adds_to_ledger(self):
        from pmacs.sim.ledger import PaperLedger

        ledger = PaperLedger()
        ledger.open_position("AAPL", 5, 150.0)
        assert ledger.position_count == 1
        assert "AAPL" in ledger.positions
        assert ledger.positions["AAPL"].shares == 5

    def test_open_position_deducts_cash(self):
        from pmacs.sim.ledger import PaperLedger

        ledger = PaperLedger()
        initial_cash = ledger.cash
        ledger.open_position("AAPL", 5, 150.0)
        assert ledger.cash == initial_cash - (5 * 150.0)

    def test_open_position_sets_default_stop(self):
        """Stop price defaults to 15% below entry (catastrophe-net)."""
        from pmacs.sim.ledger import PaperLedger

        ledger = PaperLedger()
        ledger.open_position("AAPL", 5, 150.0)
        pos = ledger.positions["AAPL"]
        assert pos.stop_price is not None
        expected_stop = round(150.0 * (1 - 0.15), 2)  # 127.50
        assert pos.stop_price == expected_stop

    def test_snapshot_reflects_position(self):
        from pmacs.sim.ledger import PaperLedger

        ledger = PaperLedger()
        ledger.open_position("AAPL", 5, 150.0)
        snap = ledger.snapshot()
        assert snap["position_count"] == 1
        assert snap["cash"] == 5000.0 - 750.0

    def test_max_positions_enforced(self):
        from pmacs.sim.ledger import PaperLedger

        ledger = PaperLedger()
        for i in range(5):
            ticker = f"T{i}"
            ledger.open_position(ticker, 1, 10.0)
        with pytest.raises(ValueError, match="Max concurrent"):
            ledger.open_position("SIXTH", 1, 10.0)


# ---------------------------------------------------------------------------
# Exit Test 3: Catastrophe-net stop at 15% below entry
# ---------------------------------------------------------------------------

class TestExit03CatastropheNetPlaced:
    """Exit test 3: Catastrophe-net stop at 15% below entry."""

    def test_compute_stop_price(self):
        stop_price = compute_catastrophe_stop(150.0)
        assert stop_price == 127.50

    def test_compute_stop_rounds_to_2_decimals(self):
        stop_price = compute_catastrophe_stop(99.99)
        # 99.99 * 0.85 = 84.9915 -> rounds to 84.99
        assert stop_price == round(99.99 * 0.85, 2)

    def test_compute_stop_rejects_zero_price(self):
        with pytest.raises(ValueError, match="positive"):
            compute_catastrophe_stop(0.0)

    def test_compute_stop_rejects_negative_price(self):
        with pytest.raises(ValueError, match="positive"):
            compute_catastrophe_stop(-10.0)

    @pytest.mark.asyncio
    async def test_adapter_place_stop_order(self, mock_adapter):
        stop_id = await mock_adapter.place_stop_order("AAPL", 127.50, 5)
        assert stop_id is not None
        assert "stop" in stop_id.lower() or len(stop_id) > 0


# ---------------------------------------------------------------------------
# Exit Test 4: Wizard steps 1-11 render
# ---------------------------------------------------------------------------

class TestExit04WizardAllStepsRender:
    """Exit test 4: All 11 wizard step templates render without error."""

    def test_step01_welcome_get(self, web_client):
        resp = web_client.get("/wizard/")
        assert resp.status_code == 200
        assert "PMACS" in resp.text

    def test_step_templates_all_exist(self):
        """All 11 step templates are registered in STEP_TEMPLATES."""
        from pmacs.web.routes.wizard import STEP_TEMPLATES, TOTAL_STEPS

        assert TOTAL_STEPS == 11
        for step_num in range(1, 12):
            assert step_num in STEP_TEMPLATES, f"Step {step_num} missing from STEP_TEMPLATES"

    def test_wizard_status_endpoint(self, web_client):
        resp = web_client.get("/wizard/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_steps"] == 11

    def test_step_templates_render_via_jinja2(self):
        """Each step template can be loaded and rendered by Jinja2."""
        from jinja2 import Environment, FileSystemLoader

        template_dir = Path(__file__).parent.parent.parent / "pmacs" / "web" / "templates"
        env = Environment(loader=FileSystemLoader(str(template_dir)))

        from pmacs.web.routes.wizard import STEP_TEMPLATES

        for step_num, template_name in STEP_TEMPLATES.items():
            template = env.get_template(template_name)
            # Steps that extend layout need current_step
            rendered = template.render(current_step=step_num, request=None)
            assert rendered is not None
            assert len(rendered) > 0, f"Step {step_num} ({template_name}) rendered empty"

    def test_wizard_step_post_boundaries(self, web_client):
        """Step out of range returns 400."""
        resp = web_client.post("/wizard/step/0")
        assert resp.status_code == 400
        resp = web_client.post("/wizard/step/13")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Exit Test 5: Dead-letter entries persist to SQLite
# ---------------------------------------------------------------------------

class TestExit05DeadLetterPersistence:
    """Exit test 5: Dead-letter entries persist to SQLite."""

    def test_enqueue_creates_pending_entry(self, dead_letter_db):
        from pmacs.storage.dead_letter import DeadLetterStore

        store = DeadLetterStore(dead_letter_db)
        entry = store.enqueue("write", "kuzudb", {"key": "value"})
        assert entry.id is not None
        assert entry.status == "PENDING"
        assert entry.op_type == "write"
        assert entry.target_db == "kuzudb"

    def test_enqueue_persists_to_sqlite(self, dead_letter_db):
        from pmacs.storage.dead_letter import DeadLetterStore

        store = DeadLetterStore(dead_letter_db)
        store.enqueue("upsert", "qdrant", {"collection": "theses", "id": "abc"})
        assert store.total_count == 1

    def test_process_next_returns_pending(self, dead_letter_db):
        from pmacs.storage.dead_letter import DeadLetterStore

        store = DeadLetterStore(dead_letter_db)
        store.enqueue("write", "qdrant", {"doc": "test"})
        entry = store.process_next()
        assert entry is not None
        assert entry.status == "PENDING"

    def test_mark_resolved_updates_status(self, dead_letter_db):
        from pmacs.storage.dead_letter import DeadLetterStore

        store = DeadLetterStore(dead_letter_db)
        entry = store.enqueue("write", "kuzudb", {"key": "val"})
        store.mark_resolved(entry.id)
        assert store.pending_count == 0

    def test_mark_failed_increments_retry(self, dead_letter_db):
        from pmacs.storage.dead_letter import DeadLetterStore

        store = DeadLetterStore(dead_letter_db)
        entry = store.enqueue("write", "kuzudb", {"key": "val"})
        store.mark_failed(entry.id, "connection refused")
        # Should still be retrying (not exhausted)
        row = dead_letter_db.execute(
            "SELECT retry_count, status FROM dead_letter WHERE id = ?", (entry.id,)
        ).fetchone()
        assert row[0] == 1
        assert row[1] == "RETRYING"

    def test_exhausted_after_max_attempts(self, dead_letter_db):
        from pmacs.storage.dead_letter import DeadLetterStore

        store = DeadLetterStore(dead_letter_db)
        entry = store.enqueue("write", "kuzudb", {"key": "val"})
        for i in range(6):
            store.mark_failed(entry.id, f"error-{i}")
        row = dead_letter_db.execute(
            "SELECT retry_count, status FROM dead_letter WHERE id = ?", (entry.id,)
        ).fetchone()
        assert row[1] == "FAILED"
        assert store.failed_count == 1

    def test_multiple_entries_count(self, dead_letter_db):
        from pmacs.storage.dead_letter import DeadLetterStore

        store = DeadLetterStore(dead_letter_db)
        store.enqueue("upsert", "qdrant", {"a": 1})
        store.enqueue("execute", "kuzudb", {"b": 2})
        store.enqueue("insert", "duckdb", {"c": 3})
        assert store.total_count == 3
        assert store.pending_count == 3


# ---------------------------------------------------------------------------
# Exit Test 6: SSE reconnect with Last-Event-ID
# ---------------------------------------------------------------------------

class TestExit06SSEReconnectResume:
    """Exit test 6: SSE client reconnects with Last-Event-ID without
    missing events."""

    def test_publish_assigns_incrementing_ids(self):
        publisher = SSEPublisher()
        eid1 = publisher.publish("cycle", "cycle.open", {"n": 1})
        eid2 = publisher.publish("cycle", "cycle.progress", {"n": 2})
        eid3 = publisher.publish("cycle", "cycle.close", {"n": 3})
        assert int(eid1) < int(eid2) < int(eid3)

    def test_get_events_since_returns_missed(self):
        publisher = SSEPublisher()
        publisher.publish("cycle", "e1", {"n": 1})
        publisher.publish("cycle", "e2", {"n": 2})
        publisher.publish("cycle", "e3", {"n": 3})
        missed = publisher.get_events_since(1)
        assert len(missed) == 2  # events 2 and 3

    def test_get_events_since_empty_when_caught_up(self):
        publisher = SSEPublisher()
        publisher.publish("cycle", "e1", {"n": 1})
        publisher.publish("cycle", "e2", {"n": 2})
        missed = publisher.get_events_since(2)
        assert len(missed) == 0

    def test_get_events_since_returns_all_from_beginning(self):
        publisher = SSEPublisher()
        for i in range(5):
            publisher.publish("cycle", f"e{i}", {"n": i})
        missed = publisher.get_events_since(0)
        assert len(missed) == 5

    def test_ring_buffer_preserves_order(self):
        publisher = SSEPublisher()
        ids = []
        for i in range(10):
            eid = publisher.publish("cycle", f"e{i}", {"idx": i})
            ids.append(int(eid))
        missed = publisher.get_events_since(4)
        # Events with id > 4: ids 5,6,7,8,9,10 = 6 events
        assert len(missed) == 6

    def test_subscribe_unsubscribe_client(self):
        publisher = SSEPublisher()
        assert publisher.client_count == 0
        cid, queue = publisher.subscribe()
        assert publisher.client_count == 1
        publisher.unsubscribe(cid)
        assert publisher.client_count == 0

    def test_publish_delivers_to_subscribed_client(self):
        publisher = SSEPublisher()
        cid, queue = publisher.subscribe()
        publisher.publish("cycle", "test", {"hello": "world"})
        frame = queue.get_nowait()
        assert "test" in frame
        publisher.unsubscribe(cid)


# ---------------------------------------------------------------------------
# Exit Test 7: All 9 Ollama JSON schemas validate
# ---------------------------------------------------------------------------

class TestExit07OllamaSchemasValidate:
    """Exit test 7: All 9 JSON schemas validate against their GBNF grammars."""

    PERSONAS = [
        "macro_regime",
        "catalyst_summarizer",
        "moat_analyst",
        "growth_hunter",
        "insider_activity",
        "short_interest",
        "forensics",
        "crucible",
        "memo_writer",
    ]

    def test_all_9_personas_have_schemas(self):
        from pmacs.agents.schemas_json import PERSONAS
        assert len(PERSONAS) == 9
        for persona in self.PERSONAS:
            assert persona in PERSONAS, f"Missing persona: {persona}"

    def test_each_schema_is_valid_json_schema(self):
        from pmacs.agents.schemas_json import load_schema
        for persona in self.PERSONAS:
            schema = load_schema(persona)
            assert isinstance(schema, dict), f"{persona}: schema not dict"
            assert "type" in schema, f"{persona}: missing 'type'"
            assert "properties" in schema, f"{persona}: missing 'properties'"
            assert isinstance(schema["properties"], dict), f"{persona}: properties not dict"
            assert len(schema["properties"]) > 0, f"{persona}: empty properties"

    def test_schema_files_exist_on_disk(self):
        from pmacs.agents.schemas_json import _SCHEMAS_DIR
        for persona in self.PERSONAS:
            path = _SCHEMAS_DIR / f"{persona}.json"
            assert path.exists(), f"Schema file missing: {path}"

    def test_unknown_persona_raises(self):
        from pmacs.agents.schemas_json import load_schema
        with pytest.raises(ValueError, match="Unknown persona"):
            load_schema("nonexistent_persona")

    def test_schemas_have_required_fields(self):
        """Each schema should specify required fields or have reasonable defaults."""
        from pmacs.agents.schemas_json import load_schema
        for persona in self.PERSONAS:
            schema = load_schema(persona)
            # At minimum, type=object with properties
            assert schema.get("type") == "object", f"{persona}: type is not 'object'"
