"""Tests for BaseAgent lifecycle and threading."""

from __future__ import annotations

import threading
import time

from bus.agent import BaseAgent
from bus.bus import Message, MessageBus
from bus.registry import AgentCapability


class EchoAgent(BaseAgent):
    """Test agent that echoes messages to a different topic."""

    def handle(self, message: Message) -> Message:
        return Message(
            topic="echo.out",
            payload={"echoed": message.payload},
        )


class SinkAgent(BaseAgent):
    """Test agent that consumes without producing."""

    def __init__(self, capability: AgentCapability) -> None:
        super().__init__(capability)
        self.received: list[Message] = []

    def handle(self, message: Message) -> None:
        self.received.append(message)
        return None


class ErrorAgent(BaseAgent):
    """Test agent that always raises."""

    def handle(self, message: Message) -> None:
        raise ValueError("intentional error")


class MultiAgent(BaseAgent):
    """Test agent that returns multiple messages."""

    def handle(self, message: Message) -> list[Message]:
        return [
            Message(topic="multi.a", payload={"n": 1}),
            Message(topic="multi.b", payload={"n": 2}),
        ]


class TestBaseAgent:
    def test_handle_not_implemented(self):
        agent = BaseAgent(AgentCapability(name="base", consumes=["t"], produces=[]))
        try:
            agent.handle(Message(topic="t", payload={}, source_agent="x"))
            assert False, "Should have raised"
        except NotImplementedError:
            pass

    def test_start_and_stop(self):
        bus = MessageBus()
        cap = AgentCapability(name="sink", consumes=["t"], produces=[])
        agent = SinkAgent(cap)
        agent.start(bus)
        assert agent._running.is_set()
        assert agent._thread is not None
        agent.stop()
        assert agent.status == "idle"

    def test_message_processing(self):
        bus = MessageBus()
        cap = AgentCapability(name="sink", consumes=["input"], produces=[])
        agent = SinkAgent(cap)
        agent.start(bus)

        bus.publish(Message(topic="input", payload={"x": 1}, source_agent="test"))
        assert len(agent.received) == 1
        assert agent.received[0].payload == {"x": 1}
        assert agent.processed_count == 1
        assert agent.status == "idle"
        agent.stop()

    def test_echo_agent_publishes_result(self):
        bus = MessageBus()
        echo_cap = AgentCapability(name="echo", consumes=["echo.in"], produces=["echo.out"])
        echo = EchoAgent(echo_cap)

        sink_cap = AgentCapability(name="sink", consumes=["echo.out"], produces=[])
        sink = SinkAgent(sink_cap)

        echo.start(bus)
        sink.start(bus)

        bus.publish(Message(topic="echo.in", payload={"msg": "hello"}, source_agent="test"))

        assert echo.processed_count == 1
        assert len(sink.received) == 1
        assert sink.received[0].payload == {"echoed": {"msg": "hello"}}
        assert sink.received[0].source_agent == "echo"

        echo.stop()
        sink.stop()

    def test_parent_id_tracing(self):
        bus = MessageBus()
        echo = EchoAgent(AgentCapability(name="echo", consumes=["echo.in"], produces=["echo.out"]))
        sink = SinkAgent(AgentCapability(name="sink", consumes=["echo.out"], produces=[]))
        echo.start(bus)
        sink.start(bus)

        original = Message(topic="echo.in", payload={}, source_agent="test")
        bus.publish(original)

        assert sink.received[0].parent_id == original.id
        echo.stop()
        sink.stop()

    def test_error_handling(self):
        bus = MessageBus()
        agent = ErrorAgent(AgentCapability(name="err", consumes=["t"], produces=[]))
        agent.start(bus)

        bus.publish(Message(topic="t", payload={}, source_agent="test"))
        assert agent.error_count == 1
        assert agent.processed_count == 0
        assert agent.status == "error"
        agent.stop()

    def test_health_check(self):
        agent = SinkAgent(AgentCapability(name="sink", consumes=["t"], produces=[]))
        health = agent.health_check()
        assert health["name"] == "sink"
        assert health["status"] == "idle"
        assert health["processed_count"] == 0
        assert health["error_count"] == 0
        assert health["last_processed_at"] is None

    def test_health_check_after_processing(self):
        bus = MessageBus()
        agent = SinkAgent(AgentCapability(name="sink", consumes=["t"], produces=[]))
        agent.start(bus)
        bus.publish(Message(topic="t", payload={}, source_agent="x"))
        health = agent.health_check()
        assert health["processed_count"] == 1
        assert health["last_processed_at"] is not None
        agent.stop()

    def test_multi_output_agent(self):
        bus = MessageBus()
        multi = MultiAgent(AgentCapability(name="multi", consumes=["in"], produces=["multi.a", "multi.b"]))
        sink_a = SinkAgent(AgentCapability(name="sa", consumes=["multi.a"], produces=[]))
        sink_b = SinkAgent(AgentCapability(name="sb", consumes=["multi.b"], produces=[]))

        multi.start(bus)
        sink_a.start(bus)
        sink_b.start(bus)

        bus.publish(Message(topic="in", payload={}, source_agent="test"))

        assert multi.processed_count == 1
        assert len(sink_a.received) == 1
        assert len(sink_b.received) == 1
        assert sink_a.received[0].payload == {"n": 1}
        assert sink_b.received[0].payload == {"n": 2}

        multi.stop()
        sink_a.stop()
        sink_b.stop()

    def test_unsubscribe_on_stop(self):
        bus = MessageBus()
        agent = SinkAgent(AgentCapability(name="sink", consumes=["t"], produces=[]))
        agent.start(bus)
        agent.stop()

        bus.publish(Message(topic="t", payload={}, source_agent="test"))
        assert len(agent.received) == 0

    def test_mark_processed_in_store(self):
        bus = MessageBus()
        agent = SinkAgent(AgentCapability(name="sink", consumes=["t"], produces=[]))
        agent.start(bus)

        msg = Message(topic="t", payload={}, source_agent="test")
        bus.publish(msg)

        stored = bus.store.get_by_id(msg.id)
        assert stored["processed_by"] == "sink"
        assert stored["processed_at"] is not None
        agent.stop()
