"""CLI for the Agent Network Bus: run, graph, publish, inspect, status."""

from __future__ import annotations

import importlib.util
import inspect
import json
import signal
import sys
import time
from pathlib import Path

import click

from bus.agent import BaseAgent
from bus.bus import Message, MessageBus
from bus.coordinator import Coordinator
from bus.registry import AgentCapability, AgentRegistry
from bus.renderer import render_live, render_snapshot
from bus.store import BusStore


def _load_agents_from_dir(agents_dir: Path) -> list[BaseAgent]:
    """Dynamically load all BaseAgent subclasses from Python files in a directory."""
    agents: list[BaseAgent] = []
    if not agents_dir.is_dir():
        raise click.ClickException(f"Agents directory not found: {agents_dir}")

    for py_file in sorted(agents_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, BaseAgent) and obj is not BaseAgent:
                    try:
                        instance = obj()
                        agents.append(instance)
                    except TypeError:
                        pass
    return agents


@click.group()
@click.option("--db", default=None, help="SQLite database path (default: in-memory)")
@click.pass_context
def cli(ctx: click.Context, db: str | None) -> None:
    """Agent Network Bus — multi-agent coordination via pub-sub."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db


@cli.command()
@click.option("--agents", "agents_dir", required=True, type=click.Path(exists=True), help="Directory containing agent modules")
@click.option("--db", default=None, help="SQLite database path")
@click.option("--watch/--no-watch", default=True, help="Show live dashboard")
def run(agents_dir: str, db: str | None, watch: bool) -> None:
    """Start the bus with all agents from the given directory."""
    store = BusStore(db) if db else BusStore()
    bus = MessageBus(store=store)
    agents = _load_agents_from_dir(Path(agents_dir))

    if not agents:
        raise click.ClickException(f"No agents found in {agents_dir}")

    click.echo(f"Loaded {len(agents)} agent(s): {', '.join(a.name for a in agents)}")

    coord = Coordinator(bus, agents=agents, idle_timeout=2.0)
    coord.start_agents()

    def _shutdown(sig, frame):
        click.echo("\nShutting down...")
        coord.stop_agents()
        store.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    if watch:
        with render_live(coord, refresh_per_second=2) as live:
            click.echo("Bus running. Press Ctrl+C to stop.")
            while True:
                time.sleep(0.5)
    else:
        click.echo("Bus running. Press Ctrl+C to stop.")
        while True:
            time.sleep(1)


@cli.command()
@click.option("--agents", "agents_dir", required=True, type=click.Path(exists=True), help="Directory containing agent modules")
def graph(agents_dir: str) -> None:
    """Show the capability topology as ASCII."""
    agents = _load_agents_from_dir(Path(agents_dir))
    registry = AgentRegistry()

    for agent in agents:
        registry.register(agent.capability)

    g = registry.capability_graph()

    # Find source nodes (no incoming edges from other agents)
    source_topics = set()
    for cap in registry.agents.values():
        for topic in cap.consumes:
            # Check if this topic is produced by anyone
            produced_by_any = any(
                topic in other.produces for other in registry.agents.values()
            )
            if not produced_by_any:
                source_topics.add(topic)
        # Also include produces from agents with no consumes (entry points)
        if not cap.consumes:
            source_topics.update(cap.produces)

    if not source_topics and agents:
        # Fallback: show all agents and their connections
        for agent in agents:
            cap = agent.capability
            consumes = ", ".join(cap.consumes) if cap.consumes else "(none)"
            produces = ", ".join(cap.produces) if cap.produces else "(terminal)"
            click.echo(f"  {consumes} → {cap.name} → {produces}")
        return

    # BFS from source topics
    visited = set()
    for topic in sorted(source_topics):
        _print_chain(registry, topic, visited, indent=0)

    # Validation warnings
    warnings = registry.validate()
    if warnings:
        click.echo()
        for w in warnings:
            click.echo(f"  ⚠ {w}")


def _print_chain(registry: AgentRegistry, topic: str, visited: set, indent: int) -> None:
    """Recursively print topic → agent → topic chains."""
    if topic in visited:
        click.echo(f"{'  ' * indent}{topic} (cycle)")
        return
    visited.add(topic)

    consumers = registry.discover(topic)
    if not consumers:
        click.echo(f"{'  ' * indent}{topic} (terminal)")
        return

    for consumer in consumers:
        if consumer.produces:
            for prod in consumer.produces:
                click.echo(f"{'  ' * indent}{topic} → {consumer.name} → {prod}")
                _print_chain(registry, prod, visited, indent + 1)
        else:
            click.echo(f"{'  ' * indent}{topic} → {consumer.name} (terminal)")


@cli.command()
@click.argument("topic")
@click.argument("payload")
@click.option("--db", default=None, help="SQLite database path")
@click.option("--source", default="cli", help="Source agent name")
def publish(topic: str, payload: str, db: str | None, source: str) -> None:
    """Publish a message to the bus."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise click.ClickException(f"Invalid JSON payload: {e}")

    store = BusStore(db) if db else BusStore()
    bus = MessageBus(store=store)
    msg = Message(topic=topic, payload=data, source_agent=source)
    bus.publish(msg)
    click.echo(f"Published message {msg.id} to '{topic}'")
    store.close()


@cli.command()
@click.argument("message_id")
@click.option("--db", default=None, help="SQLite database path")
def inspect_msg(message_id: str, db: str | None) -> None:
    """Inspect the full message trace for a given message ID."""
    store = BusStore(db) if db else BusStore()
    trace = store.get_trace(message_id)

    if not trace:
        click.echo(f"No messages found for ID: {message_id}")
        store.close()
        return

    for i, msg in enumerate(trace):
        prefix = "└─" if i == len(trace) - 1 else "├─"
        indent = "  " * (0 if i == 0 else 1)
        payload_str = json.dumps(msg["payload"], default=str)
        if len(payload_str) > 60:
            payload_str = payload_str[:57] + "..."

        click.echo(f"{indent}{prefix} [{msg['topic']}] {msg['id'][:8]}...")
        click.echo(f"{indent}   source: {msg['source_agent']}, payload: {payload_str}")
        if msg.get("processed_by"):
            click.echo(f"{indent}   processed by: {msg['processed_by']} at {msg.get('processed_at', '?')}")

    store.close()


@cli.command()
@click.option("--agents", "agents_dir", type=click.Path(exists=True), help="Directory containing agent modules")
@click.option("--db", default=None, help="SQLite database path")
@click.option("--watch/--no-watch", default=False, help="Live updating view")
def status(agents_dir: str | None, db: str | None, watch: bool) -> None:
    """Show current bus status."""
    store = BusStore(db) if db else BusStore()
    bus = MessageBus(store=store)

    agents: list[BaseAgent] = []
    if agents_dir:
        agents = _load_agents_from_dir(Path(agents_dir))

    coord = Coordinator(bus, agents=agents, idle_timeout=1.0)

    if watch and agents:
        coord.start_agents()
        with render_live(coord, refresh_per_second=2) as live:
            click.echo("Watching. Press Ctrl+C to stop.")
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                coord.stop_agents()
    else:
        render_snapshot(coord)

    store.close()


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
