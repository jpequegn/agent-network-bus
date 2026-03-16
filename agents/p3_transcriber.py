"""P3 Transcriber Agent — transcribes audio to text.

Consumes: episode.downloaded
Produces: episode.transcribed
"""

from __future__ import annotations

import random
import time

from bus.agent import BaseAgent
from bus.bus import Message
from bus.registry import AgentCapability


class P3TranscriberAgent(BaseAgent):
    """Simulates transcribing audio. Slowest stage in the pipeline."""

    capability = AgentCapability(
        name="p3_transcriber",
        consumes=["episode.downloaded"],
        produces=["episode.transcribed"],
        description="Transcribes podcast audio to text",
    )

    def __init__(self) -> None:
        super().__init__(self.capability)

    def handle(self, message: Message) -> Message:
        episode_id = message.payload.get("episode_id", 0)
        duration = message.payload.get("duration_seconds", 1800)
        # Simulate transcription (200-500ms — the bottleneck)
        time.sleep(random.uniform(0.2, 0.5))

        # Simulate occasional transcription errors (5% failure rate)
        if random.random() < 0.05:
            raise RuntimeError(f"Transcription failed for episode {episode_id}: audio corruption")

        word_count = duration * 2  # ~2 words per second
        return Message(
            topic="episode.transcribed",
            payload={
                "episode_id": episode_id,
                "title": message.payload.get("title", ""),
                "transcript_path": f"/tmp/transcripts/ep_{episode_id}.txt",
                "word_count": word_count,
            },
        )
