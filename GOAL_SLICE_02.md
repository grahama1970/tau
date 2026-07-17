# Tau Canonical Workflow Slice 02

**Status:** Active
**Owner:** Human
**Goal ID:** tau-canonical-workflow-slice-02
**Goal Version:** 1
**Goal Hash:** sha256:177927ed9b7fabe84208d492ca716b64be92a219f045b1e8911addee081eb684

## Immutable Goal

Generate a validated Tau operator reference from fixed local repository sources
and versioned public CLI evidence.

## Completion Criteria

1. Expose `tau-operator-reference` through the packaged workflow catalog.
2. Execute the four locked operator-reference nodes sequentially at concurrency one.
3. Read only the fixed Tau source set from the requested local repository.
4. Capture actual local executable output for the fixed versioned public CLI probes.
5. Keep composed JSON and Markdown drafts under `intermediate/`.
6. Publish JSON and Markdown results only after independent validator recomputation.
7. Block validation with `required_workflow_missing` when the required workflow is absent.
8. Carry the full immutable goal hash and `accepted_output` in every node receipt.

## Locked Workflow

At Slice 02 acceptance, the catalog contains `repository-readiness` and
`tau-operator-reference`; later immutable-goal rungs may add workflows without
invalidating this workflow's durable catalog-presence criterion.
The new workflow has topology `MULTI_STEP_SEQUENTIAL`, `max_concurrency: 1`, and
these nodes:

```text
collect-operator-sources
  -> capture-operator-cli
  -> compose-operator-reference
  -> validate-operator-reference
```

Every node has one attempt. There are no routes, joins, retries, provider calls,
network calls, or repository side effects.

## Fixed Evidence

The source set is immutable:

```text
pyproject.toml
README.md
docs/getting-started.md
docs/live-dag-viewer.md
docs/generic-dag-runner.md
```

The version 1 public CLI probe manifest is immutable:

```text
tau workflows list --json
tau workflows run --help
tau dag-view-capabilities --json
```

`tau dag-view --help` is excluded because the legacy callback parser rejects it.
The workflow records both public arguments and actual output from the resolved
local `tau` executable.

## Publication Gate

Collection and composition write only below `intermediate/`. The validator
independently rereads every fixed source, reruns every fixed CLI probe, rerenders
both formats, compares the draft hashes, checks the required workflow in the
fresh catalog output, and only then atomically renames a staged results directory
to publish:

```text
results/tau-operator-reference.json
results/tau-operator-reference.md
```

A run with `--required-workflow deliberately-absent` blocks at
`validate-operator-reference` with `required_workflow_missing` and publishes no
result files.

## Proof Boundary

```text
mocked: false
live: true
provider_live: false
```

This slice does not change scheduler, compiler, generic DAG, viewer, web,
scripts, or agent-skills behavior.
