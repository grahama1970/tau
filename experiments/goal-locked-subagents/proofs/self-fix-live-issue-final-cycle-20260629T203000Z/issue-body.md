## Type

bug

## Target

`src/tau_coding/self_fix_ticket_repair.py`

## Current State

`_write_proof_markdown()` always reads `loop_receipt["cycles"][0]` for the coder and reviewer Scillm receipt paths. In a bounded repair loop with multiple review cycles, the proof comment would cite the first cycle instead of the terminal/final reviewer cycle.

## Expected State

The proof writer should cite the final cycle from the coder/reviewer loop receipt.

## Route

`route:backend_python_or_skill_runtime`

## Requested Repair Agent

`agent:coder`

## Required Proof

- Tau self-fix poll selects this real GitHub issue.
- Memory `/intent` and `/recall` run before the repair loop.
- Coder and reviewer Scillm calls are streaming and live.
- The scoped source change is committed and pushed by Tau.
- The issue is closed only with a deterministic proof comment.

## Tau Repair Contract

```json
{
  "schema": "tau.self_fix_repair_request.v1",
  "request": "Change self-fix ticket proof markdown to cite the final coder/reviewer cycle instead of cycle zero.",
  "target_file": "src/tau_coding/self_fix_ticket_repair.py",
  "find_text": "    coder = loop_receipt[\"cycles\"][0][\"coder\"][\"scillm_call\"]\n    reviewer = loop_receipt[\"cycles\"][0][\"reviewer\"][\"scillm_call\"]",
  "replace_text": "    final_cycle = loop_receipt[\"cycles\"][-1]\n    coder = final_cycle[\"coder\"][\"scillm_call\"]\n    reviewer = final_cycle[\"reviewer\"][\"scillm_call\"]",
  "verification_commands": [
    "PYTHONPATH=src uv run python - <<'PY'\nfrom pathlib import Path\ntext = Path('src/tau_coding/self_fix_ticket_repair.py').read_text(encoding='utf-8')\nassert 'final_cycle = loop_receipt[\"cycles\"][-1]' in text\nassert 'loop_receipt[\"cycles\"][0]' not in text\nPY",
    "PYTHONPATH=src uv run pytest tests/test_self_fix_ticket_repair.py tests/test_self_fix_repair_loop.py tests/test_self_fix_poll.py tests/test_tau_cron.py -q"
  ],
  "max_review_cycles": 1,
  "commit_message": "Use final self-fix cycle in proof comments"
}
```

## Non-goals

- Do not broaden the repair request contract.
- Do not change Scillm streaming behavior.
- Do not change GitHub ticket helper semantics.
