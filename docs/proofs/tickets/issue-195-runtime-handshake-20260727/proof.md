# Tau #195 Closure Proof

Ticket: https://github.com/grahama1970/tau/issues/195

## Result

`status=PASS`, `mocked=false`, `live=true`, `provider_live=false`.

Primary receipt:

```text
docs/proofs/tickets/issue-195-runtime-handshake-20260727/runtime-handshake.json
```

Independent readback:

```text
docs/proofs/tickets/issue-195-runtime-handshake-20260727/readback.json
```

Readback confirms:

- Tau version is present: `0.1.0`.
- Runtime capabilities are present: 13 lanes.
- `$tau` wrapper resolves the same checkout from cwd:
  `/tmp/tau-issue189-1785166987`.
- Planned unsupported lane `proof-index build` is marked `UNAVAILABLE`.
- No direct SciLLM/project-agent provider shortcut is introduced.

## Commands

Live E2E:

```bash
/home/graham/workspace/experiments/agent-skills/skills/tau/run.sh doctor
/home/graham/workspace/experiments/agent-skills/skills/tau/run.sh status
PYTHONPATH=src uv run tau runtime-handshake --output docs/proofs/tickets/issue-195-runtime-handshake-20260727/runtime-handshake.json
/home/graham/workspace/experiments/agent-skills/skills/tau/run.sh runtime-handshake --output docs/proofs/tickets/issue-195-runtime-handshake-20260727/wrapper-runtime-handshake.json
```

Deterministic support:

```bash
PYTHONPATH=src uv run python -m py_compile src/tau_coding/runtime_handshake.py src/tau_coding/cli.py
PYTHONPATH=src uv run ruff check src/tau_coding/runtime_handshake.py src/tau_coding/cli.py
PYTHONPATH=src uv run pytest -q tests/test_cli.py tests/test_server.py
python3 -m py_compile /home/graham/workspace/experiments/agent-skills/skills/tau/scripts/tau_skill.py
uv run --project /home/graham/workspace/experiments/agent-skills/skills/tau ruff check /home/graham/workspace/experiments/agent-skills/skills/tau/scripts/tau_skill.py /home/graham/workspace/experiments/agent-skills/skills/tau/tests/test_tau_skill.py
uv run --project /home/graham/workspace/experiments/agent-skills/skills/tau pytest -q /home/graham/workspace/experiments/agent-skills/skills/tau/tests/test_tau_skill.py
```

Observed results:

- Tau compile: exit 0.
- Tau ruff: exit 0.
- Tau focused pytest: 240 passed.
- `$tau` wrapper compile: exit 0.
- `$tau` wrapper ruff: exit 0.
- `$tau` wrapper pytest: 7 passed.

## Boundary

This proves the runtime handshake and wrapper discovery path for #195. It does
not close #72 and does not prove provider/model semantic quality.
