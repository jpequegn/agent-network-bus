"""Tests for the CLI commands."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from click.testing import CliRunner

from bus.bus import Message, MessageBus
from bus.cli import cli, _load_agents_from_dir
from bus.store import BusStore

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "agents"


class TestLoadAgents:
    def test_load_from_fixtures(self):
        agents = _load_agents_from_dir(FIXTURES_DIR)
        assert len(agents) == 1
        assert agents[0].name == "echo"

    def test_load_from_nonexistent_dir(self):
        from click import ClickException
        try:
            _load_agents_from_dir(Path("/nonexistent"))
            assert False, "Should have raised"
        except ClickException:
            pass


class TestGraphCommand:
    def test_graph_output(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["graph", "--agents", str(FIXTURES_DIR)])
        assert result.exit_code == 0
        assert "echo" in result.output
        assert "input" in result.output
        assert "output" in result.output


class TestPublishCommand:
    def test_publish_to_memory(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["publish", "test.topic", '{"key": "value"}'])
        assert result.exit_code == 0
        assert "Published message" in result.output
        assert "test.topic" in result.output

    def test_publish_to_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            runner = CliRunner()
            result = runner.invoke(cli, ["publish", "t", '{"a": 1}', "--db", db_path])
            assert result.exit_code == 0

            # Verify in DB
            store = BusStore(db_path)
            msgs = store.get_by_topic("t")
            assert len(msgs) == 1
            assert msgs[0]["payload"] == {"a": 1}
            store.close()

    def test_publish_invalid_json(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["publish", "t", "not-json"])
        assert result.exit_code != 0
        assert "Invalid JSON" in result.output


class TestInspectCommand:
    def test_inspect_existing_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = BusStore(db_path)
            bus = MessageBus(store=store)
            msg = Message(topic="a.b", payload={"x": 1}, source_agent="test")
            bus.publish(msg)
            store.close()

            runner = CliRunner()
            result = runner.invoke(cli, ["inspect-msg", msg.id, "--db", db_path])
            assert result.exit_code == 0
            assert "a.b" in result.output
            assert msg.id[:8] in result.output

    def test_inspect_nonexistent(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["inspect-msg", "nonexistent-id"])
        assert result.exit_code == 0
        assert "No messages found" in result.output


class TestStatusCommand:
    def test_status_no_agents(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0

    def test_status_with_agents(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["status", "--agents", str(FIXTURES_DIR)])
        assert result.exit_code == 0
        assert "echo" in result.output
