# Issue #138 Proof: Live Evidence And Deterministic Reviewer

Issue: https://github.com/grahama1970/tau/issues/138

## Scope

Changed only the Tau workflow/generic DAG evidence path named by the ticket:

- `src/tau_coding/generic_dag.py`
- `src/tau_coding/generic_artifact_transaction.py`
- `src/tau_coding/workflows/runner.py`
- `src/tau_coding/workflows/nodes/durable_repository_qualification.py`
- `scripts/run-immutable-goal-audit.py`
- focused regression tests for those paths

## What Changed

- Local subprocess node receipts that claim `live: true` now receive Tau-owned
  `execution_evidence` before receipt validation.
- Resumed local receipts that claim `live: true` without execution evidence are
  rejected without rerunning the node.
- Transaction producer receipts and reviewer feedback receive and validate local
  execution evidence.
- Workflow receipts derive `mocked`, `live`, and `provider_live` from the DAG
  receipt instead of hardcoding `live: true`.
- The durable repository qualification reviewer now reads candidate artifacts,
  checks hashes and deterministic schema/content fields, and emits non-PASS
  verdicts when the candidate is invalid.
- Immutable-goal audit criteria 1-9 now cite artifact/run/proof references
  with paths or hashes rather than bare assertion strings.

## Deterministic Proof

Command:

```bash
uv run ruff format src/tau_coding/generic_dag.py src/tau_coding/generic_artifact_transaction.py src/tau_coding/workflows/runner.py src/tau_coding/workflows/nodes/durable_repository_qualification.py scripts/run-immutable-goal-audit.py tests/test_generic_dag.py tests/test_generic_artifact_transaction.py tests/test_durable_repository_qualification_workflow.py tests/test_immutable_goal_audit.py
```

Result:

```text
1 file reformatted, 8 files left unchanged
```

Command:

```bash
uv run python -m py_compile src/tau_coding/generic_dag.py src/tau_coding/generic_artifact_transaction.py src/tau_coding/workflows/runner.py src/tau_coding/workflows/nodes/durable_repository_qualification.py scripts/run-immutable-goal-audit.py tests/test_generic_dag.py tests/test_generic_artifact_transaction.py tests/test_durable_repository_qualification_workflow.py tests/test_immutable_goal_audit.py
```

Result: exit 0.

Command:

```bash
uv run pytest -q tests/test_generic_dag.py tests/test_generic_artifact_transaction.py tests/test_durable_repository_qualification_workflow.py tests/test_workflow_cli.py tests/test_immutable_goal_audit.py
```

Result:

```text
57 passed in 55.30s
```

Required proof clause coverage:

- `tests/test_durable_repository_qualification_workflow.py::test_durable_qualification_reviewer_blocks_invalid_candidate`
  proves a candidate violating deterministic reviewer checks yields
  `verdict: BLOCKED`.
- `tests/test_generic_dag.py::test_generic_dag_rejects_resumed_live_receipt_without_execution_evidence`
  proves a `live:true` receipt without execution evidence is rejected before
  rerun.
- `tests/test_generic_artifact_transaction.py::test_transaction_review_feedback_rejects_live_without_execution_evidence`
  proves review feedback cannot self-declare `live:true` without local execution
  evidence.
- `tests/test_immutable_goal_audit.py::test_result_and_criteria_projection_are_deterministic`
  proves established audit criteria evidence items are structured artifact,
  run, or proof references.

## Local Workflow Smoke

Command:

```bash
uv run tau workflows run durable-repository-qualification --repo /tmp/tau-issue-138-live.bTOO81/repo --run-dir /tmp/tau-issue-138-live.bTOO81/run --publish-path /tmp/tau-issue-138-live.bTOO81/published --goal 'Issue 138 live evidence smoke.' --no-browser-open
```

Result: exit 1 because the workflow correctly stopped at
`APPROVAL_REQUIRED`.

Observed receipt fields:

```text
workflow_status BLOCKED live True mocked False provider_live False
dag_status BLOCKED APPROVAL_REQUIRED live True mocked False provider_live False
producer_execution_evidence kind=local_subprocess returncode=0 runtime_backend=local runtime_event_state=EXITED runtime_submit_delivery_status=CONFIRMED runtime_artifact_count=4
review_verdict PASS findings []
review_execution_evidence kind=local_subprocess returncode=0 runtime_backend=local runtime_event_state=EXITED runtime_submit_delivery_status=CONFIRMED runtime_artifact_count=4
```

Smoke artifacts:

- `/tmp/tau-issue-138-live.bTOO81/run/workflow-receipt.json`
- `/tmp/tau-issue-138-live.bTOO81/run/run-receipt.json`
- `/tmp/tau-issue-138-live.bTOO81/run/receipts/publish-qualification.json`
- `/tmp/tau-issue-138-live.bTOO81/run/transactions/publish-qualification/attempt-001/review-feedback.json`

## Evidence Classification

- mocked: no
- live: yes, local subprocess workflow smoke
- provider_live: no
- What was exercised: generic DAG receipt validation, transaction reviewer
  feedback validation, durable reviewer deterministic candidate checks,
  workflow receipt liveness propagation, immutable audit criteria projection,
  and a local durable workflow CLI run through real subprocess nodes.
- What remains unverified: live provider-backed LLM behavior is not part of
  this ticket and was not exercised.
