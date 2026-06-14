# 03 — Tools

Tools let the assistant inspect and modify the user's environment through structured calls.

## Core model

`tau_agent` will define provider-neutral tool definitions and structured tool results.
The agent loop will receive a list of tools and execute requested calls without depending on any coding-agent UI.

## Initial coding tools

`tau_coding` will eventually provide:

- `read`
- `write`
- `edit`
- `bash`

Important behavior to preserve:

- exact-text replacement for edits
- rollback if a multi-edit operation fails
- output truncation
- bash timeouts
- structured success and error results
