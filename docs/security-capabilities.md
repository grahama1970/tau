# Tau Security Capabilities

Phase 2.1 adds deterministic capability compilation for secure DAG runs. This
is separate from Tau's skill capability registry: skill capabilities select a
capability provider, while security capabilities authorize a bounded effect.

## DAG Declaration

Every executable node in secure mode must request `process.execute`. Additional
effects must be declared separately.

```json
{
  "id": "coder",
  "agent": "coder",
  "executor": "local",
  "requested_capabilities": [
    {
      "capability": "process.execute",
      "target": "python3",
      "resource_scope": ["repository"],
      "maximum_effect": {"max_processes": 1}
    }
  ]
}
```

The command policy must contain an exact matching rule:

```json
{
  "schema": "tau.command_spec_policy.v1",
  "capability_grant_ttl_seconds": 300,
  "capability_rules": [
    {
      "capability": "process.execute",
      "targets": ["python3"],
      "resource_scope": ["repository"],
      "maximum_effect": {"max_processes": 1}
    }
  ]
}
```

Targets must match exactly. Requested resource scopes must be a subset of the
rule's scope. `maximum_effect` must match exactly. Network and mutating
capabilities additionally require the existing `allows_network` or
`allows_mutation` command-policy flags.

## Pre-Dispatch Order

Tau compiles capabilities only after security-context, provider-policy,
zero-trust, Memory/evidence, containment, and evidence-manifest gates pass. Any
denied request blocks the complete DAG before command-spec compilation.

Artifacts:

- `capability-decision-receipt.json`
- `capability-requests/<node>/*.json`
- `capability-grants/<node>/*.json`, written only when the complete decision passes

## Phase 2.1 Boundary

`tau.capability_request.v1`, `tau.capability_grant.v1`, and
`tau.capability_decision_receipt.v1` prove deterministic policy compilation and
hash binding. They do not prove runtime enforcement, sandbox isolation, secret
isolation, network isolation, or provider/model quality. Phase 2.2 must make the
secure executor consume and enforce these grants.
