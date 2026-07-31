# Herdr-Inspired Tau Orchestration Requirements

Status: draft requirements
Date: 2026-07-01

## Scope

This document captures requirements for building Herdr-style visible subagent
orchestration into Tau.

Herdr was analyzed from:

- installed Herdr CLI `0.7.1`, protocol `14`
- local Herdr config/default config and agent detection manifests
- `/home/graham/Downloads/herdr-workstation-skill.zip`
- cloned Herdr source tree at `/home/graham/workspace/experiments/herdr`
- Herdr `README.md`, `SKILL.md`, `AGENTS.md`, `Cargo.toml`
- Herdr API/schema source under `src/api/schema/`
- Herdr CLI source under `src/cli/`
- Herdr handoff and API tests under `tests/`
- Tau `PROJECT_KNOWLEDGE.md`, README, receipt validators, and loop monitor code

Important version note: the installed Herdr binary reports protocol `14`, while
the cloned repo's `docs/next/api/herdr-api.schema.json` declares protocol `15`.
Tau must treat the Herdr protocol as versioned runtime metadata, not as a stable
unversioned contract.

## Architectural Decision

Tau should borrow Herdr's orchestration model, but not make Herdr the policy
authority.

Tau remains the source of truth for:

- queue selection
- immutable goal lock
- `tau.agent_handoff.v1`
- work orders
- retry budgets
- `tau.subagent_receipt.v1`
- reviewer/verifier receipts
- PASS, BLOCKED, NEEDS_CHANGES, and human-stop decisions
- GitHub/ticket proof and closure

Herdr-style runtime behavior supplies:

- visible workspaces
- tabs and panes
- provider process launch
- agent send/read/wait
- semantic agent state reporting
- human monitoring and intervention, including over Tailscale-accessible hosts

Tau should expose this through a runtime abstraction:

```text
SubagentRuntime
  - headless
  - herdr
```

Headless remains required for tests, CI, cron, and deterministic validators.
Herdr becomes the preferred live backend for long-running or multi-subagent work.

## Herdr Feature Inventory to Tau Requirements

This is the build list. Each row names a Herdr capability observed in the
installed CLI, downloaded skill, or cloned source, then states what Tau needs to
implement for equivalent orchestration value.

| Herdr feature | What Herdr has | What Tau needs to implement | Acceptance bar |
|---|---|---|---|
| Real terminal per worker | Each pane is a real terminal process with PTY-backed IO. | Runtime adapter that can launch one bounded Tau subagent per pane/process while keeping Tau receipts separate from scrollback. | A shell worker can be launched, read, sent a work order, and forced to write a Tau receipt. |
| Workspace/tab/pane model | Workspaces contain tabs; tabs contain panes; panes can split, move, focus, zoom, and close. | `tau.runtime_workspace.v1` manifest with Tau stable ids mapped to backend workspace/tab/pane refs. | Manifest persists stable Tau ids and Herdr refs separately; missing Herdr ref blocks instead of silently recreating. |
| Agent start/read/send/wait | CLI/API supports `agent start`, `agent read`, `agent send`, `agent wait`, `agent attach`, `agent explain`. | `tau runtime dispatch/read/send/wait/inspect` commands that write JSONL coordination events and read receipts. | Command tests prove every control action emits a structured event. |
| Agent status detection | Herdr reports `idle`, `working`, `blocked`, `done`, `unknown` using evidence-based terminal detection. | Tau state normalizer that records source, maps Herdr status into Tau monitor state, and treats it as advisory only. | Test prevents Herdr `done` from satisfying Tau proof or ticket closure. |
| Detection read source | Herdr read source includes `visible`, `recent`, `recent_unwrapped`, and `detection`. | Read receipt schema must record source and line count; detection source may diagnose state only. | Contract test rejects using pane read text as final receipt proof. |
| Event stream | Herdr source defines workspace, worktree, tab, pane, output, agent-detected, and agent-status events. | Tau runtime `events.jsonl` with normalized event kinds plus original Herdr kind. | Event mapper test fails on unknown event kind unless explicitly ignored. |
| Local socket/API | Herdr has a local JSON API/socket and generated schema; installed protocol may differ from repo `next` protocol. | Herdr adapter with `doctor` that records CLI version, protocol, socket, capabilities, and compatibility decision. | `tau runtime doctor --backend herdr` records protocol and refuses incompatible protocol by default. |
| Persistent server/session | Herdr server persists sessions and supports detach/reattach. | Tau resume/inspect command that reloads runtime manifest and re-resolves live refs without skipping receipt validation. | Resume test shows existing workspace without receipt remains PENDING/BLOCKED. |
| Live handoff | Herdr tests cover `server.live_handoff` preserving pane process continuity and session socket paths. | Optional continuity operation for Tau runtime maintenance; never proof of task success. | Handoff smoke records capability/process evidence but does not mark work PASS. |
| Remote/SSH/Tailscale access | Herdr can be used remotely over SSH; host may be reachable through Tailscale/MagicDNS. | Monitor metadata with local and remote access modes, auth notes, and read-only default posture. | Monitor URL presence has no effect on receipt or approval status. |
| Worktree commands | Herdr exposes worktree create/open/remove and worktree events. | Optional Tau worktree integration, but Tau owns branch policy, checkpoint, rollback, and cleanup receipt. | Cleanup refuses destructive removal without persisted evidence and scoped change receipt. |
| Provider integrations | Herdr supports integration install/status for agents such as Codex/OpenCode/Claude-style tools. | Role-to-provider command registry with preflight checks, env redaction, and per-role tool policy. | Unknown role/empty command is rejected before launch; secrets are redacted in manifest/events. |
| Notifications | Herdr can show notifications/sounds for attention. | Optional human-attention event only, never approval/proof/control. | Test proves notification event cannot advance workflow state. |
| Pane layout operations | Herdr supports split/layout/move/focus/resize/zoom. | Minimal layout profile for Tau workers: creator/reviewer/verifier/logs tabs without making layout part of proof. | Layout creation can fail without corrupting receipt state; proof lane continues or blocks explicitly. |
| In-pane Herdr skill | Herdr `SKILL.md` requires `HERDR_ENV=1` and warns against controlling focused panes from outside Herdr. | Separate external-controller mode from in-pane-agent mode; work orders must include mode and allowed Herdr operations. | Test rejects ambiguous current-pane operations in external-controller mode. |
| Config/session metadata | Herdr default config includes session, socket, remote, labels, notifications, and detection settings. | Tau effective runtime config receipt with backend, protocol range, socket/session selection, timeouts, redaction, cleanup. | Config parser test rejects invalid backend/protocol config and prints redacted effective config. |
| Plugins | Herdr has plugin infrastructure. | No plugin dependency in first Tau implementation; plugin support only after CLI-backed runtime works. | Architecture review confirms first implementation uses CLI/runtime adapter, not plugin coupling. |
| License boundary | Cloned Herdr repo declares AGPL-3.0-or-later/commercial. | Borrow ideas first; do not vendor/link Herdr source before license review. | Vendoring check or review gate exists before copying Herdr source into Tau. |

## Tau Implementation Requirement Groups

Tau should implement the Herdr-inspired capability in these groups, in order:

1. Runtime contract and schemas:
   `tau.subagent_runtime.v1`, `tau.runtime_workspace.v1`, read receipts,
   coordination events, cleanup receipts.
2. Headless backend:
   fake/local process backend so Tau tests and CI do not require Herdr.
3. Herdr doctor:
   detect CLI version, protocol, socket path, server state, capabilities, and
   compatible/incompatible decision.
4. Herdr workspace adapter:
   create/inspect/close workspace, create tabs/panes, map refs to Tau stable
   ids, persist manifest.
5. Communication adapter:
   send work orders, read output by source, wait for advisory state, write
   read/send/wait receipts.
6. Receipt bridge:
   wait for `tau.subagent_receipt.v1`, validate schema/goal hash, convert
   timeout or invalid receipt into Tau BLOCKED.
7. Status/event bridge:
   normalize Herdr events and statuses into Tau JSONL and monitor API.
8. Bounded creator/reviewer orchestration:
   launch role panes from Tau work orders with finite retry budgets.
9. Remote monitor metadata:
   expose local/remote inspection links without turning monitor visibility into
   proof or approval.
10. Ticket/queue integration:
   lease one work item, dispatch bounded workers, verify deterministic proof,
   and only then update ticket state.

## Non-Negotiable Invariants

### TAU-HERDR-001: Tau Receipt Authority

Pane output, terminal scrollback, Herdr agent state, and Herdr workspace status
must never be accepted as proof of task success.

Acceptance proof must come from Tau receipts and validators:

- `tau.agent_handoff.v1`
- `tau.subagent_receipt.v1`
- command-loop receipts
- reviewer/verifier receipts
- deterministic command receipts
- ticket proof comments

### TAU-HERDR-002: Bounded Work Only

Every visible subagent run must have:

- explicit work item id
- role
- work order path
- receipt path
- retry budget
- stop condition
- timeout/stale threshold

No Herdr-backed subagent may start from prose-only instructions for non-trivial
work.

### TAU-HERDR-003: Runtime Is Replaceable

Tau logic must not depend on Herdr-specific ids or terminal behavior. Tau should
store Herdr ids only in runtime adapter metadata.

The same Tau handoff should be runnable through:

- `headless`
- `herdr`

with the same receipt validation path.

### TAU-HERDR-004: Herdr Runtime Ids Are Ephemeral

Herdr pane, tab, and workspace ids are runtime references, not durable Tau ids.
Tau must store them as `runtime_ref` values attached to Tau-owned stable ids:

- `run_id`
- `work_item_id`
- `subagent_run_id`
- `receipt_id`

Tau must re-resolve Herdr runtime refs before control actions and must fail
closed if the referenced pane/workspace no longer exists.

### TAU-HERDR-005: Runtime State Is Advisory

Herdr exposes `idle`, `working`, `blocked`, `done`, and `unknown` agent states.
Tau may use those states for monitoring, routing attention, and stale detection.
Tau must not use them as proof of correctness, receipt validity, ticket closure,
or human approval.

## Feature Requirements

## 1. Runtime Adapter Interface

### Purpose

Provide one Tau-owned abstraction for launching and controlling subagents.

### Requirements

- Define a `tau.subagent_runtime.v1` interface.
- Support at least `headless` and `herdr` backends.
- Runtime calls must return structured JSON receipts, not prose.
- Runtime errors must be converted into Tau BLOCKED receipts.
- Runtime adapter metadata must include backend name, CLI version, protocol,
  socket path, and capability flags where available.
- Herdr adapter must gate on protocol compatibility.
- Herdr adapter should use the Herdr CLI for the first implementation and keep
  direct socket/API usage behind a separately versioned adapter.

### Minimum Operations

```text
create_workspace(work_item)
start_agent(workspace, role, command, work_order)
send(agent, file_or_text)
read(agent, lines)
wait(agent, desired_state, timeout)
report(agent, state, message)
close_workspace(workspace)
```

### Acceptance Checks

- Unit test with fake runtime proves Tau can dispatch without Herdr installed.
- Unit test proves Herdr ids are not required in `tau.subagent_receipt.v1`.
- Unit test proves runtime failure yields BLOCKED, not PASS.
- Herdr doctor test records installed protocol and refuses unknown incompatible
  protocol by default.

## 2. Workspace Manifest

### Purpose

Represent one visible task runtime.

### Requirements

- Write `tau.runtime_workspace.v1` per work item.
- Manifest must be separate from proof receipts.
- Manifest must record:
  - `run_id`
  - `work_item_id`
  - `goal_hash`
  - `backend`
  - `backend_version`
  - `backend_protocol`
  - `repo`
  - `cwd`
  - `created_at`
  - `updated_at`
  - Tau stable workspace id
  - backend `workspace_ref`
  - tabs
  - agents
  - event log path
  - receipt root
  - monitor urls
- Manifest must explicitly state `proves_success: false`.
- Manifest must record whether the controller is running inside a Herdr pane
  (`HERDR_ENV=1`) or operating as an external Herdr controller.

### Acceptance Checks

- Schema test rejects missing `goal_hash`.
- Schema test rejects `proves_success: true`.
- Monitor summary exposes manifest separately from final receipt.
- Schema test distinguishes Tau stable ids from Herdr runtime refs.

## 3. Work Order Contract

### Purpose

Make the pane instruction durable, reviewable, and replayable.

### Requirements

- Every subagent pane receives a work-order file path.
- Work order must include:
  - work item id
  - active goal hash
  - role
  - scope
  - input artifacts
  - allowed files/commands
  - denied actions
  - required receipt path
  - validator command
  - stop condition
- Work order must include the exact receipt schema expected.
- Work order must forbid replacing receipt writes with chat claims.

### Acceptance Checks

- Validator rejects non-trivial Herdr dispatch without work-order path.
- Validator rejects missing receipt path.
- Validator rejects missing stop condition.

## 4. Role Panes / Agent Sessions

### Purpose

Support named visible subagents like coder, reviewer, verifier, Petey, Qbert,
Dewey, or battle scorekeeper.

### Requirements

- Agent roles must be selected from Tau's routable agent registry.
- Each role pane must record:
  - role
  - display name
  - command
  - backend agent id, if any
  - backend pane id, if any
  - backend tab id, if any
  - backend workspace id, if any
  - work order path
  - expected receipt path
  - current state
  - last status event
- Pane environment must include:
  - `TAU_RUN_ID`
  - `TAU_WORK_ITEM_ID`
  - `TAU_ROLE`
  - `TAU_AGENT_NAME`
  - `TAU_WORK_ORDER`
  - `TAU_RECEIPT_PATH`
  - `TAU_GOAL_HASH`

### Acceptance Checks

- Unknown role is rejected before runtime launch.
- Reviewer role is denied write permissions by default.
- Pane metadata is updated when state reports arrive.
- Runtime ref lookup failure marks the run BLOCKED/PENDING instead of creating a
  new implicit pane.

## 5. Send / Read / Notify

### Purpose

Let Tau communicate with subagents without treating chat as proof.

### Requirements

- Tau must support:
  - send work order
  - send short notification
  - read recent output
  - read visible output
  - read unwrapped recent output
  - read detection-source output when diagnosing Herdr agent state
- Every send must append a JSONL coordination event.
- Every read used for diagnosis must write a read receipt with timestamp and
  line count.
- Notifications must be bounded and point to durable artifacts.
- Read receipts must record Herdr read source: `visible`, `recent`,
  `recent_unwrapped`, or `detection`.
- Detection-source reads may support status diagnosis only; they must not be
  promoted into proof receipts.

### Forbidden

- Sending a broad task prompt without a work order.
- Reading pane output and marking a task PASS.
- Inferring receipt completion from terminal text alone.

### Acceptance Checks

- Test proves send writes an event.
- Test proves read output cannot satisfy receipt validation.

## 6. Agent State Reporting

### Purpose

Expose live subagent status to Tau and humans.

### Required States

Tau should normalize runtime states to:

```text
idle
working
blocked
done
unknown
stale
failed
```

Herdr's source schema names the native statuses as:

```text
idle
working
blocked
done
unknown
```

Tau-added states such as `stale` and `failed` must be represented as Tau
controller interpretations, not as Herdr-native states.

### Required Status Event Fields

Each state event must include:

- `subagent_run_id`
- `work_item_id`
- `role`
- `phase`
- `current_artifact`
- `command_or_api`
- `evidence`
- `bug_or_blocker`
- `next_step`
- `stop_condition`
- `timestamp`

### Timeout Diagnostics

- heartbeat interval: default 30 seconds
- stale threshold: default 120 seconds
- event must include last started command
- event must include last completed command
- event must include current artifact path

### Acceptance Checks

- Test marks an agent stale when no state event appears before threshold.
- Test preserves BLOCKED state over stale state when the agent explicitly
  reports a blocker.
- Test rejects final PASS without final receipt even if state is `done`.
- Test records whether a state came from Herdr agent detection, Tau receipt
  watcher, or explicit subagent status event.

## 7. Human Monitor Surface

### Purpose

Allow a human to inspect live Tau subagents from anywhere the Herdr host is
reachable, including over Tailscale.

### Requirements

- Workspace manifest must include monitor metadata:
  - local monitor command or URL
  - remote/Tailscale URL if configured
  - access mode
  - authentication notes
  - known limitations
- Tailscale/MagicDNS exposure is operator configuration. Tau may surface a
  configured remote monitor link, but must not assume one exists.
- Tau must distinguish:
  - human monitor link exists
  - human viewed the link
  - human approved a receipt
- Only the third may affect workflow, and only through an explicit human packet.

### Acceptance Checks

- Test proves monitor URL presence does not alter receipt status.
- Test proves human approval requires `tau.human_goal_change.v1` or another
  explicit Tau-owned human decision packet.

## 8. Event Log

### Purpose

Make live runtime behavior replayable enough for debugging.

### Requirements

- Write append-only `events.jsonl` per workspace.
- Event ids must be monotonic.
- Include event types:
  - workspace_created
  - workspace_updated
  - workspace_focused
  - tab_created
  - tab_focused
  - pane_created
  - pane_focused
  - pane_moved
  - pane_exited
  - pane_agent_detected
  - pane_agent_status_changed
  - agent_started
  - work_order_sent
  - notification_sent
  - pane_read
  - status_reported
  - receipt_detected
  - receipt_validated
  - blocker_detected
  - workspace_closed
- Events must not contain secrets.
- Events must reference artifacts by path instead of inlining large content.
- When sourced from Herdr, events must record the Herdr event kind and the Tau
  normalized event kind separately.

### Acceptance Checks

- Event stream endpoint replays events after sequence id.
- Contract check fails if events file is missing for a live runtime run.

## 9. Receipt Detection and Validation

### Purpose

Bridge visible runtime activity back into Tau's proof model.

### Requirements

- Runtime controller waits for the configured receipt path.
- Receipt must parse as JSON object.
- Receipt must validate against expected schema.
- Receipt goal hash must match active goal hash unless actor is human.
- Missing receipt before timeout yields BLOCKED.
- Invalid receipt yields BLOCKED with validator errors.

### Acceptance Checks

- Test: valid receipt advances to next agent.
- Test: malformed JSON blocks.
- Test: wrong goal hash blocks.
- Test: terminal text claiming receipt written does not count without file.

## 10. Bounded Creator / Reviewer Loop

### Purpose

Support visible creator-reviewer work without unbounded loops.

### Requirements

- Loop must have explicit `max_iterations`.
- Default maximum should be 3.
- Absolute maximum without human/project-agent override should be 4.
- Creator writes creator receipt.
- Reviewer reads creator receipt and writes reviewer receipt.
- Reviewer verdicts:
  - PASS
  - NEEDS_CHANGES
  - BLOCKED
- Retry is allowed only for actionable reviewer feedback.
- Same failure repeated twice stops.

### Acceptance Checks

- Test creator BLOCKED stops loop.
- Test reviewer PASS stops loop.
- Test reviewer NEEDS_CHANGES retries until budget.
- Test max iterations writes final NEEDS_CHANGES/BLOCKED receipt.

## 11. Provider Command Integration

### Purpose

Start provider-specific agent processes while keeping Tau policy independent.

### Requirements

- Support configured provider commands:
  - codex
  - opencode
  - claude
  - kimi
  - custom shell command
- Commands must be configured per role, not embedded in arbitrary prompts.
- Command launch must record argv.
- Command launch must record environment keys, redacting secrets.
- Runtime must support install/preflight checks for provider integration.

### Acceptance Checks

- Test rejects empty command.
- Test redacts env values in manifest/events.
- Live proof requires actual provider pane start, not CLI self-check.

## 12. Worktree / Workspace Isolation

### Purpose

Allow long-running workers to operate without corrupting the main worktree.

### Requirements

- Runtime should support optional worktree creation per task.
- Manifest must record:
  - base ref
  - branch
  - path
  - cleanup policy
- If Herdr worktree commands are used, Tau must still own branch naming,
  checkpointing, rollback notes, and cleanup receipts.
- Tau must checkpoint before mutation.
- Tau must preserve unrelated human changes.
- Cleanup must be explicit and receipt-backed.

### Acceptance Checks

- Test creates dry-run worktree manifest.
- Test refuses cleanup when uncommitted scoped changes lack receipt.
- Test records cleanup failure as BLOCKED.

## 13. Queue / Ticket Integration

### Purpose

Let Tau use visible runtimes for leased GitHub or watchdog tasks.

### Requirements

- Queue selection remains one item at a time.
- Ticket lease must happen before mutable work.
- Runtime manifest must include ticket URL/number when applicable.
- Ticket closure must require deterministic proof, not Herdr state.
- Workspace should remain open or archived when closure is blocked.

### Acceptance Checks

- Test prevents Herdr dispatch before lease for ticket-backed mutation.
- Test prevents close when only runtime state is `done`.
- Test records ticket proof artifact paths in final receipt.

## 14. Runtime Monitor API

### Purpose

Expose Tau runtime state to TUI, chat UI, and remote inspection surfaces.

### Requirements

- Extend or parallel Tau's existing loop monitor with runtime endpoints:
  - summary
  - agents
  - events
  - events/stream
  - receipts
  - monitor-links
- SSE stream must support `after_sequence`.
- API must fail closed for missing run ids or missing artifacts.
- API must not expose secrets or full prompts unless explicitly requested and
  scrubbed.

### Acceptance Checks

- Contract check exercises every endpoint.
- Missing events file returns non-200.
- Screenshot/CDP proof required for browser UI claims.

## 15. TUI / Chat UI Rendering

### Purpose

Make Tau-visible runtime status inspectable without requiring raw logs.

### Requirements

- TUI should show:
  - workspace id
  - active role panes
  - state per role
  - last event
  - current artifact
  - blocked reason
  - monitor link
- Chat UI contract should include runtime summary blocks.
- UI must distinguish runtime state from proof status.

### Acceptance Checks

- Textual test renders runtime status.
- Browser/CDP screenshot proves visible role/status rendering.
- UI test proves PASS is not shown unless receipt validation passed.

## 16. Security and Access

### Purpose

Avoid turning remote visibility into unauthorized control.

### Requirements

- Monitor URLs must document access mode.
- Remote/Tailscale exposure must be opt-in.
- Herdr control over SSH/remote hosts must be treated as a privileged operator
  path, not a public web API.
- Any control action from remote UI must map to a Tau human decision packet.
- Secrets must be redacted from manifests, events, read receipts, and monitor
  API responses.
- Runtime adapter must not allow destructive commands without Tau policy.
- If Tau vendors or links Herdr source code, license review is required because
  the cloned Herdr repo declares AGPL-3.0-or-later/commercial licensing.

### Acceptance Checks

- Redaction test covers env vars and bearer tokens.
- Test rejects remote approval without human packet.
- Security review required before exposing write/control operations over remote
  monitor surfaces.

## 17. Recovery and Resume

### Purpose

Recover long-running work after process restart or context loss.

### Requirements

- Runtime manifest must be enough to inspect existing workspace.
- Tau must support `runtime inspect`.
- Tau must support `runtime resume-monitor`.
- Tau must support `runtime mark-blocked` when workspace exists but receipt is
  missing/stale.
- Resume must not skip receipt validation.
- Herdr `server.live_handoff` may be used as a runtime continuity operation
  when supported, but handoff success is not task proof.

### Acceptance Checks

- Test reloads manifest and inspects agent states.
- Test missing workspace but existing receipt is handled as receipt-first.
- Test existing workspace with no receipt remains BLOCKED/PENDING.
- Live handoff smoke records capability and process continuity evidence without
  upgrading the work item to PASS.

## 18. Cleanup

### Purpose

Close visible runtime resources without losing evidence.

### Requirements

- Cleanup must write receipt.
- Cleanup must preserve:
  - work orders
  - events
  - receipts
  - final summary
  - monitor metadata
- Runtime may close panes/workspaces only after artifacts are persisted.
- Cleanup must support keep-open-on-blocked.

### Acceptance Checks

- Test cleanup writes `tau.runtime_cleanup_receipt.v1`.
- Test blocked run defaults to keep-open.
- Test cleanup failure is reported and does not delete artifacts.

## 19. Configuration

### Purpose

Make runtime behavior explicit and reproducible.

### Requirements

- Tau config should include:
  - default runtime backend
  - role-to-command map
  - default tabs
  - receipt root
  - timeout/stale thresholds
  - cleanup policy
  - Tailscale/remote monitor base URL
  - secret redaction patterns
  - compatible Herdr protocol range
  - Herdr socket/session selection policy
  - in-pane versus external-controller mode
- Config must allow per-run override via CLI.
- Config must print an effective config receipt.

### Acceptance Checks

- Test default config parses.
- Test invalid backend rejected.
- Test effective config redacts secrets.

## 20. CLI Commands

### Purpose

Give operators deterministic control of runtime orchestration.

### Required Commands

```bash
tau runtime doctor
tau runtime create --work-item ...
tau runtime dispatch --start start-handoff.json --backend headless|herdr
tau runtime inspect <run>
tau runtime send <agent> --file work-order.md
tau runtime read <agent> --lines 120
tau runtime wait <agent> --state blocked --timeout-s 600
tau runtime close <run>
tau runtime validate <run>
```

### Acceptance Checks

- CLI tests for each command.
- `doctor` must not require live mutation.
- `dispatch --backend herdr` must fail clearly when Herdr is unavailable.

## 21. Proof Ladder

### Rung 1: Static Contract

- schemas exist
- unit tests pass
- fake runtime proves dispatch/receipt loop

### Rung 2: Herdr CLI Smoke

- `herdr status --json` returns running/compatible
- `tau runtime doctor --backend herdr` records version/capabilities
- installed protocol is recorded and compared to configured compatible range
- no agent launched

### Rung 3: Visible Workspace Smoke

- create workspace
- create agents/logs/receipts tabs
- write manifest
- record Herdr runtime refs under Tau stable ids
- close workspace
- no provider launched

### Rung 4: Shell Agent Pane

- launch harmless shell command pane
- send/read/report state
- write fake receipt file from pane
- Tau validates receipt

### Rung 5: Provider Pane

- launch one provider pane, such as Codex or OpenCode
- send work order
- provider writes receipt
- Tau validates receipt

### Rung 6: Creator / Reviewer

- two panes
- creator receipt
- reviewer receipt
- bounded retry behavior
- final Tau receipt

### Rung 7: Ticket-Backed Live Run

- lease one real ticket
- Herdr workspace visible
- coder/reviewer provider panes
- deterministic verification
- proof comment
- close only if proof meets ticket contract

## 22. Herdr Source-Derived API Requirements

### Purpose

Keep Tau's Herdr integration aligned with the actual Herdr source contract.

### Requirements

- Herdr adapter must map Herdr schemas into Tau schemas instead of leaking raw
  Herdr objects through Tau's proof layer.
- Herdr `AgentInfo` fields that Tau may store as runtime metadata:
  - `terminal_id`
  - `name`
  - `agent`
  - `display_agent`
  - `agent_status`
  - `workspace_id`
  - `tab_id`
  - `pane_id`
  - `cwd`
  - `foreground_cwd`
  - `revision`
  - `agent_session`
- Herdr `PaneReadParams` read source must be captured in read receipts.
- Herdr event subscriptions should be normalized from dot/kind names such as:
  - `workspace.created`
  - `workspace.updated`
  - `workspace.closed`
  - `worktree.created`
  - `worktree.opened`
  - `worktree.removed`
  - `tab.created`
  - `tab.closed`
  - `pane.created`
  - `pane.output_matched`
  - `pane.agent_detected`
  - `pane.agent_status_changed`
- Herdr notifications may be used for human attention only. They are not
  workflow approvals or proof.

### Acceptance Checks

- Contract test validates the Tau Herdr adapter against a captured
  `herdr status --json` fixture.
- Contract test validates agent info normalization without preserving proof
  claims from Herdr state.
- Contract test validates event-kind mapping and fails on unknown event kinds
  unless explicitly configured as ignored.

## 23. Herdr Environment Mode Requirements

### Purpose

Separate Tau-as-external-controller behavior from the in-pane Herdr skill
contract.

### Requirements

- If Tau invokes Herdr from outside Herdr, it must identify itself as an
  external controller and use explicit workspace/pane refs.
- If Tau asks an in-pane agent to use the Herdr skill, that agent must respect
  the Herdr `SKILL.md` guard requiring `HERDR_ENV=1`.
- In-pane agents must not inspect or control a focused pane from outside a
  Herdr-managed pane.
- External Tau controller operations must avoid ambiguous "current pane" actions
  unless the manifest already binds the run to a specific runtime ref.

### Acceptance Checks

- Test rejects ambiguous current-pane operations from an external controller.
- Test records controller mode in the runtime manifest.
- Work order text for Herdr-in-pane agents includes the `HERDR_ENV=1` guard.

## 24. Borrow-Ideas-First Implementation Boundary

### Purpose

Use Herdr's proven concepts without prematurely coupling Tau to Herdr internals.

### Requirements

- First implementation should borrow the model:
  - real terminal per worker
  - visible panes
  - status detection
  - workspace/tab/pane grouping
  - remote inspection
  - event stream
  - live handoff where available
- First implementation should not vendor Herdr Rust code.
- Direct socket/API integration should follow after CLI-backed smoke tests and
  protocol pinning.
- Herdr remains a runtime substrate. Tau remains the orchestrator, policy
  engine, receipt authority, queue manager, and ticket closer.

### Acceptance Checks

- Architecture review confirms no Herdr source vendoring before license review.
- CLI-backed Herdr smoke passes before direct socket integration begins.
- Tau tests can run without Herdr installed through the headless backend.

## Open Design Questions

1. Should Tau store Herdr runtime manifests under each proof directory or under a
   shared runtime state root?
2. Should blocked Herdr workspaces default to stay open forever, or expire after
   a configured retention period?
3. Should remote monitor URLs be generated from Tailscale hostnames, MagicDNS,
   or a user-configured base URL?
4. Should provider commands be global role defaults or project-specific role
   defaults?
5. Should Tau support bidirectional remote control, or only read-only monitoring
   until a separate security review?

## Recommended Implementation Order

1. Define Tau runtime schemas and fake runtime.
2. Add `tau runtime doctor` and `tau runtime dispatch --backend headless`.
3. Add Herdr adapter with `doctor`, workspace create, inspect, close.
4. Add send/read/wait/report and event JSONL.
5. Add receipt wait/validation bridge.
6. Add TUI/chat monitor rendering.
7. Add creator/reviewer loop.
8. Add ticket-backed Herdr runtime proof.

Do not start with UI. The monitored object must be the runtime manifest plus
Tau receipts.
