"""Tests for Message model, MessageBus, and BusStore."""

from __future__ import annotations

import tempfile
from pathlib import Path

from bus.bus import Message, MessageBus
from bus.store import BusStore


class TestMessage:
    def test_default_fields(self):
        msg = Message(topic="episode.downloaded", payload={"ep": 1}, source_agent="fetcher")
        assert msg.topic == "episode.downloaded"
        assert msg.payload == {"ep": 1}
        assert msg.source_agent == "fetcher"
        assert msg.id  # UUID auto-generated
        assert msg.created_at
        assert msg.parent_id is None

    def test_parent_id(self):
        parent = Message(topic="a", payload={}, source_agent="x")
        child = Message(topic="b", payload={}, source_agent="y", parent_id=parent.id)
        assert child.parent_id == parent.id


class TestMessageBus:
    def test_publish_calls_exact_subscriber(self):
        bus = MessageBus()
        received = []
        bus.subscribe("episode.downloaded", lambda m: received.append(m))
        msg = Message(topic="episode.downloaded", payload={"ep": 1}, source_agent="test")
        bus.publish(msg)
        assert len(received) == 1
        assert received[0].topic == "episode.downloaded"

    def test_wildcard_star_matches_all(self):
        bus = MessageBus()
        received = []
        bus.subscribe("*", lambda m: received.append(m))
        bus.publish(Message(topic="episode.downloaded", payload={}, source_agent="a"))
        bus.publish(Message(topic="blog.ready", payload={}, source_agent="b"))
        assert len(received) == 2

    def test_wildcard_prefix(self):
        bus = MessageBus()
        received = []
        bus.subscribe("episode.*", lambda m: received.append(m))
        bus.publish(Message(topic="episode.downloaded", payload={}, source_agent="a"))
        bus.publish(Message(topic="episode.transcribed", payload={}, source_agent="b"))
        bus.publish(Message(topic="blog.ready", payload={}, source_agent="c"))
        assert len(received) == 2

    def test_no_match(self):
        bus = MessageBus()
        received = []
        bus.subscribe("episode.downloaded", lambda m: received.append(m))
        bus.publish(Message(topic="blog.ready", payload={}, source_agent="x"))
        assert len(received) == 0

    def test_unsubscribe(self):
        bus = MessageBus()
        received = []
        handler = lambda m: received.append(m)
        bus.subscribe("episode.downloaded", handler)
        bus.unsubscribe("episode.downloaded", handler)
        bus.publish(Message(topic="episode.downloaded", payload={}, source_agent="x"))
        assert len(received) == 0

    def test_multiple_subscribers_same_topic(self):
        bus = MessageBus()
        r1, r2 = [], []
        bus.subscribe("episode.downloaded", lambda m: r1.append(m))
        bus.subscribe("episode.downloaded", lambda m: r2.append(m))
        bus.publish(Message(topic="episode.downloaded", payload={}, source_agent="x"))
        assert len(r1) == 1
        assert len(r2) == 1

    def test_get_messages_returns_persisted(self):
        bus = MessageBus()
        bus.publish(Message(topic="ep.dl", payload={"n": 1}, source_agent="a"))
        bus.publish(Message(topic="ep.dl", payload={"n": 2}, source_agent="b"))
        msgs = bus.get_messages("ep.dl")
        assert len(msgs) == 2


class TestBusStore:
    def test_save_and_retrieve(self):
        store = BusStore()
        msg = Message(topic="t", payload={"k": "v"}, source_agent="a")
        store.save(msg)
        result = store.get_by_id(msg.id)
        assert result is not None
        assert result["topic"] == "t"
        assert result["payload"] == {"k": "v"}

    def test_get_by_topic(self):
        store = BusStore()
        store.save(Message(topic="a.b", payload={}, source_agent="x"))
        store.save(Message(topic="a.b", payload={}, source_agent="y"))
        store.save(Message(topic="c.d", payload={}, source_agent="z"))
        assert len(store.get_by_topic("a.b")) == 2
        assert len(store.get_by_topic("c.d")) == 1

    def test_mark_processed(self):
        store = BusStore()
        msg = Message(topic="t", payload={}, source_agent="a")
        store.save(msg)
        store.mark_processed(msg.id, "processor")
        result = store.get_by_id(msg.id)
        assert result["processed_by"] == "processor"
        assert result["processed_at"] is not None

    def test_get_trace(self):
        store = BusStore()
        root = Message(topic="a", payload={}, source_agent="x")
        child = Message(topic="b", payload={}, source_agent="y", parent_id=root.id)
        grandchild = Message(topic="c", payload={}, source_agent="z", parent_id=child.id)
        store.save(root)
        store.save(child)
        store.save(grandchild)
        trace = store.get_trace(root.id)
        assert len(trace) == 3

    def test_sqlite_file_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = BusStore(db_path)
            msg = Message(topic="t", payload={"x": 1}, source_agent="a")
            store.save(msg)
            store.close()
            # Reopen and verify
            store2 = BusStore(db_path)
            result = store2.get_by_id(msg.id)
            assert result is not None
            assert result["payload"] == {"x": 1}
            store2.close()
