"""Tests for the self-evolving network agent."""

from __future__ import annotations

import tempfile
from pathlib import Path

from bus.agent import BaseAgent
from bus.bus import Message, MessageBus
from bus.cli import _load_agents_from_dir
from bus.coordinator import Coordinator
from bus.evolution import (
    EvolutionAgent,
    EvolutionResult,
    GapAnalysis,
    analyze_gaps,
    analyze_missing_from_failure,
    evolve,
    generate_agent_code,
    _topic_to_agent_name,
    _snake_to_class,
)
from bus.registry import AgentCapability, AgentRegistry

AGENTS_DIR = Path(__file__).parent.parent / "agents"


def _sparse_registry() -> AgentRegistry:
    """Registry with deliberate gaps."""
    reg = AgentRegistry()
    reg.register(AgentCapability(
        name="fetcher",
        consumes=["episode.requested"],
        produces=["episode.downloaded"],
    ))
    # No one consumes episode.downloaded → orphaned
    return reg


def _dead_end_registry() -> AgentRegistry:
    """Registry with a dead end."""
    reg = AgentRegistry()
    reg.register(AgentCapability(
        name="writer",
        consumes=["episode.digested"],
        produces=["blog.ready"],
    ))
    # No one produces episode.digested → dead end
    return reg


class TestNaming:
    def test_topic_to_agent_name(self):
        assert _topic_to_agent_name("blog.draft_ready", "consumer") == "blog_draft_ready_consumer"
        assert _topic_to_agent_name("episode.downloaded", "producer") == "episode_downloaded_producer"

    def test_snake_to_class(self):
        assert _snake_to_class("blog_draft_ready_consumer") == "BlogDraftReadyConsumerAgent"
        assert _snake_to_class("episode_downloaded_producer") == "EpisodeDownloadedProducerAgent"


class TestAnalyzeGaps:
    def test_orphaned_topic(self):
        reg = _sparse_registry()
        gaps = analyze_gaps(reg)
        orphans = [g for g in gaps if g.gap_type == "orphaned"]
        assert len(orphans) == 1
        assert orphans[0].topic == "episode.downloaded"

    def test_dead_end(self):
        reg = _dead_end_registry()
        gaps = analyze_gaps(reg)
        orphans = [g for g in gaps if g.gap_type == "orphaned"]
        dead_ends = [g for g in gaps if g.gap_type == "dead_end"]
        assert len(dead_ends) == 1
        assert dead_ends[0].topic == "episode.digested"

    def test_no_gaps_in_full_pipeline(self):
        reg = AgentRegistry()
        reg.register(AgentCapability(name="a", consumes=["t1"], produces=["t2"]))
        reg.register(AgentCapability(name="b", consumes=["t2"], produces=[]))
        # t1 is a dead end (consumed, never produced) — entry point
        gaps = analyze_gaps(reg)
        dead_ends = [g for g in gaps if g.gap_type == "dead_end"]
        assert len(dead_ends) == 1  # t1 is entry point, expected


class TestAnalyzeMissingFromFailure:
    def test_orphaned_from_failure_payload(self):
        payload = {
            "goal": "do something",
            "missing_capabilities": ["orphaned topic: 'blog.draft_ready' is produced but never consumed"],
        }
        gaps = analyze_missing_from_failure(payload, AgentRegistry())
        assert len(gaps) == 1
        assert gaps[0].gap_type == "orphaned"
        assert gaps[0].topic == "blog.draft_ready"

    def test_dead_end_from_failure_payload(self):
        payload = {
            "missing_capabilities": ["dead end: 'episode.requested' is consumed but never produced"],
        }
        gaps = analyze_missing_from_failure(payload, AgentRegistry())
        assert len(gaps) == 1
        assert gaps[0].gap_type == "dead_end"
        assert gaps[0].topic == "episode.requested"

    def test_no_agent_from_failure_payload(self):
        payload = {
            "missing_capabilities": ["No agent consumes or produces 'mystery.topic'"],
        }
        gaps = analyze_missing_from_failure(payload, AgentRegistry())
        assert len(gaps) == 1
        assert gaps[0].gap_type == "missing_link"
        assert gaps[0].topic == "mystery.topic"

    def test_empty_missing(self):
        gaps = analyze_missing_from_failure({"goal": "x"}, AgentRegistry())
        assert len(gaps) == 0


class TestGenerateAgentCode:
    def test_consumer_code_valid_python(self):
        gap = GapAnalysis(
            gap_type="orphaned",
            topic="blog.draft_ready",
            description="orphaned topic",
            suggested_agent_name="blog_draft_ready_consumer",
            suggested_consumes=["blog.draft_ready"],
            suggested_produces=[],
        )
        code = generate_agent_code(gap)
        assert "class BlogDraftReadyConsumerAgent(BaseAgent):" in code
        assert 'consumes=["blog.draft_ready"]' in code or "consumes=['blog.draft_ready']" in code
        assert "def handle(self, message: Message)" in code
        assert "def __init__(self)" in code
        # Verify it's valid Python
        compile(code, "<test>", "exec")

    def test_producer_code_valid_python(self):
        gap = GapAnalysis(
            gap_type="dead_end",
            topic="episode.requested",
            description="dead end",
            suggested_agent_name="episode_requested_producer",
            suggested_consumes=[],
            suggested_produces=["episode.requested"],
        )
        code = generate_agent_code(gap)
        assert "class EpisodeRequestedProducerAgent(BaseAgent):" in code
        assert "episode.requested" in code
        compile(code, "<test>", "exec")

    def test_passthrough_code_valid_python(self):
        gap = GapAnalysis(
            gap_type="missing_link",
            topic="mystery.topic",
            description="missing link",
            suggested_agent_name="mystery_topic_handler",
            suggested_consumes=["mystery.topic"],
            suggested_produces=["mystery.topic.processed"],
        )
        code = generate_agent_code(gap)
        assert "class MysteryTopicHandlerAgent(BaseAgent):" in code
        compile(code, "<test>", "exec")


class TestEvolve:
    def test_dry_run_does_not_write(self):
        gap = GapAnalysis(
            gap_type="orphaned",
            topic="test.topic",
            description="test",
            suggested_agent_name="test_topic_consumer",
            suggested_consumes=["test.topic"],
            suggested_produces=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = evolve(gap, Path(tmpdir), dry_run=True)
            assert not result.written
            assert result.file_path is None
            assert result.code
            assert not (Path(tmpdir) / "test_topic_consumer.py").exists()

    def test_writes_file(self):
        gap = GapAnalysis(
            gap_type="orphaned",
            topic="test.topic",
            description="test",
            suggested_agent_name="test_topic_consumer",
            suggested_consumes=["test.topic"],
            suggested_produces=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = evolve(gap, Path(tmpdir))
            assert result.written
            assert result.file_path is not None
            assert result.file_path.exists()
            content = result.file_path.read_text()
            assert "class TestTopicConsumerAgent" in content

    def test_does_not_overwrite(self):
        gap = GapAnalysis(
            gap_type="orphaned",
            topic="test.topic",
            description="test",
            suggested_agent_name="test_topic_consumer",
            suggested_consumes=["test.topic"],
            suggested_produces=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write first time
            evolve(gap, Path(tmpdir))
            # Second time should not overwrite
            result2 = evolve(gap, Path(tmpdir))
            assert not result2.written

    def test_custom_generator(self):
        gap = GapAnalysis(
            gap_type="orphaned",
            topic="x",
            description="test",
            suggested_agent_name="x_consumer",
            suggested_consumes=["x"],
            suggested_produces=[],
        )

        def custom_gen(g: GapAnalysis, ctx: str) -> str:
            return "# custom generated\n"

        with tempfile.TemporaryDirectory() as tmpdir:
            result = evolve(gap, Path(tmpdir), generator_fn=custom_gen)
            assert result.code == "# custom generated\n"
            assert result.written

    def test_generated_agent_loads_and_runs(self):
        """The most important test: generated code actually works as a bus agent."""
        gap = GapAnalysis(
            gap_type="orphaned",
            topic="test.output",
            description="test",
            suggested_agent_name="test_output_consumer",
            suggested_consumes=["test.output"],
            suggested_produces=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            # Write an __init__.py so it's a package-like dir
            result = evolve(gap, tmppath)
            assert result.written

            # Load the generated agent
            agents = _load_agents_from_dir(tmppath)
            assert len(agents) == 1
            assert agents[0].name == "test_output_consumer"

            # Run it on the bus
            bus = MessageBus()
            agents[0].start(bus)
            bus.publish(Message(topic="test.output", payload={"x": 1}, source_agent="test"))
            assert agents[0].processed_count == 1
            agents[0].stop()


class SinkAgent(BaseAgent):
    def __init__(self, cap: AgentCapability):
        super().__init__(cap)
        self.received: list[Message] = []

    def handle(self, message: Message) -> None:
        self.received.append(message)
        return None


class TestEvolutionAgent:
    def test_handles_goal_failed(self):
        reg = _sparse_registry()
        bus = MessageBus()
        evolved_sink = SinkAgent(AgentCapability(name="esink", consumes=["agent.evolved"], produces=[]))
        fail_sink = SinkAgent(AgentCapability(name="fsink", consumes=["agent.evolution_failed"], produces=[]))

        with tempfile.TemporaryDirectory() as tmpdir:
            evo = EvolutionAgent(reg, Path(tmpdir), dry_run=True)
            coord = Coordinator(bus, agents=[evolved_sink, fail_sink], idle_timeout=0.1)
            evo.start(bus)
            coord.start_agents()

            bus.publish(Message(
                topic="goal.failed",
                payload={
                    "goal": "process episode",
                    "missing_capabilities": ["orphaned topic: 'episode.downloaded' is produced but never consumed"],
                },
                source_agent="planner",
            ))

            import time
            time.sleep(0.5)

            assert len(evolved_sink.received) >= 1
            payload = evolved_sink.received[0].payload
            assert payload["gap_type"] == "orphaned"
            assert payload["topic"] == "episode.downloaded"

            evo.stop()
            coord.stop_agents()

    def test_handles_gap_detected(self):
        reg = _sparse_registry()
        bus = MessageBus()
        evolved_sink = SinkAgent(AgentCapability(name="esink", consumes=["agent.evolved"], produces=[]))

        with tempfile.TemporaryDirectory() as tmpdir:
            evo = EvolutionAgent(reg, Path(tmpdir), dry_run=True)
            evo.start(bus)
            evolved_sink.start(bus)

            bus.publish(Message(
                topic="network.gap_detected",
                payload={"trigger": "periodic_scan"},
                source_agent="monitor",
            ))

            import time
            time.sleep(0.5)

            assert len(evolved_sink.received) >= 1

            evo.stop()
            evolved_sink.stop()

    def test_no_gaps_produces_failure(self):
        reg = AgentRegistry()
        reg.register(AgentCapability(name="a", consumes=["t"], produces=[]))
        # Only gap is dead end for 't', but let's test with no missing_capabilities in payload
        bus = MessageBus()
        fail_sink = SinkAgent(AgentCapability(name="fsink", consumes=["agent.evolution_failed"], produces=[]))

        with tempfile.TemporaryDirectory() as tmpdir:
            evo = EvolutionAgent(reg, Path(tmpdir), dry_run=True)
            evo.start(bus)
            fail_sink.start(bus)

            bus.publish(Message(
                topic="goal.failed",
                payload={"goal": "something", "missing_capabilities": []},
                source_agent="test",
            ))

            import time
            time.sleep(0.5)

            assert len(fail_sink.received) >= 1
            assert "No actionable gaps" in fail_sink.received[0].payload.get("reason", "")

            evo.stop()
            fail_sink.stop()

    def test_writes_real_agent_file(self):
        reg = _sparse_registry()
        bus = MessageBus()
        evolved_sink = SinkAgent(AgentCapability(name="esink", consumes=["agent.evolved"], produces=[]))

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            evo = EvolutionAgent(reg, tmppath, dry_run=False)
            evo.start(bus)
            evolved_sink.start(bus)

            bus.publish(Message(
                topic="goal.failed",
                payload={
                    "goal": "test",
                    "missing_capabilities": ["orphaned topic: 'episode.downloaded' is produced but never consumed"],
                },
                source_agent="test",
            ))

            import time
            time.sleep(0.5)

            assert len(evolved_sink.received) >= 1
            assert evolved_sink.received[0].payload["written"] is True

            # Verify the file was created and is loadable
            generated_file = tmppath / "episode_downloaded_consumer.py"
            assert generated_file.exists()
            agents = _load_agents_from_dir(tmppath)
            assert any(a.name == "episode_downloaded_consumer" for a in agents)

            evo.stop()
            evolved_sink.stop()
