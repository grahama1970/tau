# tau#294 Close Proof: Strict DAG Transition Batch and Replay Validation

Issue: https://github.com/grahama1970/tau/issues/294
Lease: 20260802T174323Z-codex-294
Generated: 2026-08-02T19:08:00Z

## Scope

This repair hardens `tau.dag_transition_batch.v1` so malformed transition
effects cannot be committed, replayed, or silently ignored as authoritative DAG
state.

The contract now validates:

- exact top-level and nested transition schemas
- declared edge/node/deadline IDs
- closed transition state vocabulary
- reason-code strings
- duplicate and conflicting effects
- finite wall-clock deadline payloads without string/int coercion
- canonical replay events
- receipt path existence and `sha256:` hash match
- replayed node result payloads through the strict #293 attempt-result contract

## Code Paths Changed

- `src/tau_coding/dag_runtime/transition.py`
- `src/tau_coding/dag_runtime/replay.py`
- `src/tau_coding/dag_runtime/run_store.py`
- `src/tau_coding/dag_runtime/scheduler.py`
- `src/tau_coding/dag_runtime/project_transition.py`
- `src/tau_coding/dag_viewer/http.py`
- `tests/test_dag_transition_validation.py`
- `tests/test_dag_runtime_admission_table.py`
- `docs/proofs/tickets/issue-294-transition-replay-validation/issue-294-live-readback.py`

## Deterministic Proof

Focused transition/replay slice:

```text
uv run pytest -q tests/test_dag_transition_validation.py tests/test_dag_runtime_replay.py
17 passed in 0.66s
```

Focused admission compatibility slice:

```text
uv run pytest -q tests/test_dag_runtime_admission_table.py tests/test_dag_transition_validation.py
24 passed in 0.82s
```

Broader DAG runtime/project/viewer slice:

```text
uv run pytest -q tests/test_dag_runtime_scheduler.py tests/test_dag_runtime_run_store.py tests/test_dag_viewer_historical.py tests/test_dag_viewer_server.py tests/test_project_dag_join_policies.py tests/test_dag_transition_validation.py tests/test_dag_runtime_replay.py
107 passed in 23.69s
```

Full repository test suite:

```text
uv run pytest -q
3521 passed in 574.70s (0:09:34)
```

Formatting/static checks:

```text
uv run ruff check src/tau_coding/dag_runtime/transition.py src/tau_coding/dag_runtime/replay.py src/tau_coding/dag_runtime/scheduler.py src/tau_coding/dag_runtime/run_store.py src/tau_coding/dag_runtime/project_transition.py src/tau_coding/dag_viewer/http.py tests/test_dag_transition_validation.py docs/proofs/tickets/issue-294-transition-replay-validation/issue-294-live-readback.py
All checks passed!
```

```text
uv run mypy src/tau_coding/dag_runtime/transition.py src/tau_coding/dag_runtime/replay.py src/tau_coding/dag_runtime/scheduler.py src/tau_coding/dag_runtime/run_store.py src/tau_coding/dag_runtime/project_transition.py src/tau_coding/dag_viewer/http.py docs/proofs/tickets/issue-294-transition-replay-validation/issue-294-live-readback.py
Success: no issues found in 7 source files
```

```text
git diff --check
exit 0
```

## Live Non-Mocked Sanity

Command:

```text
uv run python docs/proofs/tickets/issue-294-transition-replay-validation/issue-294-live-readback.py
```

Artifact:

```text
docs/proofs/tickets/issue-294-transition-replay-validation/live-readback.json
```

Readback summary:

```json
{
  "schema": "tau.issue_294.live_readback.v1",
  "mocked": false,
  "live": true,
  "valid_run": {
    "status": "PASS",
    "verdict": "PASS"
  },
  "valid_replay": {
    "run_status": "PASS",
    "transition_receipt_count": 1,
    "receipt_hash_matches_file": true
  },
  "receipt_mutation_rejected": {
    "error": "dag_transition_receipt_hash_mismatch"
  },
  "live_invalid_transition_rejected": {
    "error": "dag_transition_unknown_cancellation"
  },
  "invalid_transition_not_committed": {
    "scheduler_transition_committed_count": 0
  }
}
```

The live check is `mocked:false` and `live:true`. It does not call a paid model
provider; it exercises the real Tau scheduler, SQLite journal, receipt files,
and replay reducer.

## What This Proves

- Store commits reject malformed transition payloads before appending journal
  events.
- Scheduler-generated live transitions are validated against the active plan and
  active deadlines before persistence.
- Replay re-reads transition receipt files and blocks on path/hash mismatch.
- Replay applies transition effects fail-closed for unknown edge, node,
  cancellation, and deadline IDs.
- Replayed terminal node results are re-admitted through the strict #293
  attempt-result contract.
- Project DAG policy has an explicit compatibility adapter for exact duplicate
  same-state join effects while conflicting effects remain fail-closed.
- Viewer public errors remain stable while replay preserves lower-level
  transition failure codes internally.

## What This Does Not Prove

- The full Tau immutable goal is not accepted by this ticket alone.
- This does not prove provider/model semantic correctness.
- This does not close unrelated open Tau issues.
- This does not prove live paid-provider behavior.

## Worktree Boundary

Worktree audit before close:

```text
/home/graham/workspace/experiments/agent-skills/skills/best-practices-github-ticket/scripts/audit-worktrees.sh --repo /home/graham/workspace/experiments/tau --json
{"ok":false,"repo":"/home/graham/workspace/experiments/tau","total":30,"tmp":1,"detached":3,"prunable":0,"dirty_secondary":2,"tmp_paths":["/tmp/tau-immutable-goal-main-20260721T000650Z"],"prunable_paths":[],"dirty_secondary_paths":["/home/graham/workspace/experiments/tau-causal-replay","/home/graham/workspace/experiments/tau-gs001"]}
```

Those worktrees pre-existed this ticket and are retained rather than removed.
The unrelated local `.ask_artifacts/` directory also remains unstaged.
