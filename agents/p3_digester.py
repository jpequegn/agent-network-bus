"""P3 Digester Agent — generates summaries from transcripts.

Consumes: episode.transcribed
Produces: episode.digested
"""

from __future__ import annotations

import random
import time

from bus.agent import BaseAgent
from bus.bus import Message
from bus.registry import AgentCapability


class P3DigesterAgent(BaseAgent):
    """Simulates generating a summary/digest from a transcript."""

    capability = AgentCapability(
        name="p3_digester",
        consumes=["episode.transcribed"],
        produces=["episode.digested"],
        description="Generates summaries from transcripts",
    )

    def __init__(self) -> None:
        super().__init__(self.capability)

    def handle(self, message: Message) -> Message:
        episode_id = message.payload.get("episode_id", 0)
        # Simulate LLM summarization (100-300ms)
        time.sleep(random.uniform(0.1, 0.3))
        return Message(
            topic="episode.digested",
            payload={
                "episode_id": episode_id,
                "title": message.payload.get("title", ""),
                "summary": f"Summary of episode {episode_id}: key insights and takeaways.",
                "key_topics": [f"topic_{i}" for i in range(random.randint(3, 7))],
            },
        )
