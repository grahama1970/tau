# Tau Timeline Viewer WebGPT Review Bundle - 2026-08-13

## Objective

Review the Tau DAG/timeline viewer hardening state and recommend the next implementation and eval hardening steps for the `/tau` timeline viewer used by the `$ask` frontend and Tau agentic harness.

## Current Implementation Slice

- Repo: `/home/graham/workspace/experiments/tau`
- Commit under review: `fda703f9d Fix tau timeline pane collapse and clip geometry`
- Primary UI surface: `web/dag-viewer`
- Main changed source paths:
  - `web/dag-viewer/src/components/RunTimeline.tsx`
  - `web/dag-viewer/src/components/WorkspaceToggles.tsx`
  - `web/dag-viewer/src/styles.css`
  - `web/dag-viewer/src/tests/App.timeline.test.tsx`
  - `scripts/dag-viewer-browser-proof.mjs`
  - packaged static assets under `src/tau_coding/dag_viewer/static/`

## Local Evidence

- Browser proof JSON: `/tmp/tau-timeline-pane-label-r5/browser-proof.json`
- Browser proof screenshot: `/tmp/tau-timeline-pane-label-r5/browser-proof.png`
- CDP marker: `.codex/ui-verification/latest.json`
- CDP screenshot: `/tmp/codex-ui-verification/tau/tau-timeline-pane-label-r5/20260812T172521Z.png`
- Project-state JSON: `/tmp/tau-project-state-20260813.json`

Browser proof receipt summary:

```json
{
  "status": "PASS",
  "mocked": false,
  "live": true,
  "provider_live": false,
  "check_count": 40,
  "false_checks": []
}
```

Focused command evidence from the implementation slice:

- `npm --prefix web/dag-viewer test -- --run src/tests/App.timeline.test.tsx`
  - Result reported at implementation time: 10 tests passed.
- `npm --prefix web/dag-viewer run typecheck`
  - Result reported at implementation time: passed.
- `npm --prefix web/dag-viewer run build`
  - Result reported at implementation time: passed.

## Project-State Findings

`/tmp/tau-project-state-20260813.json` reports:

- `tests.total=3650`
- `tests.collected=true`
- `phase_1_infrastructure.frontend.exists=false`
- gap families:
  - critical security: 11 possible hardcoded secret findings
  - low documentation: 2 aspirational/TODO items

Important limitation: the generic project-state reporter did not detect the Vite app under `web/dag-viewer`, so it is repo-state context and not authoritative UI proof.

## What The Current Slice Proves

- Pane toggles are wired to actual layout collapse states.
- The timeline center canvas has independent horizontal scroll width.
- Role duration clips are not clipped by the previous nested scale-bar geometry.
- Sequence step zoom changes timeline width.
- Playhead keyboard scrubbing changes the active sequence.
- The live browser surface rendered without the checked overlap/clipping failures in the proof viewport.

## What It Does Not Prove

- It does not prove the full Tau immutable goal.
- It does not prove provider/model semantic quality.
- It does not prove robust behavior across multiple viewport families, long labels, large journals, or many orchestration runs.
- It does not prove that the generic project-state frontend detection is correct.
- It does not prove that the timeline is hardened by an agentic-evals suite.

## Agentic Evals Started

First manifest path:

- `evals/tau_timeline_viewer_agentic_eval.json`

Intended coverage:

- live browser proof over the existing DAG viewer runner;
- timeline layout and interaction unit regression;
- adversarial admission-vs-execution semantic separation;
- adversarial unique selector behavior for colliding node IDs.

## Questions For WebGPT

1. Are the current proof boundaries honest and sufficient for a pending hardening slice?
2. What additional UI failure modes should be covered before the timeline viewer is considered robust for the Tau agentic harness?
3. Should the timeline remain a DOM/CSS React implementation, or should any part of it move to canvas/Pixi-style rendering for scale? Give a recommendation based on Tau's current evidence-viewer needs, not generic UI preference.
4. What should the next `agentic-evals` cases be, beyond the first manifest?
5. Are there any signs that the implementation drifted from the user-requested video-editor timeline UX into a bespoke dashboard pattern?
6. Which local deterministic proof should be required after applying WebGPT's recommendations?

## Review Output Requested

Return:

- a prioritized finding list;
- clear acceptance criteria for the timeline hardening phase;
- specific eval cases to add;
- any design/architecture warning;
- a short verdict: `READY_FOR_EVAL_HARDENING`, `NEEDS_UI_REPAIR_FIRST`, or `NEEDS_ARCHITECTURE_REVIEW_FIRST`.

WebGPT review is advisory only. Closure still requires deterministic local receipts.
