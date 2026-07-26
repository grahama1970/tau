# Issue #165 Proof: Lease Release On Exceptional Exits

Ticket: https://github.com/grahama1970/tau/issues/165

## Change

- Wrapped the scheduler executor loop with interrupted-run cleanup.
- `KeyboardInterrupt` now records terminal run status `CANCELLED`, verdict `CANCELLED`,
  and releases the lease.
- Setup-phase unexpected exceptions now record terminal run status `BLOCKED`,
  verdict `DAG_RUN_EXCEPTION`, and release the lease.
- Replayable mid-run crash points still release the lease without terminalizing the run,
  preserving existing crash recovery and replay semantics.
- SQLite store-open failures are converted to typed `DagRunStoreError` code
  `dag_run_store_open_failed`.
- Added operator command `tau dag-clear-lease <run-dir> --run-id <id> --operator <id>
  --reason <text>`; it appends `run_stale_lease_cleared`, clears lease fields, and writes
  `stale-lease-clear.json`.

## Proof Commands

```bash
uv run python -m py_compile \
  src/tau_coding/dag_runtime/run_store.py \
  src/tau_coding/dag_runtime/scheduler.py \
  src/tau_coding/cli.py \
  tests/test_dag_runtime_run_store.py \
  tests/test_cli.py
```

Result: exit 0.

```bash
uv run pytest \
  tests/test_dag_runtime_run_store.py::test_store_open_converts_sqlite_error_to_typed_store_error \
  tests/test_dag_runtime_run_store.py::test_scheduler_releases_lease_and_records_terminal_event_on_exception \
  tests/test_dag_runtime_run_store.py::test_scheduler_releases_lease_and_records_cancelled_on_interrupt \
  tests/test_cli.py::test_cli_dag_clear_lease_writes_operator_receipt_and_releases_lease \
  -q
```

Result: `4 passed in 0.63s`.

```bash
uv run pytest tests/test_dag_runtime_run_store.py \
  tests/test_cli.py::test_cli_dag_clear_lease_writes_operator_receipt_and_releases_lease \
  tests/test_generic_dag.py::test_cli_sigint_cancels_running_dag_child_and_records_cancelled_run \
  -q
```

Result: `31 passed in 5.25s`.

```bash
uv run ruff check \
  src/tau_coding/dag_runtime/run_store.py \
  src/tau_coding/dag_runtime/scheduler.py \
  src/tau_coding/cli.py \
  tests/test_dag_runtime_run_store.py \
  tests/test_cli.py \
  tests/test_generic_dag.py
```

Result: `All checks passed!`

```bash
git diff --check
```

Result: exit 0.

## Evidence Boundary

- mocked: no
- live: yes, local deterministic SQLite/scheduler/CLI execution
- exercised: typed corrupt SQLite open failure, scheduler setup exception cleanup,
  scheduler interrupt cleanup, stale lease operator command and receipt, and existing
  crash-recovery regression paths after the try/except wrap.
- not exercised: full-disk SQLite write failure; corrupt database open is the deterministic
  local SQLite error fixture for the typed error boundary.
