# Issue 336 closure proof: Tau blocks fake visual verification

Ticket: https://github.com/grahama1970/tau/issues/336

## What changed

Tau no longer trusts reviewer JSON alone for visual verification.

A reviewer cannot pass visual work by only saying `verdict: PASS`, `represents_goal: true`, or `attractive: true`.

A reviewer also cannot pass by adding a screenshot path and hash directly in its own verdict. Tau now requires a separate path-backed `tau.visual_review_receipt.v1` and validates it by reading the receipt and PNG bytes from disk.

## What Tau checks

- The reviewer verdict has screenshot evidence.
- The screenshot exists and is a PNG.
- The screenshot `sha256:` matches the bytes on disk.
- The reviewer verdict declares `mocked` and `live`.
- A separate `visual_review_receipt` exists on disk.
- That receipt says `schema: tau.visual_review_receipt.v1`.
- That receipt says `status: PASS` and `verdict: PASS`.
- That receipt matches the same goal hash, reviewer node, reviewed node, screenshot path, and screenshot hash.
- That receipt declares an allowed verification method.

## Validation readback

- `uv run pytest --tau-suite=all tests/test_project_dag.py::test_project_dag_blocks_visual_reviewer_verdict_without_screenshot_evidence tests/test_project_dag.py::test_project_dag_blocks_visual_reviewer_verdict_with_screenshot_but_no_receipt tests/test_project_dag.py::test_project_dag_accepts_visual_reviewer_verdict_with_hash_bound_screenshot -q` -> `3 passed in 1.33s`.
- `TAU_LIVE_E2E_READBACK=1 uv run python scripts/agentic-eval-tau-visual-review-evidence.py --work local/agentic-evals/tau-visual-review-evidence/work --out local/agentic-evals/tau-visual-review-evidence-proof.json` -> `PASS BLOCKED reviewer_visual_evidence_missing BLOCKED visual_review_receipt_missing PASS`.
- `agentic-evals run evals/tau_visual_review_evidence_agentic_eval.json` -> `readiness=READY`, `PASS=2`, `trial_count=4`.
- `uv run pytest -q` -> `256 passed, 29 skipped, 3463 deselected in 26.11s`.
- `agentic-evals run evals/tau_feature_coverage_agentic_eval.json` -> `readiness=READY`, `PASS=7`, `trial_count=14`.

## Proof files

- `docs/proofs/tickets/issue-336-visual-review-receipt-enforcement-20260831T111946Z/closure-evidence.json`
- `local/agentic-evals/tau-visual-review-evidence-proof.json`
- `local/agentic-evals/tau-visual-review-evidence-agentic-evals-report.json`

## Non-claims

This does not prove human aesthetic acceptance, provider semantic quality, or every possible fake-verification class in Tau. It fixes the visual-review acceptance hole from issue #336.
