# Tau - Goal-Locked Agent Harness

<p align="center">
  <img
    src="docs/assets/tau-header.webp"
    alt="Tau agentic harness console in a science-fiction workspace"
    style="max-width: 100%; height: auto; display: block;"
  />
</p>

> Turn agent work into receipt-backed, goal-locked loops.

Tau started as a small Python coding-agent harness inspired by Pi. This fork is
being hardened into an experimental agentic harness for long-running work:
Memory-first chat, bounded subagents, explicit handoffs, human-controlled goal
changes, and GitHub tickets as the durable transport.

The important idea is simple:

```text
agents may work and recommend the next step
Tau validates the receipt and routes the next step
only the human may change the immutable goal
```

Tau is not trying to hide orchestration inside model reasoning. Every meaningful
transition should leave a local receipt, a schema-valid JSON block, or a
GitHub-shaped projection that another agent or human can inspect.

## What it does

Tau currently provides two layers:

1. **Coding-agent runtime** - an installable `tau` command with provider
   configuration, a Textual TUI, session history, slash commands, local tools,
   and print-mode execution.
2. **Agentic harness experiments** - goal-locked receipt contracts, bounded
   subagent dispatch, Memory-first chat routing, and dry-run GitHub ticket/comment
   projections.

The coding runtime keeps the original teaching goal: make a coding agent small
enough to understand. The harness experiments add the control plane needed for
longer work:

- minimal `tau.agent_handoff.v1` JSON for subagents and humans
- minimal `tau.generated_ticket.v1` JSON for ChatGPT Pro/WebGPT ticket drafts
- human-only `tau.human_goal_change.v1` packets
- deterministic goal-guardian reconciliation receipts
- command-backed subagent loops with finite steps
- GitHub transport that is dry-run by default and apply-gated
- Memory-first route handling for chat surfaces
- proof artifacts that state what was exercised and what remains unproven

## When to use it

Use Tau when an agent task needs durable state and explicit routing instead of a
single chat response.

| Situation | Why Tau helps |
| --- | --- |
| Long-running implementation work | Each bounded step emits a receipt and names the next agent. |
| Human course correction | Human goal changes are explicit packets routed through `goal-guardian`. |
| ChatGPT Pro/WebGPT collaboration | WebGPT can draft tickets; Tau validates and projects them. |
| GitHub-backed task queues | Tau derives labels such as `next:<agent>` and `executor:<executor>`. |
| Memory-first chat | User turns enter through Memory intent before routing to answer, clarify, deflect, research, or compliance paths. |
| Reliability hardening | Local tests, live browser runs, and proof summaries are kept separate from mocked wiring tests. |

Tau is still experimental. Treat dry-run GitHub transport, local command-loop
receipts, and UX Lab chat evidence as proof of specific rungs, not proof of a
finished global Sparta Chat or production orchestration system.

## Quickstart

Install and run the original Tau CLI:

```bash
cd /home/graham/workspace/experiments/tau
uv sync
uv run tau --help
uv run tau --print "Summarize this repository in three bullets."
```

Run the focused test suite:

```bash
uv run pytest tests/test_subagent_receipt.py tests/test_generated_ticket.py tests/test_human_goal_change.py -q
uv run pytest tests/test_handoff_dispatch.py tests/test_github_handoff.py -q
```

Run a local command-loop harness receipt:

```bash
uv run tau handoff-command-loop \
  experiments/goal-locked-subagents/fixtures/valid-human-goal-change.json \
  --max-steps 1 \
  --command-spec-root experiments/goal-locked-subagents/agent-command-specs
```

Render dry-run GitHub transport from a command-loop receipt:

```bash
uv run tau handoff-command-loop-github-transport \
  /path/to/command-loop-receipt.json \
  --receipt /tmp/tau-github-transport.json
```

By default, GitHub transport renders commands only. Live mutation requires
`--apply` and still runs auth/target preflight checks before comment or label
commands.

## Memory-first chat direction

Tau chat should begin with the `$memory` pipeline, not with ad hoc product logic.
The intended route is:

```text
intent -> extract entities -> access memory -> answer | clarify | deflect | research | compliance
```

The UX Lab Tau chat surface currently lives in the `pi-mono` workspace and is
used as the browser proving ground for the shared global chat UX. The latest
bounded slices prove:

- dynamic Memory stage traces can be rendered from receipt data
- CLARIFY, DEFLECT, RESEARCH, COMPLIANCE, and selected ANSWER behavior can fail
  closed when a Memory route product is missing
- successful compliance routes can render a full `tau.agent_handoff.v1` JSON
  contract
- accepted external handoff receipts can project dry-run GitHub comments/labels
  without claiming live GitHub mutation

This is a harness rung, not a final chat product. The final shared chat still
needs accepted UX, real content embed handling, `create-figure`,
`create-evidence-case`, persona voice integration, and live GitHub mutation
policy before it can be treated as production behavior.

## Goal-locked harness model

Tau's agent-facing contract is deliberately small. A normal handoff contains:

```json
{
  "schema": "tau.agent_handoff.v1",
  "github": {
    "repo": "grahama1970/tau",
    "target": "issue#123"
  },
  "goal": {
    "goal_id": "goal-example",
    "goal_version": 1,
    "goal_hash": "sha256:..."
  },
  "previous_subagent": "coder",
  "context": {
    "summary": "What matters now.",
    "artifacts": []
  },
  "result": {
    "status": "COMPLETED",
    "summary": "What changed or was observed.",
    "evidence": []
  },
  "rationale": "Why this result implies the next step.",
  "next_agent": {
    "name": "reviewer",
    "executor": "either",
    "reason": "Independent validation is required."
  },
  "required_evidence": [],
  "stop_condition": "Reviewer posts a schema-valid receipt."
}
```

Tau owns the deterministic expansion:

```text
next_agent.name     -> next:<agent>
next_agent.executor -> executor:<executor>
github.target       -> issue, PR, or new ticket projection
goal_hash           -> active goal validation
schema              -> parser and validator selection
```

Agents do not get to invent missing labels, mutate the immutable goal, or skip
the next route. If the JSON does not validate, Tau should refuse to dispatch.

## Repository map

```text
src/tau_ai/                         provider/model streaming layer
src/tau_agent/                      portable agent loop, events, tools, sessions
src/tau_coding/                     CLI app, coding tools, TUI, harness commands
experiments/goal-locked-subagents/  goal-locked contract schemas and fixtures
experiments/loop2-alignment/        Loop2 and Memory/Brave alignment experiments
docs/                               original Tau architecture and usage docs
PROJECT_KNOWLEDGE.md                current project memory for humans and agents
```

Important harness files:

```text
src/tau_coding/subagent_receipt.py
src/tau_coding/generated_ticket.py
src/tau_coding/human_goal_change.py
src/tau_coding/handoff_dispatch.py
src/tau_coding/github_handoff.py
experiments/goal-locked-subagents/schemas/
experiments/goal-locked-subagents/agent-command-specs/
```

## Evidence discipline

Tau reports should distinguish mocked wiring from live behavior.

Use this language when reporting a rung:

```text
mocked: yes|no
live: yes|no
what was exercised
what remains unverified
artifact paths
```

Examples of current proof artifacts are tracked in `PROJECT_KNOWLEDGE.md`.
Recent evidence includes:

- `/tmp/tau-memory-chat-proof-suite-20260627T233356Z/summary.json`
- `/tmp/tau-live-memory-chat-proof-compliance-20260627T233340Z`
- `/tmp/codex-ui-verification/pi-mono/tau-external-subagent-github-projection-ui/20260627T233448Z.png`

Those artifacts prove the named rung only. They do not prove final Tau/Sparta
Chat readiness, live GitHub ticket mutation, or unrestricted subagent execution.

## WebGPT escalation

Tau can use WebGPT between phases, when architecture is uncertain, when the
agent is drifting, or when a complex harness decision needs external review.
The project-local browser binding lives under `.ask/`, but the current Tau
convention is direct `$webgpt` for phase and architecture review. WebGPT output
is design input; deterministic local artifacts remain the proof source.

## Upstream

This repository is a fork of `alejandro-ao/tau`, pushed under
`grahama1970/tau` for the harness experiments. The original Tau architecture
docs are still useful and remain under `docs/`.
