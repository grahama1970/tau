# Issue 272 Stale Workspace Reads

## Behavior Added

Tau now records `tau.workspace_read_set.v1` at the node-attempt boundary for
Tau-owned source bindings and explicit adapter read receipts. When an admitted
workspace change is recorded from a node result, the run store compares the
accepted previous and new hashes against active reader attempts in the same
repository, worktree, and normalized path.

Matching stale reads emit durable `tau.workspace_change_signal.v1` records.
Nodes with `stale_read_policy=require_reconciliation` or `block` cannot settle
`PASS` while a relevant signal is unresolved. Reconciliation requires either a
reread bound to the new hash or deterministic outside-scope evidence; a plain
model statement is rejected.

## Non-Goals Preserved

- No OS-wide file tracing was added.
- Non-overlapping paths, different worktrees, and unchanged hashes are not
  serialized or treated as conflicts.
- Legacy nodes remain observe-only when `stale_read_policy` is absent.

## Focused Proof

```text
uv run pytest tests/test_workspace_stale_reads.py -q
6 passed
```

The focused fixtures exercise concurrent writer/reader signaling, fail-closed
unresolved stale reads, reread reconciliation, model-statement rejection,
non-overlapping paths, different worktrees, unchanged hashes, and reopened-store
readback of unresolved signals.

## Adjacent Proof

```text
uv run pytest tests/test_workspace_stale_reads.py tests/test_dag_runtime_scheduler.py tests/test_dag_runtime_run_store.py tests/test_dag_runtime_admission_table.py tests/test_node_input_manifest.py -q
73 passed
```

## Full Suite Proof

```text
uv run pytest -q
3415 passed in 483.21s (0:08:03)
```
