# 00 — Roadmap

Tau is being built as a Python implementation of Pi's minimalist coding-agent harness architecture.
The goal is not a line-by-line port; the goal is to preserve the same boundaries while using Python-native tools.

## Package layers

```text
tau_ai       provider/model streaming layer
tau_agent    portable agent harness, loop, tools, events, sessions
tau_coding   CLI app, resources, skills, extensions, commands, UI integration
```

## Phase plan

1. Project foundation and design docs.
2. Core message, tool, and event types.
3. Provider interface with fake and real providers.
4. Pure agent loop.
5. Reusable `AgentHarness`.
6. Built-in coding tools.
7. Non-interactive print-mode CLI.
8. Append-only session tree persistence.
9. Coding session wrapper with commands.
10. Skills, prompt templates, and system prompt assembly.
11. Rich renderers.
12. Textual TUI behind an adapter boundary.
13. Extensions.
14. Compaction and context management.
15. Packaging, docs, and examples.

## Phase 0 deliverables

Phase 0 creates the docs, package scaffold, development checks, and a basic `tau --version` command.
