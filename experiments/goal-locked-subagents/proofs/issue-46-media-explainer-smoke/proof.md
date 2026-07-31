# Issue #46 Proof: Async Linked-Asset Media Explanation Orchestration

Issue: https://github.com/grahama1970/tau/issues/46

## Scope

Implemented a Tau orchestration/dispatch contract for linked media explanation
work items. This slice does not implement Dream UI, persona memory migrations,
or live media-explainer internals.

## Changed Files

- `src/tau_coding/media_explainer_orchestration.py`
- `src/tau_coding/cli.py`
- `tests/test_media_explainer_orchestration.py`
- `experiments/goal-locked-subagents/proofs/issue-46-media-explainer-smoke/required-failure-work-item.json`
- `PROJECT_KNOWLEDGE.md`

## Deterministic Checks

```bash
uv run ruff check --select I,F \
  src/tau_coding/media_explainer_orchestration.py \
  src/tau_coding/cli.py \
  tests/test_media_explainer_orchestration.py
```

Result: `All checks passed!`

```bash
uv run pytest tests/test_media_explainer_orchestration.py -q
```

Result: `3 passed`

## Smoke Artifacts

Mixed asset smoke:

```bash
uv run tau media-explainer-smoke \
  --label issue-46-media-explainer-smoke \
  --run-root experiments/goal-locked-subagents/proofs/issue-46-media-explainer-smoke
```

Receipt:

```text
experiments/goal-locked-subagents/proofs/issue-46-media-explainer-smoke/20260701T175318Z-issue-46-media-explainer-smoke/run-receipt.json
```

Observed:

- `mocked:true`
- `live:false`
- `provider_live:false`
- `asset_count:5`
- `status_counts:{READY:4,FAILED:1}`
- media routes: image -> `vlm_description`, video -> `watch_keyframe_description`, audio -> `audio_caption_service`, text -> `text_summarizer`
- `completion_order:["optional-audio-broken","text-brief","audio-note","video-loop","image-hero"]`
- `completion_order_differs_from_manifest:true`
- `step02_gate.status:"READY"`
- `memory_policy.mocked_descriptions_persisted_as_live_truth:false`

Required failure smoke:

```bash
uv run tau media-explainer-smoke \
  --label issue-46-media-explainer-required-failure \
  --run-root experiments/goal-locked-subagents/proofs/issue-46-media-explainer-smoke \
  --work-item experiments/goal-locked-subagents/proofs/issue-46-media-explainer-smoke/required-failure-work-item.json
```

Receipt:

```text
experiments/goal-locked-subagents/proofs/issue-46-media-explainer-smoke/20260701T175337Z-issue-46-media-explainer-required-failure/run-receipt.json
```

Observed:

- `mocked:true`
- `live:false`
- `provider_live:false`
- `status_counts:{READY:1,FAILED:1}`
- `step02_gate.status:"BLOCKED"`
- `step02_gate.failed_required_asset_ids:["image-required"]`
- per-asset `memory_persistence.status:"SKIPPED_PLACEHOLDER"`

## Mocked/Live Boundary

- mocked: yes
- live: no
- actually exercised: Tau work-item validation, async per-asset dispatch via
  `asyncio.as_completed`, per-asset receipt persistence, event stream, failure
  isolation, Step 02 gate computation, and no-live-memory-truth policy.
- remains unverified: live VLM/video/audio/text provider output, live Memory
  writes, Dream UI integration, persona memory schema migration, and
  media-explainer internals.
