# Memory/Evidence-Case Gate

Tau does not trust an agent DAG, a swarm, a reviewer agent, or a consensus of
agents. A DAG is useful only as a containment map: it names who may act, what
they may touch, what evidence they must produce, which routes are allowed, and
which conditions must block.

The memory/evidence-case gate makes this rule executable for zero-trust DAGs:

```text
agent output = untrusted claim
DAG = allowed claim path
Memory intent = pre-dispatch routing claim
evidence case = structured support claim
Tau gate = narrow validator
```

The gate does not make Memory true. It checks whether Graph Memory products are
present, shaped correctly, and compatible with the active policy and data
boundary before Tau dispatches a high-stakes DAG.

## Pipeline

For DAGs with a `policy_profile`, Tau runs pre-dispatch gates in this order:

```text
zero_trust_preflight
-> memory_intent/evidence_case_gate
-> evidence_manifest_preflight
-> command_policy_dispatch
```

Legacy DAGs without a `policy_profile` keep legacy behavior. Memory intent is
required only when the policy profile opts in with `memory.intent_required:true`
or an evidence case is otherwise required by policy/intent.

## Policy Fields

`tau.policy_profile.v1` may include optional memory gate controls:

```json
{
  "memory": {
    "read": "allow",
    "write": "approval_required",
    "intent_required": true,
    "evidence_case_required_for": ["COMPLIANCE", "RESEARCH", "SUBAGENT"],
    "min_intent_confidence": 0.75,
    "clarify_blocks_dispatch": true,
    "deflect_blocks_dispatch": true
  }
}
```

These fields are dispatch policy. They do not authorize Memory writes, side
effects, provider use, or external research.

## DAG Fields

A zero-trust DAG can pass inline objects or contract-relative JSON paths:

```json
{
  "policy_profile": "policy-profile.json",
  "data_boundary": "data-boundary.json",
  "memory_intent": "memory-intent.json",
  "evidence_case": "evidence-case.json"
}
```

`memory_intent` is expected to be a planner-only Graph Memory intent product:

```json
{
  "schema": "memory.intent.v1",
  "memory_first": true,
  "planner_only": true,
  "route": "COMPLIANCE",
  "confidence": 0.91,
  "recall_profile": "proof_retrieval",
  "required_artifacts": [],
  "tool_calls": [{"name": "create_evidence_case"}],
  "evidence_case_required": true
}
```

`evidence_case` is expected to be a separate create-evidence-case product:

```json
{
  "schema": "memory.evidence_case.v1",
  "source": "graph-memory-operator:/create-evidence-case",
  "sha256": "sha256:...",
  "question": "...",
  "data_boundary": {"schema": "tau.data_boundary.v1"},
  "policy_profile": {"schema": "tau.policy_profile.v1"}
}
```

## Block Conditions

Tau blocks pre-dispatch when any of these are present:

```text
missing_memory_intent
invalid_memory_intent_schema
memory_first_not_true
intent_not_planner_only
intent_clarify_required
intent_deflected
intent_confidence_missing
intent_confidence_too_low
intent_contains_inline_evidence
missing_evidence_case
invalid_evidence_case_schema
evidence_case_hash_missing
evidence_case_boundary_mismatch
evidence_case_policy_mismatch
```

Inline evidence inside `/intent` is rejected. `/intent` is a planner/routing
product; evidence must come from `/create-evidence-case`.

## Receipts

The gate writes:

```text
memory-intent-gate-receipt.json
evidence-case-gate-receipt.json
```

Schemas:

```text
tau.memory_intent_gate_receipt.v1
tau.evidence_case_gate_receipt.v1
```

Blocked DAG receipts include these receipt paths and a `tau.dag_error.v1`
course-correction with:

```json
{
  "type": "repair_memory_evidence_gate",
  "next_agent": "goal-guardian"
}
```

## Proof Scope

This gate proves only that Tau inspected Graph Memory intent and evidence-case
artifacts before dispatch and blocked incompatible artifacts in the tested
paths.

It does not prove:

```text
Memory facts are true
the evidence case is sufficient for closure
ITAR compliance
export-control legal sufficiency
runtime sandbox enforcement
human identity verification
provider/model semantic quality
swarm or DAG trustworthiness
```
