# Consolidated Tau Timeline Viewer Review Bundle

## Request

Review the Tau DAG/timeline viewer hardening state. Focus on:

- proof-boundary honesty;
- whether the UX still matches a video-editor-style agentic timeline rather than a bespoke dashboard;
- what `agentic-evals` cases should harden the viewer next;
- whether the next step should be eval hardening, UI repair, or proof/provenance architecture repair.

Return a verdict:

- `READY_FOR_EVAL_HARDENING`
- `NEEDS_UI_REPAIR_FIRST`
- `NEEDS_ARCHITECTURE_REVIEW_FIRST`

## First WebGPT Attempt

The first `$ask webgpt` attempt reached Surf and ChatGPT, but Tau marked the run `BLOCKED` with verdict `BROWSER_ATTACHMENT_ARGUMENT_CONTRACT_FAILED`.

Surf recovered an advisory WebGPT response from tab `837389256`:

- response: `.ask_artifacts/tau-dag-runs/ask-tau-review-the-attached-tau-timeline-cfb5d61b4980/node-artifacts/handler-webgpt/response.recovered.md`
- meta: `.ask_artifacts/tau-dag-runs/ask-tau-review-the-attached-tau-timeline-cfb5d61b4980/node-artifacts/handler-webgpt/response.recovered.meta.json`
- recovered transport facts: `controlled_tab_id=837389256`, `raw_contains_sentinel=true`, `clean_contains_sentinel=false`, `response_source=assistant-dom`

That first response correctly warned that it did not receive the intended browser JSON, PNG, and review bundle. This retry is a single-attachment bundle shaped to satisfy WebGPT attachment limits.

## Local Proof-Bundle Manifest Summary

Manifest path in repo:

`docs/review-bundles/tau-timeline-proof-bundle-20260813/manifest.json`

Manifest schema:

`tau.viewer.proof_bundle.v1`

Manifest status:

`INCOMPLETE`

Reason:

The first WebGPT attachment transport failed and the agentic-evals manifest has not yet been executed. This bundle is review input, not closure proof.

## Repository And Viewer Identity

- Project: `tau`
- Project root: `/home/graham/workspace/experiments/tau`
- Commit under review: `fda703f9deb94c8f0361208008368a0154cacf8e`
- Short commit: `fda703f9d`
- Branch: `main`
- Primary viewer source root: `web/dag-viewer`
- Main viewer source paths:
  - `web/dag-viewer/src/components/RunTimeline.tsx`
  - `web/dag-viewer/src/components/WorkspaceToggles.tsx`
  - `web/dag-viewer/src/components/runTimelineModel.ts`
  - `web/dag-viewer/src/components/runTimelineSwimlanes.ts`
  - `web/dag-viewer/src/tests/App.timeline.test.tsx`
  - `scripts/run-dag-viewer-browser-proof.py`
  - `scripts/dag-viewer-browser-proof.mjs`

## Local Browser Proof Summary

The attached `browser-proof.json` reports:

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

The attached `browser-proof.png` is the browser proof screenshot. The attached `cdp-screenshot.png` is the latest CDP verification screenshot from `.codex/ui-verification/latest.json`.

Important boundary:

This browser proof is a bounded local UI proof for the timeline/pane/geometry slice. It is not full Tau immutable-goal closure and not provider/model proof.

## Focused Checks Reported By Prior Implementation Slice

- `npm --prefix web/dag-viewer test -- --run src/tests/App.timeline.test.tsx`
  - reported result: 10 tests passed
- `npm --prefix web/dag-viewer run typecheck`
  - reported result: passed
- `npm --prefix web/dag-viewer run build`
  - reported result: passed

These were reported from the prior implementation slice; this retry bundle itself does not rerun them.

## Project-State Summary

The attached `project-state.json` reports:

- `tests.total=3650`
- `tests.collected=true`
- `phase_1_infrastructure.frontend.exists=false`
- gap families:
  - critical security: 11 possible hardcoded secret findings
  - low documentation: 2 aspirational/TODO findings

Important limitation:

The generic project-state reporter did not detect the Vite app under `web/dag-viewer`, so it is generic repo-state context and not authoritative UI proof. Do not treat `tests.total=3650` as tests passed.

## First Agentic-Evals Manifest

Manifest path:

`evals/tau_timeline_viewer_agentic_eval.json`

Validated locally through the `agentic-evals` v2 loader:

- cases: 4
- trials: 2
- case names:
  - `live-browser-timeline-layout-proof`
  - `timeline-layout-and-interactions-unit-regression`
  - `adversarial-admission-execution-separation`
  - `adversarial-colliding-node-selector-uniqueness`

Manifest claims:

- proves: declared Tau timeline viewer commands meet explicit local expectations across repeated trials, including one live browser proof path;
- does not prove: full Tau immutable-goal completion, provider/model quality, all viewport sizes, all ledger shapes, or production release readiness.

The manifest has not been executed yet.

## Current Local Changes Since Commit

The current working tree has review/hardening artifacts only:

- `PROJECT_KNOWLEDGE.md` updated with timeline status and project-state limitation;
- `docs/reviews/tau-timeline-webgpt-review-20260813.md` added;
- `evals/tau_timeline_viewer_agentic_eval.json` added;
- `docs/review-bundles/tau-timeline-proof-bundle-20260813/manifest.json` added;
- `docs/review-bundles/tau-timeline-proof-bundle-20260813/webgpt-retry-request.md` added;
- this consolidated review bundle added.

Production UI code has not been changed in this hardening/review step.

## Questions

1. Given the attached screenshot and browser proof JSON, is `NEEDS_ARCHITECTURE_REVIEW_FIRST` still the right verdict, or is the project now `READY_FOR_EVAL_HARDENING`?
2. Are there visible signs of dashboard drift in the screenshot, or does it still read as a video-editor-style timeline viewer?
3. What are the highest-priority missing eval cases?
4. Should the next local implementation focus on a formal `tau.viewer.proof_bundle.v1` validator, more UI geometry checks, or actual UI repair?
5. What deterministic local proof should be required after the next change?
