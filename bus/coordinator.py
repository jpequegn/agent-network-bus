"""Coordinator for pipeline execution, fan-out, and tracing."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone

from bus.agent import BaseAgent
from bus.bus import Message, MessageBus


@dataclass
class PipelineResult:
    """Result of running a message through the agent network."""

    root_message_id: str
    trace: list[dict]
    started_at: datetime
    completed_at: datetime
    success: bool
    error: str | None = None

    @property
    def duration_ms(self) -> float:
        delta = self.completed_at - self.started_at
        return delta.total_seconds() * 1000


@dataclass
class NetworkStatus:
    """Snapshot of all agent statuses."""

    agents: list[dict] = field(default_factory=list)
    total_processed: int = 0
    total_errors: int = 0


class Coordinator:
    """Orchestrates pipeline execution across the agent network."""

    def __init__(
        self,
        bus: MessageBus,
        agents: list[BaseAgent] | None = None,
        idle_timeout: float = 1.0,
    ) -> None:
        self._bus = bus
        self._agents: list[BaseAgent] = list(agents) if agents else []
        self._idle_timeout = idle_timeout
        self._activity_lock = threading.Lock()
        self._last_activity: datetime = datetime.now(timezone.utc)
        self._original_publish = bus.publish

        # Wrap bus.publish to track activity
        bus.publish = self._tracked_publish  # type: ignore[method-assign]

    def _tracked_publish(self, message: Message) -> None:
        """Publish wrapper that records activity timestamps."""
        with self._activity_lock:
            self._last_activity = datetime.now(timezone.utc)
        self._original_publish(message)

    def start_agents(self) -> None:
        """Start all registered agents on the bus."""
        for agent in self._agents:
            agent.start(self._bus)

    def stop_agents(self) -> None:
        """Stop all registered agents."""
        for agent in self._agents:
            agent.stop()

    def run_pipeline(self, initial_message: Message) -> PipelineResult:
        """Publish a message and wait for the pipeline to reach a terminal state.

        Terminal state: no new messages published for idle_timeout seconds.
        """
        started_at = datetime.now(timezone.utc)

        try:
            self._bus.publish(initial_message)
            self._wait_for_idle()

            trace = self._bus.store.get_trace(initial_message.id)
            return PipelineResult(
                root_message_id=initial_message.id,
                trace=trace,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                success=True,
            )
        except Exception as e:
            return PipelineResult(
                root_message_id=initial_message.id,
                trace=self._bus.store.get_trace(initial_message.id),
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                success=False,
                error=str(e),
            )

    def fan_out(self, messages: list[Message]) -> list[PipelineResult]:
        """Run multiple messages through the network in parallel."""
        results: list[PipelineResult] = []
        with ThreadPoolExecutor(max_workers=len(messages)) as pool:
            futures = {pool.submit(self.run_pipeline, msg): msg for msg in messages}
            for future in as_completed(futures):
                results.append(future.result())
        return results

    def get_trace(self, root_message_id: str) -> list[dict]:
        """Full chain of messages descended from root."""
        return self._bus.store.get_trace(root_message_id)

    def status(self) -> NetworkStatus:
        """All agents: name, status, queue depth, error rate."""
        agent_statuses = [agent.health_check() for agent in self._agents]
        return NetworkStatus(
            agents=agent_statuses,
            total_processed=sum(a["processed_count"] for a in agent_statuses),
            total_errors=sum(a["error_count"] for a in agent_statuses),
        )

    def _wait_for_idle(self) -> None:
        """Block until no activity for idle_timeout seconds."""
        poll_interval = min(0.01, self._idle_timeout / 10)
        while True:
            time.sleep(poll_interval)
            with self._activity_lock:
                elapsed = (datetime.now(timezone.utc) - self._last_activity).total_seconds()
            if elapsed >= self._idle_timeout:
                break
