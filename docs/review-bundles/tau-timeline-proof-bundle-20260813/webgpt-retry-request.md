# WebGPT Retry Request: Tau Timeline Viewer Proof Bundle

Review the attached single zip bundle as the source of truth.

The previous `$ask webgpt` attempt reached ChatGPT, but Tau marked the run `BLOCKED` with verdict `BROWSER_ATTACHMENT_ARGUMENT_CONTRACT_FAILED`. Surf then recovered an advisory response from the controlled tab. That response correctly warned that WebGPT did not receive the actual browser JSON/PNG/review bundle. This retry fixes the packet shape by providing one archive containing the review request, browser proof JSON, screenshots, project-state JSON, and manifest.

Return:

- prioritized findings;
- acceptance criteria for the Tau timeline viewer hardening phase;
- additional agentic-evals cases to add;
- design or architecture warnings;
- final verdict: `READY_FOR_EVAL_HARDENING`, `NEEDS_UI_REPAIR_FIRST`, or `NEEDS_ARCHITECTURE_REVIEW_FIRST`.

Important boundaries:

- WebGPT review is advisory only.
- Local deterministic proof remains required for any closure claim.
- The proof-bundle manifest status is `INCOMPLETE` because the first WebGPT attachment transport failed and the eval manifest has not yet been executed.
- Do not treat repository test collection count as passing tests.
- Do not treat the generic project-state frontend detection as authoritative, because it reports `frontend.exists=false` while the actual viewer source is under `web/dag-viewer`.
