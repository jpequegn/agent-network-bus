"""P3 Writer Agent — generates blog posts from digests.

Consumes: episode.digested
Produces: blog.draft_ready
"""

from __future__ import annotations

import random
import time

from bus.agent import BaseAgent
from bus.bus import Message
from bus.registry import AgentCapability


class P3WriterAgent(BaseAgent):
    """Simulates generating a blog post from a digest."""

    capability = AgentCapability(
        name="p3_writer",
        consumes=["episode.digested"],
        produces=["blog.draft_ready"],
        description="Generates blog posts from episode digests",
    )

    def __init__(self) -> None:
        super().__init__(self.capability)

    def handle(self, message: Message) -> Message:
        episode_id = message.payload.get("episode_id", 0)
        # Simulate blog generation (100-250ms)
        time.sleep(random.uniform(0.1, 0.25))
        return Message(
            topic="blog.draft_ready",
            payload={
                "episode_id": episode_id,
                "title": f"Blog: {message.payload.get('title', '')}",
                "blog_path": f"/tmp/blogs/ep_{episode_id}.md",
                "word_count": random.randint(800, 2000),
            },
        )
