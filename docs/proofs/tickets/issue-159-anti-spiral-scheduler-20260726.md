# Issue #159 Anti-Spiral Scheduler Proof

Ticket: https://github.com/grahama1970/tau/issues/159

## Scope

Connected Tau's anti-spiral course-correction policy to the canonical DAG
scheduler retry path. When two consecutive failed attempts have the same
canonical failure signature and another retry would otherwise be consumed, the
scheduler now writes a `tau.course_correction.v1` receipt, marks the attempt
`COURSE_CORRECTION_REQUIRED`, sets `correction_required: true`, and blocks the
third same-context retry.

This keeps external findings advisory-only. It does not implement the separate
Memory ingestion, episode provenance, validity-window, or Memory-backed routing
tickets.

## Deterministic Checks

```bash
uv run pytest -q \
  tests/test_generic_dag.py \
  tests/test_generic_artifact_transaction.py \
  tests/test_browser_dag_handler.py \
  tests/test_browser_cdp_proof.py
```

Result:

```text
52 passed in 11.33s
```

```bash
uv run ruff check \
  src/tau_coding/dag_runtime/scheduler.py \
  src/tau_coding/browser_cdp_proof.py \
  src/tau_coding/generic_dag.py \
  tests/test_generic_dag.py \
  tests/test_generic_artifact_transaction.py \
  tests/test_browser_dag_handler.py \
  tests/test_browser_cdp_proof.py
```

Result:

```text
All checks passed!
```

```bash
uv run python -m py_compile \
  src/tau_coding/dag_runtime/scheduler.py \
  src/tau_coding/browser_cdp_proof.py \
  src/tau_coding/generic_dag.py \
  tests/test_generic_dag.py \
  tests/test_browser_dag_handler.py
git diff --check
```

Result: exit code 0.

## Regression Added

`tests/test_generic_dag.py::test_generic_dag_blocks_third_identical_command_error_with_course_correction`
uses a real failing Python subprocess with `max_attempts=3` and asserts:

- only two attempts run
- run verdict is `COURSE_CORRECTION_REQUIRED`
- `retryable` is false
- `correction_required` is true
- `tau.course_correction.v1` is written and read back from disk
- required action is `run_brave_search_then_retry`
- advisory evidence does not satisfy the acceptance gate
- the receipt records searches performed as empty and searches not performed

The existing transaction-level repeated-review detector remains covered by
`tests/test_generic_artifact_transaction.py`.

## Evidence Classification

mocked: no

live: no external provider calls

What was actually exercised: local subprocess DAG retry execution, scheduler
attempt history, course-correction receipt writing/readback, retry suppression,
and existing transaction/browser regression coverage.

What remains covered by separate open tickets: Memory-backed skill selection,
Memory episode persistence, validity windows, registered dependency docs, and
full external search execution.
