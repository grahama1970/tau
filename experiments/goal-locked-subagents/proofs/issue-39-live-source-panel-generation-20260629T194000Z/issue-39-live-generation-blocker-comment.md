## Tau issue #39 live source-panel generation result

Tau ran the stronger live source-panel rung after the source-derived receipt patch.

Evidence:

```text
live_generation_manifest: experiments/goal-locked-subagents/proofs/issue-39-live-source-panel-generation-20260629T194000Z/manifest.json
live_image_receipt: experiments/goal-locked-subagents/proofs/issue-39-live-source-panel-generation-20260629T194000Z/command-loop/command-artifacts/command-loop-step-001/scillm_image_generation_receipt.json
live_vlm_receipt: experiments/goal-locked-subagents/proofs/issue-39-live-source-panel-generation-20260629T194000Z/command-loop/command-artifacts/command-loop-step-002/visual_review_receipt.json
hash_guard_rerun: experiments/goal-locked-subagents/proofs/issue-39-live-source-panel-generation-20260629T194000Z/hash-guard-rerun/receipts/panel_repair_gate_receipt.json
```

What the live rung exercised:

```text
mocked: false
live: true
scillm_originated_inside_tau: true
source-derived prompt: yes
Scillm image generation: ok=true, stream=true, heartbeat_event_count=14, wrapper_event_count=63
generated image sha256: sha256:00833ecfe90e5953249a15cab3a34d3d1bd7b3705da6caeb18f314509df786d0
VLM review: status=PASS, http_status=200, stream=true, stream_event_count=140
```

Important repair made after this run:

```text
Tau now refuses provider_media_status=PASS unless the public probe expected_sha256 and observed_sha256 match the current generated image hash.
```

Current blocker:

```text
hash_guard_rerun status: BLOCKED_PROVIDER_MEDIA_URLS
script_coverage_status: PASS
post_generation_script_coverage_status: PASS
provider_media_status: FAIL
provider_eligibility: false
panel_source_receipt.status: BLOCKED
```

The ticket should remain open. Closure still requires publishing the newly generated image to a provider-accessible URL, probing that URL with expected/observed SHA `sha256:00833ecfe90e5953249a15cab3a34d3d1bd7b3705da6caeb18f314509df786d0`, and rerunning the provider-eligible repair-gate and panel-source validators.
