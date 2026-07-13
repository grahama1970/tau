# Canonical DagPlan

`tau.dag_plan.v1` is Tau's immutable internal representation of a validated DAG.
It lets the project and generic public contract families converge on one plan
without removing either public schema or changing their schedulers.

Compile a plan without dispatching any node:

```bash
uv run tau dag-plan path/to/dag.json --out /tmp/tau-dag-plan.json
```

The compile receipt and plan bind the canonical source payload and normalized
plan to SHA-256 hashes. Compilation validates graph references and cycles,
project route and join semantics, generic dependency/context relationships,
retry limits, and source-specific node contracts.

## Shared Model

Each plan records:

- plural entry nodes and typed terminal endpoints;
- normalized nodes and adapter kinds;
- ordered control edges separately from predecessor-context bindings;
- retries, timeouts, evidence requirements, and capability requests;
- typed route contracts and deterministic join policies;
- portable source-relative command, skill, transaction, provider, and receipt bindings;
- project/node context layers and the project runtime merge policy;
- generic working directories and project evidence manifests as explicit bindings;
- declared security inputs without claiming those gates passed.

Project command specs and generic command arrays intentionally remain distinct
artifact bindings. DagPlan normalizes orchestration meaning; it does not erase
the execution contract required by each public schema.

Canonical plan payloads exclude resolved host paths, timestamps, and runtime
metadata. Nested opaque configuration is stored as canonical JSON so callers
cannot mutate the frozen plan after its hash is computed.

Generic relative `run_dir` values retain the public runtime's
`process_invocation_directory` anchor. Skill paths remain source-document
relative. Absolute runtime working directories are represented as non-portable
rather than silently re-anchored.

## Boundary

DagPlan compilation proves that Tau accepted and normalized a supported DAG
contract and produced deterministic hashes. It does not execute the DAG, prove
worker correctness, satisfy security gates, converge the schedulers, or provide
durable restart behavior. Scheduler convergence belongs to issue #78 and the
durable event store belongs to issue #79.
