# Runtime Backend Contracts

Tau compiles every DAG node into an explicit `tau.runtime_requirement.v1`.
The requirement names the selected backend, interaction mode, required backend
capabilities, session scope, and acceptable observation sources. Backend choice
is therefore part of the immutable `DagPlan`; ambient terminal state does not
select a backend.

Project DAG nodes may set `runtime_backend` explicitly. During migration,
command-backed `executor: local` nodes default to `local`, and
`executor: provider` nodes default to the existing `herdr` provider lane. The
resolved backend is always persisted in `DagPlan`. Other executable executor
labels must declare `runtime_backend`; virtual and human nodes compile with
`backend: none` because they do not launch a runtime endpoint.

Commandless nodes that declare `persistent_subagent` require an interactive
runtime and default to `herdr` when `runtime_backend` is omitted. A node with an
inline `command_spec` remains a bounded one-shot Tau tick: the command wrapper
must emit the persistent-surface receipts, while the surface itself remains
context rather than proof. A DAG may name another registered interactive backend
explicitly; capability negotiation still blocks a backend that cannot satisfy
the requirement.

Runtime implementations register through `RuntimeBackendRegistry` and publish
`tau.runtime_backend_capabilities.v1`. Capability negotiation fails closed with
`tau.runtime_capability_decision.v1` when a backend is unknown or cannot satisfy
the compiled requirement. Backends positively declare
`supported_session_scopes`; an undeclared or misspelled scope is rejected rather
than relying on a denylist.

The bounded-ready-queue checks the compiled requirement before dispatch. A local
command adapter accepts only the canonical local one-shot requirement, and the
existing provider adapter accepts only its canonical Herdr one-shot requirement.
Interactive, persistent, unknown, or otherwise mismatched requirements block
before command artifacts or a subprocess are created.

## Local Runtime Backend

`LocalRuntimeBackend` is the one-shot reference implementation. Generic DAG
commands and non-secure project handoff commands now launch through this backend
instead of importing process launch mechanics into their scheduler callbacks.
The backend preserves the existing process-group cancellation, timeout,
working-directory, environment, stdin, stdout, stderr, and return-code behavior.

Each launch records normalized runtime evidence:

- `runtime-endpoint-lease.json` binds the local endpoint to the run, node,
  attempt, work order, goal, and backend capability hash;
- `runtime-submit-receipt.json` records command/input delivery separately from
  execution outcome;
- `runtime-event.json` records the observed process terminal state and
  liveness;
- `runtime-capture.json` records bounded command output and exit metadata.

These artifacts do not replace the node receipt. A zero exit code and an
`EXITED` runtime event cannot advance the DAG unless Tau independently admits
the required node receipt.

The common contract family also defines endpoint leases, submit receipts,
runtime events and state projections, reconciliation receipts, and Git worktree
leases. Delivery, runtime observation, and DAG completion remain separate:

Endpoint leases bind `goal_hash` to `DagPlan.runtime_goal_hash`, the complete
SHA-256 digest of the canonical goal binding. This remains available when a
legacy project declared a non-cryptographic goal label or a generic DAG omitted
`goal_hash`; backends must not copy those raw source values into a lease.

```text
backend accepted work order != node executed
runtime says done != receipt admitted
validated receipt -> Tau may advance the DAG
```

The local adapter proof is a development-host process proof, not a sandbox or
secure-executor claim. Tmux adapters, persistent runtime events, worktree
leases, and restart reconciliation remain in later children of issue #84.

## Herdr Runtime Backend

`HerdrRuntimeBackend` implements the interactive runtime contract for a named
Herdr session. The session is mandatory and is included explicitly in every
Herdr CLI invocation. It does not infer a session from the focused terminal or
ambient workspace.

The backend:

- creates Tau-owned workspaces and tabs and records their exact IDs;
- creates unique attempt-bound agent names even when human-facing labels collide;
- reserves attempt identity before `agent start`, preventing duplicate endpoint
  launch for the same run/node/attempt/execution identity;
- binds workspace, tab, pane, terminal, session, work order, goal, and attempt
  identity into `tau.runtime_endpoint_lease.v1`;
- submits work-order text at most once per endpoint lease and reports uncertain
  delivery as `INDETERMINATE` rather than retrying the full text;
- captures bounded visible pane text as diagnostic evidence only;
- records visible auth/interstitial markers as diagnostics without allowing pane
  prose to change the native/process-derived runtime state;
- observes exact pane identity, native Herdr agent state, and foreground process
  state without using pane prose as completion truth;
- treats one empty process sample as `UNKNOWN`, not confirmed process death;
- caps observation command timeouts by the caller's `wait_event` deadline;
- preserves failed observation as `UNKNOWN` unless Herdr specifically reports
  `pane_not_found`;
- maps malformed pane/process response payloads to `UNKNOWN` rather than raising
  them out of the runtime observation loop;
- requires exact lease-bound `tau.runtime_cleanup_authorization.v1` before pane
  termination;
- requires `pane_not_found` after close before claiming endpoint absence; and
- delegates workspace cleanup to the existing Herdr workspace-lease gate.

Run the development-host smoke with:

```bash
uv run python scripts/run-herdr-runtime-smoke.py \
  --out-dir /tmp/tau-herdr-runtime-smoke \
  --session default
```

The smoke uses real Herdr, creates two same-label workspaces, proves their exact
IDs differ, spawns a shell endpoint, submits and captures one marker, verifies a
wrong-session lookup fails, verifies unowned endpoint cleanup is blocked, and
post-verifies endpoint and workspace absence. It does not complete a DAG node,
exercise provider semantics, prove sandbox isolation, or prove crash-safe
restart reconciliation.

The smoke marks `live:true` only when the requested binary resolves to the same
installed executable as `herdr`. A wrapper, fixture, missing command, or other
PATH executable is recorded as `mocked:true`, `live:false`, and blocks the smoke.

## Tmux Runtime Backend

`TmuxRuntimeBackend` implements the same interactive contract through one
explicit Tau-owned tmux server (`tmux -L <server>`). It never selects the
ambient `$TMUX` server. Each run scope records an exact session ID and each node
attempt records exact window and pane IDs; labels are diagnostic names, not
ownership evidence.

Work-order delivery accepts one printable line, rejects newline/control
characters, and derives an endpoint-specific named tmux buffer. One bounded
tmux command queue loads the buffer, pastes it to the exact pane, and sends
Enter. Tau reserves and caches the submit result before any caller can repeat
the work order. If acknowledgement is lost after mutation starts, delivery is
`INDETERMINATE` and automatic retry remains forbidden. This avoids converting
transport uncertainty into duplicated agent input.

The lease binds the configured server name to a frozen socket root plus the
observed socket path, server PID, start time, and tmux version. Their canonical
hash is `backend_session_id`. Non-creating commands use tmux's `-N` guard and
recheck the server incarnation so a restarted server under the same name cannot
inherit an old lease.

Tmux inventory is process evidence. A successful complete inventory that omits
the exact pane is `DEAD`; a failed, timed-out, or malformed inventory is
`UNKNOWN`. Pane output remains diagnostic and cannot mark a DAG node complete.
Owned inventory likewise fails closed when the tmux server cannot be inspected,
rather than returning a misleading empty list.

Capture is bounded by both requested terminal lines and a configured byte
ceiling. The receipt records returned lines/bytes and whether deterministic
UTF-8 truncation occurred.

Termination requires the same lease-bound cleanup authorization fields used by
the Herdr adapter. Tau kills only the exact pane and then requires a successful
inventory proving that pane ID absent. Persistent scope/session cleanup remains
explicit and is not inferred from endpoint termination.

Run the development-host smoke with:

```bash
uv run python scripts/run-tmux-runtime-smoke.py \
  --out-dir /tmp/tau-tmux-runtime-smoke
```

The smoke uses a dedicated real tmux server, creates two same-label scopes,
spawns one shell pane, and deliberately hides the acknowledgement after one
successful real paste. A second `submit` call must return the cached receipt,
the paste count must remain one, and a filesystem side effect must contain one
byte. It also checks wrong-server isolation, cleanup authorization, owned
inventory, exact pane absence, and dedicated-server absence. It does not prove
DAG-node completion, provider/model quality, restart reconciliation, sandbox
isolation, or production readiness.
