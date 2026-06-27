<p align="center">
  <img src="docs/assets/tau-header.svg" alt="Tau — a Python coding-agent harness inspired by Pi" width="100%" />
</p>

<p align="center">
  <strong>A minimalist coding-agent harness in Python, inspired by Pi and built as a teaching project.</strong>
</p>

<p align="center">
  <a href="https://alejandro-ao.github.io/tau/">Documentation</a>
  ·
  <a href="docs/getting-started.md">Getting started</a>
  ·
  <a href="docs/architecture/index.md">Architecture notes</a>
  ·
  <a href="https://github.com/alejandro-ao/tau/issues/1">Roadmap</a>
</p>

---

## What is Tau?

Tau is a Python implementation of the minimalist coding-agent harness architecture
popularized by **Pi**. It is both:

1. a usable terminal coding agent, and
2. a readable, phase-by-phase reference implementation for learning how coding
   agents are assembled.

The project intentionally keeps the core pieces small and explicit: model
providers stream events, an agent loop turns those events into tool execution and
transcript updates, a reusable harness owns state, and the coding app adds local
files, shell tools, sessions, skills, commands, and terminal frontends.

```text
tau_ai       provider/model streaming layer
tau_agent    portable agent harness, loop, tools, events, sessions
tau_coding   CLI app, resources, skills, commands, sessions, UI integration
```

The central design boundary is:

```text
AgentHarness = reusable agent brain
AgentSession = coding-agent environment
TUI          = one possible frontend
```

Tau should make the architecture legible. If you want to understand how a coding
agent works without starting from a large production codebase, this repository is
for you.

## Why Tau exists

Tau is being built as an effort to teach how to create coding agents.

The philosophy is:

- **Small layers beat magic.** Each package has a clear job and can be explained
  independently.
- **Events are the contract.** The agent harness emits provider-neutral events;
  renderers and TUIs consume them.
- **The core stays portable.** `tau_agent` does not depend on Textual, Rich,
  shell config directories, slash commands, or application-specific resources.
- **Tools are ordinary typed functions.** File and shell capabilities are exposed
  through explicit schemas and deterministic result objects.
- **Sessions are durable and inspectable.** Tau stores append-only JSONL session
  transcripts under `~/.tau/sessions/`.
- **Documentation follows implementation.** The project is developed in small,
  documented phases so readers can trace how the system grows.

Pi is the design inspiration; Tau is the Python learning path.

## Current capabilities

Tau currently includes:

- an installable `tau` console command
- a Textual interactive TUI
- non-interactive print mode for one-shot prompts
- OpenAI-compatible, Anthropic, OpenAI Codex subscription, OpenRouter, and
  Hugging Face provider support through provider configuration
- provider retry/backoff events and thinking/reasoning deltas
- built-in local coding tools: `read`, `write`, `edit`, and `bash`
- durable per-project sessions and session resume
- session tree branching and HTML/JSONL export
- slash commands, model picker, theme picker, and autocomplete
- skills, prompt templates, and `AGENTS.md` project-context discovery
- context accounting, manual compaction, and optional automatic compaction
- Rich/plain/json/transcript rendering paths for print-mode output
- a deterministic fake provider used by tests
- experimental Loop2-compatible run receipts and monitor artifacts for bounded
  harness runs
- experimental goal-locked subagent contracts for routed handoffs, generated
  tickets, and human-only goal changes
- deterministic GitHub label projection for generated ticket drafts, where Tau
  derives `agent-work`, `next:<agent>`, and `executor:<executor>` labels instead
  of asking model agents to emit duplicated label state

Tau is still evolving. Expect the command surface and internals to improve as the
roadmap progresses.

## Experimental agentic harness work

This fork is being used to harden Tau as a goal-locked agentic harness. The
current experiment keeps model-facing JSON small and moves strict routing rules
into Tau validators.

Implemented local contract slices include:

- `tau.subagent_receipt.v1`: common receipt envelope for bounded subagents,
  requiring goal, context, result, rationale, evidence, next route, and stop
  condition.
- `tau.generated_ticket.v1`: minimal ChatGPT Pro/WebGPT ticket draft contract.
  The model supplies ticket kind/title/body, requested work, rationale, next
  agent, required evidence, and stop condition. Tau derives GitHub labels.
- `tau.agent_handoff.v1`: minimal handoff schema for subagents and existing
  ticket comments.
- `tau.human_goal_change.v1`: rare human-only goal mutation contract that must
  route to `goal-guardian`.
- `tau.human_goal_change_bridge_receipt.v1`: deterministic local bridge receipt
  for turning a trusted `tau.human_goal_change.v1` packet into a normal
  `tau.agent_handoff.v1` start handoff routed to `goal-guardian`. The bridge
  does not write goal capsules or mutate GitHub; it fails closed unless
  `--trusted-human` and the active goal hash are supplied.
- `tau.goal_guardian_reconciliation_receipt.v1`: deterministic receipt emitted
  when `goal-guardian` sees a bridged human goal-change request. The current
  slice records the proposed new goal, records that open-ticket reconciliation
  has not started without an authoritative ticket source, and routes to `human`
  before any non-human agent can continue.
- command-backed one-step handoff dispatch:
  - `tau handoff-dispatch-command` runs one bounded local command and validates
    its stdout as `tau.agent_handoff.v1`.
  - `tau handoff-dispatch-agent-command` selects the next agent from the start
    handoff, validates that agent against an `agent-skills/agents`-style
    registry, loads that agent's opt-in `tau-dispatch-command.json`, runs it
    once, and writes `tau.agent_handoff_dispatch_receipt.v1`.
  - `--command-spec-root` can point at a Tau-owned command-spec overlay such as
    `experiments/goal-locked-subagents/agent-command-specs/`. This keeps
    executable dispatch specs versioned with Tau while still validating
    identities against the real agent registry.
  - `tau handoff-agent-adapter` is a small stdin-to-handoff adapter that lets
    registry command specs emit the minimal handoff JSON without custom wrapper
    code.
  - `tau handoff-goal-guardian-adapter` is a deterministic built-in adapter
    that refuses missing/stale active goal hashes and emits a preserved-goal
    `tau.agent_handoff.v1` before routing onward. When the incoming handoff
    carries `context.human_goal_change`, the adapter writes a
    `tau.goal_guardian_reconciliation_receipt.v1` artifact and routes to
    `human` instead of continuing to another worker.
- human-goal-change bridge:
  - `tau human-goal-change-bridge <human-goal-change.json> --active-goal-hash
    <hash> --trusted-human --handoff-out <start-handoff.json> --receipt
    <receipt.json>` writes the generated handoff only on successful validation
    and always writes a receipt for success or fail-closed validation errors.
- command-backed handoff loops:
  - `tau handoff-command-loop` follows selected `next_agent` routes through
    opt-in command specs, records each command-backed dispatch step, and stops
    when the route reaches `human`, fails validation, or exhausts `--max-steps`.
  - `tau handoff-command-loop-github-transport` renders the exact dry-run
    GitHub command for the terminal handoff from a command-loop receipt. Existing
    `issue#N` and `pr#N` targets render `gh issue/pr comment` plus label edits.
    `target: "new"` renders `gh issue create` using the handoff body and derived
    labels. GitHub mutation is explicit-only via `--apply`; invalid command-loop
    receipts fail closed before any `gh` commands are run. Valid command-loop
    apply runs `gh auth status`; existing issue/PR targets also run
    `gh issue/pr view` before posting a comment or editing labels.

The current validators and dispatch receipts are intentionally local and
deterministic. GitHub writes are apply-gated: the default path only renders
commands, and the `--apply` path still requires a valid terminal command-loop
receipt plus passing GitHub auth and target preflight checks before it can call
mutating `gh` commands.

Relevant files:

```text
src/tau_coding/subagent_receipt.py
src/tau_coding/generated_ticket.py
src/tau_coding/human_goal_change.py
src/tau_coding/handoff_dispatch.py
experiments/goal-locked-subagents/
experiments/goal-locked-subagents/agent-command-specs/
experiments/goal-locked-subagents/schemas/tau.human_goal_change_bridge_receipt.v1.schema.json
tests/test_subagent_receipt.py
tests/test_generated_ticket.py
tests/test_human_goal_change.py
tests/test_handoff_dispatch.py
```

Run the focused harness checks:

```bash
uv run pytest tests/test_subagent_receipt.py tests/test_generated_ticket.py tests/test_human_goal_change.py -q
```

Run the focused dispatch checks:

```bash
uv run pytest tests/test_handoff_dispatch.py tests/test_cli.py -q
```

Run the bridge and command-loop checks:

```bash
uv run pytest tests/test_human_goal_change.py tests/test_handoff_dispatch.py tests/test_cli.py tests/test_github_handoff.py -q
```

## Install

Tau targets the Python version declared in `pyproject.toml` and uses
[`uv`](https://docs.astral.sh/uv/) for the recommended workflow.

Install from GitHub:

```bash
uv tool install git+https://github.com/alejandro-ao/tau.git
tau --version
```

Install from a local checkout:

```bash
git clone https://github.com/alejandro-ao/tau.git
cd tau
uv tool install --editable .
tau --version
```

For development:

```bash
uv sync --dev --group docs
uv run tau --version
```

## First run

Start the interactive terminal UI:

```bash
tau
```

Start the TUI and submit the first prompt immediately:

```bash
tau "explain this repository"
```

Run a one-shot non-interactive prompt:

```bash
tau -p "summarize the architecture"
```

Choose a configured provider/model:

```bash
tau --provider openai --model gpt-4.1 "review this codebase"
tau --provider local --model qwen -p "list the main modules"
```

Use another working directory for coding tools:

```bash
tau --cwd /path/to/project "find the CLI entry point"
```

## Configure a model provider

The easiest path is from inside the TUI:

```text
/login
/login openai
/login openai-codex
/logout
/logout openai
/model
```

`/login` can save API-key credentials for built-in providers or authenticate an
OpenAI Codex subscription account with OAuth. Credentials are stored in
`~/.tau/credentials.json` with private file permissions. Provider metadata lives
in `~/.tau/providers.json`. `/logout` removes only credentials saved in Tau's
`credentials.json`; environment variables and provider configuration are
unchanged.

You can also configure an OpenAI-compatible provider from the CLI:

```bash
tau --provider local \
  --base-url http://localhost:11434/v1 \
  --api-key-env LOCAL_API_KEY \
  --model qwen \
  setup
```

Then run:

```bash
export LOCAL_API_KEY="..."
tau --provider local
```

Useful provider commands:

```bash
tau providers
```

See [docs/providers.md](docs/providers.md) and
[docs/configuration.md](docs/configuration.md) for details.

## Working in the TUI

Common slash commands:

| Command | Purpose |
| --- | --- |
| `/login [provider]` | Save or refresh provider credentials. |
| `/logout [provider]` | Remove Tau-saved provider credentials. |
| `/model` | Choose the active provider/model. |
| `/scoped-models` | Pick models available for quick cycling. |
| `/session` | Show session and context information. |
| `/resume [session-id]` | Resume a previous session. |
| `/tree` | Branch from a previous session entry. |
| `/name <new name>` | Rename the current session. |
| `/compact <summary>` | Replace active context with a manual summary. |
| `/export [--format html\|jsonl] [destination]` | Export the current session. |
| `/reload` | Reload local resources and project context. |
| `/theme [name]` | Show or set the TUI theme. |
| `/hotkeys` | Show common keyboard shortcuts. |
| `/quit` | Exit the session. |

Important TUI behavior:

- Click anywhere in the main TUI to return keyboard focus to the prompt input.

Common shortcuts:

| Shortcut | Action |
| --- | --- |
| `Enter` | Submit prompt. |
| `Shift+Enter` | Insert newline. |
| `Alt+Enter` | Queue a follow-up while the agent is running. |
| `Esc` | Cancel active run. |
| `Ctrl+K` | Open slash-command completions. |
| `Ctrl+R` | Open session picker. |
| `Shift+Tab` | Cycle thinking mode. |
| `Ctrl+T` | Toggle thinking-token display. |
| `Ctrl+O` | Collapse or expand tool output. |
| `Ctrl+P` | Cycle scoped models. |
| `Ctrl+D` | Quit. |

## Sessions, resources, and files

Tau stores durable app state in your home directory:

```text
~/.tau/providers.json       provider metadata
~/.tau/credentials.json     saved API keys and OAuth credentials
~/.tau/tui.json             TUI theme/keybinding settings
~/.tau/sessions/            append-only JSONL session transcripts
~/.tau/skills/              user Tau skills
~/.tau/prompts/             user prompt templates
~/.tau/AGENTS.md            user Tau instructions
```

Tau also reads user-level `.agents` resources and project-local resources from
the active working directory, including `AGENTS.md`, `.tau/`, and `.agents/`
locations. This lets a project teach Tau how it should behave without changing
Tau's core harness.

## Use Tau as a library

Tau's reusable brain lives in `tau_agent`:

```python
from tau_agent import AgentHarness, AgentHarnessConfig

harness = AgentHarness(
    AgentHarnessConfig(
        provider=provider,
        model="my-model",
        system="You are a helpful coding agent.",
        tools=tools,
    )
)

async for event in harness.prompt("Explain this package"):
    print(event)
```

That harness is deliberately independent of the CLI/TUI. You can build another
frontend by consuming the same event stream.

## Development

Set up the repository:

```bash
uv sync --dev --group docs
```

Run checks:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Run Tau locally:

```bash
uv run tau
uv run tau -p "explain this repo"
```

Run the documentation site:

```bash
uv run --group docs mkdocs serve
```

Then open `http://127.0.0.1:8000`.

## Documentation map

- [Getting Started](docs/getting-started.md)
- [Installation](docs/installation.md)
- [Configuration and Files](docs/configuration.md)
- [Providers](docs/providers.md)
- [Architecture](docs/01-architecture.md)
- [Architecture phase notes](docs/architecture/index.md)
- [Agent Loop](docs/agent-loop.md)
- [Agent Harness](docs/harness.md)
- [Tools](docs/03-tools.md)
- [Sessions](docs/04-sessions.md)
- [Building a Custom TUI](docs/custom-tui.md)
- [Roadmap](docs/00-roadmap.md)

## Project status

Tau is under active development. The implementation roadmap is tracked in
[GitHub issue #1](https://github.com/alejandro-ao/tau/issues/1), and the docs
under `docs/architecture/` record the completed phases.

The goal is not to hide complexity. The goal is to make each part of a coding
agent visible, testable, and understandable.
