# Issue 141 Peer SSE Transport Proof

Ticket: <https://github.com/grahama1970/tau/issues/141>

This bundle captures a non-mocked local loopback HTTP/SSE proof for the Tau
TUI peer transport slice.

## Commands

```text
uv run pytest -q tests/test_tui_peer_queue.py tests/test_tui_peer_transport.py
.......                                                                  [100%]
7 passed in 1.97s

uv run ruff check src/tau_coding/tui/peer_queue.py src/tau_coding/tui/peer_transport.py tests/test_tui_peer_queue.py tests/test_tui_peer_transport.py
All checks passed!

uv run python -m py_compile src/tau_coding/tui/peer_queue.py src/tau_coding/tui/peer_transport.py
exit 0

git diff --check
exit 0
```

## Artifacts

- `summary.json`: run summary and proof boundary.
- `tau-a-peer-message.sse`: SSE frame delivered to `tau-a`.
- `tau-b-peer-message.sse`: SSE frame delivered to `tau-b`.
- `tau-a-queue.json`: durable queue for `tau-a`.
- `tau-b-queue.json`: durable queue for `tau-b` after idle drain.
- `scratch/tau-b/a-to-b/work-order.json`: scratch-confined work order.
- `scratch/tau-b/a-to-b/candidate.patch`: scratch-confined candidate patch.

## Evidence Boundary

mocked: no

live: yes, local loopback HTTP/SSE only.

This proves two local Tau peer transports exchange typed envelopes over
HTTP/SSE in both directions, the durable queue does not drain while busy, the
queue transitions to `awaiting_approval` while idle, the idle drain writes the
work order and candidate patch under a scratch root, the approval gate blocks
with `BLOCKED` before non-scratch effects, and the queue state is readable from
disk after restart.

This does not prove full rendered Textual TUI integration, semantic correctness
of a peer-proposed patch, or human approval UI rendering for queued diffs.
