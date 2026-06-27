# Project Knowledge: tau

**Last updated:** 2026-06-27 13:15 by agent
**Status:** Active development

## Current Understanding

- Project initialized, knowledge tracking started
- Tau is a fork of alejandro-ao/tau being hardened into a goal-locked agentic harness. Current local slices add Loop2-compatible run receipts plus minimal model-facing contracts for subagent receipts, generated tickets, handoffs, and human-only goal changes. Tau derives GitHub labels deterministically instead of asking model agents to duplicate label/projection fields.
- Tau now has one-step dispatch receipts for routed handoffs. `handoff-dispatch-agent-command` validates the start handoff, selects `next_agent.name`, loads an opt-in `tau-dispatch-command.json` from that agent registry entry, runs one bounded command, and validates stdout as the next `tau.agent_handoff.v1`.

## Recent Decisions

| Date | Decision | Why |
|------|----------|-----|
| 2026-06-27 | Initialize project knowledge | Enable shared human/agent context |
| 2026-06-27 | Use a fork at grahama1970/tau rather than a new unrelated repo | The project already has upstream history at alejandro-ao/tau, so a fork preserves attribution and makes future upstream comparison/pull possible. |
| 2026-06-27 | Keep GitHub mutation out of the first harness slices | Current proof is schema, validator, fixture, and deterministic projection only; live issue creation should come after the contracts are accepted. |
| 2026-06-27 | Keep registry command dispatch opt-in per agent | A real agent registry can contain many roles, but Tau should only execute commands for entries with explicit `tau-dispatch-command.json`. Missing specs fail closed with a `BLOCKED` receipt. |

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
| tests/test_subagent_receipt.py | Focused subagent receipt contract tests |
| tests/test_generated_ticket.py | Focused generated-ticket projection tests |
| tests/test_human_goal_change.py | Focused human goal-change tests |
| tests/test_handoff_dispatch.py | Focused one-step handoff dispatch tests |

## Current Proof Artifacts

| Date | Artifact | Scope |
|------|----------|-------|
| 2026-06-27 | `/tmp/tau-production-agent-registry-dispatch/summary.json` | Non-mocked local command dispatch through `/home/graham/workspace/experiments/agent-skills/agents/project-or-harness-verifier/tau-dispatch-command.json`; selected `project-or-harness-verifier`, command exit `0`, response routed to `human`. |

## Infrastructure State

<!-- Auto-populated from /project-state --quick -->
