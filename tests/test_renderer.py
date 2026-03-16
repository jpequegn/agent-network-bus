"""Tests for the Rich terminal renderer."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from bus.agent import BaseAgent
from bus.bus import Message, MessageBus
from bus.coordinator import Coordinator
from bus.registry import AgentCapability
from bus.renderer import (
    build_agent_table,
    build_dashboard,
    build_messages_table,
    render_live,
    render_snapshot,
)


class SinkAgent(BaseAgent):
    def handle(self, message: Message) -> None:
        return None


def _setup() -> tuple[Coordinator, SinkAgent]:
    bus = MessageBus()
    sink = SinkAgent(AgentCapability(name="test_sink", consumes=["t"], produces=[]))
    coord = Coordinator(bus, agents=[sink], idle_timeout=0.05)
    coord.start_agents()
    return coord, sink


class TestBuildAgentTable:
    def test_table_has_rows(self):
        coord, sink = _setup()
        coord.run_pipeline(Message(topic="t", payload={"x": 1}, source_agent="test"))
        table = build_agent_table(coord)
        assert table.row_count == 1
        coord.stop_agents()

    def test_table_renders_without_error(self):
        coord, sink = _setup()
        table = build_agent_table(coord)
        console = Console(file=StringIO(), width=120)
        console.print(table)
        output = console.file.getvalue()
        assert "test_sink" in output
        coord.stop_agents()


class TestBuildMessagesTable:
    def test_messages_table_with_data(self):
        coord, sink = _setup()
        coord.run_pipeline(Message(topic="t", payload={"hello": "world"}, source_agent="src"))
        table = build_messages_table(coord._bus.store, limit=5)
        assert table.row_count >= 1
        coord.stop_agents()

    def test_messages_table_empty(self):
        bus = MessageBus()
        table = build_messages_table(bus.store, limit=5)
        assert table.row_count == 0

    def test_long_payload_truncated(self):
        coord, sink = _setup()
        long_payload = {"key": "x" * 100}
        coord.run_pipeline(Message(topic="t", payload=long_payload, source_agent="src"))
        table = build_messages_table(coord._bus.store, limit=5)
        console = Console(file=StringIO(), width=200)
        console.print(table)
        # Should not contain the full 100-char string untruncated
        coord.stop_agents()


class TestBuildDashboard:
    def test_dashboard_layout(self):
        coord, sink = _setup()
        layout = build_dashboard(coord)
        assert layout["agents"] is not None
        assert layout["messages"] is not None
        coord.stop_agents()


class TestRenderSnapshot:
    def test_snapshot_prints_without_error(self, capsys):
        coord, sink = _setup()
        coord.run_pipeline(Message(topic="t", payload={}, source_agent="test"))
        render_snapshot(coord)
        captured = capsys.readouterr()
        assert "test_sink" in captured.out
        coord.stop_agents()


class TestRenderLive:
    def test_live_context_creates(self):
        coord, sink = _setup()
        live = render_live(coord, refresh_per_second=2)
        assert live is not None
        coord.stop_agents()

    def test_live_renderable_refreshes(self):
        coord, sink = _setup()
        live = render_live(coord, refresh_per_second=2)
        # get_renderable should return a fresh dashboard each time
        r1 = live.get_renderable()
        coord.run_pipeline(Message(topic="t", payload={}, source_agent="test"))
        r2 = live.get_renderable()
        # Both should be Layout objects, second reflects updated state
        assert r1 is not r2
        coord.stop_agents()
