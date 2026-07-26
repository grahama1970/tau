# Issue #163 Proof: Operator Remedy For Reconciliation-Required Runs

Ticket: https://github.com/grahama1970/tau/issues/163

## Change

- Added `SqliteDagRunStore.reconciliation_required_runs()`.
- Added `SqliteDagRunStore.resolve_reconciliation_required_run(...)`.
- Added `tau dag-reconcile <run-dir> --decision <reconcile|abandon> --operator <id> --reason <text>`.
- The reconciliation decision is appended to the existing journal as
  `run_reconciliation_decision_recorded` with schema
  `tau.dag_run_reconciliation_decision.v1`.
- The original uncertain run is terminally marked `BLOCKED`; `reconcile` authorizes the
  next clean generation without deleting or mutating the prior journal.

## Proof Commands

```bash
uv run python -m py_compile \
  src/tau_coding/dag_runtime/run_store.py \
  src/tau_coding/cli.py \
  tests/test_dag_runtime_run_store.py \
  tests/test_cli.py
```

Result: exit 0.

```bash
uv run pytest \
  tests/test_dag_runtime_run_store.py::test_operator_can_reconcile_uncertain_dispatched_run_and_start_next_generation \
  tests/test_cli.py::test_cli_dag_reconcile_writes_operator_decision_receipt \
  -q
```

Result: `2 passed in 1.40s`.

```bash
uv run pytest tests/test_dag_runtime_run_store.py \
  tests/test_cli.py::test_cli_dag_reconcile_writes_operator_decision_receipt \
  -q
```

Result: `27 passed in 4.15s`.

```bash
uv run ruff check \
  src/tau_coding/dag_runtime/run_store.py \
  src/tau_coding/cli.py \
  tests/test_dag_runtime_run_store.py \
  tests/test_cli.py
```

Result: `All checks passed!`

```bash
git diff --check
```

Result: exit 0.

## Evidence Boundary

- mocked: no
- live: yes, local deterministic SQLite/scheduler/CLI execution
- exercised: real `dag-run.sqlite3`, crashed dispatch path, takeover resume to
  `RECONCILIATION_REQUIRED`, operator reconciliation decision, receipt written to disk,
  preserved journal event count, and subsequent clean generation execution.
- not exercised: external provider calls; none are required for this run-store ticket.
