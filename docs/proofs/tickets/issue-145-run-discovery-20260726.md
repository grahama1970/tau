# Issue #145 Proof: Run Discovery And `--last`

Issue: https://github.com/grahama1970/tau/issues/145

## Scope

Implemented a local Tau run registry in `src/tau_coding/cli.py`:

- `tau runs list [--json] [--limit N]`
- registry path defaults to `~/.tau/runs.json`
- `TAU_RUN_REGISTRY` isolates registry state for tests
- workflow run/approve/resume/repair CLI paths update the registry when their payload includes `run_dir`
- `--last` resolves the newest registered run for `dag-view`, `dag-view-serve`, `dag-view-snapshot`, `dag-view-events`, `dag-viewer-link`, and `run-status`
- missing newest run directories fail closed as `tau_last_run_unavailable`

## Deterministic Checks

```text
$ uv run python -m py_compile src/tau_coding/cli.py tests/test_cli.py
exit 0
```

```text
$ uv run ruff check src/tau_coding/cli.py tests/test_cli.py
All checks passed!
exit 0
```

```text
$ uv run pytest tests/test_cli.py::test_cli_runs_list_and_last_resolve_recent_workflows -q
.                                                                        [100%]
1 passed in 5.19s
exit 0
```

```text
$ uv run pytest tests/test_cli.py::test_cli_dag_view_snapshot_projects_history_older_than_visible_event_window tests/test_cli.py::test_cli_dag_viewer_link_exports_project_dag_viewer_contract tests/test_cli.py::test_cli_dag_view_rejects_non_numeric_event_range_without_traceback -q
...                                                                      [100%]
3 passed in 0.93s
exit 0
```

```text
$ git diff --check
exit 0
```

## Proof Meaning

mocked: no

live: yes, local deterministic workflow execution only

provider_live: no

The new behavior test runs two real `repository-readiness` workflows through the Tau CLI against a temporary Git repository, then verifies:

- `tau runs list --json` returns both runs newest-first
- both recorded workflows are `repository-readiness`
- both recorded states are `PASS`
- `dag-viewer-link --last` resolves to the newest run directory
- `dag-view-snapshot --last` reads the newest run's SQLite DAG journal
- deleting the newest run directory makes `runs list` report `UNAVAILABLE`
- `dag-viewer-link --last` fails closed with `tau_last_run_unavailable`

This proves the requested CLI run discovery behavior and `--last` path resolution. It does not prove provider/model quality or production deployment readiness.
