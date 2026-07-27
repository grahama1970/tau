# Issue 195 Runtime Handshake Proof

Ticket: https://github.com/grahama1970/tau/issues/195

## Live E2E Commands

```bash
/home/graham/workspace/experiments/agent-skills/skills/tau/run.sh doctor
/home/graham/workspace/experiments/agent-skills/skills/tau/run.sh status
PYTHONPATH=src uv run tau runtime-handshake --output docs/proofs/tickets/issue-195-runtime-handshake-20260727/runtime-handshake.json
/home/graham/workspace/experiments/agent-skills/skills/tau/run.sh runtime-handshake --output docs/proofs/tickets/issue-195-runtime-handshake-20260727/wrapper-runtime-handshake.json
```

Readback artifact: `readback.json`

## Deterministic Gates

```bash
PYTHONPATH=src uv run python -m py_compile src/tau_coding/runtime_handshake.py src/tau_coding/cli.py
PYTHONPATH=src uv run ruff check src/tau_coding/runtime_handshake.py src/tau_coding/cli.py
PYTHONPATH=src uv run pytest -q tests/test_cli.py tests/test_server.py
python3 -m py_compile /home/graham/workspace/experiments/agent-skills/skills/tau/scripts/tau_skill.py
uv run --project /home/graham/workspace/experiments/agent-skills/skills/tau ruff check /home/graham/workspace/experiments/agent-skills/skills/tau/scripts/tau_skill.py /home/graham/workspace/experiments/agent-skills/skills/tau/tests/test_tau_skill.py
uv run --project /home/graham/workspace/experiments/agent-skills/skills/tau pytest -q /home/graham/workspace/experiments/agent-skills/skills/tau/tests/test_tau_skill.py
```

Results captured this turn:

- Tau compile: exit 0.
- Tau ruff: exit 0, all checks passed.
- Tau focused pytest: 240 passed.
- `$tau` wrapper compile: exit 0.
- `$tau` wrapper ruff: exit 0, all checks passed.
- `$tau` wrapper pytest: 7 passed.

## Boundary

mocked: no

live: yes

provider_live: no

This proves the Tau CLI exposes a runtime-handshake receipt, the `$tau` wrapper
can resolve the same checkout from cwd, implemented runtime lanes are listed,
planned unsupported lanes are marked unavailable, and provider/model access
remains behind Tau-owned adapter boundaries. It does not prove full #72 program
completion or provider/model semantic quality.
