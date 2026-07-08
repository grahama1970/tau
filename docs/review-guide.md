# Tau External Review Guide

## What This Review Is

A synthetic review of Tau as the Embry-OS / Sparta Explorer agentic harness.

Tau gates local agent/model work with policy profiles, data boundaries, typed receipts, evidence manifests, proof indexes, posture contracts, and human approval boundaries.

## What This Review Is Not

This is not ITAR certification, ATO readiness, SCIF readiness, real controlled-data processing, or production approval.

## Quickstart

```bash
git clone https://github.com/grahama1970/tau.git
cd tau
uv sync
uv run tau demo airgap-itar-basic --out /tmp/tau-review-demo
uv run tau run-status /tmp/tau-review-demo
```

## What To Inspect

- `/tmp/tau-review-demo/policy-profile.json`
- `/tmp/tau-review-demo/data-boundary.json`
- `/tmp/tau-review-demo/local-provider-readiness-receipt.json`
- `/tmp/tau-review-demo/airgap-no-egress-receipt.json`
- `/tmp/tau-review-demo/itar-contract-receipt.json`
- `/tmp/tau-review-demo/sparta-posture-contract.json`
- `/tmp/tau-review-demo/proof-index.jsonl`

## Expected Result

The demo harness should return `PASS`, but Sparta posture should remain `NOT_SIGNOFF_READY`.

Expected blocker:

```text
human_export_control_review_required
```

## Reviewer Questions

1. Is the harness boundary clear?
2. Are the non-claims credible?
3. Are the receipts useful for review?
4. What evidence would your organization need before trusting this workflow?
5. What would block air-gapped deployment?
6. What compliance or legal signoff must remain human-only?

