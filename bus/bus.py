"""Core message model and pub-sub bus."""

from __future__ import annotations

import fnmatch
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from bus.store import BusStore

Handler = Callable[["Message"], None]


@dataclass
class Message:
    """A message routed through the bus."""

    topic: str
    payload: dict
    source_agent: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    parent_id: str | None = None


class MessageBus:
    """Pub-sub message bus with dot-notation topic wildcards and SQLite persistence."""

    def __init__(self, store: BusStore | None = None) -> None:
        self._subscribers: dict[str, list[Handler]] = {}
        self._lock = threading.Lock()
        self._store = store or BusStore()

    @property
    def store(self) -> BusStore:
        return self._store

    def subscribe(self, topic: str, handler: Handler) -> None:
        """Subscribe a handler to a topic pattern.

        Supports dot-notation wildcards:
        - 'episode.downloaded' — exact match
        - 'episode.*' — all episode events
        - '*' — all events
        """
        with self._lock:
            self._subscribers.setdefault(topic, []).append(handler)

    def unsubscribe(self, topic: str, handler: Handler) -> None:
        with self._lock:
            handlers = self._subscribers.get(topic, [])
            if handler in handlers:
                handlers.remove(handler)
                if not handlers:
                    del self._subscribers[topic]

    def publish(self, message: Message) -> None:
        """Publish a message. Persists to store and dispatches to matching subscribers."""
        self._store.save(message)

        with self._lock:
            matching_handlers = self._collect_handlers(message.topic)

        for handler in matching_handlers:
            handler(message)

    def get_messages(self, topic: str, limit: int = 100) -> list[dict]:
        """Retrieve persisted messages for a topic."""
        return self._store.get_by_topic(topic, limit)

    def _collect_handlers(self, topic: str) -> list[Handler]:
        """Find all handlers whose subscription pattern matches the given topic."""
        handlers: list[Handler] = []
        for pattern, pattern_handlers in self._subscribers.items():
            if self._topic_matches(pattern, topic):
                handlers.extend(pattern_handlers)
        return handlers

    @staticmethod
    def _topic_matches(pattern: str, topic: str) -> bool:
        """Check if a subscription pattern matches a topic.

        Rules:
        - '*' matches everything
        - 'episode.*' matches 'episode.downloaded', 'episode.transcribed', etc.
        - 'episode.downloaded' matches only 'episode.downloaded'
        """
        if pattern == "*":
            return True
        return fnmatch.fnmatch(topic, pattern)
