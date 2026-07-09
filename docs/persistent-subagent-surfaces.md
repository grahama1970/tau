# Persistent Subagent Surfaces

Tau supports persistent visible subagent surfaces as DAG node metadata. This is
for project agents that need a local UI route, service room, Herdr pane,
listener, or worker surface to remain available across bounded Tau ticks.

The DAG remains authoritative. A persistent surface may stay open, keep local UI
state, or remain visible to a human, but it does not become an autonomous loop
and its output does not count without receipts.

## DAG Field

Add `persistent_subagent` to the node that owns the persistent surface:

```json
{
  "id": "persistent-worker",
  "agent": "domain-persistent-subagent",
  "executor": "local",
  "command_spec": "agent-command-specs/persistent-worker/tau-dispatch-command.json",
  "required_evidence": [
    "persistent_subagent_receipt",
    "domain_turn_receipt"
  ],
  "persistent_subagent": {
    "schema": "tau.persistent_subagent.v1",
    "surface_id": "domain-surface",
    "surface_url": "http://localhost:3002/#domain-surface",
    "session_mode": "persistent",
    "tau_control": "bounded_receipt_gated_ticks",
    "dag_parameter": "persistent_surface",
    "required_receipts": ["domain.turn_receipt.v1"],
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

## Project-Agent Recipe

1. Pick the already-working local proof command for the first rung.
2. Wrap that command in a Tau `command_spec`.
3. Add `nodes[].persistent_subagent` to the same DAG node.
4. Require `persistent_subagent_receipt` plus the domain receipt in
   `nodes[].required_evidence`.
5. Keep the command bounded with `timeout_s`, `--max-turns`, `--timeout`, or an
   equivalent one-tick limit.
6. Treat the visible surface as context only. The receipts decide whether the
   output counts.

Do not move domain routing into Tau just because the surface is persistent. If
an existing skill owns the live proof command, Tau should invoke that skill as a
bounded local node, bind the outputs to the DAG, and validate the resulting
receipts.

## Worked Example: Embry Voice

Embry voice uses a persistent local Chat UX route:

```text
http://localhost:3002/#embry-voice
```

The first rung should wrap the existing static query proof command:

```bash
/home/graham/workspace/experiments/agent-skills/skills/embry-voice-control/run.sh \
  embry-chat-static-query-live --play-local --local-playback-target 64
```

For that node, require:

- `persistent_subagent_receipt`
- `embry_chat_turn_receipt.v1`
- `chatterbox_audio_receipt`
- `pipewire_playback_receipt`

The first rung should not require the browser route to be reachable unless the
task is specifically the Chat UX/orb/replay proof rung.

## Non-Claims

This contract does not prove that the UI route is reachable, that audio works,
that Memory writes are correct, that the subagent is truthful, that provider
output is semantically correct, or that the task is complete. It proves only
that the DAG explicitly declared a persistent local surface and that Tau
propagated the declaration to the bounded node dispatch path.
