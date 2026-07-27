# Issue 72 Program Epic Status

Ticket: <https://github.com/grahama1970/tau/issues/72>

## Result

This run does **not** close #72. The labeled `epic:72` child tranche currently
has zero open children, but #72 is a program-level epic and its own completion
criteria remain broader than those children.

## Current Reconciliation

- `epic72-labeled-children.json`: live `gh issue list --label epic:72`
  snapshot. All listed children are closed.
- `open-issues.json`: live open issue snapshot; remaining open Tau issues are
  #72 and #150.
- `issue-72-state.json`: live #72 issue state and labels.
- `summary.json`: machine-readable status with `closable=false`.

## Why It Remains Open

The closed child tranche proves important runtime hardening slices, but #72 still
requires deterministic proof of the full program workload, including secure
execution hardening, resource leases, bounded adaptive revision, sprite-sheet
conformance with six blocked and eight killed frames, targeted repair, project
profile authority, a real worker plus controlled-data denial/correction demo,
signatures/RBAC/audit hardening, and the `$tau` runtime handshake.

No deterministic local proof bundle in this run demonstrates those program
completion criteria. Closing #72 now would be a false green.

## Evidence Boundary

`mocked=false`, `live=true`, `provider_live=false` for the GitHub tracker
reconciliation. No provider/model call or implementation proof was run for the
remaining program criteria.
