"""Test agent for CLI tests."""

from bus.agent import BaseAgent
from bus.bus import Message
from bus.registry import AgentCapability


class EchoAgent(BaseAgent):
    """Echoes input to output topic."""

    capability = AgentCapability(
        name="echo",
        consumes=["input"],
        produces=["output"],
        description="Echoes messages",
    )

    def __init__(self):
        super().__init__(self.capability)

    def handle(self, message: Message) -> Message:
        return Message(topic="output", payload=message.payload)
