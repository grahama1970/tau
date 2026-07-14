# Herdr Native Runtime Events

Tau's Herdr runtime backend can use Herdr's local AF_UNIX socket API for
`pane.agent_status_changed` events. Herdr observes the pane; Tau preserves DAG,
receipt, and completion authority.

## Activation

Native events activate only when `herdr status server --json` proves all of the
following for the requested session:

- the server is running and compatible with the installed CLI;
- the server version/protocol pair is one Tau has verified (`0.7.1/14` or
  `0.7.1/15`);
- the reported socket is an existing Unix socket;
- the reported session matches the requested session.

Herdr 0.7.1 protocols 14 and 15 represent the default session as `null`; Tau
accepts that encoding only for the default session and only for those verified
version/protocol pairs. Named sessions must match exactly. The advertised
native capability hash also binds the resolved absolute socket, socket device,
inode, and change time, plus session, server version, and protocol. Tau rechecks the socket
identity before connecting so an endpoint lease cannot be reused with a
replaced or different Herdr server binding.

Otherwise the backend advertises `native_events=false` and retains bounded
polling. If a verified native stream later fails during setup or delivery, the
same `wait_event()` call falls back to bounded polling and records the failure
code under `observation.native_event_fallback`.

## Binding And Evidence

Each subscription is limited to the exact pane in the Tau endpoint lease. Tau
rejects events whose session, workspace, pane, or declared agent conflicts with
the lease. The normalized event stores a hash of the complete Herdr payload and
a bounded projection containing only pane, workspace, status, and agent fields.
Titles, terminal output, prompts, and credentials are not copied into the run
journal.

Herdr events are diagnostic evidence. Text such as `PASS`, `done`, or `tests
passed` never accepts a node, activates an edge, satisfies a terminal, or
replaces a Tau node receipt.

## Live Smoke

```bash
uv run python scripts/run-herdr-native-event-smoke.py \
  --out /tmp/tau-herdr-native-event-smoke.json
```

The smoke creates and removes a Tau-owned workspace and pane. It invokes no
model provider. A passing receipt has `mocked:false`, `live:true`,
`provider_live:false`, and `node_completion_claimed:false`.

## Non-Claims

- Native runtime observations do not prove agent correctness or node completion.
- Polling fallback does not prove native delivery occurred.
- Verified protocol compatibility is limited to the explicitly supported Herdr
  protocols and does not prove future compatibility.
