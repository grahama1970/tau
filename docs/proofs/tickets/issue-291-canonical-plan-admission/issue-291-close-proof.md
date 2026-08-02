# tau#291 Close Proof: Canonical DAG Plan Admission

Issue: https://github.com/grahama1970/tau/issues/291
Lease: 20260802T150002Z-codex-291

## Scope

This repair adds canonical `DagPlan` validation before scheduler admission,
run-store persistence/deserialization, durable replay, and project/generic DAG
compilation. Invalid plans now fail before worker dispatch with stable
`tau.dag_plan_validation.v1` issue codes and JSON paths.

## Code Paths Changed

- `src/tau_coding/dag_runtime/model.py`
- `src/tau_coding/dag_runtime/compiler.py`
- `src/tau_coding/dag_runtime/scheduler.py`
- `src/tau_coding/dag_runtime/run_store.py`
- `src/tau_coding/dag_runtime/replay.py`
- `src/tau_coding/dag_runtime/__init__.py`
- `src/tau_coding/project_dag.py`
- `tests/test_dag_plan_validation.py`
- `tests/test_dag_runtime_scheduler.py`
- `tests/test_dag_runtime_run_store.py`
- `tests/test_project_dag.py`

## Deterministic Proof

Focused DAG validation/runtime tests:

```text
uv run pytest -q tests/test_dag_plan.py tests/test_dag_runtime_scheduler.py tests/test_dag_runtime_run_store.py tests/test_dag_plan_validation.py
82 passed in 4.67s
```

Regression check for project DAG invalid-plan receipt plus new validator tests:

```text
uv run pytest -q tests/test_project_dag.py::test_project_dag_skipped_only_terminal_route_blocks tests/test_dag_plan_validation.py
13 passed in 0.94s
```

Full repository test suite:

```text
uv run pytest -q
3474 passed in 514.09s (0:08:34)
```

Formatting/static checks:

```text
uv run ruff check src/tau_coding/project_dag.py src/tau_coding/dag_runtime/model.py src/tau_coding/dag_runtime/compiler.py src/tau_coding/dag_runtime/scheduler.py src/tau_coding/dag_runtime/run_store.py src/tau_coding/dag_runtime/replay.py tests/test_project_dag.py tests/test_dag_plan_validation.py tests/test_dag_runtime_scheduler.py tests/test_dag_runtime_run_store.py
All checks passed!
```

```text
uv run mypy src/tau_coding/dag_runtime/model.py src/tau_coding/dag_runtime/compiler.py src/tau_coding/dag_runtime/scheduler.py src/tau_coding/dag_runtime/run_store.py src/tau_coding/dag_runtime/replay.py
Success: no issues found in 5 source files
```

```text
git diff --check
exit 0
```

Note: `src/tau_coding/project_dag.py` still has pre-existing mypy debt and was
not claimed as mypy-clean by this proof.

## Live Non-Mocked Sanity

Command:

```text
uv run tau dag-run docs/proofs/tickets/issue-291-canonical-plan-admission/dead-end-project-dag.json --receipt-dir docs/proofs/tickets/issue-291-canonical-plan-admission/live-run-canonical-with-spec --agents-root docs/proofs/tickets/issue-291-canonical-plan-admission/agents --scheduler bounded-ready-queue --no-resume
```

Expected result: non-zero exit because the canonical validator blocks the
invalid plan before dispatch.

Receipt:

```text
docs/proofs/tickets/issue-291-canonical-plan-admission/live-run-canonical-with-spec/dag-receipt.json
```

Readback:

```json
{
  "schema": "tau.dag_receipt.v1",
  "status": "BLOCKED",
  "verdict": "node_dead_end",
  "ok": false,
  "mocked": false,
  "live": true,
  "provider_live": false,
  "selected_agents": [],
  "dispatches": [],
  "dag_plan_validation": {
    "codes": [
      "node_dead_end"
    ],
    "issues": [
      {
        "code": "node_dead_end",
        "detail": "accept",
        "path": "$.nodes"
      }
    ],
    "ok": false,
    "schema": "tau.dag_plan_validation.v1"
  },
  "command_executed": false,
  "dag_plan_sha256": null
}
```

This live check is `mocked:false` and `live:true`. It does not call a paid model
provider; it proves the local Tau CLI admission gate and zero-dispatch blocked
receipt behavior.

## What This Proves

- Duplicate-node rehash is rejected before scheduler/store dispatch in tests.
- Invalid target kind, duplicate edge ID, duplicate binding ID, cycle,
  unreachable node, dead-end node, malformed terminal, bad timeout, and boolean
  attempt count produce stable validation failures in tests.
- `run_dag_plan()` calls the canonical validator before adapter dispatch.
- Stored/deserialized plans use the same validator path.
- Durable replay uses the same validator path.
- Project DAG runtime returns a typed blocked receipt for canonical invalid
  plans with zero selected agents and zero dispatches.

## What This Does Not Prove

- The full Tau immutable goal is not accepted by this ticket alone.
- This does not prove provider/model semantic correctness.
- This does not close unrelated open Tau issues.
- This does not make pre-existing `project_dag.py` mypy debt disappear.

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

Dirty secondary readback:

```text
/home/graham/workspace/experiments/tau-causal-replay
## fix/external-review-integrity...origin/main [ahead 9, behind 745]
 M src/tau_coding/dag_viewer/server.py
 M tests/test_dag_viewer_server.py

/home/graham/workspace/experiments/tau-gs001
## HEAD (no branch)
 M src/tau_coding/skill_dag_adapter.py
```

These worktrees are unrelated to issue #291 and were not modified by this
repair.
