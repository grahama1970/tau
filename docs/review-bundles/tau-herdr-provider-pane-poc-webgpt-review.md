# Tau + Herdr Provider Pane POC Review Bundle

## Review Request

Please review the current Tau approach for using Herdr as a visible subagent/session fabric.

Focus questions:

1. Is the separation of responsibilities correct: Tau owns queue/DAG/work orders/receipts/final status, while Herdr owns visible terminal workspaces, panes, input, reads, and state telemetry?
2. Is the current flat provider-pane POC the right next rung before implementing DAG execution with real task dispatch?
3. Are the current API and artifacts low-friction enough for a project agent to launch, monitor, and reason about provider subagents?
4. What should be changed before extending this into real DAG execution, ticket closure workflows, or remote Tailscale monitoring?
5. Are there risks in relying on visible TUI prompt detection for readiness, and what stronger readiness signal should Tau request from Herdr/OpenCode/Codex?

## Repository Links

Local Tau repo:

```text
/home/graham/workspace/experiments/tau
```

Tau GitHub remotes:

- `git@github.com:grahama1970/tau.git`
  - Web URL: `https://github.com/grahama1970/tau`
- `git@github.com:alejandro-ao/tau.git`
  - Web URL: `https://github.com/alejandro-ao/tau`

Local Herdr repo:

```text
/home/graham/workspace/experiments/herdr
```

Herdr GitHub remote:

- `git@github.com:ogulcancelik/herdr.git`
  - Web URL: `https://github.com/ogulcancelik/herdr`

Herdr workstation skill wrapper:

```text
/home/graham/workspace/experiments/agent-skills/skills/herdr-workstation
```

## Current State

Tau has two Herdr-related proof rungs:

1. `visible-dag-poc`
   - Fixture DAG: `creator -> reviewer`
   - Uses Herdr panes for visible bounded workers.
   - Uses fixture Python workers, not real Codex/OpenCode provider sessions.

2. `provider-pane-poc`
   - Flat provider allocation: `Tau root -> {Codex pane, OpenCode pane}`
   - Launches real provider CLIs in Herdr panes.
   - Writes durable work orders and a final Tau receipt.
   - Verifies provider prompts were visible before marking the run `PASS`.

This bundle focuses on the second rung.

## Intended Responsibility Split

Tau should own:

- Queue selection.
- DAG compilation.
- Work-order generation.
- Policy gates.
- Receipt schema and validation.
- Final `PASS` / `BLOCKED` decision.
- Ticket closure workflow, once proven separately.

Herdr should own:

- Workspace creation.
- Tabs and panes.
- Starting provider CLIs.
- Pane input.
- Pane reads.
- Foreground process visibility.
- Live human monitoring.
- Provider-specific integration hooks/plugins.

Important boundary: Herdr chat or visible pane state is not the canonical approval record. Tau receipts and work-order artifacts remain canonical.

## Current Provider Pane DAG

The POC DAG is deliberately flat:

```text
Tau provider-pane run
├── codex provider pane
└── opencode provider pane
```

There is no edge between Codex and OpenCode in this proof. The purpose is to prove provider session allocation and monitoring before adding dependency execution.

The next intended DAG shape is likely:

```text
Tau run
├── node A: coder/codex
│   └── writes receipt/handoff
└── node B: reviewer/opencode
    └── depends on node A receipt
```

Longer term:

```text
Tau DAG compiler
├── allocate workspace
├── allocate provider panes per node
├── write node work orders
├── dispatch node prompts or work-order paths
├── monitor pane/process/receipt state
├── advance ready downstream nodes
└── emit final receipt and ticket decision
```

## Project Agent API

Current command-line API:

```bash
uv run tau provider-pane-poc \
  --repo /home/graham/workspace/experiments/tau \
  --run-root /home/graham/workspace/experiments/tau/experiments/goal-locked-subagents/proofs/provider-pane-poc \
  --label tau-provider-pane-poc-ready-v4
```

Inspection API:

```bash
uv run tau provider-pane-inspect <run-dir>
```

Representative inspect output:

```json
{
  "schema": "tau.provider_pane_inspect.v1",
  "ok": true,
  "status": "PASS",
  "mocked": false,
  "live": true,
  "providers": [
    {
      "provider_id": "codex",
      "pane_id": "wE:p5",
      "terminal_id": "term_6558ba00d98e040",
      "ready_prompt_observed": true,
      "readiness_actions": ["codex_update_prompt_skipped"],
      "visible_log": ".../logs/codex.visible.txt",
      "work_order_path": ".../work-orders/codex.json"
    },
    {
      "provider_id": "opencode",
      "pane_id": "wE:p6",
      "terminal_id": "term_6558ba0118bd841",
      "ready_prompt_observed": true,
      "readiness_actions": [],
      "visible_log": ".../logs/opencode.visible.txt",
      "work_order_path": ".../work-orders/opencode.json"
    }
  ]
}
```

Low-friction monitoring commands for a project agent:

```bash
herdr pane list --workspace wE
herdr pane read wE:p5 --source visible --lines 80
herdr pane read wE:p6 --source visible --lines 80
herdr pane process-info --pane wE:p5
herdr pane process-info --pane wE:p6
```

The project agent does not need to remember provider process IDs. Tau records `pane_id`, `terminal_id`, `work_order_path`, and visible logs in the receipt.

## Code Entry Points

Main implementation:

```text
src/tau_coding/provider_pane_poc.py
```

Important code locations:

- `ProviderPane` model: `src/tau_coding/provider_pane_poc.py:18`
- `run_provider_pane_poc`: `src/tau_coding/provider_pane_poc.py:28`
- Provider definitions:
  - Codex: `src/tau_coding/provider_pane_poc.py:54`
  - OpenCode: `src/tau_coding/provider_pane_poc.py:60`
- Executable preflight: `src/tau_coding/provider_pane_poc.py:70`
- Herdr doctor: `src/tau_coding/provider_pane_poc.py:85`
- Integration install: `src/tau_coding/provider_pane_poc.py:103`
- Herdr workstation create: `src/tau_coding/provider_pane_poc.py:121`
- Sequential pane launch: `src/tau_coding/provider_pane_poc.py:162`
- Readiness settling: `src/tau_coding/provider_pane_poc.py:228`
- Visible pane log capture: `src/tau_coding/provider_pane_poc.py:267`
- Final fail-closed receipt: `src/tau_coding/provider_pane_poc.py:293`
- Inspect API: `src/tau_coding/provider_pane_poc.py:335`
- Work-order schema: `src/tau_coding/provider_pane_poc.py:403`
- Pane record extraction from Herdr wrapper output: `src/tau_coding/provider_pane_poc.py:428`
- Provider readiness detector: `src/tau_coding/provider_pane_poc.py:468`

CLI wiring:

```text
src/tau_coding/cli.py
```

Important code locations:

- Import: `src/tau_coding/cli.py:102`
- `provider-pane-poc` dispatch: `src/tau_coding/cli.py:636`
- `provider-pane-inspect` dispatch: `src/tau_coding/cli.py:647`
- CLI parser: `src/tau_coding/cli.py:1252`

Focused tests:

```text
tests/test_provider_pane_poc.py
```

## Implementation Approach

The current implementation:

1. Creates a run directory under the requested proof root.
2. Writes `provider-pane-spec.json`.
3. Checks `codex --version` and `opencode --version`.
4. Runs the Herdr workstation skill `doctor`.
5. Installs or refreshes Herdr integrations for Codex and OpenCode.
6. Creates a Herdr workstation with tabs:
   - `providers`
   - `logs`
   - `receipts`
7. Writes durable JSON work orders:
   - `work-orders/codex.json`
   - `work-orders/opencode.json`
8. Starts provider panes sequentially to avoid a manifest write race observed during manual parallel starts.
9. Extracts pane ids and terminal ids from Herdr `agent start` results.
10. Reads visible pane output until readiness is observed or the loop times out.
11. Handles known Codex interstitials:
    - update prompt: choose skip
    - hook review prompt: trust Herdr hook when shown
12. Writes visible pane logs:
    - `logs/codex.visible.txt`
    - `logs/opencode.visible.txt`
13. Writes:
    - `events.jsonl`
    - `inspect.json`
    - `runtime-manifest.json`
    - `run-receipt.json`

The final receipt is fail-closed:

```python
all_provider_prompts_observed = all(
    provider.get("ready_prompt_observed") is True for provider in pane_records
)
final_receipt = {
    "ok": all_provider_prompts_observed,
    "status": "PASS" if all_provider_prompts_observed else "BLOCKED",
    "mocked": False,
    "live": True,
    ...
}
```

## Proof Artifact

Canonical live proof artifact:

```text
/home/graham/workspace/experiments/tau/experiments/goal-locked-subagents/proofs/provider-pane-poc/20260701T121352Z-tau-provider-pane-poc-ready-v4/run-receipt.json
```

Run directory:

```text
/home/graham/workspace/experiments/tau/experiments/goal-locked-subagents/proofs/provider-pane-poc/20260701T121352Z-tau-provider-pane-poc-ready-v4
```

Receipt summary:

```json
{
  "schema": "tau.provider_pane_run_receipt.v1",
  "ok": true,
  "status": "PASS",
  "mocked": false,
  "live": true,
  "all_provider_prompts_observed": true,
  "providers": [
    {
      "provider_id": "codex",
      "pane_id": "wE:p5",
      "terminal_id": "term_6558ba00d98e040",
      "ready_prompt_observed": true,
      "readiness_actions": ["codex_update_prompt_skipped"]
    },
    {
      "provider_id": "opencode",
      "pane_id": "wE:p6",
      "terminal_id": "term_6558ba0118bd841",
      "ready_prompt_observed": true,
      "readiness_actions": []
    }
  ]
}
```

Visible Herdr panes:

```text
workspace: wE
Codex:    wE:p5
OpenCode: wE:p6
```

Visible pane logs:

```text
/home/graham/workspace/experiments/tau/experiments/goal-locked-subagents/proofs/provider-pane-poc/20260701T121352Z-tau-provider-pane-poc-ready-v4/logs/codex.visible.txt
/home/graham/workspace/experiments/tau/experiments/goal-locked-subagents/proofs/provider-pane-poc/20260701T121352Z-tau-provider-pane-poc-ready-v4/logs/opencode.visible.txt
```

Proof scope from receipt:

Proves:

- Tau found real `codex` and `opencode` executables.
- Tau verified Herdr is reachable through `herdr-workstation doctor`.
- Tau installed or refreshed Herdr Codex/OpenCode integrations.
- Tau created a visible Herdr workstation for provider sessions.
- Tau sequentially launched real Codex and OpenCode provider panes.
- Tau settled known Codex launch interstitials when present.
- Tau captured workstation inspect output, pane ids, terminal ids, work orders, event log, and visible pane text.

Does not prove:

- Codex/OpenCode semantic task completion.
- Provider authentication beyond process/session launch.
- Remote Tailscale monitoring from another machine.
- GitHub ticket closure workflow.

## Verification Commands Already Run

Focused lint/import check:

```bash
uv run ruff check --select I,F \
  src/tau_coding/cli.py \
  src/tau_coding/provider_pane_poc.py \
  tests/test_provider_pane_poc.py
```

Observed result:

```text
All checks passed!
```

Focused tests:

```bash
uv run pytest tests/test_provider_pane_poc.py tests/test_visible_dag_poc.py
```

Observed result:

```text
3 passed
```

Live proof command:

```bash
uv run tau provider-pane-poc \
  --repo /home/graham/workspace/experiments/tau \
  --run-root /home/graham/workspace/experiments/tau/experiments/goal-locked-subagents/proofs/provider-pane-poc \
  --label tau-provider-pane-poc-ready-v4
```

Inspect command:

```bash
uv run tau provider-pane-inspect \
  /home/graham/workspace/experiments/tau/experiments/goal-locked-subagents/proofs/provider-pane-poc/20260701T121352Z-tau-provider-pane-poc-ready-v4
```

Observed result:

```text
ok=true
status=PASS
mocked=false
live=true
both provider prompts observed
```

## Known Issues / Review Flags

1. Readiness detection currently parses visible TUI text.
   - Codex readiness: `"OpenAI Codex"` and prompt marker.
   - OpenCode readiness: `"Ask"` and `"anything"`.
   - This is pragmatic but brittle.
   - Preferred future direction: Herdr/provider integration should emit structured readiness state or agent session metadata.

2. The Codex update prompt is handled by sending `2\n`.
   - This avoids updating during a proof run.
   - It assumes option `2` remains “Skip.”
   - Safer future direction: pass a Codex flag/env var to disable update prompts if available.

3. The Herdr hook trust prompt is handled if present.
   - Since the hook is installed by the Herdr integration step, the current behavior is intentional.
   - Review whether Tau should ever auto-trust hooks, or whether this should require a pre-approved integration state.

4. OpenCode sometimes takes longer to render its TUI.
   - A 30 second readiness loop was required to make the proof stable.
   - Review whether process-info plus provider integration metadata should be used instead of waiting on visible text.

5. The current DAG is flat.
   - This is intentional for provider allocation proof.
   - It does not yet prove dependency execution, prompt dispatch, downstream gating, or subagent receipt handoff.

6. CLI integration is in Tau's current ad hoc dispatcher.
   - This matches nearby POC commands but may not be the final long-term command architecture.

7. Cosmetic code issue:
   - `src/tau_coding/provider_pane_poc.py` has an ugly indentation block around `proof_scope` in the final receipt.
   - It is syntactically valid and passes current checks.
   - Should be cleaned before merge.

## Proposed Next Rungs

Suggested order:

1. Add structured provider readiness.
   - Request Herdr/provider integration state like `session_ready`, `auth_required`, `interstitial`, `blocked`, `prompt_ready`.

2. Add a real two-node provider DAG.
   - `coder/codex -> reviewer/opencode`
   - Coder gets a bounded work order.
   - Reviewer waits on coder receipt.
   - Tau advances reviewer only after durable receipt validation.

3. Add pane-to-receipt monitoring.
   - Tau should monitor:
     - Herdr pane process state.
     - Visible pane text for operator context.
     - Subagent receipt files for canonical progress.
     - Timeout/staleness.

4. Add remote monitoring proof.
   - Use Tailscale-accessible Herdr or Herdr-compatible remote endpoint.
   - Prove a second machine/session can list/read the same workspace/panes.

5. Add ticket closure workflow proof.
   - Only after real provider task execution and review receipts exist.
   - Ticket closure should require deterministic local artifacts and GitHub mutation receipts.

## What WebGPT Should Assess

Please assess:

- Whether this is the correct architectural split between Tau and Herdr.
- Whether Tau should treat Herdr as an adapter/fabric underneath a first-class Tau DAG executor.
- Whether the current work-order and receipt fields are sufficient for project agents.
- Whether visible TUI readiness is acceptable for the current POC or should be replaced before the next rung.
- Whether auto-installing integrations and auto-trusting the Codex Herdr hook is acceptable in a local POC.
- What minimal API would make this easiest for project agents:
  - CLI only?
  - Python function?
  - JSON contract input?
  - `tau dag run <dag.json>`?
- What should be the next deterministic proof artifact.
