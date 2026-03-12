# Agent Network Bus — Implementation Plan

## What We're Building

A minimal multi-agent coordination protocol where agents register capabilities, publish results to a shared bus, and subscribe to each other's outputs. The bus handles routing, failure isolation, and emergent network behavior — without tight coupling between agents.

## Why This Matters

Every major AI lab (NVIDIA, Anthropic, Google) is now talking about *networks* of agents, not single agents. But what does that actually mean in practice? Most implementations hard-code agent dependencies. The real insight from NVIDIA's Dynamo and the AI Breakdown's "agent networks" framing is simpler: agents should be loosely coupled via a pub-sub bus. Each agent declares what it produces and what it consumes. The bus routes everything.

Three problems this solves:
1. **Before this**: agents are orchestrated by hand — brittle, hard to extend
2. **With the bus**: add a new agent by registering its capabilities, no other code changes
3. **Emergent behavior**: chains of agents self-assemble from capability declarations

The P³ pipeline is the perfect test case: fetch → transcribe → digest → write are 4 agents that currently run as a monolithic CLI. With the bus, they become pluggable, observable, and independently scalable.

## Architecture

```
bus/
├── __init__.py
├── registry.py      # AgentRegistry: register, discover, capability graph
├── bus.py           # MessageBus: publish, subscribe, route
├── agent.py         # BaseAgent: lifecycle, capability declaration, message handling
├── coordinator.py   # Coordinator: task decomposition, agent selection, fan-out
├── store.py         # BusStore: SQLite persistence for messages and routing history
├── renderer.py      # Rich terminal: live view of agent network activity
└── cli.py           # `bus run`, `bus status`, `bus inspect`, `bus graph`

agents/
├── p3_fetcher.py    # Fetches episodes from RSS (wraps P³ downloader)
├── p3_transcriber.py # Transcribes audio (wraps P³ transcriber)
├── p3_digester.py   # Generates summaries (wraps P³ cleaner)
├── p3_writer.py     # Generates blog posts (wraps P³ writer)
└── topic_extractor.py # Extracts and clusters topics across episodes

tests/
├── test_registry.py
├── test_bus.py
└── test_coordinator.py

pyproject.toml
README.md
```

## Core Concept: Capability-Driven Routing

```python
@agent(
    consumes=["episode.downloaded"],
    produces=["episode.transcribed"],
    name="transcriber"
)
class TranscriberAgent(BaseAgent):
    def handle(self, message: Message) -> Message:
        episode_id = message.payload["episode_id"]
        # ... transcription logic
        return Message(topic="episode.transcribed", payload={"episode_id": episode_id})

# The bus figures out the rest:
bus.publish(Message(topic="episode.downloaded", payload={"episode_id": 42}))
# → TranscriberAgent automatically receives and processes it
# → Publishes episode.transcribed
# → DigestAgent (subscribed to episode.transcribed) receives it
# → Chain continues without any orchestration code
```

## Implementation Phases

### Phase 1: Message model (bus.py)

```python
@dataclass
class Message:
    id: str                  # UUID
    topic: str               # "episode.downloaded", "episode.transcribed", etc.
    payload: dict            # Arbitrary JSON payload
    source_agent: str        # Which agent published this
    created_at: datetime
    parent_id: str | None    # For tracing chains

class MessageBus:
    def publish(self, message: Message) -> None
    def subscribe(self, topic: str, handler: Callable) -> None
    def unsubscribe(self, topic: str, handler: Callable) -> None
    def get_messages(self, topic: str, limit: int = 100) -> list[Message]
```

Topics use dot-notation with wildcards:
- `episode.downloaded` — specific event
- `episode.*` — all episode events
- `*` — all events (for monitoring)

### Phase 2: Agent registry (registry.py)

```python
@dataclass
class AgentCapability:
    name: str
    consumes: list[str]      # Topics this agent handles
    produces: list[str]      # Topics this agent emits
    description: str
    max_concurrent: int = 1  # Parallelism limit

class AgentRegistry:
    def register(self, agent: BaseAgent) -> None
    def discover(self, topic: str) -> list[BaseAgent]
        """Find all agents capable of handling this topic."""

    def capability_graph(self) -> nx.DiGraph
        """Directed graph: topic → agent → topic chains."""

    def validate(self) -> list[str]
        """Check for: orphaned topics (produced but never consumed),
        dead ends (consumed but never produced), cycles."""
```

### Phase 3: Base agent (agent.py)

```python
class BaseAgent:
    capability: AgentCapability
    status: str              # idle | processing | error
    processed_count: int
    error_count: int

    def handle(self, message: Message) -> Message | list[Message] | None:
        """Override this. Return None to consume without publishing."""

    def start(self, bus: MessageBus) -> None
        """Subscribe to consumed topics, begin processing loop."""

    def stop(self) -> None

    def health_check(self) -> dict
        """Return status, counts, last processed time."""
```

Agents run in threads (one thread per agent). The `start()` method subscribes to the bus and enters a blocking loop.

### Phase 4: Coordinator (coordinator.py)

```python
class Coordinator:
    def run_pipeline(self, initial_message: Message) -> PipelineResult:
        """Publish message, wait for terminal state, collect trace."""

    def fan_out(self, messages: list[Message]) -> list[PipelineResult]:
        """Run multiple messages in parallel through the network."""

    def get_trace(self, root_message_id: str) -> list[Message]:
        """Full chain of messages descended from root."""

    def status(self) -> NetworkStatus:
        """All agents: name, status, queue depth, error rate."""
```

Terminal state detection: a pipeline is "done" when no agent has processed a message for N seconds (configurable idle timeout).

### Phase 5: SQLite persistence (store.py)

```sql
CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    topic TEXT,
    payload JSON,
    source_agent TEXT,
    parent_id TEXT,
    created_at TIMESTAMP,
    processed_by TEXT,
    processed_at TIMESTAMP
);

CREATE TABLE agent_stats (
    agent_name TEXT,
    processed_count INTEGER,
    error_count INTEGER,
    avg_latency_ms REAL,
    last_active TIMESTAMP
);
```

### Phase 6: Terminal renderer (renderer.py)

```
Agent Network Bus — P³ Pipeline
─────────────────────────────────────────────────────
Agent              Status       Processed  Errors  Queue
p3_fetcher         ✓ idle       12         0       0
p3_transcriber     🔄 running   11         0       1
p3_digester        ⏳ idle       10         0       0
p3_writer          ⏳ idle       3          0       0
topic_extractor    ✓ idle       10         0       0

Recent Messages
  12:04:01  episode.downloaded     → p3_transcriber  [ep:1152]
  12:04:01  episode.transcribed    → p3_digester     [ep:1151]
  12:03:58  episode.processed      → topic_extractor [ep:1150]
  12:03:55  blog.draft_ready       → (terminal)      [ep:1149]
```

### Phase 7: CLI

```bash
# Start the bus with all registered agents
bus run --agents agents/

# Show network topology as ASCII graph
bus graph
# episode.downloaded → transcriber → episode.transcribed
#                                   → digester → episode.processed
#                                               → writer → blog.draft_ready
#                                               → topic_extractor

# Inject a test message
bus publish episode.downloaded '{"episode_id": 1152}'

# Inspect message trace
bus inspect <message_id>

# Show live status
bus status --watch
```

### Phase 8: P³ pipeline integration

Re-implement the P³ pipeline as a bus of agents. Run 10 episodes through the bus and compare:
- Wall time vs sequential CLI
- Error isolation (one agent crash doesn't kill the pipeline)
- Observability (full message trace per episode)

Write `NETWORK_FINDINGS.md` documenting emergent behaviors observed: out-of-order processing, bottleneck agents, failure cascades.

## Key Design Decisions

**Why pub-sub over direct RPC between agents?**
RPC requires agent A to know about agent B. Pub-sub means agent A only knows about the *topic* it publishes. Adding a new agent that processes the same topic requires zero changes to existing agents. This is loose coupling at the protocol level.

**Why SQLite for message persistence, not in-memory?**
In-memory queues lose messages on crash. SQLite gives you the full message trace for debugging and replaying failures — without requiring a message broker like Kafka. This is the same tradeoff as durable-workflow.

**Why threads over asyncio?**
Agents wrap existing synchronous code (transcribers, LLM calls). Threads let you reuse that code without conversion. Asyncio is the right follow-on for pure I/O agents.

**What we're NOT building**
- Distributed agents across machines (single-process only for now)
- Message ordering guarantees (best-effort delivery)
- Back-pressure / rate limiting (follow-on)
- Dead letter queues (failed messages just increment error_count)

## Acceptance Criteria

1. P³ pipeline runs end-to-end through the bus: fetch → transcribe → digest → write
2. `bus graph` shows correct capability topology with no orphaned topics
3. Kill one agent mid-run: other agents continue processing queued messages
4. `bus inspect <id>` shows complete message trace for any episode
5. `NETWORK_FINDINGS.md` documents at least 2 emergent behaviors from real runs

## Learning Outcomes

After building this you will understand:
- Why pub-sub is the right primitive for loose agent coupling (and when it's not)
- What "emergent network behavior" actually means in a multi-agent system
- How capability declarations replace orchestration code
- Why message tracing is essential for debugging agent networks
- The difference between fan-out parallelism and sequential pipelines (and when each wins)
