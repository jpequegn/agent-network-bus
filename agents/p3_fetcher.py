"""P3 Fetcher Agent — fetches episodes from RSS feeds.

Consumes: episode.requested
Produces: episode.downloaded
"""

from __future__ import annotations

import random
import time

from bus.agent import BaseAgent
from bus.bus import Message
from bus.registry import AgentCapability


class P3FetcherAgent(BaseAgent):
    """Simulates fetching an episode from an RSS feed."""

    capability = AgentCapability(
        name="p3_fetcher",
        consumes=["episode.requested"],
        produces=["episode.downloaded"],
        description="Fetches podcast episodes from RSS feeds",
    )

    def __init__(self) -> None:
        super().__init__(self.capability)

    def handle(self, message: Message) -> Message:
        episode_id = message.payload.get("episode_id", 0)
        # Simulate network fetch (50-150ms)
        time.sleep(random.uniform(0.05, 0.15))
        return Message(
            topic="episode.downloaded",
            payload={
                "episode_id": episode_id,
                "title": f"Episode {episode_id}",
                "audio_path": f"/tmp/episodes/ep_{episode_id}.mp3",
                "duration_seconds": random.randint(1200, 3600),
            },
        )
