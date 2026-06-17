"""Tests for SSE publisher ring buffer and Last-Event-ID resume."""
from __future__ import annotations

import json

import pytest

from pmacs.nervous.sse_publisher import SSEPublisher


class TestRingBuffer:
    def test_publish_stores_events_in_ring_buffer(self):
        pub = SSEPublisher()
        pub.publish("cycle", "cycle.open", {"cycle_id": "c1"})
        pub.publish("cycle", "cycle.close", {"cycle_id": "c1"})

        assert len(pub._event_log) == 2
        eid1, frame1 = pub._event_log[0]
        eid2, frame2 = pub._event_log[1]
        assert eid2 > eid1
        event1 = json.loads(frame1)
        assert event1["type"] == "cycle.open"

    def test_ring_buffer_max_size(self):
        pub = SSEPublisher()
        pub.RING_BUFFER_SIZE = 5
        for i in range(10):
            pub.publish("system", "test", {"seq": i})

        assert len(pub._event_log) == 5
        # Should contain last 5 events (seq 5..9)
        first_eid, first_frame = pub._event_log[0]
        event = json.loads(first_frame)
        assert event["data"]["seq"] == 5

    def test_get_events_since_returns_correct_subset(self):
        pub = SSEPublisher()
        id1 = pub.publish("cycle", "e1", {"n": 1})
        id2 = pub.publish("cycle", "e2", {"n": 2})
        id3 = pub.publish("cycle", "e3", {"n": 3})

        # Get events after id1
        frames = pub.get_events_since(int(id1))
        assert len(frames) == 2

        # Get events after id2
        frames = pub.get_events_since(int(id2))
        assert len(frames) == 1
        event = json.loads(frames[0])
        assert event["type"] == "e3"

    def test_get_events_since_returns_empty_for_latest(self):
        pub = SSEPublisher()
        id1 = pub.publish("cycle", "e1", {"n": 1})

        frames = pub.get_events_since(int(id1))
        assert frames == []

    def test_get_events_since_returns_all_for_zero(self):
        pub = SSEPublisher()
        pub.publish("cycle", "e1", {"n": 1})
        pub.publish("cycle", "e2", {"n": 2})

        frames = pub.get_events_since(0)
        assert len(frames) == 2

    def test_get_events_since_handles_pruned_buffer(self):
        """If the ring buffer has pruned old events, returns only what's available."""
        pub = SSEPublisher()
        pub.RING_BUFFER_SIZE = 3
        id1 = pub.publish("cycle", "e1", {"n": 1})
        id2 = pub.publish("cycle", "e2", {"n": 2})
        pub.publish("cycle", "e3", {"n": 3})
        pub.publish("cycle", "e4", {"n": 4})

        # id1 has been pruned from the buffer
        frames = pub.get_events_since(int(id1))
        # Should return events 2, 3, 4 even though 1 is gone
        assert len(frames) == 3


class TestLastEventIDHeader:
    """Test that the SSE endpoint replays events when Last-Event-ID is present.

    These test the publisher's get_events_since method which the API endpoint
    calls. Full API integration tests would require an async test client.
    """

    def test_replay_preserves_order(self):
        pub = SSEPublisher()
        pub.publish("cycle", "e1", {"seq": 1})
        id2 = pub.publish("cycle", "e2", {"seq": 2})
        pub.publish("cycle", "e3", {"seq": 3})

        frames = pub.get_events_since(int(id2))
        assert len(frames) == 1
        event = json.loads(frames[0])
        assert event["data"]["seq"] == 3

    def test_replay_empty_when_no_missed_events(self):
        pub = SSEPublisher()
        last_id = pub.publish("cycle", "e1", {"seq": 1})

        frames = pub.get_events_since(int(last_id))
        assert frames == []

    def test_subscribe_unsubscribe_lifecycle(self):
        pub = SSEPublisher()
        assert pub.client_count == 0
        cid, queue = pub.subscribe()
        assert pub.client_count == 1
        pub.unsubscribe(cid)
        assert pub.client_count == 0

    def test_last_event_id_property(self):
        pub = SSEPublisher()
        # IDs are seeded from a ms timestamp; track increments from baseline.
        base = pub.last_event_id
        pub.publish("cycle", "e1", {"n": 1})
        pub.publish("cycle", "e2", {"n": 2})
        assert pub.last_event_id == base + 2
