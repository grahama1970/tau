# Issue #164 Proof: Clean DAG Cancellation

Ticket: https://github.com/grahama1970/tau/issues/164

## Change

- Added durable run-level `CANCELLED` status and `run_cancelled` journal event.
- Added a scheduler `cancel_requested` hook that sets existing node cancel events and
  drains in-flight futures through the existing cancellation collector.
- Added SIGINT/SIGTERM handling in the generic DAG CLI runner so operator stop
  signals become cooperative DAG cancellation.
- Preserved process-group subprocess behavior: the existing cancellable subprocess
  helper still terminates the child process group when its cancel event is set.
- Kept node receipts inside the existing node schema by recording node status as
  `BLOCKED` with verdict `CANCELLED`; run-level status is `CANCELLED`.

## Proof Commands

```bash
uv run python -m py_compile \
  src/tau_coding/dag_runtime/run_store.py \
  src/tau_coding/dag_runtime/scheduler.py \
  src/tau_coding/generic_dag.py \
  tests/test_generic_dag.py
```

Result: exit 0.

```bash
uv run pytest \
  tests/test_generic_dag.py::test_cli_sigint_cancels_running_dag_child_and_records_cancelled_run \
  -q
```

Result: `1 passed in 1.80s`.

```bash
uv run pytest tests/test_generic_dag.py \
  tests/test_dag_runtime_run_store.py::test_store_reuses_unfinished_generation_and_advances_finished_run \
  -q
```

Result: `28 passed in 4.64s`.

```bash
uv run ruff check \
  src/tau_coding/dag_runtime/run_store.py \
  src/tau_coding/dag_runtime/scheduler.py \
  src/tau_coding/generic_dag.py \
  tests/test_generic_dag.py
```

Result: `All checks passed!`

```bash
git diff --check
```

Result: exit 0.

## Required Scenario Covered

`test_cli_sigint_cancels_running_dag_child_and_records_cancelled_run` starts a real
`tau dag-run` CLI process, waits until the long-running worker command writes its PID,
sends SIGINT to the scheduler process, then asserts:

- the CLI exits after cancellation instead of waiting for the 30 second child sleep,
- the child PID no longer exists,
- `dag-run.sqlite3` contains run status `CANCELLED`,
- the run verdict is `CANCELLED`,
- the lease owner is released,
- the final journal events include `run_cancelled` followed by `run_lease_released`.

## Evidence Boundary

- mocked: no
- live: yes, local deterministic subprocess/signal/SQLite execution
- exercised: real OS SIGINT, real child process termination, real scheduler cancel events,
  real `dag-run.sqlite3` journal inspection after process exit.
- not exercised: second interrupt escalation; the signal handler implements it, but the focused
  proof covers the required clean first-interrupt path.
