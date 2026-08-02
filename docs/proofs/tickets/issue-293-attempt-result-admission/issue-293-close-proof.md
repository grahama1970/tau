# tau#293 Close Proof: Strict DAG Attempt Result Admission

Issue: https://github.com/grahama1970/tau/issues/293
Lease: 20260802T164038Z-codex-293
Generated: 2026-08-02T17:12:32Z

## Scope

This repair hardens the DAG attempt-result boundary so raw adapter output cannot
enter durable staging, validation, replay-prefix reconstruction, retry logic, or
successor release until Tau has normalized it into
`tau.dag_attempt_result.v1`.

The normalized contract binds:

- `schema`
- `run_id`
- `plan_sha256`
- `node_id`
- `attempt_id`
- `attempt`
- `status`
- `verdict`
- `retryable`
- `accepted_output`
- `errors`
- `alert_codes`

## Code Paths Changed

- `src/tau_coding/dag_runtime/attempt_result.py`
- `src/tau_coding/dag_runtime/scheduler.py`
- `src/tau_coding/dag_runtime/run_store.py`
- `src/tau_coding/dag_runtime/replay.py`
- `tests/test_dag_attempt_result.py`
- `tests/test_dag_runtime_scheduler.py`
- `tests/test_dag_runtime_run_store.py`
- `tests/test_dag_runtime_admission_table.py`
- `tests/test_storage_redaction_boundaries.py`
- `tests/test_durable_repository_qualification_workflow.py`
- `docs/proofs/tickets/issue-293-attempt-result-admission/issue-293-live-readback.py`

## Deterministic Proof

Focused attempt-result/store/scheduler slice:

```text
uv run pytest -q tests/test_dag_attempt_result.py tests/test_dag_runtime_scheduler.py tests/test_dag_runtime_run_store.py -x
64 passed in 4.46s
```

Broader DAG runtime/compiler/input slice:

```text
uv run pytest -q tests/test_dag_attempt_result.py tests/test_dag_plan.py tests/test_dag_runtime_scheduler.py tests/test_dag_runtime_run_store.py tests/test_dag_plan_validation.py tests/test_node_input_manifest.py -x
124 passed in 6.71s
```

Regression slice covering the full-suite failures found during this repair:

```text
uv run pytest -q tests/test_dag_attempt_result.py tests/test_dag_runtime_scheduler.py tests/test_dag_runtime_run_store.py tests/test_dag_runtime_admission_table.py tests/test_storage_redaction_boundaries.py tests/test_durable_repository_qualification_workflow.py -x
82 passed in 60.97s (0:01:00)
```

Full repository test suite:

```text
uv run pytest -q
3504 passed in 534.34s (0:08:54)
```

Formatting/static checks:

```text
uv run ruff check src/tau_coding/dag_runtime/attempt_result.py src/tau_coding/dag_runtime/scheduler.py src/tau_coding/dag_runtime/run_store.py src/tau_coding/dag_runtime/replay.py tests/test_dag_attempt_result.py tests/test_dag_runtime_scheduler.py tests/test_dag_runtime_run_store.py tests/test_dag_runtime_admission_table.py tests/test_storage_redaction_boundaries.py tests/test_durable_repository_qualification_workflow.py docs/proofs/tickets/issue-293-attempt-result-admission/issue-293-live-readback.py
All checks passed!
```

```text
uv run mypy src/tau_coding/dag_runtime/attempt_result.py src/tau_coding/dag_runtime/scheduler.py src/tau_coding/dag_runtime/run_store.py src/tau_coding/dag_runtime/replay.py docs/proofs/tickets/issue-293-attempt-result-admission/issue-293-live-readback.py
Success: no issues found in 5 source files
```

```text
git diff --check
exit 0
```

## Live Non-Mocked Sanity

Command:

```text
uv run python docs/proofs/tickets/issue-293-attempt-result-admission/issue-293-live-readback.py
```

Artifact:

```text
docs/proofs/tickets/issue-293-attempt-result-admission/live-readback.json
```

Readback:

```json
{
  "schema": "tau.issue_293_live_readback.v1",
  "status": "PASS",
  "mocked": false,
  "live": true,
  "provider_live": false,
  "cases": [
    {
      "name": "scheduler_malformed_result_blocks_successor",
      "ok": true,
      "status": "BLOCKED",
      "verdict": "DAG_ATTEMPT_RESULT_INVALID",
      "calls": [
        "producer"
      ],
      "completed_node_ids": []
    },
    {
      "name": "store_rejects_raw_malformed_without_staging",
      "ok": true,
      "error_code": "dag_attempt_result_status_invalid",
      "staged_rows": []
    }
  ]
}
```

The live check is `mocked:false` and `live:true`. It does not call a paid model
provider; it exercises the real local Tau scheduler and SQLite run store.

## What This Proves

- Raw adapter output is normalized into `tau.dag_attempt_result.v1` before
  durable staging.
- Canonical attempt-result validation is bound to scheduler-owned `run_id`,
  `plan_sha256`, `node_id`, `attempt_id`, and `attempt`.
- `status`, `verdict`, `retryable`, `errors`, and `alert_codes` are typed
  without string/bool coercion.
- PASS with a non-PASS verdict is converted into a scheduler-owned blocked
  replacement and does not release successors.
- Direct store staging rejects malformed raw output and leaves zero staged rows.
- Store validation rejects packets whose `result_sha256` is computed over the
  old raw adapter result rather than the normalized staged result.
- Replay-prefix reconstruction rejects non-normalized staged attempt results.
- Redaction still applies to staged normalized attempt results.
- Crash recovery after staged result uses the same normalized payload and
  validation path.

## What This Does Not Prove

- The full Tau immutable goal is not accepted by this ticket alone.
- This does not prove provider/model semantic correctness.
- This does not close unrelated open Tau issues.
- This does not prove live paid-provider behavior.

## Worktree Boundary

Unrelated untracked local path remains outside this ticket and was not staged:

```text
.ask_artifacts/
```
