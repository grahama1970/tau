# Tau issue #141 proof notes

Ticket: <https://github.com/grahama1970/tau/issues/141>

Implemented Tau-side substrate:

- `PeerEnvelope` / `build_peer_envelope()` defines a typed TUI peer envelope.
- `DurablePeerQueue` persists per-harness queue state to JSON.
- Queue drain is idle-gated: busy instances leave items queued.
- Idle drain moves work to `awaiting_approval` and records a required human
  approval gate before any worktree effect.
- `sse_event()` serializes a Server-Sent Event frame for the envelope payload.

Focused checks:

```text
uv run pytest -q tests/test_tui_peer_queue.py
....
4 passed in 0.43s

uv run ruff check src/tau_coding/tui/peer_queue.py tests/test_tui_peer_queue.py
All checks passed!

uv run python -m py_compile src/tau_coding/tui/peer_queue.py
exit 0
```

Generated proof artifact:

```text
docs/proofs/tickets/issue-141-peer-idle-queue-20260726/summary.json
busy_drained: []
idle_state: awaiting_approval
approval_status: BLOCKED
reloaded_state: awaiting_approval
```

Evidence boundary:

- This proves the reusable peer envelope, SSE frame serialization, durable queue
  persistence, idle-gated state transition, and approval-block state.
- This does not prove a full two-process TUI SSE server, scratch-worktree patch
  execution, or rendered TUI integration.
