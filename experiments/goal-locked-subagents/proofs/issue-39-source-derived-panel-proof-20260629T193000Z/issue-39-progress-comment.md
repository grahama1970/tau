## Tau issue #39 progress and remaining blocker

Tau processed this ticket through the live self-fix queue, then patched the persona-dream panel proof path.

Evidence:

```text
commit: 49be7a0
self_fix_first_attempt: experiments/goal-locked-subagents/proofs/self-fix-cron-issue39-20260629T192612Z/receipts/20260629T192612Z/self-fix-poll-receipt.json
self_fix_retry: experiments/goal-locked-subagents/proofs/self-fix-cron-issue39-20260629T192659Z/receipts/20260629T192659Z/self-fix-poll-receipt.json
source_panel_proof: experiments/goal-locked-subagents/proofs/issue-39-source-derived-panel-proof-20260629T193000Z/run/manifest.json
panel_repair_gate: experiments/goal-locked-subagents/proofs/issue-39-source-derived-panel-proof-20260629T193000Z/run/receipts/panel_repair_gate_receipt.json
panel_source: experiments/goal-locked-subagents/proofs/issue-39-source-derived-panel-proof-20260629T193000Z/run/receipts/panel_source_receipt.json
```

What changed:

```text
tau persona-dream-panel-proof now accepts --panel-source.
Panel prompts can derive from source action/entities/props/environment/motion cues.
script_coverage_receipt and post_generation_script_coverage_receipt are no longer hardcoded FAIL when source coverage evidence exists.
provider_media_probe_receipt can be consumed into the repair gate.
panel_repair_gate_receipt can emit PASS_PANEL_REVIEWED only when script, post-generation, reference, visual, no-overlay, and provider-media subgates pass.
```

Deterministic checks:

```text
PYTHONPATH=src uv run pytest tests/test_cli.py -q -k 'persona_dream_panel' -> 7 passed
PYTHONPATH=src uv run pytest tests/test_tau_cron.py tests/test_self_fix_poll.py tests/test_self_fix_repair_loop.py -q -> 9 passed
PYTHONPATH=src uv run python -m py_compile src/tau_coding/persona_dream_panel_agent.py src/tau_coding/persona_dream_panel_proof.py src/tau_coding/cli.py tests/test_cli.py -> exit 0
python /home/graham/workspace/experiments/agent-skills/skills/persona-dream/scripts/validate_panel_repair_gate.py --require-provider-eligible experiments/goal-locked-subagents/proofs/issue-39-source-derived-panel-proof-20260629T193000Z/run/receipts/panel_repair_gate_receipt.json -> status PASS
python /home/graham/workspace/experiments/agent-skills/skills/persona-dream/scripts/validate_panel_source_receipt.py experiments/goal-locked-subagents/proofs/issue-39-source-derived-panel-proof-20260629T193000Z/run/receipts/panel_source_receipt.json --run-root experiments/goal-locked-subagents/proofs/issue-39-source-derived-panel-proof-20260629T193000Z/run --json -> status PASS_PANEL_SOURCE
```

Not closing this ticket yet:

```text
mocked: false
live: true
remaining blocker: source_panel_proof manifest has scillm_originated_inside_tau=false.
reason: this proof consumed the existing issue-33 Tau Scillm image generation receipt and public provider probe instead of initiating a new Scillm image generation inside this specific rerun.
```

Closure requires a follow-up live run where `tau persona-dream-panel-proof --scillm-live-panel --panel-source ...` initiates the image generation inside Tau and then repeats the provider-publication/probe and persona-dream validation gates.
