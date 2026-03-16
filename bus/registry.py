"""Agent registry with capability declarations and topology validation."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

import networkx as nx


@dataclass
class AgentCapability:
    """Declares what an agent consumes and produces."""

    name: str
    consumes: list[str]
    produces: list[str]
    description: str = ""
    max_concurrent: int = 1


class AgentRegistry:
    """Registry for agent capabilities with discovery and graph validation."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentCapability] = {}

    @property
    def agents(self) -> dict[str, AgentCapability]:
        return dict(self._agents)

    def register(self, capability: AgentCapability) -> None:
        """Register an agent's capability declaration."""
        self._agents[capability.name] = capability

    def unregister(self, name: str) -> None:
        self._agents.pop(name, None)

    def discover(self, topic: str) -> list[AgentCapability]:
        """Find all agents capable of handling this topic."""
        results = []
        for cap in self._agents.values():
            for pattern in cap.consumes:
                if self._topic_matches(pattern, topic):
                    results.append(cap)
                    break
        return results

    def capability_graph(self) -> nx.DiGraph:
        """Build a directed graph: topic -> agent -> topic chains."""
        g = nx.DiGraph()
        for cap in self._agents.values():
            g.add_node(cap.name, type="agent")
            for topic in cap.consumes:
                g.add_node(topic, type="topic")
                g.add_edge(topic, cap.name)
            for topic in cap.produces:
                g.add_node(topic, type="topic")
                g.add_edge(cap.name, topic)
        return g

    def validate(self) -> list[str]:
        """Check for orphaned topics, dead ends, and cycles.

        Returns a list of warning strings (empty = valid).
        """
        warnings: list[str] = []

        all_consumed: set[str] = set()
        all_produced: set[str] = set()
        for cap in self._agents.values():
            all_consumed.update(cap.consumes)
            all_produced.update(cap.produces)

        # Orphaned: produced but never consumed by any agent
        for topic in all_produced:
            if not self._any_pattern_matches(topic, all_consumed):
                warnings.append(f"orphaned topic: '{topic}' is produced but never consumed")

        # Dead ends: consumed but never produced by any agent
        for pattern in all_consumed:
            if not self._any_topic_matches_pattern(pattern, all_produced):
                warnings.append(f"dead end: '{pattern}' is consumed but never produced")

        # Cycles in the capability graph
        g = self.capability_graph()
        try:
            cycles = list(nx.simple_cycles(g))
            for cycle in cycles:
                cycle_str = " -> ".join(cycle)
                warnings.append(f"cycle detected: {cycle_str}")
        except nx.NetworkXError:
            pass

        return warnings

    @staticmethod
    def _topic_matches(pattern: str, topic: str) -> bool:
        if pattern == "*":
            return True
        return fnmatch.fnmatch(topic, pattern)

    @classmethod
    def _any_pattern_matches(cls, topic: str, patterns: set[str]) -> bool:
        """Check if any pattern in the set matches the given topic."""
        return any(cls._topic_matches(p, topic) for p in patterns)

    @classmethod
    def _any_topic_matches_pattern(cls, pattern: str, topics: set[str]) -> bool:
        """Check if any concrete topic matches the given pattern."""
        return any(cls._topic_matches(pattern, t) for t in topics)
