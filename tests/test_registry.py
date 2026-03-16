"""Tests for AgentRegistry and AgentCapability."""

from bus.registry import AgentCapability, AgentRegistry


def _cap(name: str, consumes: list[str], produces: list[str]) -> AgentCapability:
    return AgentCapability(name=name, consumes=consumes, produces=produces)


class TestAgentCapability:
    def test_defaults(self):
        cap = AgentCapability(name="test", consumes=["a"], produces=["b"])
        assert cap.max_concurrent == 1
        assert cap.description == ""


class TestAgentRegistry:
    def test_register_and_discover(self):
        reg = AgentRegistry()
        reg.register(_cap("transcriber", ["episode.downloaded"], ["episode.transcribed"]))
        found = reg.discover("episode.downloaded")
        assert len(found) == 1
        assert found[0].name == "transcriber"

    def test_discover_wildcard_consumer(self):
        reg = AgentRegistry()
        reg.register(_cap("monitor", ["*"], []))
        found = reg.discover("episode.downloaded")
        assert len(found) == 1

    def test_discover_prefix_wildcard(self):
        reg = AgentRegistry()
        reg.register(_cap("ep_handler", ["episode.*"], ["episode.processed"]))
        assert len(reg.discover("episode.downloaded")) == 1
        assert len(reg.discover("episode.transcribed")) == 1
        assert len(reg.discover("blog.ready")) == 0

    def test_discover_no_match(self):
        reg = AgentRegistry()
        reg.register(_cap("writer", ["episode.processed"], ["blog.ready"]))
        assert len(reg.discover("episode.downloaded")) == 0

    def test_unregister(self):
        reg = AgentRegistry()
        reg.register(_cap("a", ["t"], []))
        reg.unregister("a")
        assert len(reg.discover("t")) == 0

    def test_multiple_agents_same_topic(self):
        reg = AgentRegistry()
        reg.register(_cap("digester", ["episode.transcribed"], ["episode.processed"]))
        reg.register(_cap("archiver", ["episode.transcribed"], ["episode.archived"]))
        found = reg.discover("episode.transcribed")
        assert len(found) == 2


class TestCapabilityGraph:
    def test_graph_structure(self):
        reg = AgentRegistry()
        reg.register(_cap("fetcher", [], ["episode.downloaded"]))
        reg.register(_cap("transcriber", ["episode.downloaded"], ["episode.transcribed"]))
        g = reg.capability_graph()
        assert g.has_edge("fetcher", "episode.downloaded")
        assert g.has_edge("episode.downloaded", "transcriber")
        assert g.has_edge("transcriber", "episode.transcribed")

    def test_graph_node_types(self):
        reg = AgentRegistry()
        reg.register(_cap("fetcher", [], ["episode.downloaded"]))
        g = reg.capability_graph()
        assert g.nodes["fetcher"]["type"] == "agent"
        assert g.nodes["episode.downloaded"]["type"] == "topic"


class TestValidation:
    def test_valid_pipeline(self):
        reg = AgentRegistry()
        reg.register(_cap("fetcher", [], ["episode.downloaded"]))
        reg.register(_cap("transcriber", ["episode.downloaded"], ["episode.transcribed"]))
        reg.register(_cap("digester", ["episode.transcribed"], []))
        warnings = reg.validate()
        assert len(warnings) == 0

    def test_orphaned_topic(self):
        reg = AgentRegistry()
        reg.register(_cap("fetcher", [], ["episode.downloaded"]))
        # No one consumes episode.downloaded
        warnings = reg.validate()
        orphaned = [w for w in warnings if "orphaned" in w]
        assert len(orphaned) == 1
        assert "episode.downloaded" in orphaned[0]

    def test_dead_end(self):
        reg = AgentRegistry()
        reg.register(_cap("transcriber", ["episode.downloaded"], []))
        # No one produces episode.downloaded
        warnings = reg.validate()
        dead = [w for w in warnings if "dead end" in w]
        assert len(dead) == 1
        assert "episode.downloaded" in dead[0]

    def test_wildcard_consumer_prevents_orphan(self):
        reg = AgentRegistry()
        reg.register(_cap("fetcher", [], ["episode.downloaded"]))
        reg.register(_cap("monitor", ["episode.*"], []))
        warnings = reg.validate()
        orphaned = [w for w in warnings if "orphaned" in w]
        assert len(orphaned) == 0

    def test_cycle_detection(self):
        reg = AgentRegistry()
        reg.register(_cap("a", ["t1"], ["t2"]))
        reg.register(_cap("b", ["t2"], ["t1"]))
        warnings = reg.validate()
        cycles = [w for w in warnings if "cycle" in w]
        assert len(cycles) > 0
