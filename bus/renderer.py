"""Rich terminal renderer with live network view."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from bus.coordinator import Coordinator
    from bus.store import BusStore

_STATUS_ICONS = {
    "idle": "[green]\u2713[/green]",
    "processing": "[yellow]\U0001f504[/yellow]",
    "error": "[red]\u2717[/red]",
}


def build_agent_table(coordinator: Coordinator) -> Table:
    """Build a Rich table showing all agent statuses."""
    table = Table(title="Agent Network Bus", expand=True)
    table.add_column("Agent", style="cyan", no_wrap=True)
    table.add_column("Status", justify="center")
    table.add_column("Processed", justify="right", style="green")
    table.add_column("Errors", justify="right", style="red")
    table.add_column("Avg Latency", justify="right", style="yellow")

    status = coordinator.status()
    store = coordinator._bus.store

    for agent_info in status.agents:
        name = agent_info["name"]
        agent_status = agent_info["status"]
        icon = _STATUS_ICONS.get(agent_status, agent_status)

        stats = store.get_agent_stats(name)
        avg_latency = ""
        if stats and stats["avg_latency_ms"] > 0:
            avg_latency = f"{stats['avg_latency_ms']:.1f}ms"

        table.add_row(
            name,
            icon,
            str(agent_info["processed_count"]),
            str(agent_info["error_count"]),
            avg_latency,
        )

    return table


def build_messages_table(store: BusStore, limit: int = 10) -> Table:
    """Build a Rich table showing recent messages."""
    table = Table(title="Recent Messages", expand=True)
    table.add_column("Time", style="dim", no_wrap=True)
    table.add_column("Topic", style="magenta")
    table.add_column("Source", style="cyan")
    table.add_column("Processed By", style="blue")
    table.add_column("Payload", style="white", max_width=40)

    history = store.get_history(limit=limit)
    for msg in history:
        created = msg["created_at"]
        if isinstance(created, str):
            try:
                dt = datetime.fromisoformat(created)
                time_str = dt.strftime("%H:%M:%S")
            except ValueError:
                time_str = created[:8]
        else:
            time_str = str(created)

        payload_str = json.dumps(msg["payload"], default=str)
        if len(payload_str) > 37:
            payload_str = payload_str[:37] + "..."

        table.add_row(
            time_str,
            msg["topic"],
            msg["source_agent"],
            msg.get("processed_by") or "-",
            payload_str,
        )

    return table


def build_dashboard(coordinator: Coordinator, message_limit: int = 10) -> Layout:
    """Build the full dashboard layout."""
    layout = Layout()
    layout.split_column(
        Layout(name="agents", ratio=1),
        Layout(name="messages", ratio=1),
    )
    layout["agents"].update(Panel(build_agent_table(coordinator)))
    layout["messages"].update(
        Panel(build_messages_table(coordinator._bus.store, limit=message_limit))
    )
    return layout


def render_snapshot(coordinator: Coordinator, message_limit: int = 10) -> None:
    """Print a single snapshot of the dashboard to the console."""
    console = Console()
    console.print(build_agent_table(coordinator))
    console.print()
    console.print(build_messages_table(coordinator._bus.store, limit=message_limit))


def render_live(
    coordinator: Coordinator,
    refresh_per_second: float = 2,
    message_limit: int = 10,
) -> Live:
    """Create a Rich Live context for continuous dashboard updates.

    Usage:
        with render_live(coordinator) as live:
            # run pipeline...
            # dashboard auto-refreshes
    """
    live = Live(
        build_dashboard(coordinator, message_limit),
        refresh_per_second=refresh_per_second,
        transient=True,
    )

    original_get_renderable = live.get_renderable

    def refreshing_renderable():
        return build_dashboard(coordinator, message_limit)

    live.get_renderable = refreshing_renderable  # type: ignore[method-assign]
    return live
