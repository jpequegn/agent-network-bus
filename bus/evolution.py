"""Self-evolving network: generates new agents to fill capability gaps."""

from __future__ import annotations

import logging
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from bus.agent import BaseAgent
from bus.bus import Message, MessageBus
from bus.registry import AgentCapability, AgentRegistry

logger = logging.getLogger(__name__)

# Type for a pluggable code generator.
# Input: (gap description, registry context) -> generated Python source code
GeneratorFn = Callable[["GapAnalysis", str], str]


@dataclass
class GapAnalysis:
    """Analysis of a capability gap in the network."""

    gap_type: str  # "orphaned" | "dead_end" | "missing_link"
    topic: str
    description: str
    suggested_agent_name: str
    suggested_consumes: list[str]
    suggested_produces: list[str]


@dataclass
class EvolutionResult:
    """Result of an evolution attempt."""

    gap: GapAnalysis
    code: str
    file_path: Path | None  # None if dry_run
    written: bool
    agent_name: str


def analyze_gaps(registry: AgentRegistry) -> list[GapAnalysis]:
    """Analyze the capability graph and identify all gaps."""
    gaps: list[GapAnalysis] = []

    all_consumed: set[str] = set()
    all_produced: set[str] = set()
    for cap in registry.agents.values():
        all_consumed.update(cap.consumes)
        all_produced.update(cap.produces)

    # Orphaned topics: produced but never consumed
    for topic in sorted(all_produced - all_consumed):
        name = _topic_to_agent_name(topic, "consumer")
        gaps.append(GapAnalysis(
            gap_type="orphaned",
            topic=topic,
            description=f"'{topic}' is produced but no agent consumes it",
            suggested_agent_name=name,
            suggested_consumes=[topic],
            suggested_produces=[],
        ))

    # Dead ends: consumed but never produced
    for topic in sorted(all_consumed - all_produced):
        name = _topic_to_agent_name(topic, "producer")
        gaps.append(GapAnalysis(
            gap_type="dead_end",
            topic=topic,
            description=f"'{topic}' is consumed but no agent produces it",
            suggested_agent_name=name,
            suggested_consumes=[],
            suggested_produces=[topic],
        ))

    return gaps


def analyze_missing_from_failure(payload: dict, registry: AgentRegistry) -> list[GapAnalysis]:
    """Extract gaps from a goal.failed message payload."""
    gaps: list[GapAnalysis] = []
    missing = payload.get("missing_capabilities", [])
    reason = payload.get("reason", "")

    for desc in missing:
        # Parse "orphaned topic: 'X' is produced but never consumed"
        orphan_match = re.search(r"orphaned.*?'([^']+)'", desc)
        if orphan_match:
            topic = orphan_match.group(1)
            name = _topic_to_agent_name(topic, "consumer")
            gaps.append(GapAnalysis(
                gap_type="orphaned",
                topic=topic,
                description=desc,
                suggested_agent_name=name,
                suggested_consumes=[topic],
                suggested_produces=[],
            ))
            continue

        # Parse "dead end: 'X' is consumed but never produced"
        dead_match = re.search(r"dead end.*?'([^']+)'", desc)
        if dead_match:
            topic = dead_match.group(1)
            name = _topic_to_agent_name(topic, "producer")
            gaps.append(GapAnalysis(
                gap_type="dead_end",
                topic=topic,
                description=desc,
                suggested_agent_name=name,
                suggested_consumes=[],
                suggested_produces=[topic],
            ))
            continue

        # Parse "No agent consumes or produces 'X'"
        no_agent_match = re.search(r"No agent.*?'([^']+)'", desc)
        if no_agent_match:
            topic = no_agent_match.group(1)
            name = _topic_to_agent_name(topic, "handler")
            gaps.append(GapAnalysis(
                gap_type="missing_link",
                topic=topic,
                description=desc,
                suggested_agent_name=name,
                suggested_consumes=[topic],
                suggested_produces=[f"{topic}.processed"],
            ))

    return gaps


def generate_agent_code(gap: GapAnalysis, registry_context: str = "") -> str:
    """Generate Python source code for a BaseAgent subclass to fill a gap."""
    class_name = _snake_to_class(gap.suggested_agent_name)
    consumes_str = repr(gap.suggested_consumes)
    produces_str = repr(gap.suggested_produces)

    if gap.gap_type == "orphaned":
        handle_body = _gen_consumer_handle(gap)
        desc = f"Auto-generated consumer for {gap.topic}"
    elif gap.gap_type == "dead_end":
        handle_body = _gen_producer_handle(gap)
        desc = f"Auto-generated producer for {gap.topic}"
    else:
        handle_body = _gen_passthrough_handle(gap)
        desc = f"Auto-generated handler for {gap.topic}"

    consumes_doc = ", ".join(gap.suggested_consumes) or "(none)"
    produces_doc = ", ".join(gap.suggested_produces) or "(terminal)"

    lines = [
        f'"""{desc}.',
        f"",
        f"Consumes: {consumes_doc}",
        f"Produces: {produces_doc}",
        f"",
        f"Auto-generated by EvolutionAgent to fill a capability gap.",
        f'"""',
        f"",
        f"from __future__ import annotations",
        f"",
        f"from bus.agent import BaseAgent",
        f"from bus.bus import Message",
        f"from bus.registry import AgentCapability",
        f"",
        f"",
        f"class {class_name}(BaseAgent):",
        f'    """{desc}."""',
        f"",
        f"    capability = AgentCapability(",
        f'        name="{gap.suggested_agent_name}",',
        f"        consumes={consumes_str},",
        f"        produces={produces_str},",
        f'        description="{desc}",',
        f"    )",
        f"",
        f"    def __init__(self) -> None:",
        f"        super().__init__(self.capability)",
        f"",
        handle_body,
    ]
    return "\n".join(lines) + "\n"


def evolve(
    gap: GapAnalysis,
    agents_dir: Path,
    generator_fn: GeneratorFn | None = None,
    dry_run: bool = False,
    registry_context: str = "",
) -> EvolutionResult:
    """Generate and optionally write a new agent to fill a gap.

    Returns the result with generated code and write status.
    """
    if generator_fn:
        code = generator_fn(gap, registry_context)
    else:
        code = generate_agent_code(gap, registry_context)

    file_name = f"{gap.suggested_agent_name}.py"
    file_path = agents_dir / file_name

    written = False
    if not dry_run:
        if file_path.exists():
            logger.warning("File %s already exists, skipping", file_path)
        else:
            agents_dir.mkdir(parents=True, exist_ok=True)
            file_path.write_text(code)
            written = True
            logger.info("Evolved new agent: %s -> %s", gap.suggested_agent_name, file_path)

    return EvolutionResult(
        gap=gap,
        code=code,
        file_path=file_path if not dry_run else None,
        written=written,
        agent_name=gap.suggested_agent_name,
    )


class EvolutionAgent(BaseAgent):
    """Agent that detects capability gaps and generates new agents to fill them.

    Consumes: goal.failed, network.gap_detected
    Produces: agent.evolved, agent.evolution_failed
    """

    capability = AgentCapability(
        name="evolution",
        consumes=["goal.failed", "network.gap_detected"],
        produces=["agent.evolved", "agent.evolution_failed"],
        description="Generates new agents to fill capability gaps in the network",
    )

    def __init__(
        self,
        registry: AgentRegistry,
        agents_dir: Path,
        generator_fn: GeneratorFn | None = None,
        dry_run: bool = False,
    ) -> None:
        super().__init__(self.capability)
        self._registry = registry
        self._agents_dir = agents_dir
        self._generator_fn = generator_fn
        self._dry_run = dry_run

    def handle(self, message: Message) -> Message | list[Message]:
        if message.topic == "goal.failed":
            return self._handle_goal_failed(message)
        elif message.topic == "network.gap_detected":
            return self._handle_gap_detected(message)
        return Message(
            topic="agent.evolution_failed",
            payload={"error": f"Unknown topic: {message.topic}"},
        )

    def _handle_goal_failed(self, message: Message) -> Message | list[Message]:
        gaps = analyze_missing_from_failure(message.payload, self._registry)
        if not gaps:
            return Message(
                topic="agent.evolution_failed",
                payload={
                    "reason": "No actionable gaps found in failure",
                    "original_goal": message.payload.get("goal", ""),
                },
            )
        return self._evolve_gaps(gaps)

    def _handle_gap_detected(self, message: Message) -> Message | list[Message]:
        gaps = analyze_gaps(self._registry)
        if not gaps:
            return Message(
                topic="agent.evolution_failed",
                payload={"reason": "No gaps found in current registry"},
            )
        return self._evolve_gaps(gaps)

    def _evolve_gaps(self, gaps: list[GapAnalysis]) -> list[Message]:
        from bus.planner import describe_graph
        context = describe_graph(self._registry)
        results: list[Message] = []

        for gap in gaps:
            try:
                result = evolve(
                    gap,
                    self._agents_dir,
                    generator_fn=self._generator_fn,
                    dry_run=self._dry_run,
                    registry_context=context,
                )
                results.append(Message(
                    topic="agent.evolved",
                    payload={
                        "agent_name": result.agent_name,
                        "gap_type": gap.gap_type,
                        "topic": gap.topic,
                        "description": gap.description,
                        "file_path": str(result.file_path) if result.file_path else None,
                        "written": result.written,
                        "code_preview": result.code[:200] + "..." if len(result.code) > 200 else result.code,
                    },
                ))
            except Exception as e:
                results.append(Message(
                    topic="agent.evolution_failed",
                    payload={
                        "agent_name": gap.suggested_agent_name,
                        "error": str(e),
                    },
                ))

        return results


def _topic_to_agent_name(topic: str, suffix: str) -> str:
    """Convert a topic like 'blog.draft_ready' to an agent name like 'blog_draft_ready_consumer'."""
    base = topic.replace(".", "_").replace("*", "all")
    return f"{base}_{suffix}"


def _snake_to_class(name: str) -> str:
    """Convert 'blog_draft_ready_consumer' to 'BlogDraftReadyConsumerAgent'."""
    parts = name.split("_")
    return "".join(p.capitalize() for p in parts) + "Agent"


def _gen_consumer_handle(gap: GapAnalysis) -> str:
    """Generate handle() for a consumer stub."""
    return "\n".join([
        f"    def handle(self, message: Message) -> None:",
        f"        # Auto-generated consumer stub for '{gap.topic}'.",
        f"        # Replace this with real processing logic.",
        f"        return None",
    ])


def _gen_producer_handle(gap: GapAnalysis) -> str:
    """Generate handle() for a producer stub."""
    topic = gap.suggested_produces[0] if gap.suggested_produces else "unknown"
    return "\n".join([
        f"    def handle(self, message: Message) -> Message:",
        f"        # Auto-generated producer stub for '{topic}'.",
        f"        # Replace this with real production logic.",
        f"        return Message(",
        f'            topic="{topic}",',
        f"            payload=message.payload,",
        f"        )",
    ])


def _gen_passthrough_handle(gap: GapAnalysis) -> str:
    """Generate handle() for a pass-through stub."""
    out_topic = gap.suggested_produces[0] if gap.suggested_produces else f"{gap.topic}.processed"
    return "\n".join([
        f"    def handle(self, message: Message) -> Message:",
        f"        # Auto-generated pass-through stub for '{gap.topic}'.",
        f"        # Replace this with real transformation logic.",
        f"        return Message(",
        f'            topic="{out_topic}",',
        f"            payload=message.payload,",
        f"        )",
    ])
