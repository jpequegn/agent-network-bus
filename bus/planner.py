"""Auto-wiring planner: maps natural language goals to pipelines via capability graph introspection."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

import networkx as nx

from bus.agent import BaseAgent
from bus.bus import Message, MessageBus
from bus.coordinator import Coordinator, PipelineResult
from bus.registry import AgentCapability, AgentRegistry

logger = logging.getLogger(__name__)

# Type for a pluggable LLM planner function.
# Input: (goal_text, graph_description) -> (entry_topic, payload_dict)
PlannerFn = Callable[[str, str], tuple[str, dict]]


@dataclass
class ExecutionPlan:
    """A plan mapping a goal to a pipeline entry point."""

    goal: str
    entry_topic: str
    payload: dict
    agent_chain: list[str]
    confidence: str  # "high" | "medium" | "low"
    reasoning: str
    missing_capabilities: list[str] = field(default_factory=list)


def describe_graph(registry: AgentRegistry) -> str:
    """Produce a human/LLM-readable description of the capability graph."""
    lines = ["Available agents and their capabilities:", ""]
    for cap in sorted(registry.agents.values(), key=lambda c: c.name):
        consumes = ", ".join(cap.consumes) if cap.consumes else "(none — entry point)"
        produces = ", ".join(cap.produces) if cap.produces else "(terminal)"
        desc = f" — {cap.description}" if cap.description else ""
        lines.append(f"  {cap.name}{desc}")
        lines.append(f"    consumes: {consumes}")
        lines.append(f"    produces: {produces}")
        lines.append("")

    # Add topology
    lines.append("Pipeline chains:")
    g = registry.capability_graph()
    # Find entry topics (topics with no incoming agent edges)
    for node in g.nodes:
        if g.nodes[node].get("type") == "topic" and g.in_degree(node) == 0:
            chain = _trace_chain(g, node)
            lines.append(f"  {chain}")

    # Validation warnings
    warnings = registry.validate()
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in warnings:
            lines.append(f"  - {w}")

    return "\n".join(lines)


def _trace_chain(g: nx.DiGraph, start: str, visited: set | None = None) -> str:
    """Trace a chain from a topic node through the graph for display."""
    if visited is None:
        visited = set()
    if start in visited:
        return f"{start} (cycle)"
    visited.add(start)

    successors = list(g.successors(start))
    if not successors:
        return start

    parts = []
    for succ in successors:
        next_nodes = list(g.successors(succ))
        if next_nodes:
            for nn in next_nodes:
                sub = _trace_chain(g, nn, visited.copy())
                parts.append(f"{start} → {succ} → {sub}")
        else:
            parts.append(f"{start} → {succ}")

    return " | ".join(parts) if len(parts) > 1 else parts[0]


def _find_entry_topics(registry: AgentRegistry) -> set[str]:
    """Find topics that are consumed but not produced by any agent (entry points)."""
    all_consumed: set[str] = set()
    all_produced: set[str] = set()
    for cap in registry.agents.values():
        all_consumed.update(cap.consumes)
        all_produced.update(cap.produces)
    return all_consumed - all_produced


def _find_terminal_topics(registry: AgentRegistry) -> set[str]:
    """Find topics that are produced but not consumed by any agent (terminal)."""
    all_consumed: set[str] = set()
    all_produced: set[str] = set()
    for cap in registry.agents.values():
        all_consumed.update(cap.consumes)
        all_produced.update(cap.produces)
    return all_produced - all_consumed


def _trace_agent_chain(registry: AgentRegistry, entry_topic: str) -> list[str]:
    """Walk the capability graph from an entry topic and return the agent chain."""
    chain: list[str] = []
    visited: set[str] = set()
    queue = [entry_topic]
    while queue:
        topic = queue.pop(0)
        if topic in visited:
            continue
        visited.add(topic)
        consumers = registry.discover(topic)
        for cap in consumers:
            if cap.name not in chain:
                chain.append(cap.name)
                queue.extend(cap.produces)
    return chain


def rule_based_planner(registry: AgentRegistry) -> PlannerFn:
    """Create a rule-based planner that maps goals to entry topics via keyword matching.

    This works without any LLM — pure graph introspection + keyword heuristics.
    """
    entry_topics = _find_entry_topics(registry)

    # Build keyword → entry_topic mapping from agent descriptions and topic names
    keyword_map: dict[str, str] = {}
    for cap in registry.agents.values():
        for topic in cap.consumes:
            if topic in entry_topics:
                # Extract keywords from topic name, agent name, and description
                words = set()
                words.update(topic.replace(".", " ").split())
                words.update(cap.name.replace("_", " ").split())
                if cap.description:
                    words.update(cap.description.lower().split())
                for word in words:
                    keyword_map[word.lower()] = topic

    def planner(goal: str, graph_desc: str) -> tuple[str, dict]:
        goal_lower = goal.lower()

        # Score each entry topic by keyword overlap
        scores: dict[str, int] = {t: 0 for t in entry_topics}
        for word, topic in keyword_map.items():
            if word in goal_lower and topic in scores:
                scores[topic] += 1

        best_topic = max(scores, key=lambda t: scores[t]) if scores else None

        if not best_topic or scores.get(best_topic, 0) == 0:
            # Fallback: pick the first entry topic
            best_topic = sorted(entry_topics)[0] if entry_topics else ""

        # Extract payload hints from the goal (numbers → IDs)
        payload: dict = {}
        words = goal.split()
        for i, word in enumerate(words):
            if word.isdigit():
                # Guess it's an episode/item ID
                payload["episode_id"] = int(word)
                break

        return best_topic, payload

    return planner


def create_plan(
    goal: str,
    registry: AgentRegistry,
    planner_fn: PlannerFn | None = None,
) -> ExecutionPlan:
    """Create an execution plan for a natural language goal.

    Uses the provided planner_fn, or falls back to rule-based planning.
    """
    graph_desc = describe_graph(registry)

    if planner_fn is None:
        planner_fn = rule_based_planner(registry)

    entry_topic, payload = planner_fn(goal, graph_desc)

    # Validate the entry topic exists
    entry_topics = _find_entry_topics(registry)
    if entry_topic not in entry_topics:
        # Check if it's a known topic at all
        all_topics = set()
        for cap in registry.agents.values():
            all_topics.update(cap.consumes)
            all_topics.update(cap.produces)
        if entry_topic not in all_topics:
            return ExecutionPlan(
                goal=goal,
                entry_topic=entry_topic,
                payload=payload,
                agent_chain=[],
                confidence="low",
                reasoning=f"Topic '{entry_topic}' not found in capability graph",
                missing_capabilities=[f"No agent consumes or produces '{entry_topic}'"],
            )

    agent_chain = _trace_agent_chain(registry, entry_topic)
    terminal_topics = _find_terminal_topics(registry)

    # Check for gaps
    missing: list[str] = []
    warnings = registry.validate()
    for w in warnings:
        if "orphaned" in w or "dead end" in w:
            missing.append(w)

    confidence = "high" if agent_chain and not missing else "medium" if agent_chain else "low"
    reasoning = (
        f"Goal maps to entry topic '{entry_topic}', "
        f"which triggers chain: {' → '.join(agent_chain)}. "
        f"Terminal topics: {', '.join(sorted(terminal_topics))}."
    )

    return ExecutionPlan(
        goal=goal,
        entry_topic=entry_topic,
        payload=payload,
        agent_chain=agent_chain,
        confidence=confidence,
        reasoning=reasoning,
        missing_capabilities=missing,
    )


class PlannerAgent(BaseAgent):
    """Agent that receives goals and auto-wires pipelines from the capability graph.

    Consumes: goal.requested
    Produces: goal.planned, goal.completed, goal.failed
    """

    capability = AgentCapability(
        name="planner",
        consumes=["goal.requested"],
        produces=["goal.planned", "goal.completed", "goal.failed"],
        description="Maps natural language goals to pipeline executions via capability graph introspection",
    )

    def __init__(
        self,
        registry: AgentRegistry,
        coordinator: Coordinator,
        planner_fn: PlannerFn | None = None,
    ) -> None:
        super().__init__(self.capability)
        self._registry = registry
        self._coordinator = coordinator
        self._planner_fn = planner_fn

    def handle(self, message: Message) -> Message | list[Message]:
        goal = message.payload.get("goal", "")
        if not goal:
            return Message(
                topic="goal.failed",
                payload={"error": "No goal provided", "original": message.payload},
            )

        # Step 1: Create plan
        plan = create_plan(goal, self._registry, self._planner_fn)
        plan_msg = Message(
            topic="goal.planned",
            payload={
                "goal": plan.goal,
                "entry_topic": plan.entry_topic,
                "payload": plan.payload,
                "agent_chain": plan.agent_chain,
                "confidence": plan.confidence,
                "reasoning": plan.reasoning,
                "missing_capabilities": plan.missing_capabilities,
            },
        )

        if plan.confidence == "low" or not plan.agent_chain:
            return [
                plan_msg,
                Message(
                    topic="goal.failed",
                    payload={
                        "goal": goal,
                        "reason": plan.reasoning,
                        "missing_capabilities": plan.missing_capabilities,
                    },
                ),
            ]

        # Step 2: Execute pipeline
        pipeline_msg = Message(
            topic=plan.entry_topic,
            payload=plan.payload,
            source_agent="planner",
        )
        result = self._coordinator.run_pipeline(pipeline_msg)

        # Step 3: Report result
        result_payload = {
            "goal": goal,
            "plan": {
                "entry_topic": plan.entry_topic,
                "agent_chain": plan.agent_chain,
                "confidence": plan.confidence,
            },
            "pipeline": {
                "root_message_id": result.root_message_id,
                "success": result.success,
                "duration_ms": result.duration_ms,
                "trace_length": len(result.trace),
                "error": result.error,
            },
        }

        if result.success:
            return [plan_msg, Message(topic="goal.completed", payload=result_payload)]
        else:
            return [plan_msg, Message(topic="goal.failed", payload=result_payload)]
