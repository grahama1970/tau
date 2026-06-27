# Project Knowledge: tau

**Last updated:** 2026-06-27 10:09 by agent
**Status:** Active development

## Current Understanding

- Project initialized, knowledge tracking started
- Tau is a fork of alejandro-ao/tau being hardened into a goal-locked agentic harness. Current local slices add Loop2-compatible run receipts plus minimal model-facing contracts for subagent receipts, generated tickets, handoffs, and human-only goal changes. Tau derives GitHub labels deterministically instead of asking model agents to duplicate label/projection fields.

## Recent Decisions

| Date | Decision | Why |
|------|----------|-----|
| 2026-06-27 | Initialize project knowledge | Enable shared human/agent context |
| 2026-06-27 | Use a fork at grahama1970/tau rather than a new unrelated repo | The project already has upstream history at alejandro-ao/tau, so a fork preserves attribution and makes future upstream comparison/pull possible. |
| 2026-06-27 | Keep GitHub mutation out of the first harness slices | Current proof is schema, validator, fixture, and deterministic projection only; live issue creation should come after the contracts are accepted. |

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
| experiments/goal-locked-subagents/ | Schema artifacts and fixtures for the harness contracts |
| tests/test_subagent_receipt.py | Focused subagent receipt contract tests |
| tests/test_generated_ticket.py | Focused generated-ticket projection tests |
| tests/test_human_goal_change.py | Focused human goal-change tests |

## Infrastructure State

<!-- Auto-populated from /project-state --quick -->
