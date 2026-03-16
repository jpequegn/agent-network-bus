"""Topic Extractor Agent — extracts and clusters topics across episodes.

Consumes: episode.transcribed
Produces: topics.extracted

Note: This agent consumes the same topic as p3_digester (episode.transcribed),
demonstrating fan-out — a single message triggers two independent processing paths.
"""

from __future__ import annotations

import random
import time

from bus.agent import BaseAgent
from bus.bus import Message
from bus.registry import AgentCapability


class TopicExtractorAgent(BaseAgent):
    """Simulates topic extraction and clustering from transcripts."""

    capability = AgentCapability(
        name="topic_extractor",
        consumes=["episode.transcribed"],
        produces=["topics.extracted"],
        description="Extracts and clusters topics across episodes",
    )

    def __init__(self) -> None:
        super().__init__(self.capability)

    def handle(self, message: Message) -> Message:
        episode_id = message.payload.get("episode_id", 0)
        # Simulate topic extraction (50-150ms)
        time.sleep(random.uniform(0.05, 0.15))

        topics = [
            {"name": f"topic_{i}", "score": round(random.uniform(0.5, 1.0), 2)}
            for i in range(random.randint(2, 5))
        ]
        return Message(
            topic="topics.extracted",
            payload={
                "episode_id": episode_id,
                "topics": topics,
                "cluster_count": len(set(t["name"] for t in topics)),
            },
        )
