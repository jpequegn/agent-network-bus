"""BaseAgent with lifecycle management and threaded processing."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from bus.registry import AgentCapability

if TYPE_CHECKING:
    from bus.bus import Message, MessageBus

logger = logging.getLogger(__name__)


class BaseAgent:
    """Base class for bus agents. Subclass and override handle()."""

    def __init__(self, capability: AgentCapability) -> None:
        self.capability = capability
        self.status: str = "idle"  # idle | processing | error
        self.processed_count: int = 0
        self.error_count: int = 0
        self.last_processed_at: datetime | None = None
        self._bus: MessageBus | None = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()

    @property
    def name(self) -> str:
        return self.capability.name

    def handle(self, message: Message) -> Message | list[Message] | None:
        """Override this. Process a message and optionally return result(s).

        Return None to consume without publishing.
        Return a Message or list of Messages to publish results.
        """
        raise NotImplementedError

    def start(self, bus: MessageBus) -> None:
        """Subscribe to consumed topics and start processing in a background thread."""
        self._bus = bus
        self._running.set()

        for topic in self.capability.consumes:
            bus.subscribe(topic, self._on_message)

        self._thread = threading.Thread(
            target=self._run_loop, name=f"agent-{self.name}", daemon=True
        )
        self._thread.start()
        logger.info("Agent '%s' started", self.name)

    def stop(self) -> None:
        """Stop the agent and unsubscribe from the bus."""
        self._running.clear()
        if self._bus:
            for topic in self.capability.consumes:
                self._bus.unsubscribe(topic, self._on_message)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self.status = "idle"
        logger.info("Agent '%s' stopped", self.name)

    def health_check(self) -> dict:
        """Return status, counts, last processed time."""
        return {
            "name": self.name,
            "status": self.status,
            "processed_count": self.processed_count,
            "error_count": self.error_count,
            "last_processed_at": self.last_processed_at.isoformat()
            if self.last_processed_at
            else None,
        }

    def _on_message(self, message: Message) -> None:
        """Handler called by the bus. Processes inline (bus dispatches synchronously)."""
        self.status = "processing"
        try:
            result = self.handle(message)
            self.processed_count += 1
            self.last_processed_at = datetime.now(timezone.utc)

            if self._bus:
                self._bus.store.mark_processed(message.id, self.name)

            if result is not None and self._bus:
                results = result if isinstance(result, list) else [result]
                for msg in results:
                    msg.source_agent = self.name
                    if msg.parent_id is None:
                        msg.parent_id = message.id
                    self._bus.publish(msg)

            self.status = "idle"
        except Exception:
            self.error_count += 1
            self.status = "error"
            logger.exception("Agent '%s' failed processing message %s", self.name, message.id)

    def _run_loop(self) -> None:
        """Background thread that keeps the agent alive."""
        self._running.wait()
