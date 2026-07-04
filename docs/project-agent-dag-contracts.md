# Project-Agent DAG Contracts

For multi-step project-agent work, the default interface to Tau is a DAG
contract. Direct `tau.agent_handoff.v1` packets remain the node-level protocol
and are still acceptable for trivial one-step work, but creator/reviewer loops,
repair loops, provider work, goal-guardian gates, reviewer joins, and any
workflow with retry or iteration policy should be represented as a DAG.

The DAG contract is the durable instruction. Tau owns dispatch, receipt
validation, route continuity, resume behavior, timeout and max-attempt handling,
immutable-goal enforcement, and fail-closed drift detection.

```text
project agent -> tau.dag_contract.v1
Tau -> node-level tau.agent_handoff.v1 turns + receipts
subagents -> artifacts + receipts
Tau -> DAG receipt/verdict
```

## Contract Minimum

Every project-agent DAG contract should include:

- `schema`: `tau.dag_contract.v1`
- `dag_id`: stable workflow id for receipts and resume
- `goal.goal_id`, `goal.goal_version`, and `goal.goal_hash`
- `target`: repo, issue/PR/artifact, or local target scope
- `nodes`: bounded subagent, provider, or human steps
- `edges`: allowed route graph
- `entry_node`: first node to dispatch
- `terminal_nodes`: usually `human`, `releaser`, or an explicit blocked node
- `limits`: max attempts, timeouts, and whether resume is allowed
- `required_evidence`: DAG-level proof requirements
- `fail_closed_on`: invariant violations Tau must block

## Examples

Checked-in YAML examples live under `docs/examples/dag-contracts/`:

- `local-creator-reviewer-repair.yml`: local creator/reviewer loop with one
  bounded repair route.
- `provider-backed-node.yml`: provider-backed node with provider evidence,
  cleanup receipt, and resume metadata.
- `parallel-branches-reviewer-join.yml`: two local branches that must join at a
  reviewer before terminal handoff.

The examples are authoring contracts for project agents. Runtime support should
fail closed when a DAG violates the declared goal hash, target, node set, edge
set, attempt limits, required evidence, or join requirements.

## Node Handoffs

Each executable node should emit the appropriate node receipt and, for subagent
turns, a `tau.agent_handoff.v1` packet. The DAG runner should treat those node
artifacts as evidence, not as permission to change the immutable goal or target.
Only trusted human input may change the immutable goal through
`tau.human_goal_change.v1`.

## Drift Tau Must Block

Tau should fail closed on:

- goal hash mismatch
- target mutation without explicit permission
- unexpected node
- unexpected edge
- missing required evidence
- malformed node receipt or handoff
- stale work order
- max attempts exceeded
- timeout
- missing required join
- branch goal-hash divergence
- branch target divergence
- unresolved blocking monitor alert

## Proof Boundary

This document specifies the preferred project-agent interface. Existing Tau
proof lanes already exercise generic DAGs, provider-backed DAGs, resume,
timeouts, receipts, and status surfaces, but a new DAG contract integration
must still produce its own deterministic receipt before claiming support for a
new workflow.
