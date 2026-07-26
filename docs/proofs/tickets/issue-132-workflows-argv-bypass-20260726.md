# Issue #132 Proof: Workflow CLI Argv Bypass

Issue: https://github.com/grahama1970/tau/issues/132

## Change

Startup extension flag parsing now bypasses the `workflows` command and the
`dag-view*` command family. Their subcommand parsers receive the original argv
so command-specific options such as `--json`, `--repo`, `--goal`, `--run-dir`,
and `--output` are no longer consumed as startup extension flags.

## Deterministic Proof

Commands were run from clean main worktree after the patch:

```bash
uv run pytest -q tests/test_workflow_cli.py
```

Result:

```text
7 passed in 19.93s
```

```bash
uv run pytest -q \
  tests/test_cli.py::test_cli_dag_view_capabilities_is_read_only \
  tests/test_cli.py::test_cli_dag_view_rejects_non_numeric_event_range_without_traceback \
  tests/test_cli.py::test_cli_dag_view_snapshot_projects_history_older_than_visible_event_window \
  tests/test_cli.py::test_cli_dag_viewer_link_exports_project_dag_viewer_contract
```

Result:

```text
4 passed in 0.81s
```

```bash
uv run python -m py_compile src/tau_coding/cli.py tests/test_workflow_cli.py tests/test_cli.py
```

Result: exit 0.

```bash
git diff --check
```

Result: exit 0.

Live local documented rung-1 command:

```bash
uv run tau workflows run repository-readiness \
  --repo "$repo" \
  --goal "Determine whether this checkout is ready for focused work." \
  --require-clean \
  --run-dir "$run_dir" \
  --no-browser-open
```

Result:

```text
status=PASS
workflow_id=repository-readiness
receipt=/tmp/tau-issue-132-rung1.hzVwRx/workflow-receipt.json
command_status=0
run_dir=/tmp/tau-issue-132-rung1.hzVwRx
repo=/tmp/tau-issue-132-repo.low9na
```

## Evidence Boundary

mocked: no
live: local process and local Git fixture; no external service calls

This proves the argv handling failure for `workflows` is repaired and that a
documented rung-1 workflow command writes a PASS workflow receipt. It also
checks the affected DAG-view command family. It does not prove provider/model
quality, browser rendering, or the full immutable five-DAG goal.
