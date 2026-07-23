# Tau Replacement-Harness Hardening

Status: active hardening backlog.

Purpose: make Tau practical enough to use when Codex, Claude Code, or pi-mono are not giving the operator reliable control. This document is source-derived from Tau's current goal, the WebGPT readiness review run on 2026-07-23, local pi-mono, and local/upstream OpenCode evidence.

## Current Readiness

Tau is a credible zero-trust orchestration substrate, but not yet a daily-driver replacement harness. The immediate gap is not feature count. The gap is repeatable operator confidence:

1. Scripted health checks must be deterministic and non-interactive.
2. Agent modes must make mutability explicit before work starts.
3. Permission requests must be durable, inspectable, and replyable.
4. Sessions must support practical branch/fork/recover workflows.
5. Provider/model auth and switching must be obvious from the first screen.
6. The five canonical DAG ladder must run live from a clean checkout with receipts and a truthful viewer.

## Borrowed Patterns

### OpenCode

Source evidence:

- Local checkout `/home/graham/workspace/experiments/opencode`, upstream `anomalyco/opencode`.
- README lines 102-111 define switchable `build`, `plan`, and `general` agents.
- `packages/core/src/permission.ts` lines 34-69 define permission requests with `sessionID`, `action`, `resources`, `save`, `metadata`, and source.
- `packages/core/src/permission.ts` lines 174-219 evaluate configured and saved rules into `deny`, `ask`, or `allow`, then block on pending permission replies.

Tau hardening to borrow:

1. Add explicit Tau modes:
   - `build`: can edit and run approved local commands.
   - `plan`: read-only by default; commands require approval.
   - `review`: read-only plus evidence/verdict constraints.
   - `general`: bounded search/multistep helper with no mutation unless promoted.
2. Promote Tau approval gates into a first-class pending permission queue:
   - request id
   - session/run id
   - action
   - resources
   - proposed save rule
   - source node/tool
   - durable reply: `once`, `always`, `reject`
3. Make denied and pending permissions visible in `tau status` and the DAG viewer.

Acceptance gates:

- `tau doctor --json` reports configured mode support and permission-store readiness.
- A read-only `plan` run attempting edit/write emits a pending or denied permission receipt, not a mutation.
- A human `once` approval allows only that request.
- A human `always` approval persists a scoped allow rule.
- A `reject` reply fails the tool and clears same-session dependent pending requests.

### Pi

Source evidence:

- Local checkout `/home/graham/workspace/experiments/pi-mono`, upstream `badlogic/pi-mono`.
- `packages/coding-agent/README.md` lines 19-21 document interactive, print/JSON, RPC, and SDK modes.
- Lines 75-108 document broad provider/model selection and subscription/API-key auth paths.
- Lines 137-160 document command surfaces for login, model switching, resume, session tree, fork, compact, export, share, and reload.
- Lines 180-187 document steering and follow-up queues while an agent is running.

Tau hardening to borrow:

1. Keep four harness entry modes healthy:
   - interactive TUI
   - print/JSON
   - local HTTP/RPC
   - embeddable Python API
2. Make session operations first-class:
   - resume
   - new
   - tree
   - fork
   - compact
   - export
3. Preserve Tau's existing steering/follow-up queue work, but expose it with receipts and CLI status.
4. Make provider/model auth readiness visible and actionable, not buried in provider settings.

Acceptance gates:

- Every mode has a non-interactive smoke command and JSON receipt.
- `tau status --json` can show current session id, model/provider, queued steering/follow-up messages, active tool, last error, and cost/token usage when available.
- Resume/fork/export run from a clean checkout without opening the TUI unexpectedly.
- Provider auth failures produce exact repair actions and never masquerade as model failure.

## Immediate Slices

### Slice 1: Non-Interactive Doctor Dispatch

Problem: `tau doctor --json` fell through to TUI because unknown callback options were treated as prompt args.

Status: implemented in this branch.

Acceptance:

- `uv run pytest tests/test_cli.py -k 'doctor_command_reports_read_only_runtime_preflight or doctor_json_option_does_not_fall_through_to_tui or doctor_rejects_unknown_options'`
- `uv run tau doctor --json`

### Slice 2: Mode Manifest In Doctor

Add a `modes` object to `tau.doctor.v1` with `build`, `plan`, `review`, and `general` readiness. This is a low-risk bridge from OpenCode's mode clarity into Tau without changing execution behavior yet.

Acceptance:

- Doctor receipt contains `modes.<mode>.mutating_default`.
- Doctor receipt contains `modes.<mode>.permission_default`.
- Tests prove `plan` and `review` are read-only by default.

### Slice 3: Pending Permission Receipt

Add a durable permission request/response receipt model before adding new UI.

Acceptance:

- A denied write attempt records a fail-closed permission receipt.
- A pending command approval records request id, action, resources, source node, and allowed replies.
- Reply receipts support `once`, `always`, and `reject`.

### Slice 4: Replacement-Harness Sanity Script

Create one command that exercises the minimum replacement loop:

1. doctor JSON
2. plan-mode read-only command
3. build-mode local edit in a temp repo
4. approval-gated side effect
5. resume/export/status receipt

Acceptance:

- One command emits a receipt bundle with `mocked: no` where live runtime is used and explicit `does_not_prove` boundaries where not.

## Non-Goals

- Do not copy OpenCode or Pi UI wholesale.
- Do not weaken Tau's zero-trust evidence rules to feel more convenient.
- Do not treat WebGPT, producer PASS fields, or local unit tests as replacement readiness.
- Do not add more orchestration layers until the next hardening slice has deterministic proof.
