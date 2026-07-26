# Issue 174 Regression Proof: Redacted Dispatch Dedupe

Ticket: https://github.com/grahama1970/tau/issues/174

## Scope

Issue #174 was reopened because the storage-redaction repair caused project DAG
dispatches to appear twice in receipts. This was duplicate accounting, not a
second process execution.

Root cause: `_run_shared_project_dag_plan` appended an in-memory dispatch during
node execution, then merged the scheduler's staged node result after the SQLite
run completed. The staged result had been passed through storage redaction, so
the full dispatch dictionaries were no longer equal. The old `dispatch not in
dispatches` check treated the redacted copy of the same runtime attempt as a new
dispatch.

This patch dedupes dispatches by logical identity:

- `selected_agent`
- runtime `attempt_id` when present
- runtime `endpoint_id` when present
- command spec SHA as a fallback

The storage redaction boundary remains in place.

## Changed Paths

- `src/tau_coding/project_dag.py`
- `docs/proofs/tickets/issue-174-dispatch-dedupe-regression-20260726.md`

## Deterministic Commands

Reopened failure set after repair:

```bash
uv run pytest -q \
  tests/test_project_dag.py::test_project_dag_ambiguous_exclusive_route_blocks_without_branch_dispatch \
  tests/test_project_dag.py::test_project_dag_durable_replay_preserves_receipt_evidence \
  tests/test_project_dag.py::test_project_dag_fanout_activates_only_matching_subset \
  tests/test_project_dag.py::test_project_dag_route_no_match_does_not_stall_or_dispatch_branch \
  tests/test_project_dag.py::test_project_dag_skipped_only_terminal_route_blocks \
  tests/test_project_dag.py::test_project_dag_typed_route_modes_dispatch_only_activated_branches \
  tests/test_project_dag.py::test_ready_queue_derives_final_verdict_from_terminal_handler_receipt \
  tests/test_project_dag_join_policies.py::test_conditional_skip_contributes_and_minimum_join_releases \
  tests/test_project_dag_join_policies.py::test_short_circuit_batches_cancelled_contributions_before_final_evaluation
```

Result:

```text
12 passed in 2.22s
```

Storage-redaction acceptance tests:

```bash
uv run pytest -q \
  tests/test_dag_viewer_redaction.py \
  tests/test_storage_redaction_boundaries.py
```

Result:

```text
7 passed in 0.52s
```

Lint:

```bash
uv run ruff check \
  src/tau_coding/dag_viewer/redaction.py \
  src/tau_coding/runtime_backends/local.py \
  src/tau_coding/handoff_dispatch.py \
  src/tau_coding/dag_runtime/run_store.py \
  src/tau_coding/session_export.py \
  src/tau_coding/project_dag.py \
  tests/test_dag_viewer_redaction.py \
  tests/test_storage_redaction_boundaries.py \
  tests/test_project_dag.py \
  tests/test_project_dag_join_policies.py
```

Result:

```text
All checks passed!
```

Compile:

```bash
uv run python -m py_compile \
  src/tau_coding/dag_viewer/redaction.py \
  src/tau_coding/runtime_backends/local.py \
  src/tau_coding/handoff_dispatch.py \
  src/tau_coding/dag_runtime/run_store.py \
  src/tau_coding/session_export.py \
  src/tau_coding/project_dag.py \
  tests/test_dag_viewer_redaction.py \
  tests/test_storage_redaction_boundaries.py \
  tests/test_project_dag.py \
  tests/test_project_dag_join_policies.py
```

Result: exit code `0`.

Diff hygiene:

```bash
git diff --check
```

Result: exit code `0`.

## Evidence Classification

- mocked: yes, for project DAG pytest fixture dispatches.
- live: yes, for local subprocess/storage-redaction boundary tests that exercise
  runtime capture, dispatch receipt, SQLite journal, and session export.
- provider/model calls: no.
- exercised: duplicate dispatch merge path, selected agent receipt accounting,
  storage redaction tests, lint, compile, and diff hygiene.
- remains unverified: external provider/service redaction behavior and any
  future artifact writer outside the patched storage boundaries.
