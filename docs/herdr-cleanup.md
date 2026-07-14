# Tau Herdr Cleanup

Tau cleanup is scoped to one run directory. It reads only the run's
`runtime-manifest.json` and closes resources that the manifest proves are
run-owned.

Cleanup receipts record both `runtime_manifest` and
`runtime_manifest_sha256`, so operators can tie cleanup candidates and applied
actions to the exact ownership manifest that was evaluated.

## CLI

```bash
uv run tau herdr-cleanup audit --run-dir path/to/run
uv run tau herdr-cleanup dry-run --run-dir path/to/run
uv run tau herdr-cleanup apply --run-dir path/to/run
uv run tau herdr-cleanup gc --run-dir path/to/gc-receipts
uv run tau herdr-cleanup gc --run-dir path/to/gc-receipts --apply
```

`audit` and `dry-run` do not mutate Herdr. `apply` runs
`herdr workspace close <workspace_id>` for candidate run-owned workspaces, then
verifies the post-condition with `herdr workspace get <workspace_id>`. The
cleanup receipt is `PASS` only when the close command succeeds and the follow-up
get reports `workspace_not_found`.

`gc` is Herdr workspace garbage collection for stale Tau proof workspaces. It
uses `herdr workspace list`, selects only known Tau / real-world-sanity provider
workspace label prefixes, protects the current workspace, focused workspaces,
and workspaces whose Herdr status is not `done` or `idle`, then writes
`herdr-gc-receipt.json`. `gc` without `--apply` is a dry run. `gc --apply`
runs `herdr workspace close <workspace_id>` and verifies each closed workspace
with `herdr workspace get <workspace_id>` expecting `workspace_not_found`.

The initial GC label prefixes are:

- `rw-sanity-generic-provider-`
- `rw-sanity-provider-`
- `tau-live-provider-`
- `tau-provider-dag-`
- `tau-generic-provider-`
- `tau-traycer-`

## Supported Manifest Sources

Cleanup candidates can come from:

- `provider_sessions`
- `visible_subagents`
- `provider_session_states`
- `readiness_records`

The last two allow `provider-readiness-poc` lifecycle runs to be cleaned up
without requiring a provider-DAG final receipt.

## Safety Rules

- No regex or global cleanup is performed.
- The current `HERDR_WORKSPACE_ID` is skipped unless
  `--include-current-workspace` is explicitly passed.
- GC protects focused workspaces and non-`done`/non-`idle` workspaces.
- Session stop/delete is still reported as a candidate only; Tau does not apply
  session deletion until session ownership is recorded strongly enough.
- Git worktrees are not deleted by this command.

## Proof Boundary

`mocked:false`, `live:true` on an apply receipt means Tau invoked real Herdr
cleanup commands for run-owned resources and verified applied workspaces are no
longer addressable through Herdr. It does not prove global Herdr cleanup, Git
worktree deletion, session deletion, remote Tailscale monitoring, ticket
closure, or provider semantic completion.

`tau.herdr_gc_receipt.v1` with `mocked:false`, `live:true`, and
`post_verified_absent_count == applied_action_count` proves Tau used Herdr to
close stale Tau-labeled workspaces and verified they were no longer addressable.
It does not prove cleanup of arbitrary non-Tau Herdr workspaces, proof artifact
deletion, Git worktree deletion, or provider/model semantic quality.

Cleanup and GC target a named Herdr session. The CLI accepts `--session` and
uses the explicit `default` session when omitted for compatibility. Tau records
`backend_session_id` and invokes workspace list/close/get as
`herdr --session <name> ...`; it does not rely on the focused session. Apply
cleanup blocks when the manifest session differs from the selected session, and
GC approval targets include the session so approval cannot be replayed across
Herdr namespaces.

Current-workspace protection applies only when `HERDR_SESSION` identifies the
same named session being cleaned. Workspace IDs are session-local, so an
ambient workspace ID never suppresses cleanup in a different selected session.
