# tau#292 Close Proof: Context Lineage And By-Value Hashes

Issue: https://github.com/grahama1970/tau/issues/292
Lease: 20260802T155326Z-codex-292

## Scope

This repair tightens DAG context-input authority in two places:

- canonical `DagPlan` validation now emits precise stable codes when a context
  binding's source/target does not match its controlling edge, the controlling
  edge targets a terminal, or the binding references undeclared endpoints;
- by-value node input manifests compute Tau-owned selected-input hashes and
  block malformed or mismatched producer-declared `sha256` values before the
  consumer adapter can run.

By-reference artifact admission and dereference checks remain separate and
unchanged.

## Code Paths Changed

- `src/tau_coding/dag_runtime/model.py`
- `src/tau_coding/dag_runtime/node_input_manifest.py`
- `tests/test_dag_plan_validation.py`
- `tests/test_node_input_manifest.py`
- `docs/proofs/tickets/issue-292-context-lineage-hashes/issue-292-live-readback.py`

## Deterministic Proof

Focused validator and node-input tests:

```text
uv run pytest -q tests/test_dag_plan_validation.py tests/test_node_input_manifest.py
35 passed in 2.03s
```

Broader DAG runtime/compiler/scheduler slice:

```text
uv run pytest -q tests/test_dag_plan.py tests/test_dag_runtime_scheduler.py tests/test_dag_runtime_run_store.py tests/test_dag_plan_validation.py tests/test_node_input_manifest.py
105 passed in 5.27s
```

Full repository test suite:

```text
uv run pytest -q
3485 passed in 502.45s (0:08:22)
```

Formatting/static checks:

```text
uv run ruff check src/tau_coding/dag_runtime/model.py src/tau_coding/dag_runtime/node_input_manifest.py tests/test_dag_plan_validation.py tests/test_node_input_manifest.py docs/proofs/tickets/issue-292-context-lineage-hashes/issue-292-live-readback.py
All checks passed!
```

```text
uv run mypy src/tau_coding/dag_runtime/model.py src/tau_coding/dag_runtime/node_input_manifest.py docs/proofs/tickets/issue-292-context-lineage-hashes/issue-292-live-readback.py
Success: no issues found in 3 source files
```

```text
git diff --check
exit 0
```

## Live Non-Mocked Sanity

Command:

```text
uv run python docs/proofs/tickets/issue-292-context-lineage-hashes/issue-292-live-readback.py
```

Artifact:

```text
docs/proofs/tickets/issue-292-context-lineage-hashes/live-readback.json
```

Readback:

```json
{
  "schema": "tau.issue_292_live_readback.v1",
  "status": "PASS",
  "mocked": false,
  "live": true,
  "provider_live": false,
  "cases": [
    {
      "name": "lineage_mismatch",
      "ok": true,
      "status": "BLOCKED",
      "verdict": "dag_context_binding_edge_mismatch",
      "calls": []
    },
    {
      "name": "declared_hash_mismatch",
      "ok": true,
      "status": "BLOCKED",
      "verdict": "NODE_INPUT_DECLARED_HASH_MISMATCH",
      "calls": [
        "producer"
      ],
      "computed_sha256": "sha256:b97af0a367c7d5fc0c200548a7747ec1bc03ab6d34c922199ff4208d6f047886"
    }
  ]
}
```

The live check is `mocked:false` and `live:true`. It does not call a paid model
provider; it exercises the real local Tau scheduler and validator.

## What This Proves

- A binding activated by one edge while sourcing a different predecessor is
  blocked before any node adapter dispatch.
- Source mismatch, target mismatch, terminal control edge, missing source,
  missing target, missing control edge, and duplicate binding ID have stable
  validator fixtures.
- A producer-declared by-value hash mismatch blocks before consumer dispatch.
- A malformed producer-declared by-value hash blocks before consumer dispatch.
- Missing producer hash remains allowed, with Tau recording the computed
  authoritative `selected_sha256`.
- Matching producer-declared by-value hash passes and records the computed hash.
- Manifest ordering and canonical manifest hashes are stable across repeated
  resolution.

## What This Does Not Prove

- The full Tau immutable goal is not accepted by this ticket alone.
- This does not prove provider/model semantic correctness.
- This does not close unrelated open Tau issues.
- This does not change by-reference artifact provenance behavior beyond
  preserving existing checks.

## Worktree Audit

Required multi-worktree audit command:

```text
/home/graham/workspace/experiments/agent-skills/skills/best-practices-github-ticket/scripts/audit-worktrees.sh --repo /home/graham/workspace/experiments/tau --json
```

Result:

```json
{
  "ok": false,
  "repo": "/home/graham/workspace/experiments/tau",
  "total": 30,
  "tmp": 1,
  "detached": 3,
  "prunable": 0,
  "dirty_secondary": 2,
  "tmp_paths": [
    "/tmp/tau-immutable-goal-main-20260721T000650Z"
  ],
  "prunable_paths": [],
  "dirty_secondary_paths": [
    "/home/graham/workspace/experiments/tau-causal-replay",
    "/home/graham/workspace/experiments/tau-gs001"
  ]
}
```

These worktrees are unrelated to issue #292 and were not modified by this
repair.
