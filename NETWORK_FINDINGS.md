# Network Findings — P3 Pipeline on Agent Network Bus

## Run Summary

| Metric | Sequential | Fan-out |
|--------|-----------|---------|
| Episodes | 10 | 10 |
| Total time | 19979ms | 2141ms |
| Speedup | 1.0x | 9.3x |
| Errors | 0 | 0 |
| Total processed | 96 | - |
| Total errors | 1 | - |

## Pipeline Topology

```
episode.requested → p3_fetcher → episode.downloaded
                                  → p3_transcriber → episode.transcribed
                                                      → p3_digester → episode.digested
                                                      |                → p3_writer → blog.draft_ready
                                                      → topic_extractor → topics.extracted
```

## Emergent Behaviors Observed

### 1. Out-of-Order Processing

When running episodes in fan-out mode, later episodes frequently complete before earlier
ones. This happens because the transcriber (the bottleneck) processes messages in arrival
order, but random timing variations mean episode 7 might finish transcription before
episode 3. Downstream agents (digester, writer) then process whatever arrives first.

**Evidence**: In fan-out results, completion order does not match submission order.
This is expected and correct behavior — the bus makes no ordering guarantees.

### 2. Bottleneck Agent: Transcriber

The transcriber consistently has the highest average latency across all agents:

| Agent | Avg Latency |
|-------|-------------|
| p3_digester | 195.1ms |
| p3_fetcher | 104.9ms |
| p3_transcriber | 358.8ms |
| p3_writer | 173.9ms |
| topic_extractor | 111.5ms |

The transcriber at ~350ms average is the pipeline bottleneck. All downstream agents
(digester, writer, topic_extractor) idle while waiting for transcription to complete.
In a production system, this agent would benefit from `max_concurrent > 1` to allow
parallel transcription.

### 3. Fan-Out at episode.transcribed

The `episode.transcribed` topic triggers two independent processing paths:
1. `p3_digester → p3_writer → blog.draft_ready` (content pipeline)
2. `topic_extractor → topics.extracted` (analytics pipeline)

This fan-out happens automatically — neither agent knows about the other. Adding a
third consumer (e.g., a sentiment analyzer) requires zero changes to existing agents.
This is the core benefit of pub-sub over direct RPC.

### 4. Error Isolation

The transcriber has a 5% simulated failure rate. When it fails:
- The failed message is recorded with error status in the store
- Other agents continue processing their queues unaffected
- Episodes that were already past transcription complete normally
- No cascade failure — the bus isolates errors per-agent

**Error count**: 1 total errors across 5 agents.

### 5. Message Tracing

Every message carries a `parent_id` linking it to its predecessor. This creates a
complete trace tree from `episode.requested` through every processing stage. The
`bus inspect-msg <id>` command can reconstruct the full processing history for any
episode, making debugging straightforward even in fan-out scenarios.

## Key Metrics

- **Sequential wall time**: 19979ms for 10 episodes
- **Fan-out wall time**: 2141ms for 10 episodes
- **Speedup**: 9.3x
- **Bottleneck**: p3_transcriber (358.8ms avg)
- **Pipeline depth**: 5 stages (requested → downloaded → transcribed → digested → draft_ready)
- **Fan-out width**: 2 at transcribed (digester + topic_extractor)

## Conclusions

1. **Pub-sub enables zero-config fan-out**: Adding topic_extractor alongside digester required no changes to the transcriber
2. **Error isolation works**: Agent failures don't cascade through the pipeline
3. **Parallelism via fan-out is significant**: 9.3x speedup from running episodes concurrently
4. **The bottleneck is visible**: Agent stats immediately identify p3_transcriber as the constraint
5. **Message tracing makes debugging tractable**: Full parent_id chains through all processing stages
