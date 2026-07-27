# Issue 95 Runtime Conformance Proof

Ticket: <https://github.com/grahama1970/tau/issues/95>

This bundle captures the backend conformance and adversarial proof used to
close #95 after #94 was closed.

## Commands

```text
uv run pytest -q tests/test_runtime_backend_contracts.py tests/test_local_runtime_backend.py tests/test_herdr_runtime_backend.py tests/test_tmux_runtime_backend.py tests/test_runtime_event_bridge.py tests/test_git_worktree_leases.py
........................................................................ [ 27%]
........................................................................ [ 55%]
........................................................................ [ 83%]
...........................................                              [100%]
259 passed in 20.17s

uv run python scripts/run-tmux-runtime-smoke.py --out-dir /tmp/tau-issue-95-tmux-smoke-20260727
status: PASS
mocked: false
live: true
provider_live: false
side_effect_count: 1
paste_attempt_count: 1
unowned_cleanup_blocked: true
server_cleanup.post_verified_absent: true

uv run python scripts/run-herdr-runtime-smoke.py --out-dir /tmp/tau-issue-95-herdr-smoke-20260727
status: PASS
mocked: false
live: true
provider_live: false
unowned_cleanup_blocked: true
workspace_cleanup.status: PASS
workspace_cleanup.post_verified_absent_count: 2
```

## Artifacts

- `summary.json`
- `tmux-runtime-smoke-receipt.json`
- `herdr-runtime-smoke-receipt.json`
- `herdr-cleanup-receipt.json`

## Evidence Boundary

mocked: no

live: yes, local runtime paths plus real tmux and real Herdr development-host
smokes.

provider_live: no

This proves the current backend contract/adversarial matrix across runtime
contracts, local runtime, Herdr runtime, tmux runtime, runtime-event replay, and
Git worktree leases, with live tmux and Herdr smoke receipts.

This does not prove provider/model semantic quality, production security
certification, or future backend implementations.
