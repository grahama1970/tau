# Issue #167 Proof: Bounded Project DAG Progress Writes

Issue: https://github.com/grahama1970/tau/issues/167

## Summary

`dag-progress.json` no longer embeds the full project DAG event history on every
progress write. The progress side channel now stores only a recent event window
while preserving aggregate state:

- `event_count`
- `recent_event_count`
- `events_window_limit`
- `events_truncated`
- `first_recent_event_index`
- `last_event`
- `node_progress`
- `active_subagents`
- `completed_subagents`

The durable scheduler journal is unchanged; this patch bounds only the progress
artifact's `events` field.

## Changed Files

- `src/tau_coding/project_dag.py`
- `tests/test_project_dag.py`

## Verification

mocked: no service or provider mocks. The benchmark-style test monkeypatches
the atomic writer to measure serialized payload size and write duration without
depending on disk speed; it still exercises the real progress-payload builder.

live: yes for local project DAG progress generation and run-status/viewer
surface checks.

Commands run from `/tmp/tau-main-issue-137.7nchbf`:

```text
$ uv run ruff check src/tau_coding/project_dag.py tests/test_project_dag.py tests/test_run_status.py
All checks passed!

$ uv run python -m py_compile src/tau_coding/project_dag.py tests/test_project_dag.py tests/test_run_status.py
exit 0

$ uv run pytest -q tests/test_project_dag.py::test_project_dag_progress_events_are_bounded_and_status_still_reads_aggregates tests/test_project_dag.py::test_project_dag_writes_live_subagent_progress_for_handoff_loop tests/test_project_dag.py::test_shared_project_scheduler_persists_running_progress tests/test_run_status.py::test_run_status_summarizes_project_dag_progress_before_final_receipt
....                                                                     [100%]
4 passed in 2.23s

$ uv run pytest -q tests/test_cli.py::test_cli_dag_view_snapshot_projects_history_older_than_visible_event_window
.                                                                        [100%]
1 passed in 0.75s

$ git diff --check
exit 0
```

## Coverage Against Ticket

- Bounded progress payload:
  `test_project_dag_progress_events_are_bounded_and_status_still_reads_aggregates`
  writes 1,000 synthetic progress updates and asserts `events` contains only the
  200-event recent window while `event_count` remains 1,000 and `last_event`
  stays accurate.
- Linear/bounded write behavior: the same test records serialized payload size
  and per-write duration across the run and asserts the final payload and total
  serialized bytes stay bounded by the fixed event window rather than growing
  quadratically with event history.
- Run-status surface: the same test calls `build_run_status()` on the bounded
  progress file and asserts active subagents, node progress, and completed count
  remain available from aggregate fields.
- Existing progress behavior:
  `test_project_dag_writes_live_subagent_progress_for_handoff_loop` and
  `test_shared_project_scheduler_persists_running_progress` still pass.
- Viewer surface: `test_cli_dag_view_snapshot_projects_history_older_than_visible_event_window`
  still passes, proving the DAG viewer snapshot path already projects history
  older than the visible event window from its authoritative journal.

## Remaining Non-Claims

This proof does not remove full scheduler-event history from final
`dag-receipt.json`; it bounds the side-channel progress artifact named in the
ticket and leaves the durable journal/archive path intact.
