# Issue 135 Proof: Generic DAG Goal Hash Enforcement

Issue: https://github.com/grahama1970/tau/issues/135

## Change

Declared-goal generic DAG runs now fail closed when receipts omit or change the
active `goal_hash`.

The patch binds `goal_hash` through:

- legacy node receipt validation when the DAG declares `goal_hash`;
- transaction producer receipt validation;
- transaction review feedback validation;
- stored transaction receipts used for resume/replay;
- continuation approval `expected_target`;
- packaged workflow transaction review receipts.

Goal-less legacy specs remain accepted; enforcement activates when a DAG
declares `goal_hash` or a full canonical `goal` object.

## Deterministic Commands

```text
uv run python -m py_compile src/tau_coding/generic_dag.py src/tau_coding/generic_artifact_transaction.py src/tau_coding/workflows/nodes/approved_release_bundle.py src/tau_coding/workflows/nodes/durable_repository_qualification.py src/tau_coding/approval_gate.py src/tau_coding/workflows/runner.py src/tau_coding/cli.py tests/test_generic_dag.py tests/test_generic_artifact_transaction.py tests/test_approval_gate.py tests/test_workflow_cli.py tests/test_durable_repository_qualification_workflow.py
exit 0
```

```text
uv run ruff format src/tau_coding/generic_dag.py src/tau_coding/generic_artifact_transaction.py tests/test_generic_dag.py tests/test_generic_artifact_transaction.py
3 files reformatted, 1 file left unchanged
```

```text
uv run pytest -q tests/test_generic_dag.py tests/test_generic_artifact_transaction.py tests/test_approval_gate.py tests/test_workflow_cli.py tests/test_durable_repository_qualification_workflow.py
57 passed in 54.35s
```

Focused regression coverage added:

- node receipt missing `goal_hash` under a declared-goal DAG returns
  `INVALID_RECEIPT` with `goal_hash must be a non-empty string`;
- replayed transaction receipt with stale `goal_hash` returns
  `STALE_ACCEPTED_STATE` with `transaction_receipt_goal_hash_mismatch`;
- continuation approval packet whose target omits `goal_hash` stays
  `APPROVAL_REQUIRED` with `target.goal_hash must match expected value`.

## Real CLI Smoke

Command family used a temporary real git repository and real packaged workflow
CLI, with no mocked service responses:

```text
tmp_root=/tmp/tau-issue-135-live.6QubzY
run_ec=1
approval expected_target.goal_hash -> sha256:096a5b06dda70e910bbc8906cee09559a8a51febcafd2789951965f6d62657cd
transaction-receipt.goal_hash -> sha256:096a5b06dda70e910bbc8906cee09559a8a51febcafd2789951965f6d62657cd
run-receipt publish-approved-release.goal_hash -> sha256:096a5b06dda70e910bbc8906cee09559a8a51febcafd2789951965f6d62657cd
resume status/result -> PASS / APPROVED
```

## Evidence Scope

mocked: no
live: yes, local Tau CLI workflow execution against a real temporary git repo
provider_live: no

This proves declared-goal generic DAG receipts and transaction replay are no
longer goal-hash fail-open on the tested paths, and packaged workflow approval
targets include the active run goal hash.

This does not prove provider/model correctness or cryptographic human identity.
