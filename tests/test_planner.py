"""Tests for the auto-wiring planner agent."""

from __future__ import annotations

from pathlib import Path

from bus.agent import BaseAgent
from bus.bus import Message, MessageBus
from bus.cli import _load_agents_from_dir
from bus.coordinator import Coordinator
from bus.planner import (
    ExecutionPlan,
    PlannerAgent,
    create_plan,
    describe_graph,
    rule_based_planner,
    _find_entry_topics,
    _find_terminal_topics,
    _trace_agent_chain,
)
from bus.registry import AgentCapability, AgentRegistry

AGENTS_DIR = Path(__file__).parent.parent / "agents"


def _p3_registry() -> AgentRegistry:
    """Build a registry from P3 agents."""
    agents = _load_agents_from_dir(AGENTS_DIR)
    registry = AgentRegistry()
    for a in agents:
        registry.register(a.capability)
    return registry


class TestDescribeGraph:
    def test_includes_agent_names(self):
        registry = _p3_registry()
        desc = describe_graph(registry)
        assert "p3_fetcher" in desc
        assert "p3_transcriber" in desc
        assert "p3_writer" in desc

    def test_includes_topics(self):
        registry = _p3_registry()
        desc = describe_graph(registry)
        assert "episode.downloaded" in desc
        assert "episode.transcribed" in desc

    def test_includes_pipeline_chains(self):
        registry = _p3_registry()
        desc = describe_graph(registry)
        assert "Pipeline chains:" in desc


class TestFindTopics:
    def test_entry_topics(self):
        registry = _p3_registry()
        entries = _find_entry_topics(registry)
        assert "episode.requested" in entries

    def test_terminal_topics(self):
        registry = _p3_registry()
        terminals = _find_terminal_topics(registry)
        assert "blog.draft_ready" in terminals
        assert "topics.extracted" in terminals

    def test_entry_not_in_terminal(self):
        registry = _p3_registry()
        entries = _find_entry_topics(registry)
        terminals = _find_terminal_topics(registry)
        assert entries.isdisjoint(terminals)


class TestTraceAgentChain:
    def test_full_chain(self):
        registry = _p3_registry()
        chain = _trace_agent_chain(registry, "episode.requested")
        assert "p3_fetcher" in chain
        assert "p3_transcriber" in chain
        assert "p3_digester" in chain
        assert "p3_writer" in chain
        assert "topic_extractor" in chain

    def test_chain_order(self):
        registry = _p3_registry()
        chain = _trace_agent_chain(registry, "episode.requested")
        assert chain.index("p3_fetcher") < chain.index("p3_transcriber")
        assert chain.index("p3_transcriber") < chain.index("p3_digester")


class TestRuleBasedPlanner:
    def test_episode_goal(self):
        registry = _p3_registry()
        planner = rule_based_planner(registry)
        topic, payload = planner("turn podcast episode 42 into a blog post", "")
        assert topic == "episode.requested"

    def test_extracts_episode_id(self):
        registry = _p3_registry()
        planner = rule_based_planner(registry)
        _, payload = planner("process episode 42", "")
        assert payload.get("episode_id") == 42

    def test_no_number_in_goal(self):
        registry = _p3_registry()
        planner = rule_based_planner(registry)
        topic, payload = planner("fetch the latest podcast", "")
        assert topic == "episode.requested"
        assert "episode_id" not in payload


class TestCreatePlan:
    def test_plan_for_podcast(self):
        registry = _p3_registry()
        plan = create_plan("turn podcast episode 7 into a blog post", registry)
        assert isinstance(plan, ExecutionPlan)
        assert plan.entry_topic == "episode.requested"
        assert plan.confidence in ("high", "medium")
        assert len(plan.agent_chain) >= 3

    def test_plan_with_custom_planner(self):
        registry = _p3_registry()

        def custom_planner(goal: str, desc: str) -> tuple[str, dict]:
            return "episode.requested", {"episode_id": 99}

        plan = create_plan("anything", registry, planner_fn=custom_planner)
        assert plan.entry_topic == "episode.requested"
        assert plan.payload == {"episode_id": 99}

    def test_plan_unknown_topic(self):
        registry = _p3_registry()

        def bad_planner(goal: str, desc: str) -> tuple[str, dict]:
            return "nonexistent.topic", {}

        plan = create_plan("something impossible", registry, planner_fn=bad_planner)
        assert plan.confidence == "low"
        assert len(plan.missing_capabilities) > 0

    def test_plan_includes_reasoning(self):
        registry = _p3_registry()
        plan = create_plan("process episode 1", registry)
        assert plan.reasoning
        assert "entry topic" in plan.reasoning


class SinkAgent(BaseAgent):
    def __init__(self, cap: AgentCapability):
        super().__init__(cap)
        self.received: list[Message] = []

    def handle(self, message: Message) -> None:
        self.received.append(message)
        return None


class TestPlannerAgent:
    def _setup(self) -> tuple[Coordinator, PlannerAgent, SinkAgent, SinkAgent, SinkAgent]:
        bus = MessageBus()
        p3_agents = _load_agents_from_dir(AGENTS_DIR)
        registry = AgentRegistry()
        for a in p3_agents:
            registry.register(a.capability)

        # Sinks to capture planner output
        plan_sink = SinkAgent(AgentCapability(name="plan_sink", consumes=["goal.planned"], produces=[]))
        done_sink = SinkAgent(AgentCapability(name="done_sink", consumes=["goal.completed"], produces=[]))
        fail_sink = SinkAgent(AgentCapability(name="fail_sink", consumes=["goal.failed"], produces=[]))

        all_agents = p3_agents + [plan_sink, done_sink, fail_sink]
        coord = Coordinator(bus, agents=all_agents, idle_timeout=1.5)

        planner = PlannerAgent(registry, coord)
        all_agents.append(planner)
        planner.start(bus)

        coord.start_agents()
        return coord, planner, plan_sink, done_sink, fail_sink

    def test_goal_produces_plan_and_completion(self):
        coord, planner, plan_sink, done_sink, fail_sink = self._setup()

        msg = Message(
            topic="goal.requested",
            payload={"goal": "turn podcast episode 1 into a blog post"},
            source_agent="test",
        )
        bus = coord._bus
        bus.publish(msg)

        # Wait for pipeline to complete
        import time
        time.sleep(3)

        assert len(plan_sink.received) >= 1
        plan_payload = plan_sink.received[0].payload
        assert plan_payload["entry_topic"] == "episode.requested"
        assert "p3_fetcher" in plan_payload["agent_chain"]

        # Should have either completed or failed
        total_outcomes = len(done_sink.received) + len(fail_sink.received)
        assert total_outcomes >= 1

        coord.stop_agents()
        planner.stop()

    def test_empty_goal_fails(self):
        coord, planner, plan_sink, done_sink, fail_sink = self._setup()

        msg = Message(
            topic="goal.requested",
            payload={"goal": ""},
            source_agent="test",
        )
        coord._bus.publish(msg)

        import time
        time.sleep(2)

        assert len(fail_sink.received) >= 1
        assert "No goal provided" in fail_sink.received[0].payload.get("error", "")

        coord.stop_agents()
        planner.stop()

    def test_plan_includes_confidence(self):
        coord, planner, plan_sink, done_sink, fail_sink = self._setup()

        msg = Message(
            topic="goal.requested",
            payload={"goal": "process episode 5"},
            source_agent="test",
        )
        coord._bus.publish(msg)

        import time
        time.sleep(3)

        assert len(plan_sink.received) >= 1
        assert plan_sink.received[0].payload["confidence"] in ("high", "medium", "low")

        coord.stop_agents()
        planner.stop()


class TestMissingCapabilities:
    def test_detect_gap_in_sparse_registry(self):
        registry = AgentRegistry()
        # Only register fetcher — no downstream agents
        registry.register(AgentCapability(
            name="fetcher",
            consumes=["episode.requested"],
            produces=["episode.downloaded"],
        ))

        plan = create_plan("process episode 1", registry)
        # Should detect orphaned topic
        assert len(plan.missing_capabilities) > 0 or plan.confidence != "high"

    def test_empty_registry(self):
        registry = AgentRegistry()
        plan = create_plan("do anything", registry)
        assert plan.confidence == "low"
