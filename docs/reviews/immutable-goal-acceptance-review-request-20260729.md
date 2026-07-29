# Tau Immutable Goal Acceptance Review Request

Review target: Tau current main `58507c63c343f0a78ba500e55d8d5f8433691195`.

Question: does the supplied evidence establish Tau's immutable goal enough for
beta use, and what remains outstanding before a human may accept it?

## Source Of Truth

- Goal: `GOAL.md`
- README product target: `README.md`
- Acceptance readback JSON:
  `docs/status/immutable-goal-acceptance-20260729T2005Z/acceptance-readback.json`
- Acceptance readback Markdown:
  `docs/status/immutable-goal-acceptance-20260729T2005Z/acceptance-readback.md`
- Generator:
  `scripts/generate-immutable-goal-acceptance-readback.py`
- Verifier:
  `scripts/verify-immutable-goal-acceptance-readback.py`

## Proof Commands Already Run

```bash
uv run python scripts/generate-immutable-goal-acceptance-readback.py \
  --audit /tmp/tau-issue-258-main-ci-artifacts-20260729T2000Z/tau-canonical-proofs-30485773678/audit/immutable-goal-audit.json \
  --artifact-manifest /tmp/tau-issue-258-main-ci-artifacts-20260729T2000Z/tau-canonical-proofs-30485773678/artifact-manifest.json \
  --project-state /tmp/tau-issue-258-context-20260729T1928Z/project-state.json \
  --source-ref 58507c63c343f0a78ba500e55d8d5f8433691195 \
  --out-dir docs/status/immutable-goal-acceptance-20260729T2005Z
```

Result:

- `status`: `HUMAN_ACCEPTANCE_REQUIRED`
- `immutable_goal_status`: `NOT_MET`
- checked artifacts: 37
- proof receipts: 10
- screenshots: 16
- remaining unmet criterion: criterion 10, human acceptance missing

```bash
uv run python scripts/verify-immutable-goal-acceptance-readback.py \
  docs/status/immutable-goal-acceptance-20260729T2005Z/acceptance-readback.json
```

Result: `status=PASS`, `immutable_goal_status=NOT_MET`, proof count 10,
screenshot count 16.

Current GitHub evidence:

- Canonical Tau proofs run `30485773678`: success for head
  `58507c63c343f0a78ba500e55d8d5f8433691195`
- Tests run `30485773584`: success for head
  `58507c63c343f0a78ba500e55d8d5f8433691195`

## Review Instructions

Review only the supplied source files and artifacts. Do not treat model prose,
README narrative, issue comments, CI names, or a PASS field as proof without
checking the referenced receipt/hash/path.

Return:

- `PASS_FOR_BETA`, `NEEDS_REPAIR`, or `INSUFFICIENT_EVIDENCE`
- A criterion-by-criterion judgment against `GOAL.md`
- Any missing artifacts, hash gaps, stale-source risks, or mocked-only evidence
- Whether the readback correctly refuses to claim immutable-goal achievement
  before human acceptance
- Tightly scoped follow-up tickets if defects remain

## Known Boundary

The automated evidence deliberately does not record human acceptance. If all
technical criteria look established, the appropriate conclusion is not
`ACHIEVED`; it is that Tau is technically reviewable and still awaiting the
human's explicit acceptance of criterion 10.
