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

Status: implemented in this branch.

Acceptance:

- Doctor receipt contains `modes.<mode>.mutating_default`.
- Doctor receipt contains `modes.<mode>.permission_default`.
- Tests prove `plan` and `review` are read-only by default.

### Slice 3: Non-Interactive Status Receipt

Add `tau status --json` so operators and supervisors can inspect the latest
indexed session without opening the TUI. Process-local fields such as active
tool and queued prompts must be reported as unavailable unless a live process
contract exists.

Status: implemented in this branch.

Acceptance:

- Status receipt contains current indexed session id, path, cwd, provider, and model.
- Status receipt summarizes JSONL transcript counts.
- Status receipt fails closed for unreadable or missing session files.
- Status receipt explicitly marks process-local active tool and queue state as unavailable.

### Slice 4: Pending Permission Receipt

Add a durable permission request/response receipt model before adding new UI.

Status: implemented in this branch.

Proof:

- `tau permission-request` writes `tau.permission_request_receipt.v1` artifacts.
- `tau permission-request --deny` records a fail-closed blocked permission receipt.
- `tau permission-reply` writes `tau.permission_reply_receipt.v1` artifacts for
  `once`, `always`, and `reject`, linked to the request receipt SHA-256.

Acceptance:

- A denied write attempt records a fail-closed permission receipt.
- A pending command approval records request id, action, resources, source node, and allowed replies.
- Reply receipts support `once`, `always`, and `reject`.

### Slice 5: Replacement-Harness Sanity Script

Create one command that exercises the minimum replacement loop:

1. doctor JSON
2. plan-mode read-only command
3. build-mode local edit in a temp repo
4. approval-gated side effect
5. resume/export/status receipt

Status: implemented in this branch.

Command:

- `tau replacement-harness-sanity --run-dir <run-dir>`

Acceptance:

- One command emits a receipt bundle with `mocked: no` where live runtime is used and explicit `does_not_prove` boundaries where not.

### Slice 6: SciLLM Slash Surface

Pi exposes a `/llama` command for local model management. Tau's equivalent
surface is SciLLM, not a separate llama.cpp router. Add a read-only `/scillm`
slash command so TUI users can discover the active SciLLM base URL, auth
environment status, health/auth endpoints, and Tau receipt commands without
repository archaeology.

Status: implemented in this branch.

Acceptance:

- `/scillm` is listed in slash-command autocomplete through the default command
  registry.
- `/scillm` reports the configured `SCILLM_BASE_URL`, redacted auth-env
  presence, and existing Tau SciLLM receipt commands.
- `/scillm` does not make provider calls or mutate state.

### Slice 7: Unavailable Scoped Models Remain Editable

Upstream Pi commit `a3ee1d28` keeps configured scoped models visible in the
scoped-model selector even when the current provider catalog or credentials make
them unavailable. Tau should mirror that operator behavior without weakening
quick-cycle safety.

Status: implemented in this branch.

Acceptance:

- The `/scoped-models` picker receives all configured scoped entries, including
  currently unavailable provider/model pairs.
- Unavailable scoped entries render with an explicit unavailable marker and do
  not appear in the ordinary active-model picker or quick-cycle set.
- Existing unavailable entries can be preserved, reordered, or removed from the
  scoped list, while brand-new unavailable scoped entries remain rejected.

### Slice 8: Pi Shortcut Discoverability In Fallback Hotkeys

Tau's interactive `/hotkeys` modal already reflects Pi-style shortcut bindings,
but the command-registry fallback help can be shown outside the full TUI. Keep
that fallback aligned with the daily-driver shortcuts operators expect from Pi.

Status: implemented in this branch.

Acceptance:

- Fallback `/hotkeys` lists model picker, scoped model cycling, and copy-last
  assistant shortcuts.
- The configuration example includes `model_picker`, reverse scoped cycling,
  and `copy_last_message` bindings.

### Slice 9: Extension Inventory In Startup Resources

Pi's startup header lists loaded extensions beside context files, prompt
templates, and skills. Tau already loads bounded Python extension tools, but
the TUI resource surfaces did not expose loaded extension names directly.

Status: implemented in this branch.

Acceptance:

- `CodingSession` exposes loaded extension metadata and extension-tool source
  mapping.
- Startup resources include an Extensions compact section when extensions are
  loaded.
- `/resources` includes an Extensions section with extension paths.

### Slice 10: HTTP Idle Timeout In TUI Settings

Pi exposes message-delivery timeout controls in `/settings`. Tau already has
provider HTTP timeouts, so surface the HTTP idle timeout in the TUI and apply it
to future provider streams without inventing unsupported WebSocket transport
behavior.

Status: implemented in this branch.

Acceptance:

- `/settings` includes an HTTP idle timeout row with Pi-style values.
- The setting is durable in `~/.tau/tui.json` as `http_idle_timeout_ms` and also
  accepts Pi-compatible `httpIdleTimeoutMs`.
- Changing the setting updates the active provider runtime timeout for future
  model calls.
- Tau does not claim WebSocket or websocket-cached transport support until a
  provider adapter implements those transports.

### Slice 11: Resource Provenance Labels

Pi's config/resource surfaces distinguish project, user, and explicit path
resources. Tau already preserves resource paths, so expose derived provenance
labels in `/resources` instead of requiring operators to infer scope from raw
paths.

Status: implemented in this branch.

Acceptance:

- `/resources` labels context files, skills, prompts, extensions, and resource
  diagnostics with `[project]`, `[user]`, `[user .agents]`, or `[path]` when a
  path is available.
- Resource provenance is derived from existing paths only; Tau does not invent
  package provenance for resources that were not loaded through a package
  manager.

### Slice 12: Startup Resource Diagnostics

Pi surfaces trust and resource-loading problems during startup. Tau already
records resource diagnostics, but collapsed startup only listed successful
resources. Add a compact diagnostics count so ignored project resources and
resource errors are visible immediately.

Status: implemented in this branch.

Acceptance:

- The collapsed startup resource summary includes a Diagnostics section when
  resource diagnostics are present.
- Diagnostics are summarized by recorded severity and do not claim that missing
  or ignored resources were loaded.

### Slice 13: Extension-Owned Tool Call Labels

Pi lets extensions customize tool rendering. Tau's Python extension contract is
currently bounded to tool registration, so do not invent custom render hooks.
Instead, use the extension inventory already recorded by `CodingSession` to show
which extension owns a tool call in the TUI transcript.

Status: implemented in this branch.

Acceptance:

- Tool calls registered by extensions render with an `extension:<name>` source
  label in the collapsed tool-call row.
- Built-in and ordinary tools keep their existing rendering.
- Tau still documents custom extension TUI renderers as unsupported until the
  extension API has a real renderer contract.

### Slice 14: Self-Contained Workflow Picker Runs

Tau's immutable goal requires operators to choose and run five canonical DAGs
without repository archaeology. The TUI workflow picker already exposed the
catalog and command insertion, but rows were too terse and inserted viewer
commands could hold indefinitely after completion.

Status: implemented in this branch.

Acceptance:

- The workflow picker row for each canonical DAG includes summary, topology,
  runtime boundary, result schema, and result node.
- TUI-inserted workflow commands keep `--open-viewer` for live DAG progress but
  add a bounded `--viewer-hold-seconds 120` so the Tau prompt is not trapped by
  an unbounded post-run viewer hold.
- Shell/CLI workflow usage remains unchanged; operators can still omit
  `--viewer-hold-seconds` when they intentionally want a long-held viewer.

### Slice 15: Read-Only Config Map

Pi has a `pi config` resource selector for package-backed extensions, skills,
prompts, and themes. Tau does not yet have a package selector contract, and a
fake selector would be misleading. Add a read-only `/config` TUI command that
maps the actual Tau config files, resource directories, loaded resource counts,
and the commands that mutate supported settings.

Status: implemented in this branch.

Acceptance:

- `/config` is visible in slash-command help and autocomplete.
- `/config` reports `~/.tau/tui.json`, `~/.tau/providers.json`,
  `~/.tau/credentials.json`, `~/.tau/trust.json`, user/project resource
  directories, loaded resource counts, and resource diagnostic count.
- `/config` explicitly states that it is read-only and that Tau does not yet
  provide Pi's package selector TUI.

### Slice 16: Anthropic Subscription Auth Warning

Pi warns operators when Anthropic subscription-style auth is active because
third-party harness use draws from extra usage rather than normal Claude plan
limits. Tau already supports Anthropic API-key storage and
`ANTHROPIC_AUTH_TOKEN` bearer auth; add the same warning without changing the
provider runtime contract.

Status: implemented in this branch.

Acceptance:

- Startup warns once when the active Tau provider is Anthropic and the actual
  auth source is subscription-style.
- `/login anthropic` warns immediately when the saved key starts with
  `sk-ant-oat`.
- `/settings` exposes `Anthropic extra usage` so the warning can be disabled
  without disabling all startup notifications.
- The warning follows Tau's Anthropic auth precedence: `TAU_RUNTIME_API_KEY`,
  stored credential, `ANTHROPIC_AUTH_TOKEN`, then `ANTHROPIC_API_KEY`.

### Slice 17: Permission Receipt Command Surface

Pi makes gated permissions part of the operator loop. Tau does not yet have a
live pending-permission queue in the TUI, but it already has durable
`permission-request`, `permission-reply`, and `approval-gate-check` receipt
commands. Expose those commands in the interactive command surface without
claiming live queue behavior.

Status: implemented in this branch.

Acceptance:

- `/permissions` is visible in slash-command help and autocomplete.
- `/approvals` resolves to the same command for operators looking for approval
  gates.
- The command lists the existing receipt entry points, allowed replies, allowed
  gated actions, and receipt schemas.
- The output states that these commands write receipts only and do not execute
  mutations.

## Non-Goals

- Do not copy OpenCode or Pi UI wholesale.
- Do not weaken Tau's zero-trust evidence rules to feel more convenient.
- Do not treat WebGPT, producer PASS fields, or local unit tests as replacement readiness.
- Do not add more orchestration layers until the next hardening slice has deterministic proof.
