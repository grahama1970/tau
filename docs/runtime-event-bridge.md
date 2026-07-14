# Runtime Event Bridge

Tau runtime backends observe persistent execution surfaces. The runtime event
bridge records those observations in the canonical SQLite DAG journal without
giving a backend authority over node completion.

## Contract

`RuntimeEventBridge.wait_and_append()`:

1. Calls the selected backend's bounded `wait_event()` method.
2. Validates the run, backend, and exact endpoint lease binding.
3. Verifies that the backend capabilities hash matches the endpoint lease.
4. Verifies the authoritative run lease before backend I/O.
5. Bounds and redacts backend observation data under one evidence budget.
6. Stores backend transport evidence under `observation.transport`.
7. Appends `runtime_event_appended` and reads its projection in one transaction.
8. Rebuilds `tau.runtime_state_projection.v1` from validated endpoint journal order.

The top-level `tau.runtime_event.v1` schema remains backend-neutral. The
SQLite `dag_run_events.seq` value is the authoritative replay order. A native
backend cursor or sequence is transport evidence used for resumption and
deduplication; it never replaces journal order.

Observation evidence has global node and character budgets in addition to
per-value limits. Sensitive fields are redacted, and Tau omits the complete
payload hash when redaction or truncation would make that hash a guessing
oracle. Opaque stream IDs and cursors that exceed the contract limit are
rejected rather than silently truncated. Runtime event IDs are likewise bounded
and may not contain control characters. The store append primitive is internal;
external callers must pass through the bridge's endpoint and backend binding
checks.

`FrozenJson` canonicalizes mapping order before normalization, so the bridge can
consume only a bounded key prefix without scanning an arbitrary backend mapping.
Projection replay is scoped by the endpoint-bound event-key prefix and validates
every selected row before deriving state.

The durable event key is:

```text
runtime:<endpoint_lease_sha256>:<runtime_event.event_id>
```

The same event ID with the same canonical semantic payload is idempotent.
`observed_at` may differ on repeated polling. Reusing an event ID with changed
state or observation data blocks with `runtime_event_conflict`.

## Completion Boundary

Runtime observations can wake or inform orchestration. They cannot:

- accept a node;
- activate an outgoing edge;
- satisfy a terminal;
- replace a required Tau node receipt.

Terminal text such as `PASS`, `done`, or `tests passed` remains diagnostic
content and is redacted from the journal. A backend may normalize an
unavailable observation as `UNKNOWN`; backend binding, ownership, and contract
errors propagate and block instead of being converted into observations. Tau
does not invent process death or successful completion, and an event returned
after the caller's deadline is not appended.

## Backend Support

The bridge uses the common `RuntimeBackend.wait_event()` contract. Current
Herdr and tmux adapters provide bounded polling and declare
`native_events=false`. A deterministic conformance backend proves that nested
native cursor and sequence evidence can use the same bridge.
Backends that advertise native-event support may emit `mode=poll` while using
their bounded fallback path; only `mode=native` requires the native capability.

Real Herdr AF_UNIX `events.subscribe` transport is tracked separately in issue
`#101` and is not claimed by this implementation.

## Focused Proof

```bash
uv run ruff check \
  src/tau_coding/dag_runtime/run_store.py \
  src/tau_coding/runtime_backends/event_bridge.py \
  tests/test_runtime_event_bridge.py

uv run mypy \
  src/tau_coding/dag_runtime/run_store.py \
  src/tau_coding/runtime_backends/event_bridge.py

uv run pytest \
  tests/test_runtime_event_bridge.py \
  tests/test_runtime_backend_contracts.py \
  tests/test_dag_runtime_run_store.py \
  tests/test_herdr_runtime_backend.py \
  tests/test_tmux_runtime_backend.py -q
```

These checks are deterministic and non-provider-live. They prove contract,
journal, replay, and current backend polling behavior. They do not prove the
future Herdr native subscription transport, provider semantic quality, or node
completion correctness outside Tau's required receipt validators.
