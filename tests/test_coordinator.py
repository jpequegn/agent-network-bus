"""Tests for Coordinator pipeline execution."""

from __future__ import annotations

from bus.agent import BaseAgent
from bus.bus import Message, MessageBus
from bus.coordinator import Coordinator, NetworkStatus, PipelineResult
from bus.registry import AgentCapability


class UpperAgent(BaseAgent):
    """Transforms payload text to uppercase and publishes to next topic."""

    def handle(self, message: Message) -> Message:
        text = message.payload.get("text", "")
        return Message(topic="text.uppered", payload={"text": text.upper()})


class SinkAgent(BaseAgent):
    def __init__(self, capability: AgentCapability) -> None:
        super().__init__(capability)
        self.received: list[Message] = []

    def handle(self, message: Message) -> None:
        self.received.append(message)
        return None


class StepA(BaseAgent):
    def handle(self, message: Message) -> Message:
        n = message.payload["n"]
        return Message(topic="step.b", payload={"n": n + 1})


class StepB(BaseAgent):
    def handle(self, message: Message) -> Message:
        n = message.payload["n"]
        return Message(topic="step.done", payload={"n": n + 1})


class ErrorAgent(BaseAgent):
    def handle(self, message: Message) -> None:
        raise ValueError("boom")


class TestCoordinator:
    def _make_pipeline(self) -> tuple[MessageBus, Coordinator, SinkAgent]:
        bus = MessageBus()
        upper = UpperAgent(AgentCapability(name="upper", consumes=["text.raw"], produces=["text.uppered"]))
        sink = SinkAgent(AgentCapability(name="sink", consumes=["text.uppered"], produces=[]))
        coord = Coordinator(bus, agents=[upper, sink], idle_timeout=0.05)
        coord.start_agents()
        return bus, coord, sink

    def test_run_pipeline(self):
        bus, coord, sink = self._make_pipeline()
        msg = Message(topic="text.raw", payload={"text": "hello"}, source_agent="test")
        result = coord.run_pipeline(msg)

        assert result.success
        assert len(result.trace) >= 2  # root + at least one child
        assert result.duration_ms >= 0
        assert len(sink.received) == 1
        assert sink.received[0].payload == {"text": "HELLO"}
        coord.stop_agents()

    def test_get_trace(self):
        bus, coord, sink = self._make_pipeline()
        msg = Message(topic="text.raw", payload={"text": "hi"}, source_agent="test")
        result = coord.run_pipeline(msg)

        trace = coord.get_trace(msg.id)
        assert len(trace) >= 2
        topics = [m["topic"] for m in trace]
        assert "text.raw" in topics
        assert "text.uppered" in topics
        coord.stop_agents()

    def test_multi_step_pipeline(self):
        bus = MessageBus()
        a = StepA(AgentCapability(name="step_a", consumes=["step.a"], produces=["step.b"]))
        b = StepB(AgentCapability(name="step_b", consumes=["step.b"], produces=["step.done"]))
        sink = SinkAgent(AgentCapability(name="sink", consumes=["step.done"], produces=[]))

        coord = Coordinator(bus, agents=[a, b, sink], idle_timeout=0.05)
        coord.start_agents()

        msg = Message(topic="step.a", payload={"n": 0}, source_agent="test")
        result = coord.run_pipeline(msg)

        assert result.success
        assert len(sink.received) == 1
        assert sink.received[0].payload == {"n": 2}  # 0 -> 1 -> 2

        trace = coord.get_trace(msg.id)
        assert len(trace) == 3  # step.a -> step.b -> step.done
        coord.stop_agents()

    def test_fan_out(self):
        bus = MessageBus()
        upper = UpperAgent(AgentCapability(name="upper", consumes=["text.raw"], produces=["text.uppered"]))
        sink = SinkAgent(AgentCapability(name="sink", consumes=["text.uppered"], produces=[]))
        coord = Coordinator(bus, agents=[upper, sink], idle_timeout=0.05)
        coord.start_agents()

        messages = [
            Message(topic="text.raw", payload={"text": f"msg{i}"}, source_agent="test")
            for i in range(3)
        ]
        results = coord.fan_out(messages)

        assert len(results) == 3
        assert all(r.success for r in results)
        assert len(sink.received) == 3
        coord.stop_agents()

    def test_status(self):
        bus, coord, sink = self._make_pipeline()
        msg = Message(topic="text.raw", payload={"text": "hi"}, source_agent="test")
        coord.run_pipeline(msg)

        status = coord.status()
        assert isinstance(status, NetworkStatus)
        assert len(status.agents) == 2
        assert status.total_processed >= 2  # upper + sink
        assert status.total_errors == 0
        coord.stop_agents()

    def test_pipeline_result_duration(self):
        bus, coord, sink = self._make_pipeline()
        msg = Message(topic="text.raw", payload={"text": "hi"}, source_agent="test")
        result = coord.run_pipeline(msg)
        assert result.duration_ms >= 0
        coord.stop_agents()

    def test_error_in_pipeline(self):
        bus = MessageBus()
        err = ErrorAgent(AgentCapability(name="err", consumes=["t"], produces=[]))
        coord = Coordinator(bus, agents=[err], idle_timeout=0.05)
        coord.start_agents()

        msg = Message(topic="t", payload={}, source_agent="test")
        result = coord.run_pipeline(msg)

        # Pipeline still completes (error is isolated to the agent)
        assert result.success
        status = coord.status()
        assert status.total_errors == 1
        coord.stop_agents()

    def test_start_stop_agents(self):
        bus = MessageBus()
        sink = SinkAgent(AgentCapability(name="sink", consumes=["t"], produces=[]))
        coord = Coordinator(bus, agents=[sink], idle_timeout=0.05)
        coord.start_agents()
        coord.stop_agents()

        # After stop, messages shouldn't be received
        bus.publish(Message(topic="t", payload={}, source_agent="test"))
        assert len(sink.received) == 0
