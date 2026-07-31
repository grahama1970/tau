# Issue #45 Blocker Proof

Ticket: https://github.com/grahama1970/tau/issues/45

## Status

Tau now consumes `persona_dream.panel_repair_work_order.v1` through the
panel-creator -> panel-reviewer -> persona-dream-panel-repair-gate command loop.

The ticket is not closable because the required forward pipeline status still
blocks at `panel_repair_gate` on provider-media URL/probe eligibility.

## Mocked / Live

- mocked: no
- live: yes for the Tau command loop, Scillm image generation wrapper, and Scillm
  VLM reviewer receipt
- live: no for public provider-media hosting/probe
- Kling call: no
- paid provider call: no
- public upload: no

## Artifacts

- Tau proof root:
  `experiments/goal-locked-subagents/proofs/issue-45-persona-dream-panel-repair-gate-loop-20260629T221536Z`
- Command-loop receipt:
  `experiments/goal-locked-subagents/proofs/issue-45-persona-dream-panel-repair-gate-loop-20260629T221536Z/command-loop/command-loop-receipt.json`
- Image generation receipt:
  `experiments/goal-locked-subagents/proofs/issue-45-persona-dream-panel-repair-gate-loop-20260629T221536Z/command-loop/command-artifacts/command-loop-step-001/scillm_image_generation_receipt.json`
- Reviewer source receipt:
  `experiments/goal-locked-subagents/proofs/issue-45-persona-dream-panel-repair-gate-loop-20260629T221536Z/command-loop/command-artifacts/command-loop-step-002/visual_review_receipt.json`
- Canonical run-root panel repair gate:
  `experiments/goal-locked-subagents/proofs/issue-41-persona-dream-dream-packet-loop-20260629T204320Z/dream-run/receipts/panel_repair_gate_receipt.json`
- Canonical run-root visual-review adapter receipt:
  `experiments/goal-locked-subagents/proofs/issue-41-persona-dream-dream-packet-loop-20260629T204320Z/dream-run/receipts/visual_review_receipt.json`
- Forward pipeline status:
  `experiments/goal-locked-subagents/proofs/issue-45-persona-dream-panel-repair-gate-loop-20260629T221536Z/pipeline-loop-status-forward.json`

## Commands

```bash
PYTHONPATH=src uv run python -m pytest \
  tests/test_cli.py::test_persona_dream_panel_context_accepts_panel_repair_work_order \
  tests/test_cli.py::test_persona_dream_visual_review_adapter_adds_run_root_gate_fields \
  tests/test_cli.py::test_cli_persona_dream_panel_proof_uses_supplied_panel_evidence -q
```

Result: `3 passed`.

```bash
/home/graham/workspace/experiments/agent-skills/skills/persona-dream/run.sh \
  validate-panel-repair-work-order \
  experiments/goal-locked-subagents/proofs/issue-41-persona-dream-dream-packet-loop-20260629T204320Z/dream-run/receipts/panel_repair_work_order.json \
  --json
```

Result: `PASS_PANEL_REPAIR_WORK_ORDER`.

```bash
/home/graham/workspace/experiments/agent-skills/skills/persona-dream/run.sh \
  validate-panel-repair-gate \
  experiments/goal-locked-subagents/proofs/issue-41-persona-dream-dream-packet-loop-20260629T204320Z/dream-run/receipts/panel_repair_gate_receipt.json
```

Result: `PASS`.

```bash
/home/graham/workspace/experiments/agent-skills/skills/persona-dream/run.sh \
  pipeline-loop-status \
  experiments/goal-locked-subagents/proofs/issue-41-persona-dream-dream-packet-loop-20260629T204320Z/dream-run \
  --direction forward --json
```

Result: exit `1`, status `BLOCKED`, active loop `panel_repair_gate`.

Blocker:

```text
provider_media_probe_receipt: provider media probe receipt status must be PASS_PROVIDER_MEDIA_URL_PROBE;
provider_media_probe_receipt: provider media probe URL is not listed in provider_media_urls;
provider_media_probe_receipt: provider media probe expected_sha256 is not listed in media_hashes;
provider_media_probe_receipt: provider media probe http_status must be 200;
provider_media_probe_receipt: provider media probe must be a live public HTTP(S) fetch;
--require-provider-eligible requires provider_eligibility=true;
receipt is not provider eligible
```

## Stop Condition

Do not close #45 until either:

1. a human explicitly authorizes publication/probing of the exact generated
   panel image as provider media, or
2. the ticket acceptance criteria are changed so provider media eligibility is
   not required for `pipeline-loop-status --direction forward` to advance past
   `panel_repair_gate`.
