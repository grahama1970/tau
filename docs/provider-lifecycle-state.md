# Tau Provider Lifecycle State

Tau normalizes provider readiness into a reusable provider-session lifecycle
record:

```json
{
  "schema": "tau.provider_session_state.v1",
  "provider_id": "codex",
  "workspace_id": "w1",
  "pane_id": "w1:p5",
  "terminal_id": "term_...",
  "state": "ready",
  "ready": true,
  "source": "herdr_process_info",
  "process": {
    "pid": 12345,
    "alive": true,
    "foreground": true,
    "command": "codex",
    "argv": ["codex", "--cd", "/repo"],
    "cwd": "/repo"
  },
  "auth": {
    "status": "unknown",
    "method": "unknown"
  },
  "interstitial": {
    "present": false,
    "kind": null,
    "safe_actions": []
  },
  "provider_api": {
    "available": false,
    "endpoint": "none",
    "last_event_type": null
  }
}
```

## States

Valid lifecycle states:

- `starting`
- `ready`
- `running`
- `waiting_on_input`
- `waiting_on_approval`
- `auth_required`
- `interstitial`
- `blocked`
- `exited`
- `crashed`
- `unknown`

## Semantics

- `ready:true` is only set when normalized state is `ready`.
- Visible prompt text is diagnostic, not a readiness gate.
- Auth prompts map to `auth_required`.
- Known launch prompts map to `interstitial`.
- Missing or non-alive foreground provider processes in the final readiness
  record map to `crashed`, even if the raw provider readiness claim says
  `ready`. A non-alive process cannot be treated as schedulable.
- Early transient empty process samples are still preserved in
  `evidence.readiness_probe_samples`; repeated samples are captured before the
  final readiness state is decided.
- Non-matching foreground provider processes map to `blocked`.
- Provider-native APIs are represented but currently reported as unavailable by
  this Herdr process-info adapter.

## Sampling

Provider readiness probes sample Herdr `pane get` and `pane process-info`
multiple times before deciding readiness. The probe stops early when the
expected foreground command is observed. Each `tau.provider_readiness.v1`
artifact records:

- `evidence.readiness_probe_attempt_count`
- `evidence.readiness_probe_samples`

This preserves transient process-info failures as evidence while avoiding a
single early empty process sample becoming the final readiness state.

## Artifacts

`tau provider-readiness-poc` writes:

```text
readiness/<provider>.readiness.json
readiness/<provider>.session-state.json
run-receipt.json
runtime-manifest.json
```

The readiness receipt points to the session-state file, and
`tau provider-readiness-inspect <run-dir>` / `tau run-status <run-dir>` include
compact lifecycle summaries. The compact summary includes provider identity,
workspace/pane/terminal ids, lifecycle state, readiness, source, observation
time, process liveness, foreground command, auth status, interstitial state,
provider API availability, visible log path, readiness path, and provider event
log path when present. When `run-status` loads lifecycle artifacts from files,
it also reports SHA-256 fingerprints for the readiness and session-state files
so operators can tie the lifecycle summary to exact telemetry artifacts.

## Proof Boundary

`tau.provider_session_state.v1` proves normalized lifecycle telemetry from
Herdr process/pane evidence. It does not prove semantic task completion,
provider-native `session.ready` events, remote Tailscale monitoring, ticket
closure, or production repository mutation.
