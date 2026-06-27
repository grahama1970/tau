# Project Knowledge: tau

**Last updated:** 2026-06-27 13:43 by agent
**Status:** Active development

## Current Understanding

- Project initialized, knowledge tracking started
- Tau is a fork of alejandro-ao/tau being hardened into a goal-locked agentic harness. Current local slices add Loop2-compatible run receipts plus minimal model-facing contracts for subagent receipts, generated tickets, handoffs, and human-only goal changes. Tau derives GitHub labels deterministically instead of asking model agents to duplicate label/projection fields.
- Tau now has one-step dispatch receipts for routed handoffs. `handoff-dispatch-agent-command` validates the start handoff, selects `next_agent.name`, loads an opt-in `tau-dispatch-command.json` from that agent registry entry, runs one bounded command, and validates stdout as the next `tau.agent_handoff.v1`.
- Tau can now use a committed command-spec overlay with `--command-spec-root`. The selected agent is still validated against `/home/graham/workspace/experiments/agent-skills/agents`, but the executable `tau-dispatch-command.json` can live under Tau's `experiments/goal-locked-subagents/agent-command-specs/` tree for reproducible harness experiments.
- Built-in `goal-guardian` now has a deterministic adapter. It reads the start handoff from stdin, requires `TAU_HANDOFF_ACTIVE_GOAL_HASH`, refuses stale goal hashes, and emits a normal `tau.agent_handoff.v1` only when the active goal hash is preserved.

## Recent Decisions

| Date | Decision | Why |
|------|----------|-----|
| 2026-06-27 | Initialize project knowledge | Enable shared human/agent context |
| 2026-06-27 | Use a fork at grahama1970/tau rather than a new unrelated repo | The project already has upstream history at alejandro-ao/tau, so a fork preserves attribution and makes future upstream comparison/pull possible. |
| 2026-06-27 | Keep GitHub mutation out of the first harness slices | Current proof is schema, validator, fixture, and deterministic projection only; live issue creation should come after the contracts are accepted. |
| 2026-06-27 | Keep registry command dispatch opt-in per agent | A real agent registry can contain many roles, but Tau should only execute commands for entries with explicit `tau-dispatch-command.json`. Missing specs fail closed with a `BLOCKED` receipt. |
| 2026-06-27 | Store experimental dispatch command specs in Tau overlays | This avoids depending on untracked files in the dirty `agent-skills` repo while still proving routes against real registry identities. |
| 2026-06-27 | Treat `goal-guardian` as a Tau built-in dispatch role | It is a protocol guard rather than a current `agent-skills/agents` directory entry, so the overlay loader allows built-in Tau roles while still requiring external registry entries for non-built-in agents. |

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
| tests/test_handoff_dispatch.py | Focused one-step handoff dispatch tests |

## Current Proof Artifacts

| Date | Artifact | Scope |
|------|----------|-------|
| 2026-06-27 | `/tmp/tau-production-agent-registry-dispatch/summary.json` | Earlier non-mocked local command dispatch through a temporary local `agent-skills` command spec; superseded by the committed Tau overlay proof below. |
| 2026-06-27 | `/tmp/tau-production-agent-registry-overlay-dispatch/summary.json` | Non-mocked local command dispatch validating `/home/graham/workspace/experiments/agent-skills/agents/project-or-harness-verifier/AGENTS.md` while loading the executable spec from Tau's committed overlay; selected `project-or-harness-verifier`, command exit `0`, response routed to `human`. |
| 2026-06-27 | `/tmp/tau-goal-guardian-overlay-dispatch/summary.json` | Non-mocked local command dispatch through built-in `goal-guardian`; adapter verified the active goal hash, returned result `PASS`, and routed next to `project-or-harness-verifier`. |

## Infrastructure State

<!-- Auto-populated from /project-state --quick -->
