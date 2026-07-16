# DAG Viewer Causal Replay Requirements

## Immutable Goal

Tau's DAG viewer is a read-only, journal-authoritative workbench for answering:

> Why is this run in its current state, what Tau-authoritative evidence caused
> it, and what requires human attention next?

The workbench may adopt useful trace-inspection patterns from LangSmith and
LangGraph, but it must not introduce another scheduler, reducer, event store,
or authority plane.

## Authority Boundary

- The immutable source DAG and compiled `DagPlan` define topology.
- The verified SQLite journal prefix defines execution state.
- Scheduler and viewer replay share one reduction implementation.
- The browser replaces complete Tau-authored snapshots.
- Runtime observations remain diagnostic and cannot admit a node.
- Historical inspection never dispatches, retries, forks, resumes, approves,
  cancels, terminates, cleans up, or mutates a run.
- Viewer HTTP remains loopback-only and GET-only.

## Shared Event-Prefix Replay Contract

Historical replay must not combine a truncated event stream with current
mutable rows from `dag_runs`, `dag_node_attempts`, attempt outputs, or runtime
projection queries. Those rows may be cross-checked at the journal head, but
they are never historical inputs.

The sole read entrypoint is owned by `tau_coding.dag_runtime.replay`:

```python
def replay_dag_run_at_sequence(
    reader: SqliteDagRunReader,
    run_id: str,
    at_sequence: int | None,
) -> HistoricalReplayResult:
    ...
```

`HistoricalReplayResult` contains:

```text
replay
events
selected_sequence
selected_event_created_at
head_sequence
view_mode: LIVE | HISTORICAL
```

Required semantics:

1. `None` selects the verified run-local journal head.
2. An integer must identify an exact committed event for the selected run.
3. Journal sequences are database-global and may be non-contiguous per run.
4. The function verifies and reduces only the prefix ending at the selected
   event.
5. Run status, verdict, lease state, attempts, outputs, runtime projections,
   corrections, transitions, deadlines, and receipt references are derived
   from that prefix.
6. The existing transition reduction remains shared with scheduler restore.
7. Viewer projection formats the returned state but never interprets journal
   events independently.
8. Latest and historical viewer requests call this same entrypoint.
9. Manifest and receipt indexes include only references committed by the
   selected sequence.
10. A gap, future sequence, another-run sequence, duplicate parameter, unknown
    parameter, or invalid event blocks closed.

`at_sequence=N` means the logical Tau journal prefix ending at exact run-local
event sequence `N`. It does not claim that `N` is an independent SQLite
transaction boundary.

## Historical HTTP Contract

```text
GET /api/v1/state
GET /api/v1/state?at_sequence=<exact-run-local-sequence>
GET /api/v1/manifest?at_sequence=<exact-run-local-sequence>
GET /api/v1/receipts/<receipt-id>?at_sequence=<exact-run-local-sequence>
```

The snapshot view contains:

```text
mode: LIVE | HISTORICAL
sequence
sequence_created_at
head_sequence (response metadata only for historical hash purposes)
```

Rules:

- Historical hashes exclude moving journal-head metadata.
- `ETag` is the snapshot hash.
- The current head is returned as `X-Tau-Journal-Head-Sequence`.
- A historical manifest excludes receipts first committed later.
- A historical receipt request rejects receipts first committed later.
- Source DAG and `DagPlan` hashes are identical at every sequence.

## Browser Response Binding

- The URL owns the selected mode and sequence.
- Every response echoes its mode and selected sequence.
- The browser accepts a response only when it matches the current URL
  selection and request generation.
- Live and historical ETags are tracked separately.
- A pending live poll cannot overwrite a newly selected historical snapshot.
- Historical mode is frozen across refresh.
- Returning to live is explicit and resumes full-snapshot polling.
- The sequence navigator uses actual run-local event IDs, not integer
  increment/decrement assumptions.

## Directly Useful Adopted Features

1. Exact-sequence historical inspection without executable time travel.
2. Causal explanations for run, node, edge, terminal, route, join, attempt,
   correction, and attention subjects.
3. Populated route and join projections from committed decisions and receipts.
4. Deterministic attention ordering from Tau-authored blocked, uncertain,
   approval, exhausted, reconciliation, and stale states.
5. Bounded server-side filtering over redacted IDs, codes, schemas, states, and
   compact previews.
6. Exactly-two comparison for sequence pairs, same-node attempt pairs, and
   correction before/after.
7. Timing, token, and cost values only when explicitly recorded; otherwise
   `NOT_RECORDED`.

## Delivery Order

### PR 1: Historical Replay Vertical Slice

Implement the shared event-prefix replay API, exact-sequence HTTP contract,
sequence-bounded receipt access, and minimal LIVE/HISTORICAL browser controls.

Stop condition:

For one live self-healing run, every actual run-local journal event sequence
produces a deterministic, hash-stable, prefix-bounded snapshot through the
single shared replay entrypoint. No snapshot contains later attempt, runtime,
correction, transition, or receipt state. The packaged browser preserves a
frozen sequence across refresh, rejects stale live-poll responses, returns to
live explicitly, performs GET requests only, acquires no lease, writes no
SQLite or files, and performs no frontend event reduction.

### PR 2: Causal Explanations, Route/Join, Attention

Add bounded causal explanations, committed route/join projections, and a
deterministically ordered read-only attention rail.

### PR 3: Filter and Exactly-Two Comparison

Add bounded server-side query and same-run comparison contracts over
whitelisted redacted projections.

### PR 4: Integrated Operator Workbench

Synchronize graph, timeline, sequence navigation, causal inspector, attention,
filters, and comparison. Prove desktop and mobile rendering from an installed
wheel without Node at runtime.

## Explicit Exclusions

This goal excludes cross-run dashboards, alerts, webhooks, hosted observability,
sharing, mutable annotations, evaluation datasets, prompt/model experiments,
OpenTelemetry export, browser mutations, executable replay, fork/resume,
frontend event reduction, inferred progress, raw prompts, hidden reasoning,
credentials, unbounded output, and estimated timing/token/cost values.

## Sources

- Tau main `26839406e9f962dfa263393000740c97f3031b38`
- LangSmith Studio: <https://docs.langchain.com/langsmith/studio>
- LangSmith Observability: <https://docs.langchain.com/langsmith/observability>
- LangGraph Observability: <https://docs.langchain.com/oss/python/langgraph/observability>
- LangGraph Persistence: <https://docs.langchain.com/oss/python/langgraph/persistence>
- LangGraph Time Travel: <https://docs.langchain.com/oss/python/langgraph/use-time-travel>

## Non-Claims

The workbench does not prove agent truthfulness, semantic correctness, provider
quality, legal compliance, human approval, or future route correctness. It
proves only the bounded Tau-authored projection and evidence relationships that
its deterministic validators and browser proof actually exercise.
