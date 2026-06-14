# 04 — Sessions

Sessions preserve conversations and agent state across runs.

## Design direction

Tau will use an append-only session tree. Instead of mutating old state, Tau appends entries and reconstructs state by replaying them.

## Planned entry types

- `message`
- `model_change`
- `thinking_level_change`
- `compaction`
- `branch_summary`
- `label`
- `leaf`
- `session_info`
- `custom`

## Boundary

Low-level session primitives belong in `tau_agent`. File locations, slash commands, and coding-agent workflows belong in `tau_coding`.
