# Tau Ask / Browser-Oracle Binding

Tau uses `$browser-oracle` for project-local WebGPT binding resolution.

Committed registry:

- `.ask/browser-oracles.yaml`

Machine-local binding:

- project: `tau`
- backend: `webgpt`
- state file: `~/.projects/browser-oracle/webgpt/tau.json`

Resolve and verify from the Tau repo root:

```bash
cd /home/graham/workspace/experiments/agent-skills/skills/browser-oracle
./run.sh doctor --from /home/graham/workspace/experiments/tau --backend webgpt --json
```

Use direct `$webgpt` from the Tau repo for creation/review bundles:

```bash
cd /home/graham/workspace/experiments/agent-skills/skills/webgpt
./run.sh submit -p tau /path/to/creation-bundle.md
```

## When Tau Should Escalate To WebGPT

Use direct `$webgpt` with this project binding when Tau is:

- crossing a phase boundary and the next phase changes architecture,
- blocked or repeating the same failure,
- error-spiraling into unrelated tests or implementation churn,
- validating complex harness, loop, TUI, chat, memory, or subagent architecture,
- using `$create-architecture` for a scoped missing implementation slice.

For `$create-architecture`, use creation framing, not pass/fail review framing:

- WebGPT asks clarifying questions if material ambiguity remains.
- WebGPT creates a finished solution bundle when ready.
- Tau/local project agent ports the bundle, fixes only mechanical integration
  issues, and proves behavior with deterministic local commands and receipts.
- WebGPT output is not closure proof by itself.

`$ask` can still be used when a later workflow truly needs its review runtime,
but Tau's current project-agent escalation path is direct `$webgpt` because the
extra ask wrapper added avoidable browser-bundle friction for this project.
