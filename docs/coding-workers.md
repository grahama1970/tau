# Coding Workers

Tau's coding layer is a containment layer, not a standalone coding-agent race.
Agents and external workers may propose code changes, but Tau decides what can
count by checking policy, hashes, receipts, review findings, and evidence.

## Current Tau-Native Primitives

### Hash-Bound Code Patches

`tau.code_patch.v1` describes one target file edit bound to:

- `goal_hash`
- `target_file`
- `base_file_sha256`
- `allowed_paths`
- anchors such as `content_hash`, `line_span`, or `symbol`
- deterministic patch operations
- `expected_post_sha256`

The first Tau-native patch language is deliberately narrow. The `patch` field
is a JSON array string of exact replacement operations:

```json
[
  {
    "op": "replace",
    "old": "return 41",
    "new": "return 42"
  }
]
```

`tau.code_patch_receipt.v1` blocks stale base hashes, missing anchors,
disallowed or generated paths, goal-hash mismatches, malformed patch operations,
and post-hash mismatches. Passing the receipt proves only that the deterministic
patch gate ran and the before/after hashes matched. It does not prove semantic
correctness, test success, production safety, or agent truthfulness.

CLI:

```bash
uv run tau code-patch \
  --patch patch.json \
  --repo . \
  --out code-patch-receipt.json \
  --goal-hash sha256:...
```

Use `--zero-trust --policy-profile policy.json --data-boundary boundary.json`
when the patch is part of a zero-trust or high-stakes DAG. Use `--dry-run` to
write the receipt without applying the staged content.

### Structured Review Findings

`tau.review_findings.v1` turns reviewer output into machine-actionable findings:

```json
{
  "schema": "tau.review_findings.v1",
  "goal_hash": "sha256:...",
  "reviewer": "reviewer",
  "verdict": "PASS|REVISE|BLOCKED",
  "findings": []
}
```

Routing rules:

- `P0` or `required_action:block` derives `BLOCKED`.
- `P1`/`P2` or `required_action:revise` derives `REVISE`.
- no blocking or revision findings derives `PASS`.

P0/P1 findings require evidence. Tau blocks understated verdicts such as a
declared `PASS` with P1/P2 findings or a declared `REVISE` with P0 findings.
The receipt does not prove the reviewer is correct or exhaustive.

CLI:

```bash
uv run tau review-findings \
  --findings review-findings.json \
  --out review-findings-receipt.json \
  --goal-hash sha256:...
```

### Coding Course Correction

`tau.course_correction.v1` now includes coding failure triggers:

- `patch_stale`
- `patch_failed`
- `lsp_diagnostics_regressed`
- `reviewer_p0`
- `reviewer_p1`
- `test_failed_twice`
- `debugger_evidence_required`
- `worker_result_missing`
- `worker_changed_forbidden_path`

These triggers route coding failures away from blind retry and toward bounded
next actions such as fresh patch receipts, structured review, debugger evidence,
quarantine, goal-guardian review, or human review.

## Intended Worker Adapters

External coding workers remain untrusted. Tau should wrap them with work orders,
allowed/forbidden paths, goal hashes, policy/data-boundary metadata, and required
result artifacts.

Planned adapters:

- `tau.executor.omp.v1`
- `tau.omp_worker_receipt.v1`
- `tau.executor.scillm_worker.v1`

These adapters should reject prose-only results, goal-hash drift, disallowed
file changes, missing test logs, public GitHub mutation without policy receipts,
and external research without sanitized-query authorization.

## Non-Claims

Tau does not claim:

- agents are trustworthy
- reviewer agents decide truth
- a passing patch receipt proves semantic correctness
- LSP or tests prove full safety
- worker adapters make OMP or SciLLM trusted
- policy/data-boundary gates are legal compliance

The design goal is narrower: make coding-agent output bounded, inspectable,
rejectable, and course-correctable.
