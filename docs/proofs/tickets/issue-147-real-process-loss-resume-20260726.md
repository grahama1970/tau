# Issue #147 Proof: Real Process Loss Resume

Issue: https://github.com/grahama1970/tau/issues/147

## Diagnosis

The existing crash-resume coverage used an in-process diagnostic exception after
a staged result. That did not prove actual OS process loss. Current Tau already
has the required durable primitives in `dag_runtime/run_store.py`: WAL-backed
events, unique run/node idempotency keys, lease takeover, staged/validated/
committed attempt states, and `DAG_ATTEMPT_EFFECT_UNCERTAIN` for dispatched
attempts that died before staging.

The missing proof was an integration test that starts the packaged durable
qualification workflow in a child Python process, kills that process with
`SIGKILL`, and resumes from the persisted run directory.

## Changed Files

- `tests/test_durable_repository_qualification_workflow.py`

## Proof Commands

```text
uv run python -m py_compile tests/test_durable_repository_qualification_workflow.py
exit 0
```

```text
uv run ruff check tests/test_durable_repository_qualification_workflow.py
All checks passed!
```

```text
uv run pytest tests/test_durable_repository_qualification_workflow.py -q
6 passed in 64.43s (0:01:04)
```

## What The New Tests Exercise

- `test_sigkill_after_staged_result_resumes_without_duplicate_publication`
  starts a real child workflow process, waits until the child reaches the
  durable `after_result_staged` boundary for `reconcile-qualification`, sends
  `SIGKILL`, resumes the packaged workflow, verifies the same branch receipts
  are reused, verifies journal order `attempt_result_staged <
  run_lease_taken_over < attempt_result_validated`, approves publication, resumes
  twice, and asserts `publication-ledger.json` has `effect_count == 1`.

- `test_sigkill_before_staging_fails_closed_without_rerunning` starts a real
  child workflow process, waits until `qualify-tests` is dispatched but not
  staged, sends `SIGKILL`, resumes the packaged workflow, and verifies the run
  blocks with `DAG_ATTEMPT_EFFECT_UNCERTAIN`, the attempt state is
  `UNCERTAIN/UNCERTAIN`, no `qualify-tests` receipt exists, no staged event was
  recorded, and no second attempt was created.

## Evidence Scope

mocked: no
live: yes
provider_live: no

This proves the packaged durable qualification workflow has deterministic local
recovery behavior for real OS process loss at the tested staged and pre-staged
boundaries. It does not prove live provider behavior, distributed process
supervision, arbitrary external side effects, or crash recovery for every
possible instruction boundary.
