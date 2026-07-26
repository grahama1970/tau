# Issue #168 DAG Run JSON Stdout Regression Proof

Date: 2026-07-26

Issue: https://github.com/grahama1970/tau/issues/168

## Scope

The reopened #168 regression was that `tau dag-run` and `tau run` no longer
emitted parseable machine JSON because diagnostic log lines shared the receipt
stdout stream.

The repair was made in commit `ba73e5bc63e24c7901584e65134417734ebd6dbb`
while resolving #132: `_run_dag_cli_command` now redirects DAG diagnostic stderr
while emitting the JSON receipt. The Loguru JSONL diagnostic trail remains
available through the run diagnostics file and existing diagnostics tests.

## Changed Files

```text
docs/proofs/tickets/issue-168-dag-run-json-stdout-regression-20260726.md
```

No additional source change was needed for this ticket because the stdout
regression was already repaired by the pushed #132 commit.

## Deterministic Proof

Reopened-ticket tests:

```text
uv run pytest -q tests/test_cli.py::test_cli_dag_run_and_run_alias_execute_generic_dag -vv
```

Result:

```text
tests/test_cli.py::test_cli_dag_run_and_run_alias_execute_generic_dag[dag-run] PASSED
tests/test_cli.py::test_cli_dag_run_and_run_alias_execute_generic_dag[run] PASSED
2 passed in 0.72s
```

Diagnostics regression checks:

```text
uv run pytest -q \
  tests/test_generic_dag_diagnostics.py \
  tests/test_cli.py::test_version_command \
  tests/test_cli.py::test_version_short_flag_prints_version \
  tests/test_cli.py::test_doctor_json_option_does_not_fall_through_to_tui
```

Result:

```text
4 passed in 0.66s
```

Live `dag-run` JSON stdout smoke:

```text
uv run tau dag-run /tmp/tau-issue-168-live.qCfI38/dag.json --no-resume \
  >/tmp/tau-issue-168-live-dag-run.stdout \
  2>/tmp/tau-issue-168-live-dag-run.stderr
```

Result:

```text
status=1
stdout_json_parse=PASS
receipt_status=BLOCKED
stderr_has_diagnostics=False
stderr_len=0
```

The fixture receipt in this smoke was intentionally minimal and did not prove a
semantic DAG PASS. It did prove the machine-readable stdout property that
reopened #168: `json.loads(stdout)` succeeds and diagnostic lines are absent
from the captured stderr/stdout stream used by the CLI receipt.

Syntax and whitespace checks:

```text
uv run python -m py_compile \
  src/tau_coding/cli.py \
  src/tau_coding/diagnostics.py \
  src/tau_coding/generic_dag.py

git diff --check
```

Result:

```text
both exited 0
```

## Evidence Boundary

- mocked: no
- live: yes for local CLI subprocess stdout/stderr capture and JSON parsing
- provider_live: no
- proves: diagnostics no longer corrupt `tau dag-run` / `tau run` machine JSON
  stdout; diagnostic logging tests still pass
- does_not_prove: all diagnostics are complete across every Tau subsystem; the
  full repository test suite is green; provider/model behavior; full immutable
  Tau product goal
