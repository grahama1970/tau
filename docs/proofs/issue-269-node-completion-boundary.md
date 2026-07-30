# Issue #269 node completion boundary proof

## Scope

Ticket: <https://github.com/grahama1970/tau/issues/269>

This change adds the opt-in `tau.node_completion_boundary.v1` contract for
high-assurance DAG nodes. Nodes that include the schema in `required_evidence`
must return a typed `node_completion_boundary`; the scheduler validates it
against the current goal hash, plan hash, node id, and attempt id before a PASS
result can settle.

## Implemented behavior

- Boundary schema module: `src/tau_coding/node_completion_boundary.py`.
- Scheduler fail-closed enforcement before attempt result staging.
- Durable boundary write under `node-completion-boundaries/<attempt_id>.json`.
- Admission row with `receipt_kind = tau.node_completion_boundary.v1`.
- Run-store readback by `receipt_kind` and `admission_id`.
- Policy switch `tau.node_completion_boundary_policy.v1` for required and
  non-empty sections.
- Existing nodes without this required evidence keep their current behavior.

## Proof commands

```text
uv run ruff check --select F,E9 src/tau_coding/node_completion_boundary.py src/tau_coding/dag_runtime/scheduler.py src/tau_coding/dag_runtime/run_store.py tests/test_node_completion_boundary.py tests/test_dag_runtime_admission_table.py tests/test_dag_runtime_run_store.py
```

Result: `All checks passed!`

```text
uv run pytest tests/test_node_completion_boundary.py tests/test_dag_runtime_admission_table.py tests/test_dag_runtime_run_store.py -q
```

Result: `49 passed in 4.48s`

```text
uv run pytest tests/test_node_completion_boundary.py tests/test_dag_runtime_admission_table.py tests/test_dag_runtime_run_store.py tests/test_dag_plan.py -q
```

Result: `75 passed in 4.87s`

```text
uv run python /tmp/tau-issue-269-node-boundary-proof-20260730T230309Z-2905425/tau_issue269_live_e2e.py /tmp/tau-issue-269-node-boundary-proof-20260730T230309Z-2905425
```

Result artifact:
`/tmp/tau-issue-269-node-boundary-proof-20260730T230309Z-2905425/summary.json`

Readback summary:

```json
{
  "live": true,
  "missing_alert_codes": [
    "node_completion_boundary_missing"
  ],
  "missing_boundary_admissions": 0,
  "missing_status": "BLOCKED",
  "missing_verdict": "NODE_COMPLETION_BOUNDARY_INVALID",
  "mocked": false,
  "pass_boundary_admissions": 1,
  "pass_boundary_path": "/tmp/tau-issue-269-node-boundary-proof-20260730T230309Z-2905425/node-completion-boundaries/attempt-4b7acc2a97ff8b514e2b5515d58de4d5.json",
  "pass_boundary_sha256": "sha256:abf19e2138c7051615e2f92699d66137db48ebbd1731c7a255845f2a874c0b87",
  "pass_status": "PASS",
  "pass_verdict": "PASS",
  "provider_live": false,
  "readback_boundary_admissions": 1,
  "schema": "tau.issue_269_live_e2e_proof.v1"
}
```

## Proof boundaries

mocked: no

live: yes, for the scheduler and SQLite run-store/admission path.

provider_live: no. This ticket is scheduler/admission behavior, not provider
semantics.

This proves a valid boundary is canonicalized, hash-bound, admitted, and
replayable; missing boundary and identity/policy errors fail closed. It does
not prove that a node's self-reported checked scope is complete or semantically
correct.

```text
uv run pytest -q
```

Result: `3396 passed in 503.15s (0:08:23)`
