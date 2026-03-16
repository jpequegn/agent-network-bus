"""SQLite persistence for bus messages."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bus.bus import Message


_SCHEMA = """\
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    payload JSON NOT NULL,
    source_agent TEXT NOT NULL,
    parent_id TEXT,
    created_at TIMESTAMP NOT NULL,
    processed_by TEXT,
    processed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_messages_topic ON messages(topic);
CREATE INDEX IF NOT EXISTS idx_messages_parent_id ON messages(parent_id);
CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);
"""


class BusStore:
    """SQLite-backed message store for durability and tracing."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def save(self, message: Message) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO messages "
            "(id, topic, payload, source_agent, parent_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                message.id,
                message.topic,
                json.dumps(message.payload),
                message.source_agent,
                message.parent_id,
                message.created_at.isoformat(),
            ),
        )
        self._conn.commit()

    def mark_processed(self, message_id: str, agent_name: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE messages SET processed_by = ?, processed_at = ? WHERE id = ?",
            (agent_name, now, message_id),
        )
        self._conn.commit()

    def get_by_topic(self, topic: str, limit: int = 100) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE topic = ? ORDER BY created_at DESC LIMIT ?",
            (topic, limit),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_by_id(self, message_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_trace(self, root_id: str) -> list[dict]:
        """Get the full chain of messages descended from root_id."""
        result = []
        queue = [root_id]
        while queue:
            current_id = queue.pop(0)
            msg = self.get_by_id(current_id)
            if msg:
                result.append(msg)
            children = self._conn.execute(
                "SELECT id FROM messages WHERE parent_id = ? ORDER BY created_at",
                (current_id,),
            ).fetchall()
            queue.extend(row["id"] for row in children)
        return result

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        if isinstance(d.get("payload"), str):
            d["payload"] = json.loads(d["payload"])
        return d
