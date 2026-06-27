# Loop2 Alignment Experiment

This directory is for proving small Tau loop changes before touching production
loop code.

Current proven slices:

- fixture Tau receipt wiring with `FakeProvider`
- live Chutes provider call through Tau's provider/session stack
- live delegated Scillm/OpenCode repair through native Loop2
- delegated artifact sanitization with `tau-sanitization.json`
- delegated Scillm auth preflight before native Loop2 runner invocation
- `tau loop2-inspect --loop2-inspect-validate` native validation for delegated runs

The `FakeProvider` path is fixture evidence only. The live delegated proof path
uses the real Scillm proxy/OpenCode transport, repairs `src/buggy_math.py`, runs
the configured pytest command, writes native Loop2 artifacts, and validates the
Tau sidecar sanitization evidence.

Current live proof index:

- `live-sidecar-20260626T182740Z/live-sidecar-proof.json`
- `live-env-auth-20260626T183449Z/live-env-auth-proof.json`
- `live-auth-preflight-20260626T184040Z/live-auth-preflight-proof.json`
