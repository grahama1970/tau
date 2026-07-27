# Issue 158 TUI Voice Proof

Ticket: <https://github.com/grahama1970/tau/issues/158>

This bundle captures the optional Tau TUI voice adapter proof. The ticket
requested stubbed Chatterbox and RealtimeSTT endpoints; this proof uses local
HTTP stubs for those external services.

## Commands

```text
uv run pytest -q tests/test_tui_voice.py
.....                                                                    [100%]
5 passed in 1.96s

uv run ruff check src/tau_coding/tui/voice.py tests/test_tui_voice.py
All checks passed!

uv run python -m py_compile src/tau_coding/tui/voice.py
exit 0

git diff --check
exit 0
```

## Acceptance Coverage

- Approval-wait announcement posts to a Chatterbox-shaped endpoint.
- Spoken status query response is derived from the same run snapshot used by
  the TUI layer.
- Mid-utterance interruption posts to the turn-control endpoint.
- Missing service configuration degrades explicitly instead of crashing.
- A recognized approval phrase does not authorize side effects and does not
  satisfy the human approval gate.

## Evidence Boundary

mocked: yes, for external Chatterbox and RealtimeSTT services represented by
local HTTP stubs.

live: yes, for Tau adapter code, local HTTP transport, and local tests.

provider_live: no

This does not prove real microphone capture quality, real Chatterbox audio
synthesis quality, or voice authentication.
