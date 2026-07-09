# Persistent Subagent Surfaces

Tau supports persistent visible subagent surfaces as DAG node metadata. This is
for agents that should remain available across bounded Tau ticks, such as the
Embry Chatterbox voice surface at `http://localhost:3002/#embry-voice`.

The DAG remains authoritative. A persistent surface may stay open, keep local UI
state, or remain visible to a human, but it does not become an autonomous loop
and its output does not count without receipts.

## DAG Field

Add `persistent_subagent` to the node that owns the persistent surface:

```json
{
  "id": "embry-chatterbox",
  "agent": "embry-chatterbox-voice",
  "executor": "local",
  "required_evidence": [
    "persistent_subagent_receipt",
    "embry_voice_turn_receipt"
  ],
  "persistent_subagent": {
    "schema": "tau.persistent_subagent.v1",
    "surface_id": "embry-voice",
    "surface_url": "http://localhost:3002/#embry-voice",
    "session_mode": "persistent",
    "tau_control": "bounded_receipt_gated_ticks",
    "dag_parameter": "embry_voice_surface",
    "required_receipts": ["embry.chatterbox_voice_receipt.v1"],
    "unbounded_autonomy_allowed": false,
    "memory_write_requires_receipt": true
  }
}
```

## Validation Rules

Tau rejects a DAG node when:

- `persistent_subagent` is not an object.
- `schema` is not `tau.persistent_subagent.v1`.
- `surface_id`, `surface_url`, `session_mode`, `tau_control`, or
  `dag_parameter` is missing.
- `surface_url` is not a local UX route.
- `session_mode` is not `persistent`.
- `tau_control` is not `bounded_receipt_gated_ticks`.
- `unbounded_autonomy_allowed` is not `false`.
- `required_receipts` is empty.
- the node `required_evidence` does not include
  `persistent_subagent_receipt`.

## Dispatch Behavior

Tau copies the `persistent_subagent` declaration into:

- the compiled node command spec under `tau_dag_node.persistent_subagent`;
- the start handoff context under `context.persistent_subagent`;
- the full node metadata under `context.tau_dag_node.persistent_subagent`.

This makes the persistent surface straightforward for project agents: specify it
in the DAG node, then consume the same object from the command spec or handoff.

## Non-Claims

This contract does not prove that the UI route is reachable, that Embry voice
audio works, that Memory writes are correct, that the subagent is truthful, or
that the task is complete. It proves only that the DAG explicitly declared a
persistent local surface and that Tau propagated the declaration to the bounded
node dispatch path.
