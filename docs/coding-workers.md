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

### LSP-Style Diagnostics And Rename Planning

`tau.lsp_diagnostics_receipt.v1` records local diagnostics evidence for a
workspace. Tau uses Ruff when available and falls back to Python AST parsing for
syntax evidence. The receipt records the adapter used, inspected files,
diagnostics, severity counts, and whether the adapter was available.

CLI:

```bash
uv run tau lsp-diagnostics --workspace . --out lsp-diagnostics-receipt.json
```

Use `--zero-trust --policy-profile policy.json --data-boundary boundary.json`
when LSP evidence is part of a high-stakes coding route. In zero-trust mode,
Tau blocks diagnostics, symbol, and rename-plan receipts that omit the policy
profile or data boundary.

`tau.lsp_symbol_receipt.v1` and `tau.lsp_rename_receipt.v1` provide read-only
symbol lookup and rename planning. Rename planning does not apply edits by
default; it records references and planned edits as evidence for review.

CLI:

```bash
uv run tau lsp-symbols --workspace . --query Example --out lsp-symbols.json
uv run tau lsp-rename-plan \
  --workspace . \
  --symbol Example \
  --new-name BetterExample \
  --out lsp-rename-plan.json
```

These receipts do not prove semantic correctness, complete language-server
parity, or that a rename is safe to apply.

### Atomic Commit Planning

`tau.commit_plan_receipt.v1` inspects a Git working tree and proposes dry-run
commit groups for source, tests, docs, and lockfiles. It records changed files,
dependency order, risk level, required evidence per group, lockfile handling,
and approval requirements.

CLI:

```bash
uv run tau commit-plan --repo . --out commit-plan-receipt.json
```

Use `--zero-trust --policy-profile policy.json --data-boundary boundary.json`
when planning commits for a high-stakes coding route. In zero-trust mode, Tau
blocks commit plans that omit policy or boundary metadata.

The command is dry-run by default. `--apply` is intentionally blocked unless a
future approval lane authorizes commit application. High-risk paths such as
`.github/`, `secrets/`, `.env`, `pyproject.toml`, `uv.lock`, and
`package-lock.json` are flagged for approval.

### Debugger Evidence

`tau.debug_session_receipt.v1` records debugger/DAP evidence from a structured
local session packet. Supported adapter labels are `debugpy`, `lldb-dap`, `dlv`,
and `node`. The receipt records the target, adapter availability, breakpoints,
stopped frame, variables, commands, stdout/stderr artifacts, conclusion, and
non-claims.

CLI:

```bash
uv run tau debug-session-receipt \
  --session debug-session.json \
  --out debug-session-receipt.json
```

Use `--zero-trust --policy-profile policy.json --data-boundary boundary.json`
when debugger evidence is part of a high-stakes coding route. In zero-trust
mode, Tau blocks debug receipts that omit policy or boundary metadata.

Use `--required` when a missing adapter must block the coding route. The receipt
does not prove the bug is fixed, the debug conclusion is complete, or the code
is correct.

### GitHub Read Schemes

`tau.github_read_receipt.v1` turns URI-style GitHub references into read-only
inspection receipts:

```text
issue://owner/repo/123
pr://owner/repo/456
diff://owner/repo/pull/456
commit://owner/repo/<sha>
```

CLI:

```bash
uv run tau github-read \
  --uri issue://grahama1970/tau/67 \
  --out github-read-receipt.json
```

Use `--zero-trust --policy-profile policy.json --data-boundary boundary.json`
when GitHub read projections are part of a high-stakes coding route. In
zero-trust mode, Tau blocks read receipts that omit policy or boundary
metadata.

The receipt records the parsed target, a suggested `gh` read command, blocked
mutation verbs, and `mutation_allowed:false`. It does not call GitHub, prove
live auth, prove the target object exists, or prove content freshness.

GitHub mutation remains separate. Commenting, labeling, closing, merging,
pushing, and releasing must go through `tau.github_apply_policy_receipt.v1`
with the existing repo allowlist, preflight, redaction, and approval gates.

### Orchestration Reliability

`tau.orchestration_reliability_receipt.v1` summarizes whether a DAG run obeyed
the harness rules separately from whether the code is correct. It reports:

- goal-hash continuity
- DAG route discipline
- unexpected nodes and edges
- required receipt and evidence presence
- course-correction emission and handling
- retry budget discipline
- terminal condition validity
- `agent_truthfulness: NOT_CLAIMED`

CLI:

```bash
uv run tau orchestration-reliability \
  --dag-receipt run/dag-receipt.json \
  --out orchestration-reliability.json
```

Existing run-directory usage remains supported:

```bash
uv run tau orchestration-reliability --run-dir run --out orchestration-reliability.json
```

This receipt does not prove code correctness, agent truthfulness, provider/model
quality, GitHub mutation, or human acceptance.

## Intended Worker Adapters

External coding workers remain untrusted. Tau should wrap them with work orders,
allowed/forbidden paths, goal hashes, policy/data-boundary metadata, and required
result artifacts.

Current validation adapters:

- `tau.executor.omp.v1` work orders validated into `tau.omp_worker_receipt.v1`
- `tau.executor.scillm_worker.v1` work orders validated into
  `tau.scillm_worker_receipt.v1`

These adapters reject missing results, invalid schemas, prose-only results,
goal-hash drift, disallowed file changes, missing required artifacts, PASS test
claims without durable logs, public GitHub mutation without policy receipts, and
external research without research-query/source receipts. High-stakes work
orders must name an allowed execution substrate such as Herdr-visible execution
or a sandbox, and must carry `policy_profile` plus `data_boundary` metadata
before Tau accepts the worker result. Sandbox substrates must include an
existing `sandbox_receipt_path`; Herdr substrates must include `herdr_binding`
or `herdr_receipt_path`.

CLI:

```bash
uv run tau omp-worker-validate \
  --work-order omp-work-order.json \
  --result omp-result.json \
  --out omp-worker-receipt.json

uv run tau scillm-worker-validate \
  --work-order scillm-work-order.json \
  --result scillm-result.json \
  --out scillm-worker-receipt.json
```

For SciLLM coding delegates, Tau should use the OpenCode serve surface
(`/v1/scillm/opencode/runs`) with an agent profile such as `build` or
`scillm-debugger`, not chat completions or raw OpenCode ports. The current Tau
receipt validates the declared model/provider route; it does not prove Tau
launched the worker.

Copyable examples:

```bash
examples/coding-reliability-basic/run.sh /tmp/tau-coding-reliability-basic
examples/omp-worker/run.sh /tmp/tau-omp-worker-example
```

`examples/omp-worker` validates a bounded OMP-shaped worker result. By default
it uses a fixture result and marks the demo `mocked:true`, `live:false`. Set
`OMP_WORKER_RESULT=/path/to/tau.omp_worker_result.v1.json` to validate an
external worker artifact. The example does not prove Tau launched OMP until a
separate launcher receipt exists.

## Non-Claims

Tau does not claim:

- agents are trustworthy
- reviewer agents decide truth
- a passing patch receipt proves semantic correctness
- LSP or tests prove full safety
- worker adapters make OMP or SciLLM trusted
- worker validation proves Tau launched the worker
- GitHub read receipts authorize mutation
- policy/data-boundary gates are legal compliance

The design goal is narrower: make coding-agent output bounded, inspectable,
rejectable, and course-correctable.
