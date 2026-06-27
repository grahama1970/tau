# Project Knowledge: tau

**Last updated:** 2026-06-27 18:22Z / 14:22 EDT by agent
**Status:** Active development

## Current Understanding

- Project initialized, knowledge tracking started
- Tau is a fork of alejandro-ao/tau being hardened into a goal-locked agentic harness. Current local slices add Loop2-compatible run receipts plus minimal model-facing contracts for subagent receipts, generated tickets, handoffs, and human-only goal changes. Tau derives GitHub labels deterministically instead of asking model agents to duplicate label/projection fields.
- Tau now has one-step dispatch receipts for routed handoffs. `handoff-dispatch-agent-command` validates the start handoff, selects `next_agent.name`, loads an opt-in `tau-dispatch-command.json` from that agent registry entry, runs one bounded command, and validates stdout as the next `tau.agent_handoff.v1`.
- Tau can now use a committed command-spec overlay with `--command-spec-root`. The selected agent is still validated against `/home/graham/workspace/experiments/agent-skills/agents`, but the executable `tau-dispatch-command.json` can live under Tau's `experiments/goal-locked-subagents/agent-command-specs/` tree for reproducible harness experiments.
- Built-in `goal-guardian` now has a deterministic adapter. It reads the start handoff from stdin, requires `TAU_HANDOFF_ACTIVE_GOAL_HASH`, refuses stale goal hashes, and emits a normal `tau.agent_handoff.v1` only when the active goal hash is preserved.
- Tau now has a command-backed local loop receipt. `handoff-command-loop` repeatedly validates the current handoff, loads the selected agent command spec, runs one bounded command, validates the emitted handoff, and stops at `human` or fails closed.
- Tau can render or explicitly apply GitHub transport for a command-loop terminal handoff. `handoff-command-loop-github-transport` extracts the last response projection from a successful command-loop receipt that stopped at `human`, renders the exact `gh issue/pr comment` and label-edit commands by default, and only runs them when `--apply` is passed.
- The command-loop GitHub apply path is fail-closed before mutation: invalid receipts return `ok: false`, `applied: false`, and zero command executions even when `--apply` is passed.
- Valid command-loop GitHub apply now runs live preflight checks before mutation: `gh auth status --hostname github.com` and `gh issue/pr view <number> --repo <repo> --json number`. If preflight fails, the receipt records `preflight_results`, leaves `command_results` empty, and returns `applied: false`.
- The experiment-local Memory/Brave harness now writes `stage_trace` and `current_stage` into `tau.loop2_memory_skill_selector_harness.v1` receipts so chat/TUI consumers can render dynamic Memory pipeline state from receipt data instead of static "thinking" text.
- UX Lab's Tau chat surface now renders a receipt-backed current-stage panel from the Memory stage-trace proof. The pi-mono commit is `225321964` on `persona/tim-blazytko-1774553751276`; the rendered `#tau` page shows `Receipt-backed current stage`, `Clarifying...`, and the stage-trace artifact path.
- UX Lab's Tau chat composer has live interaction proof for the Memory stage indicator. A real browser turn on `http://127.0.0.1:3002/#tau` submitted `How does Tau handle a CWE-287 SPARTA evidence case?`, observed `shared-chat:live-thinking-trace` with `Accessing Memory...`, recorded five `/api/memory/*` responses, and produced the final Memory-first contract fields.
- UX Lab's Tau chat adapter now fails closed when a selected Memory route endpoint does not return a route product. The pi-mono commit is `26dbf4917` on `persona/tim-blazytko-1774553751276`; focused tests cover CLARIFY, DEFLECT, ANSWER, RESEARCH, and COMPLIANCE route behavior plus no-handoff semantics for missing CLARIFY/DEFLECT/ANSWER products.
- UX Lab's Tau chat has live browser route evidence for CLARIFY, DEFLECT, RESEARCH, and COMPLIANCE through the real Memory proxy. `/api/memory/answer` is live and returned `memory.answer.v1` with `can_answer: true`, but current `/api/memory/intent` selected `QUERY` rather than `ANSWER` for the probed Tau answer prompt, so no browser ANSWER route was forced or mocked.
- UX Lab's Tau chat now renders the full `tau.agent_handoff.v1` JSON contract in successful route messages after the human-readable handoff and GitHub projection tables. The pi-mono commit is `57ddd5304` on `persona/tim-blazytko-1774553751276`; fail-closed route product failures still omit the handoff JSON.

## Recent Decisions

| Date | Decision | Why |
|------|----------|-----|
| 2026-06-27 | Initialize project knowledge | Enable shared human/agent context |
| 2026-06-27 | Use a fork at grahama1970/tau rather than a new unrelated repo | The project already has upstream history at alejandro-ao/tau, so a fork preserves attribution and makes future upstream comparison/pull possible. |
| 2026-06-27 | Keep GitHub mutation out of the first harness slices | Current proof is schema, validator, fixture, and deterministic projection only; live issue creation should come after the contracts are accepted. |
| 2026-06-27 | Keep registry command dispatch opt-in per agent | A real agent registry can contain many roles, but Tau should only execute commands for entries with explicit `tau-dispatch-command.json`. Missing specs fail closed with a `BLOCKED` receipt. |
| 2026-06-27 | Store experimental dispatch command specs in Tau overlays | This avoids depending on untracked files in the dirty `agent-skills` repo while still proving routes against real registry identities. |
| 2026-06-27 | Treat `goal-guardian` as a Tau built-in dispatch role | It is a protocol guard rather than a current `agent-skills/agents` directory entry, so the overlay loader allows built-in Tau roles while still requiring external registry entries for non-built-in agents. |
| 2026-06-27 | Prove multi-step command loops before GitHub mutation | Local command-loop receipts provide stronger evidence for route continuity than isolated one-step receipts while still avoiding durable GitHub writes. |
| 2026-06-27 | Require dry-run terminal GitHub transport before live GitHub writes | The command-loop terminal projection must render exact comment and label commands before any future `--apply` path is considered. |
| 2026-06-27 | Keep live GitHub writes behind an explicit `--apply` flag | Default command-loop GitHub transport remains dry-run; `--apply` is only allowed after the terminal command-loop receipt validates and stops at `human`. |
| 2026-06-27 | Require GitHub auth and target preflight before command-loop mutation | A valid receipt alone is not enough for live writes; Tau must prove the local `gh` session and target issue/PR are available before comment or label commands run. |
| 2026-06-27 | Make Memory pipeline stages receipt-backed | Sparta Chat/TUI should render `Getting Intent...`, `Extracting Entities...`, `Accessing Memory...`, and branch-specific labels from harness receipts, not from hidden reasoning text or UI-only theater. |
| 2026-06-27 | Version the UX Lab Tau chat surface with receipt-backed stage rendering | `#tau` was already referenced by UX Lab routing, but the Tau chat files were untracked in pi-mono. Committing them makes the route reproducible and ties visible process status to `tau.loop2_pipeline_stage.v1` metadata. |
| 2026-06-27 | Treat live composer stage capture as a separate proof rung | Static receipt panels do not prove dynamic chat behavior. The next UI rungs should keep saving screenshots and summaries for actual submitted turns. |
| 2026-06-27 | Do not emit subagent/GitHub handoff metadata from failed Memory route products | `/intent` success alone is not enough to route downstream. If `/clarify`, `/deflect`, or `/answer` fails, Tau should expose the failed stage and stop before fabricating a Memory product or agent handoff. |
| 2026-06-27 | Do not fake an ANSWER browser route when Memory intent does not select it | The live answer endpoint is separate evidence from browser route selection. A future Tau slice can add an explicit answer route fixture or improve intent coverage, but current live UI proof should report the limitation honestly. |
| 2026-06-27 | Render handoff JSON from Tau-owned adapter content instead of a shared-chat-only panel | The shared chat file has unrelated local edits, so the safer bounded slice is to emit the JSON contract from `TauReceiptAdapter` message content and verify it through the existing renderer. |

## Open Questions

- [ ] What are the key architectural decisions?
- [ ] What are the known issues?

## Key Files

| File | Purpose |
|------|---------|
| PROJECT_KNOWLEDGE.md | Shared project knowledge |
| README.md | Human-facing project overview and current harness notes |
| src/tau_coding/subagent_receipt.py | Validates common subagent receipt envelopes |
| src/tau_coding/generated_ticket.py | Validates minimal generated-ticket drafts and derives GitHub labels |
| src/tau_coding/human_goal_change.py | Validates trusted-human-only immutable goal changes |
| src/tau_coding/handoff_dispatch.py | Runs one-step file, command, and registry-command handoff dispatch and writes receipts |
| experiments/goal-locked-subagents/ | Schema artifacts and fixtures for the harness contracts |
| experiments/goal-locked-subagents/agent-command-specs/ | Tau-owned command-spec overlays for real agent registry identities |
| tests/test_subagent_receipt.py | Focused subagent receipt contract tests |
| tests/test_generated_ticket.py | Focused generated-ticket projection tests |
| tests/test_human_goal_change.py | Focused human goal-change tests |
| tests/test_handoff_dispatch.py | Focused one-step and command-loop handoff dispatch tests |
| tests/test_github_handoff.py | Focused dry-run GitHub transport tests for handoffs, generated tickets, and command-loop terminal handoffs |

## Current Proof Artifacts

| Date | Artifact | Scope |
|------|----------|-------|
| 2026-06-27 | `/tmp/tau-production-agent-registry-dispatch/summary.json` | Earlier non-mocked local command dispatch through a temporary local `agent-skills` command spec; superseded by the committed Tau overlay proof below. |
| 2026-06-27 | `/tmp/tau-production-agent-registry-overlay-dispatch/summary.json` | Non-mocked local command dispatch validating `/home/graham/workspace/experiments/agent-skills/agents/project-or-harness-verifier/AGENTS.md` while loading the executable spec from Tau's committed overlay; selected `project-or-harness-verifier`, command exit `0`, response routed to `human`. |
| 2026-06-27 | `/tmp/tau-goal-guardian-overlay-dispatch/summary.json` | Non-mocked local command dispatch through built-in `goal-guardian`; adapter verified the active goal hash, returned result `PASS`, and routed next to `project-or-harness-verifier`. |
| 2026-06-27 | `/tmp/tau-command-loop-overlay-dispatch/summary.json` | Non-mocked local command loop through `goal-guardian` then `project-or-harness-verifier`; both command exits were `0`, the loop stopped at `human`, and route continuity preserved target and goal. |
| 2026-06-27 | `/tmp/tau-command-loop-github-transport/summary.json` | Dry-run GitHub transport for the command-loop terminal handoff; rendered `gh issue comment 123 --repo grahama1970/chatgpt-lab --body-file -` and `gh issue edit 123 --add-label agent-work,next:human,executor:human` without applying them. |
| 2026-06-27 | `/tmp/tau-command-loop-github-transport-apply-rerun/summary.json` | Valid command-loop terminal handoff rendered two dry-run GitHub commands with `ok: true`, `dry_run: true`, `applied: false`, and no errors. |
| 2026-06-27 | `/tmp/tau-command-loop-github-apply-gate/summary.json` | Invalid command-loop receipt run with `--apply` failed closed with `ok: false`, `dry_run: false`, `applied: false`, and `command_count: 0`. |
| 2026-06-27 | `/tmp/tau-command-loop-github-preflight/live-invalid-target-summary.json` | Live `gh` preflight for a valid command-loop receipt targeting a nonexistent repo returned auth exit `0`, target-view exit `1`, `command_count: 0`, and `applied: false`. |
| 2026-06-27 | `/tmp/tau-memory-stage-trace/live-memory-stage-trace-summary.json` | Live Memory-backed harness receipt with `mocked: false`, `live: true`, `memory_first: true`, `selected_skill: memory.clarify`, and `stage_trace` stages `intent`, `extract_entities`, `recall`, `clarify`. |
| 2026-06-27 | `/tmp/codex-ui-verification/pi-mono/tau-uxlab-stage-trace-render/20260627T175254Z.png` | Fresh CDP proof for `http://127.0.0.1:3002/#tau`; read JSON contains `Receipt-backed current stage`, `Clarifying...`, and `/tmp/tau-memory-stage-trace/live-memory-stage-trace-summary.json`. |
| 2026-06-27 | `/tmp/tau-uxlab-live-chat-stage-turn/summary.json` | Live browser composer turn on `#tau`; `mocked: false`, `live: true`, `network_count: 5`, `sample_count: 6`, `live_trace_seen: true`, observed live status `Accessing Memory...`, and screenshots before/live/after. |
| 2026-06-27 | pi-mono commit `26dbf4917` | Tau route fail-closed adapter slice; `npx vitest run src/components/tau/TauChatView.test.ts src/components/tau/tauAgentHandoff.test.ts src/components/tau/tauPeerStatus.test.ts` passed 3 files / 23 tests, and `npx tsc --noEmit --pretty false` exited 0. |
| 2026-06-27 | `/tmp/codex-ui-verification/pi-mono/tau-route-fail-closed-memory-adapter/20260627T180728Z.png` | Fresh CDP proof marker for `http://127.0.0.1:3002/#tau` after the route fail-closed adapter slice; latest marker copied to `/home/graham/.codex/ui-verification/latest.json`. |
| 2026-06-27 | `/tmp/tau-uxlab-live-route-turns/summary.json` | Live browser route harness for `#tau`; `mocked: false`, `live: true`, `route_count: 4`, CLARIFY observed `memory.clarify.v1`, DEFLECT observed `memory.deflect.v1`, RESEARCH observed action `RESEARCH` and stopped before unsupported web claims, COMPLIANCE observed action `COMPLIANCE`, and direct `/api/memory/answer` probe returned `memory.answer.v1` with `can_answer: true`. |
| 2026-06-27 | `/tmp/codex-ui-verification/pi-mono/tau-live-route-turns-memory-pipeline/20260627T181155Z.png` | Fresh CDP proof marker for `http://127.0.0.1:3002/#tau` after live route evidence capture; latest marker copied to `/home/graham/.codex/ui-verification/latest.json`. |
| 2026-06-27 | pi-mono commit `57ddd5304` | Tau handoff JSON contract slice; `npx vitest run src/components/tau/TauChatView.test.ts src/components/tau/tauAgentHandoff.test.ts src/components/tau/tauPeerStatus.test.ts` passed 3 files / 23 tests, and `npx tsc --noEmit --pretty false` exited 0. |
| 2026-06-27 | `/tmp/tau-uxlab-handoff-json-proof/summary.json` | Live browser proof for `#tau` showing `Tau handoff JSON contract`, `"schema": "tau.agent_handoff.v1"`, `"name": "reviewer"`, GitHub projection labels, and production non-claim text after 5 Memory API requests. |
| 2026-06-27 | `/tmp/codex-ui-verification/pi-mono/tau-handoff-json-contract-ui/20260627T182214Z.png` | Fresh CDP proof marker for `http://127.0.0.1:3002/#tau` after the handoff JSON slice; latest marker copied to `/home/graham/.codex/ui-verification/latest.json`. |

## Infrastructure State

<!-- Auto-populated from /project-state --quick -->
