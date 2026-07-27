# Issue 47 Script Contract Proof

Ticket: <https://github.com/grahama1970/tau/issues/47>

## Scope

This bundle exercises the Tau Phase 06 script creator-reviewer loop from a real
local persona-dream prompt bundle. It does not call a paid provider, Kling,
voice synthesis, or public upload service.

## Primary Command

```bash
uv run python -m tau_coding.persona_dream_dream_packet_agent \
  --script-proof \
  --work-order docs/proofs/tickets/issue-47-script-contract-20260727/input/script_contract_work_order.json \
  --out-dir docs/proofs/tickets/issue-47-script-contract-20260727 \
  --active-goal-hash sha256:0000000000000000000000000000000000000000000000000000000000000047 \
  --github-target issue#47
```

Result: `command_loop_ok=true`, `command_loop_step_count=2`,
`terminal_agent=human`, `stop_reason=next_agent_is_human`,
`validate_script_contract_status=PASS_SCRIPT_CONTRACT`.

## Verification Commands

```bash
uv run pytest -q tests/test_persona_dream_dream_packet_agent.py
uv run ruff check src/tau_coding/persona_dream_dream_packet_agent.py tests/test_persona_dream_dream_packet_agent.py
uv run python -m py_compile src/tau_coding/persona_dream_dream_packet_agent.py
```

Results: `6 passed in 0.43s`, ruff `All checks passed!`, py_compile exit `0`.

## Key Artifacts

- `manifest.json`
- `command-loop/command-loop-receipt.json`
- `run/script_contract.json`
- `run/timed_transcript.json`
- `run/timed_beats.json`
- `run/entity_environment_script_table.json`
- `run/receipts/validate_script_contract.json`
- `run/script-reviewer-verdict.json`

## Evidence Limits

`mocked=false`, `live=true`, `provider_live=false`. This proves the local Tau
command-spec loop and artifact/receipt contract. It does not prove model-authored
screenplay quality or downstream media generation.
