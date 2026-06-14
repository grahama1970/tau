# Tau

Tau is a Python implementation of Pi's minimalist coding-agent harness architecture.

The project is being built in documented phases. Phase 0 establishes the package layout,
development tooling, design documents, and a basic `tau --version` command.

## Development

```bash
uv sync --dev
uv run tau --version
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
