# Tau Run Status

`tau run-status <run-dir>` is a read-only inspection surface for Tau proof runs.
It summarizes known receipt artifacts without launching providers, mutating a
workspace, closing tickets, or cleaning Herdr resources.

## Inputs

The command accepts one run directory and looks for known Tau artifacts:

- `run-receipt.json`
- `runtime-manifest.json`
- `checkpoint.json`
- `current-state.json`
- `herdr-cleanup-receipt.json`
- `approval-gate-receipt.json`
- `orchestration-evidence-receipt.json`
- `planner-receipt.json`
- `real-world-sanity-receipt.json`
- `suite-receipt.json`
- `campaign-receipt.json`

Provider lifecycle state files referenced by a runtime manifest are loaded and
summarized when present.

## Output

The command prints `tau.run_status.v1` JSON with:

- detected run type
- overall status and mocked/live boundary
- required artifact presence
- run receipt summary
- checkpoint or current-state summary
- generic DAG node counts, blocked/resumed/dispatched counts, and work-order
  hash summaries when present
- generic DAG node timing summaries, including `started_at`, `finished_at`, and
  `duration_seconds` when the run receipt recorded them
- generic DAG node failure summaries, including per-node `attempt_count`,
  `error_count`, and `errors` when present
- provider-pane allocation summaries when present, including provider count,
  prompt-observed count, pane IDs, work-order paths, and visible logs
- structured provider-readiness summaries when present, including readiness
  record count, provider session state count, ready count, state counts, and
  whether visible prompt text was used only as diagnostics
- compact provider-session lifecycle records with source, observed time,
  process liveness, auth/interstitial state, provider API availability, and
  readiness/event log paths when present
- provider lifecycle summaries include SHA-256 fingerprints for loaded
  provider-readiness and provider-session-state artifacts when those files are
  available
- provider-DAG and planner-only DAG summaries when present
- event log count
- provider session state summaries
- cleanup, approval, orchestration evidence, and DAG stress summaries when present
- cleanup summaries include the source `runtime_manifest` path and
  `runtime_manifest_sha256` when present
- approval-gate packet summaries, including packet path, approved action, human
  id, target id, evidence count, expiration timestamp, and approval packet
  SHA-256 when present
- embedded provider-DAG cleanup summaries, including resource, candidate,
  applied-action, and post-verified-absent counts when the run receipt recorded
  a cleanup finalizer
- real-world sanity suite checks and nested post-cleanup summaries when present
- real-world sanity generic-DAG node totals, including aggregate dispatched,
  resumed, blocked, timed, and errored node counts across suite checks

The command exits non-zero for `BLOCKED`, `FAIL`, `FAILED`, or `MISSING` status.

## Boundaries

This command proves only that Tau can inspect already-written artifacts. It does
not prove new provider execution, semantic task completion, Herdr cleanup unless
a cleanup receipt is present, GitHub ticket closure, production repo mutation,
or browser/CDP UI rendering.
