# Typed DAG Routing

Tau's `bounded-ready-queue` scheduler supports a closed route-condition language
over direct typed fields in a completed node's `tau.agent_handoff.v1.result`.
Routes do not evaluate prose, evidence, templates, Python, JSONPath, or nested
objects.

## Contract

Declare one routing mode on the source node:

```json
{
  "id": "reviewer",
  "agent": "reviewer",
  "executor": "local",
  "command_spec": "reviewer/tau-dispatch-command.json",
  "route": {"mode": "exclusive"}
}
```

Declare typed conditions on every outgoing edge from that source:

```json
{
  "from": "reviewer",
  "to": "revise",
  "condition": {
    "schema": "tau.route_condition.v1",
    "op": "eq",
    "field": "verdict",
    "value": "REVISE"
  }
}
```

Supported leaf operators are `eq`, `neq`, `in`, `not_in`, and `exists`.
Compound operators are `all`, `any`, and `not`. Comparisons are strict and do
not coerce strings, numbers, or booleans.

The source result must expose the field directly:

```json
{
  "result": {
    "status": "PASS",
    "verdict": "REVISE",
    "summary": "Untrusted explanatory prose.",
    "evidence": []
  }
}
```

`summary`, `evidence`, `errors`, `artifacts`, `commands_run`,
`policy_exceptions`, and `proof_scope` cannot control routes.

## Modes

| Mode | Selection | Fail-closed case |
| --- | --- | --- |
| `exclusive` | Exactly one matching edge | Zero or multiple matches |
| `first_match` | First matching edge in contract order | Zero matches |
| `fanout` | Every matching edge | Zero matches |
| `all_matching` | All edges, only when every condition matches | Any false condition |

A conditional source without `route` defaults to `exclusive`. A source cannot
mix conditional and unconditional edges. Conditional targets cannot have
multiple predecessors until Tau has an explicit join policy.

## Receipts

Tau writes one deterministic `tau.dag_route_decision.v1` receipt before a
selected successor can start:

```text
<run-dir>/route-decisions/<source-node>/attempt-<NNN>.json
```

The receipt embeds the normalized ordered route contract, referenced source
field projection, per-edge evaluations, selected targets, full source-result
hash, typed-field projection hash, route-contract hash, and decision hash.
Replaying identical inputs yields the same decision payload and hashes. The
typed-field projection is self-verifying; verifying the full source-result hash
also requires the original handoff result. Receipt validation also requires the
trusted DAG ID, goal hash, source node ID, and attempt; a self-rehashed receipt
cannot authorize reuse under a different run context. Unknown receipt fields
are rejected.

The JSON Schema caps route-condition depth at eight and each compound list at
64 children. Tau's runtime normalizer additionally caps the complete condition
tree at 64 objects; that global bound cannot be expressed by standard JSON
Schema alone and remains an authoritative pre-dispatch check.

The receipt proves which typed branch Tau activated. It does not prove the
source result is truthful, the branch will succeed, provider/model quality, or
join and terminal-contribution semantics.

## Fail-Closed Boundaries

Tau blocks before command compilation for arbitrary expressions, invalid
condition objects, mixed edge kinds, conditional virtual sources, and implicit
conditional joins. Typed routes also block under the legacy `handoff-loop`;
they require `--scheduler bounded-ready-queue`.
