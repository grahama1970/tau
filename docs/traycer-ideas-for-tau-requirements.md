# Traycer Ideas For Tau: Requirements Assessment

Date: 2026-07-03

Source checkout: `/tmp/traycer`

Traycer commit inspected: `0d749ba8ca0342035620f08c658a15ba9feeb146`

Scope: analyze which Traycer ideas are useful for Tau. This is not an implementation plan and does not claim Tau parity with Traycer.

## Method

1. Read Traycer `README.md` for public claims.
2. Read Traycer `AGENTS.md`, `protocol/README.md`, `clients/traycer-cli/README.md`, and `docs/DEVELOPMENT.md` for repo architecture.
3. Inspect cloned source under `protocol/src`, `clients/traycer-cli/src`, `clients/shared`, and `clients/gui-app/src`.
4. Compare those ideas to Tau's current local project state from `PROJECT_KNOWLEDGE.md`, `README.md`, and `agents/{planner,orchestrator}/AGENTS.md`.

## Important Boundary

Traycer's README describes the product. The cloned repo contains the open-source clients, CLI, and protocol. Traycer's `AGENTS.md` says the host and cloud backend are not in this repository; the CLI provisions a signed host binary from GitHub Releases and the clients run against production cloud. Therefore, this assessment separates:

- README claim: public product claim.
- Repo-evidenced surface: code or schema visible in the clone.
- Tau recommendation: whether Tau should adopt the idea, and in what form.

## Executive Assessment

Tau should not copy Traycer's architecture wholesale. Tau already has a stricter receipt-gated orchestration model: planner/orchestrator subagents, Herdr-visible workers, structured provider readiness, DAG receipts, handoff loops, fail-closed stress rungs, and dry-run/apply-gated external mutation.

The strongest Traycer ideas for Tau are productization and protocol ideas:

1. Formalize work artifacts around goal/spec/ticket/review.
2. Make subagent lineage first-class.
3. Add agent-to-agent messaging as a typed, receipt-backed control channel.
4. Add an inspectable harness/provider catalog.
5. Add worktree lifecycle as a first-class orchestration primitive.
6. Add host/session binding rules for visible panes and remote monitors.
7. Add comment/review-thread artifacts as repair-loop inputs.
8. Add a read-only execution history view over Tau receipts.

Tau should avoid adopting Traycer ideas that would weaken Tau's current guarantees:

- Do not make chat transcript state canonical.
- Do not replace Tau receipts with UI state.
- Do not introduce hidden autonomous loops without receipts.
- Do not let provider/session fabric become the DAG authority.
- Do not add collaboration UI before event and receipt schemas are canonical.

## Requirement Matrix

### R1. Product-Level Work Artifact Stack

README claim:

- Traycer supports regular and Epic modes.
- Epic mode is for structured, multi-step coding workflows.
- Collaboration includes boards, ticket assignment, and workspace progress.

Repo-evidenced surface:

- `protocol/src/persistence/epic/artifacts.ts` defines Epic artifact kinds: `spec`, `ticket`, `story`, and `review`.
- Artifacts carry `parentId`, timestamps, `artifactRoomId`, and type-specific fields.
- `protocol/src/host/epic/contracts.ts` exposes artifact create/delete/status/rename/reparent RPCs.
- `protocol/src/host/epic/unary-schemas.ts` defines task, epic, phase, repo, workspace, permission, and collaborator shapes.

Tau current state:

- Tau has `goal-helper.json`, `start-handoff.json`, `tau.agent_handoff.v1`, `tau.dag_run_spec.v1`, node receipts, and DAG receipts.
- Tau has GitHub-shaped ticket projections and ticket-repair lanes, but the project artifact hierarchy is not as productized as Traycer's Epic model.

Tau requirement:

- Define a first-class Tau work artifact stack:
  - `tau.goal.v1`
  - `tau.workflow_or_epic.v1`
  - `tau.spec.v1`
  - `tau.ticket.v1`
  - `tau.review.v1`
  - `tau.dag_run_spec.v1`
  - `tau.node_receipt.v1`
  - `tau.dag_run_receipt.v1`
- Each artifact must have stable `id`, `parent_id`, `created_at`, `updated_at`, `artifact_path`, `status`, and `source` fields.
- Keep Tau's receipts as canonical proof; product artifacts organize work but do not prove completion.

Recommendation: integrate.

Priority: high.

## R2. Agent-To-Agent Messaging Channel

README claim:

- Traycer supports agent-to-agent communication.
- Agents can debate architecture or peer-review code.

Repo-evidenced surface:

- `protocol/src/host/agent/shared.ts` defines `agent.create`, `agent.list`, `agent.sendMessage`, `agent.getTranscript`, and `agent.stop` request/response schemas.
- `clients/traycer-cli/src/commands/agent-create.ts` creates child agents with surface, harness, model, mode, reasoning effort, fast mode, and workspace bindings.
- `clients/traycer-cli/src/commands/agent-send.ts` supports `--expect-reply` and `--response-id`.
- `protocol/src/agent/a2a-message-format.ts` formats messages differently for GUI and CLI receivers and embeds reply instructions.
- `canParticipateInA2A()` gates participation by surface/harness.

Tau current state:

- Tau has `tau.agent_handoff.v1`, command specs, planner/orchestrator/coder/reviewer roles, and Herdr-visible worker panes.
- Tau's handoff model is stronger for route control, but it is not yet a general inbox/reply channel between live workers.

Tau requirement:

- Add `tau.agent_message.v1`:
  - `message_id`
  - `run_id`
  - `sender_agent_id`
  - `receiver_agent_id`
  - `expects_reply`
  - `response_id`
  - `body`
  - `created_at`
  - `delivery_surface`
  - `receipt_path`
- Add `tau.agent_inbox_receipt.v1` for message delivery and reply capture.
- Treat A2A messages as control-plane communication, not proof of task success.
- Keep DAG edges and handoff receipts as the scheduling authority.

Recommendation: integrate, but behind Tau receipt gates.

Priority: high.

## R3. Subagent Lineage And Tree Rendering

README claim:

- Traycer runs multiple agents in parallel without losing context.
- It supports agent-to-agent communication and collaboration.

Repo-evidenced surface:

- `agent.create` sets the new agent's `parentId` to the sender.
- `protocol/src/agent/agent-list-format.ts` renders "You", "Parent", "Siblings", "Children", and "Other agents" and builds a tree from `parentId`.
- `protocol/src/host/agent/gui/subagent-nesting.ts` contains a default-nest child runtime event policy so child events do not leak unparented into parent timelines.
- `clients/gui-app/src/components/chat/chat-active-agents-panel.tsx` shows active agents and descendants with stop controls.
- `clients/gui-app/src/stores/chats/subagent-open-store.ts` persists open/closed subagent display state.

Tau current state:

- Tau has visible Herdr panes for planner, orchestrator, coder, and reviewer in provider-DAG proofs.
- Tau receipts already record provider sessions, visible subagents, panes, and work orders, but lineage is not yet a generalized tree contract.

Tau requirement:

- Extend DAG/runtime receipts with lineage:
  - `agent_id`
  - `parent_agent_id`
  - `node_id`
  - `parent_node_id`
  - `run_id`
  - `workspace_id`
  - `pane_id`
  - `terminal_id`
  - `provider_session_id`
  - `transcript_path`
  - `worktree_path`
- Define a Tau event rule equivalent to default-nest:
  - Child worker events must either be attached to the child agent node or suppressed.
  - Child errors must close/block the child node, not leak as parent terminal success/failure without interpretation.
- Add `tau agent-tree inspect <run-dir>` or include this tree in `provider-dag-inspect`.

Recommendation: integrate.

Priority: high.

## R4. Harness/Provider Catalog And Capability Selection

README claim:

- Traycer supports Claude Code, Codex, Cursor, OpenCode, and Traycer inference.
- Users can switch models in the same chat.
- Users bring existing provider subscriptions.

Repo-evidenced surface:

- `protocol/src/host/agent/shared.ts` defines canonical `harnessIdSchema`, GUI harnesses, TUI harnesses, and agent-facing harnesses.
- `agent.listHarnessModels` is versioned in `protocol/src/host/agent/contracts.ts`.
- CLI `agent-create` accepts `--surface`, `--harness`, `--model`, `--agent-mode`, `--reasoning-effort`, and `--fast`.
- The repo distinguishes GUI agents from TUI agents and gates A2A participation by harness capability.

Tau current state:

- Tau has provider settings, provider-DAG proofs using Codex and OpenCode, and structured readiness records.
- Tau has Scillm paths and model/provider settings, but provider capability selection for DAG roles is not as explicit as Traycer's harness surface.

Tau requirement:

- Add `tau.provider_capability.v1` catalog entries:
  - `provider_id`
  - `surface`: `headless`, `herdr_pane`, `tui`, `api`, `scillm`
  - `roles_supported`
  - `a2a_supported`
  - `transcript_supported`
  - `structured_readiness_supported`
  - `stop_supported`
  - `workspace_binding_supported`
  - `models`
  - `default_timeout_seconds`
  - `known_failure_modes`
- Make DAG planner select providers from this catalog rather than embedding provider names ad hoc.
- Receipts must record selected provider, model, capability version, and selection reason.

Recommendation: integrate.

Priority: high.

## R5. Worktree Lifecycle

README claim:

- Traycer supports shared workspace and coding workflow execution.

Repo-evidenced surface:

- `clients/traycer-cli/src/commands/worktree-create.ts` creates worktrees through host RPC, supports new or existing branches, source branch, and carrying uncommitted changes.
- `protocol/src/host/worktree-schemas.ts` and worktree-related host contracts exist.
- `agent.create` supports workspace bindings, including `--cwd`, `--workspace-path`, and structured `source-path=run-path` entries.

Tau current state:

- Tau provider-DAG proofs create scratch worktrees.
- Tau has Herdr cleanup receipts but live apply cleanup has only been dry-run by default in the recorded finalizer proof.

Tau requirement:

- Define `tau.worktree_binding.v1`:
  - source repo path
  - run worktree path
  - branch selection
  - base SHA
  - dirty-before policy
  - carry-uncommitted policy
  - owner run/node
  - cleanup mode
- Define `tau.worktree_cleanup_receipt.v1` separate from Herdr workspace cleanup.
- Planner must choose scratch worktree vs production worktree explicitly.
- Orchestrator must refuse production mutation unless policy allows it.

Recommendation: integrate.

Priority: high.

## R6. Host/Session Binding

README claim:

- Traycer supports cross-device sync and collaboration from any device.

Repo-evidenced surface:

- Traycer `AGENTS.md` states `hostId` is canonical and tabs are bound to a host for life.
- `protocol/src/persistence/epic/chat.ts` stores `hostId` on chat records.
- `clients/gui-app/src/components/epic-canvas/tab-host-provider.tsx` enforces per-tile host binding.
- `clients/shared/host-transport/remote-path.ts` states remote/relay must use the same versioned RPC envelope and must not introduce a separate remote protocol.

Tau current state:

- Tau has Herdr workspace/pane/session visibility and planned remote/Tailscale monitoring, but remote monitoring has not been proven.

Tau requirement:

- Add a `tau.session_binding.v1` record:
  - `session_id`
  - `host_id`
  - `workspace_id`
  - `pane_id`
  - `terminal_id`
  - `agent_id`
  - `node_id`
  - `created_at`
  - `reachable_at_start`
  - `remote_endpoint`
- Treat sessions as bound for life; if host is unreachable, mark `STALE`/`BLOCKED` rather than silently reassigning.
- If Tau later supports remote monitoring, use the same event/receipt schema over local and remote transports.

Recommendation: integrate conceptually. Tailscale-specific proof remains separate.

Priority: medium-high.

## R7. Versioned Protocol Registry

README claim:

- Not a user-facing README feature, but central to Traycer architecture.

Repo-evidenced surface:

- `protocol/README.md` describes per-method `{ major, minor }` runtime negotiation.
- `protocol/src/framework/versioned-rpc.ts` and contract files define versioned RPCs.
- `protocol/src/host/agent/contracts.ts` includes upgrade/downgrade paths for agent list and harness model listing.

Tau current state:

- Tau schemas are versioned by names like `tau.dag_run_receipt.v1`, but there is no comparable per-method negotiated registry for command APIs.

Tau requirement:

- Keep simple schema-name versioning for local files.
- Add a small command/API manifest for public Tau commands:
  - command name
  - input schema
  - output schema
  - compatibility policy
  - deprecation state
- Do not overbuild full RPC negotiation until Tau has multiple clients that need independent versioning.

Recommendation: partially integrate.

Priority: medium.

## R8. Comments And Review Threads

README claim:

- Collaboration includes real-time editing, ticket assignment, and team workspaces.

Repo-evidenced surface:

- `protocol/src/host/comments/schemas.ts` defines comment thread list/status schemas.
- `protocol/src/comments/comments-xml-formatting.ts` formats comments as XML for agent consumption.
- Epic contracts include create/reply/edit/delete/resolve comment thread RPCs.

Tau current state:

- Tau reviewer receipts and WebGPT reviews exist as artifacts.
- Tau does not yet have a generalized review-comment object that can be selected, resolved, or fed back into a bounded repair loop.

Tau requirement:

- Define `tau.review_comment.v1`:
  - `comment_id`
  - `artifact_path`
  - `anchor`
  - `severity`
  - `status`: `open`, `resolved`, `rejected`, `stale`
  - `author_agent_id`
  - `body`
  - `evidence_path`
  - `created_at`
- Add `tau.review_comment_resolution_receipt.v1`.
- Allow orchestrator to create repair DAG nodes from selected unresolved comments.

Recommendation: integrate.

Priority: medium-high.

## R9. Execution Activity Timeline

README claim:

- Users can run multiple agents in parallel without losing context.
- Users can collaborate in real time.

Repo-evidenced surface:

- `clients/gui-app/src/components/chat/chat-activity-groups.ts` groups activity segments such as tools, commands, file changes, approvals, and spawned subagents.
- `clients/gui-app/src/components/chat/chat-active-agents-panel.tsx` exposes active agents and stop controls.
- `protocol/src/host/agent/gui/subagent-nesting.ts` defines child event nesting and suppression.

Tau current state:

- Tau writes `events.jsonl`, receipts, visible logs, and Herdr pane handles.
- Tau lacks a single normalized read-only execution-history view over those artifacts.

Tau requirement:

- Define `tau.execution_event.v1` normalized from DAG, Herdr, provider, and receipt events.
- Provide `tau run-history inspect <run-dir> --json`.
- Optional UI should render from receipts/events only, not invent operational state.

Recommendation: integrate.

Priority: medium-high.

## R10. Stop Controls And Cascading Cancellation

README claim:

- Multi-agent orchestration and collaboration imply the ability to manage active agents.

Repo-evidenced surface:

- `agent.stop` is a versioned RPC in `protocol/src/host/agent/contracts.ts`.
- `chat-active-agents-panel.tsx` describes stopping an agent and its delegated subtree.

Tau current state:

- Tau has bounded timeouts, max attempts, and Herdr cleanup receipts.
- Tau does not yet have a general cascade-stop receipt for visible subagent trees.

Tau requirement:

- Define `tau.agent_stop_request.v1` and `tau.agent_stop_receipt.v1`.
- Stop receipt should record:
  - target agent
  - cascade mode
  - affected panes/processes
  - signals sent
  - final process state
  - cleanup receipt path
- Orchestrator must emit stop receipts on timeout/max-attempt failure.

Recommendation: integrate.

Priority: medium.

## R11. Agent Selection Guide

README claim:

- Not explicit in top-level README.

Repo-evidenced surface:

- `protocol/src/host/agent/shared.ts` defines `agent.selectionGuide` schemas.
- Sources include workspace and global guides with priority, path, and content.
- GUI has agent selection guide editor surfaces.

Tau current state:

- Tau has `agents/*/AGENTS.md`, persona YAMLs, command specs, and skill contracts.
- Planner/orchestrator/coder/reviewer roles exist, but selection guidance is not yet a single ranked artifact.

Tau requirement:

- Add `tau.agent_selection_guide.v1`:
  - available agents
  - capabilities
  - forbidden actions
  - required receipts
  - provider preferences
  - selection examples
  - source files read
- Planner must cite guide sources used when selecting workers.

Recommendation: integrate.

Priority: medium.

## R12. Collaboration And Cloud Sync

README claim:

- Traycer supports shareable boards, real-time editing, ticket assignment, collaboration, and cross-device sync.

Repo-evidenced surface:

- Epic collaborator schemas, permissions, Yjs-like artifact rooms, Tiptap room info, notifications, and auth/cloud client boundaries exist.
- The actual production cloud backend is not in the repo.

Tau current state:

- Tau is local-first with GitHub transport, Herdr visibility, Memory, UX Lab viewer, and possible future remote monitoring.

Tau requirement:

- Do not copy cloud collaboration.
- For Tau, collaboration should remain:
  - local receipts
  - Herdr-visible panes
  - GitHub issue/PR projection
  - optional web viewer over local artifacts
- Only consider multi-user sync after Tau's local run/event schema is stable.

Recommendation: do not integrate now.

Priority: low.

## R13. Privacy And Telemetry Boundary

README claim:

- Code is processed in memory and not used for training.
- Privacy mode controls prompt logging.
- Crash reporting and analytics may be enabled in release builds.

Repo-evidenced surface:

- Traycer repo includes auth, Sentry, PostHog references, release builds, and production cloud configuration stamping.

Tau current state:

- Tau is local experimental code with proof artifacts and logs that may contain prompts, paths, and model outputs.

Tau requirement:

- Add a Tau artifact privacy policy:
  - what receipts may store
  - what visible logs may store
  - redaction requirements for provider keys and private prompts
  - retention/cleanup defaults
- This is especially important if Tau adopts Traycer-like transcript and agent-inbox concepts.

Recommendation: integrate a local privacy contract, not Traycer's cloud terms.

Priority: medium.

## R14. Signed Host / Local Service Lifecycle

README claim:

- Traycer Desktop/CLI manages a local host.

Repo-evidenced surface:

- `clients/traycer-cli/README.md` documents host ensure/status/doctor/logs/update.
- CLI source includes installer, registry, minisign verification, service install/status/uninstall, and host doctor.

Tau current state:

- Tau has a Python CLI, cron/docker paths, Herdr dependency, Scillm dependency, and UX Lab integration, but not a signed host service.

Tau requirement:

- Do not adopt signed host machinery now.
- Adopt the operational idea:
  - `tau doctor`
  - `tau service status`
  - dependency availability checks for Herdr, Scillm, Memory, GitHub, provider CLIs
  - structured doctor receipt

Recommendation: partially integrate as doctor/status receipts.

Priority: medium.

## What Tau Already Does Better

1. Canonical receipts: Tau's strongest feature is durable receipts that explicitly state proof scope and non-claims.
2. Fail-closed mutation: Tau keeps GitHub and cleanup apply behind explicit policy/flags.
3. Goal lock: Tau distinguishes human goal changes from agent recommendations.
4. Proof ladder discipline: Tau records mocked/live/provider-live boundaries.
5. Herdr visibility: Tau can expose planner/orchestrator/coder/reviewer panes for human monitoring.

Traycer is stronger on productized interaction, shared artifacts, lineage display, host binding, and CLI ergonomics.

## Recommended Tau Integration Backlog

### Phase 1: Protocol Only

1. `tau.agent_lineage.v1`
2. `tau.provider_capability.v1`
3. `tau.agent_message.v1`
4. `tau.review_comment.v1`
5. `tau.worktree_binding.v1`
6. `tau.execution_event.v1`

Acceptance bar:

- Schemas or dataclasses exist.
- Inspect command emits these fields from existing provider-DAG proof artifacts.
- Unit tests cover serialization and fail-closed missing required fields.
- No live provider claim.

### Phase 2: Inspectors

1. `tau provider-dag-inspect` includes agent tree and execution timeline.
2. `tau agent-tree inspect <run-dir> --json`.
3. `tau comments inspect <run-dir> --json` for reviewer findings.
4. `tau doctor --json` emits dependency capability status.

Acceptance bar:

- Commands run against existing proof dirs.
- Output is deterministic JSON.
- Receipts retain mocked/live boundaries.

### Phase 3: Live Orchestration Improvements

1. Planner selects providers from `tau.provider_capability.v1`.
2. Orchestrator records worktree binding and cleanup receipts.
3. Workers can send typed messages, but scheduling still flows through DAG/handoff receipts.
4. Reviewer comments can create bounded repair-loop nodes.

Acceptance bar:

- One live scratch provider-DAG run records provider capability selection, lineage, worktree binding, message or comment artifacts, and final DAG receipt.
- No GitHub closure.
- No production repo mutation.

## Explicit Non-Goals

- Build Traycer-style cloud collaboration.
- Replace Tau's receipt model with Traycer chat history.
- Implement remote/Tailscale monitoring in this analysis slice.
- Implement UI before event/receipt contracts.
- Claim Traycer backend behavior from client-only source.

## Assessment Result

Useful ideas: yes, but mostly as protocol and UX organization around Tau's existing stronger receipt-gated orchestration.

Most valuable first integration: agent lineage plus execution timeline over existing Tau provider-DAG receipts.

Second most valuable: provider capability catalog for planner/orchestrator provider selection.

Third most valuable: typed review comments that can become bounded repair-loop work items.

Mocked: no.

Live: no.

Implementation: no.

Evidence: local source inspection of `/tmp/traycer` at commit `0d749ba8ca0342035620f08c658a15ba9feeb146` and Tau local project files.
