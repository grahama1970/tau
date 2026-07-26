# Issue #168 Proof: Tau Diagnostic Logging

Issue: https://github.com/grahama1970/tau/issues/168

## Summary

Tau now has a Loguru-backed diagnostic logging surface in
`tau_coding.diagnostics`. The generic DAG runner configures a per-run JSONL log
at `run_dir/tau-diagnostics.jsonl` unless `TAU_LOG_PATH` overrides it. DAG node
log records bind `run_id`, `scheduler_run_id`, `node_id`, `attempt`,
`attempt_id`, `idempotency_key`, and `receipt_path` so diagnostics can be joined
to node receipts and scheduler attempts.

The CLI now exposes `--log-level` and `--log-file`. Existing `--verbose` also
raises the diagnostic level to `DEBUG` when no explicit log level is supplied.

## Changed Files

- `pyproject.toml`
- `uv.lock`
- `src/tau_coding/diagnostics.py`
- `src/tau_coding/generic_dag.py`
- `src/tau_coding/dag_runtime/scheduler.py`
- `src/tau_coding/cli.py`
- `tests/test_generic_dag_diagnostics.py`

## Verification

mocked: no provider or service mocks. The pre-receipt failure proof uses Tau's
existing `diagnostic_fault_injector` hook to deterministically raise after an
attempt is dispatched and before the worker receipt can be written.

live: yes for local scheduler execution, filesystem receipt/log creation, and
CLI option parsing.

Commands run from `/tmp/tau-main-issue-137.7nchbf`:

```text
$ uv lock
Resolved 59 packages in 319ms
Added loguru v0.7.3
Added win32-setctime v1.2.0

$ uv run ruff check src/tau_coding/diagnostics.py src/tau_coding/generic_dag.py src/tau_coding/dag_runtime/scheduler.py src/tau_coding/cli.py tests/test_generic_dag_diagnostics.py
All checks passed!

$ uv run python -m py_compile src/tau_coding/diagnostics.py src/tau_coding/generic_dag.py src/tau_coding/dag_runtime/scheduler.py src/tau_coding/cli.py tests/test_generic_dag_diagnostics.py
exit 0

$ uv run pytest -q tests/test_generic_dag_diagnostics.py tests/test_skill_dag_adapter.py::test_generic_dag_runs_native_skill_node tests/test_skill_dag_adapter.py::test_generic_dag_resume_reuses_hash_valid_skill_receipt tests/test_cli.py::test_version_command tests/test_cli.py::test_version_short_flag_prints_version tests/test_cli.py::test_doctor_json_option_does_not_fall_through_to_tui
......                                                                   [100%]
6 passed in 0.97s

$ uv run python - <<'PY'
from typer.testing import CliRunner
from tau_coding.cli import app
r = CliRunner().invoke(app, ['--log-level', 'debug', '--version'])
print({'exit_code': r.exit_code, 'stdout': r.stdout.strip(), 'stderr_len': len(r.stderr or '')})
PY
{'exit_code': 0, 'stdout': 'tau 0.1.0', 'stderr_len': 0}

$ git diff --check
exit 0
```

## Coverage Against Ticket

- Structured logging: `configure_tau_logging` writes Loguru JSONL when a log
  file is configured; generic DAG runs default to `run_dir/tau-diagnostics.jsonl`.
- Run/node correlation: generic DAG node logs bind `run_id`, `node_id`,
  `attempt`, `attempt_id`, `idempotency_key`, and `receipt_path`.
- Verbosity control: CLI supports `--log-level`, `--log-file`, and existing
  `--verbose` maps to debug diagnostics when no explicit level is provided.
- Pre-receipt exception proof:
  `test_generic_dag_logs_correlated_pre_receipt_exception` raises after
  scheduler dispatch and before a node receipt can be written, asserts the
  receipt is absent, reads `tau-diagnostics.jsonl`, parses the JSONL, and checks
  the error record names `run_id=diagnostic-run`, `node_id=worker`,
  `attempt=1`, and `fault_point=after_attempt_dispatched`.

## Remaining Non-Claims

This proof does not claim complete observability for every Tau subsystem. It
establishes the logging surface, CLI controls, and the scheduler/DAG pre-receipt
failure trail required by this ticket.
