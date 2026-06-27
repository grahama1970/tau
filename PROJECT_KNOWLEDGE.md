# Project Knowledge: tau

**Last updated:** 2026-06-27 18:05Z / 14:05 EDT by agent
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

## Infrastructure State

<!-- Auto-populated from /project-state --quick -->
