"""Tests for the P3 pipeline agents and end-to-end pipeline execution."""

from __future__ import annotations

from pathlib import Path

from bus.bus import Message, MessageBus
from bus.cli import _load_agents_from_dir
from bus.coordinator import Coordinator

AGENTS_DIR = Path(__file__).parent.parent / "agents"


class TestAgentLoading:
    def test_load_all_p3_agents(self):
        agents = _load_agents_from_dir(AGENTS_DIR)
        names = {a.name for a in agents}
        assert names == {"p3_fetcher", "p3_transcriber", "p3_digester", "p3_writer", "topic_extractor"}

    def test_agent_capabilities_consistent(self):
        agents = _load_agents_from_dir(AGENTS_DIR)
        by_name = {a.name: a for a in agents}

        # Fetcher produces what transcriber consumes
        assert "episode.downloaded" in by_name["p3_fetcher"].capability.produces
        assert "episode.downloaded" in by_name["p3_transcriber"].capability.consumes

        # Transcriber produces what digester and topic_extractor consume
        assert "episode.transcribed" in by_name["p3_transcriber"].capability.produces
        assert "episode.transcribed" in by_name["p3_digester"].capability.consumes
        assert "episode.transcribed" in by_name["topic_extractor"].capability.consumes

        # Digester produces what writer consumes
        assert "episode.digested" in by_name["p3_digester"].capability.produces
        assert "episode.digested" in by_name["p3_writer"].capability.consumes


class TestEndToEndPipeline:
    def _run_pipeline(self) -> tuple[Coordinator, list]:
        bus = MessageBus()
        agents = _load_agents_from_dir(AGENTS_DIR)
        coord = Coordinator(bus, agents=agents, idle_timeout=1.5)
        coord.start_agents()
        return coord, agents

    def test_single_episode(self):
        coord, agents = self._run_pipeline()
        msg = Message(
            topic="episode.requested",
            payload={"episode_id": 1},
            source_agent="test",
        )
        result = coord.run_pipeline(msg)

        # Pipeline should complete (may have transcriber errors occasionally)
        assert result.root_message_id == msg.id
        assert len(result.trace) >= 1  # At least the root message

        if result.success:
            # Check we got messages through the pipeline
            topics = {m["topic"] for m in result.trace}
            assert "episode.requested" in topics
            assert "episode.downloaded" in topics

        coord.stop_agents()

    def test_multiple_episodes_sequential(self):
        coord, agents = self._run_pipeline()
        results = []
        for i in range(3):
            msg = Message(
                topic="episode.requested",
                payload={"episode_id": i + 1},
                source_agent="test",
            )
            results.append(coord.run_pipeline(msg))

        # At least some should succeed (transcriber has 5% error rate)
        success_count = sum(1 for r in results if r.success)
        assert success_count >= 1

        coord.stop_agents()

    def test_fan_out(self):
        coord, agents = self._run_pipeline()
        messages = [
            Message(
                topic="episode.requested",
                payload={"episode_id": 100 + i},
                source_agent="test",
            )
            for i in range(3)
        ]
        results = coord.fan_out(messages)
        assert len(results) == 3

        coord.stop_agents()

    def test_pipeline_trace_has_parent_chain(self):
        coord, agents = self._run_pipeline()
        msg = Message(
            topic="episode.requested",
            payload={"episode_id": 42},
            source_agent="test",
        )
        result = coord.run_pipeline(msg)

        if len(result.trace) >= 3:
            # Second message should have parent_id pointing to first
            assert result.trace[1]["parent_id"] == result.trace[0]["id"]

        coord.stop_agents()

    def test_network_status_after_run(self):
        coord, agents = self._run_pipeline()
        msg = Message(
            topic="episode.requested",
            payload={"episode_id": 1},
            source_agent="test",
        )
        coord.run_pipeline(msg)

        status = coord.status()
        assert len(status.agents) == 5
        assert status.total_processed >= 1

        coord.stop_agents()

    def test_graph_command_with_p3_agents(self):
        """Verify the CLI graph command works with P3 agents."""
        from click.testing import CliRunner
        from bus.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["graph", "--agents", str(AGENTS_DIR)])
        assert result.exit_code == 0
        assert "p3_fetcher" in result.output
        assert "p3_transcriber" in result.output
