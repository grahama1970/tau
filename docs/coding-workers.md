# Coding Workers

Tau's coding layer is a containment layer, not a standalone coding-agent race.
Agents and external workers may propose code changes, but Tau decides what can
count by checking policy, hashes, receipts, review findings, and evidence.

## Starter Profile

Use the coding starter when creating a project that should collect coding
evidence before trusting any agent-authored patch:

```bash
uv run tau init --profile coding-zero-trust --out .
```

The profile writes `.tau/policy-profile.json`, `.tau/data-boundary.json`,
`.tau/command-policy.json`, `.tau/dag-template.json`, and `.tau/README.md`.
The DAG template includes a `tau.coding_contract.v1` block requiring
hash-bound patch receipts, LSP diagnostics, focused test-run receipts,
structured review findings, dry-run commit planning, and course-correction
receipts for blocked routes.

This starter does not prove semantic code correctness, sandbox isolation,
human identity, legal compliance, or provider/model quality.

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

`line_span` anchors must identify an exact line, the exact whole-file text
without surrounding whitespace, or a hash-bound range in the form
`line_span:<start>:<end>:sha256:<hash>`. Partial substrings do not count as
line-span anchors because they are too easy to satisfy accidentally after
nearby code drifts.

`symbol` anchors are Python token anchors. Tau accepts only valid identifier
names that appear as `NAME` tokens in the target file; comments, strings, and
partial identifier substrings do not satisfy a symbol anchor.

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
disallowed, policy-write-disallowed, explicitly forbidden, or generated paths,
goal-hash mismatches, malformed patch operations, and post-hash mismatches. If
the active `policy_profile.filesystem.write_allowlist` is present, the target
file must match that policy allowlist as well as the patch-local
`allowed_paths`; an empty policy write allowlist denies all writes. In
zero-trust mode, the patch gate also validates the full `tau.policy_profile.v1`
and `tau.data_boundary.v1` shapes and blocks `classified-not-allowed` data with
`classified_not_allowed` before applying any patch. The receipt records the
active `policy_profile`, `data_boundary`, `allowed_paths`,
`forbidden_paths`, built-in generated-path patterns, and the inspected patch
artifact's `patch_sha256` plus `patch_bytes`.
The built-in generated-path patterns cover both nested and repo-root generated
or vendor directories, including `generated/**`, `__generated__/**`,
`node_modules/**`, `.venv/**`, `dist/**`, and `build/**`.
Patch-local `allowed_paths` is mandatory and must contain at least one
non-empty string pattern; missing or empty scopes block with
`missing_allowed_paths` instead of becoming implicit broad write permission.
Patch-local `allowed_paths` and `forbidden_paths` must be lists of non-empty
strings; malformed path-scope fields block with `invalid_allowed_paths` or
`invalid_forbidden_paths` instead of becoming implicit empty scopes. The same
patch input is exposed as
`patch_artifact` with label, resolved path, existence, SHA-256, and byte count.
When the target file exists, the receipt also records `target_artifact_before`
and `target_artifact_after` descriptors with label, resolved path, existence,
SHA-256, and byte count for the file state before and after the attempted apply.
Passing the receipt proves only that the deterministic patch gate ran against
that exact patch artifact and the before/after hashes matched. It does not
prove semantic correctness, test success, production safety, or agent
truthfulness.

Unreadable, missing, or non-object patch artifacts also produce BLOCKED
`tau.code_patch_receipt.v1` receipts. Tau records alert codes such as
`code_patch_missing`, `code_patch_unreadable`, or `code_patch_not_object`
instead of failing before a receipt can be inspected. When the patch artifact
exists, even unreadable/non-object patch receipts include `patch_sha256` and
`patch_bytes`; missing artifacts record those fields as `null`.

CLI:

```bash
uv run tau code-patch \
  --patch patch.json \
  --repo . \
  --out code-patch-receipt.json \
  --goal-hash sha256:...
```

Use `--zero-trust --policy-profile policy.json --data-boundary boundary.json`
when the patch is part of a zero-trust or high-stakes DAG. In zero-trust mode,
the patch artifact itself must resolve under `--repo`; external patch artifacts
block with `code_patch_outside_repo` and cannot apply. Use `--dry-run` to write
the receipt without applying the staged content. Dry-run receipts record
`apply_requested:false`, `dry_run:true`, `applied:false`, and the staged post-
patch hash while leaving `after_sha256` bound to the unchanged target file.

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
- `P2` with `required_action:note` is accepted only with an explicit waiver
  object containing `approved:true`, `approved_by`, `reason`, and waiver
  `evidence`.
- no blocking or revision findings derives `PASS`.

P0/P1 findings require evidence. Tau blocks understated verdicts such as a
declared `PASS` with P1/P2 findings or a declared `REVISE` with P0 findings.
When `allowed_paths` or `forbidden_paths` are present, they must be lists of
non-empty strings; malformed path-scope fields block instead of becoming an
empty permissive scope. Finding files are normalized as repo-relative POSIX
paths and checked against those scopes.
The receipt records the inspected findings artifact's `findings_sha256` and
`findings_bytes`, plus `findings_artifact` with label, path, existence,
SHA-256, and byte count. It does not prove the reviewer is correct or
exhaustive.

Unreadable, missing, or non-object review finding artifacts also produce
BLOCKED `tau.review_findings.v1` receipts. Tau records alert codes such as
`review_findings_missing`, `review_findings_unreadable`, or
`review_findings_not_object` instead of failing before a reviewer receipt can
be inspected. When the findings artifact exists, even unreadable/non-object
receipts include `findings_sha256` and `findings_bytes`; missing artifacts
record those fields as `null` and `findings_artifact.exists:false`.

CLI:

```bash
uv run tau review-findings \
  --findings review-findings.json \
  --out review-findings-receipt.json \
  --goal-hash sha256:...
```

Use `--zero-trust --goal-hash sha256:... --policy-profile policy.json
--data-boundary boundary.json` when reviewer findings are part of a high-stakes
coding route. In zero-trust mode, Tau blocks review finding receipts that omit
the caller-supplied expected goal hash, policy metadata, or boundary metadata,
carry invalid `tau.policy_profile.v1` / `tau.data_boundary.v1` objects, or mark
the boundary as `classified-not-allowed`. Missing caller binding records
`missing_expected_goal_hash`; a packet/caller mismatch records
`goal_hash_mismatch`.
Review finding payloads may also declare `allowed_paths` and `forbidden_paths`;
Tau normalizes each `findings[].file` to a repo-relative path and blocks
absolute paths, `..` escapes, files outside `allowed_paths`, or files matching
`forbidden_paths`. This keeps reviewer claims inside the same coding boundary
as patch receipts and worker results.
In zero-trust mode, any review-findings payload with one or more findings must
declare a non-empty `allowed_paths` list; otherwise Tau blocks with
`missing_allowed_paths` before using the reviewer output for routing.

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
- `receipt_timeout`
- `provider_crashed`
- `herdr_stale`

These triggers route coding failures away from blind retry and toward bounded
next actions such as fresh patch receipts, structured review, debugger evidence,
quarantine, goal-guardian review, or human review.

Course-correction receipts include `input_valid`, `alerts`, and `alert_codes`.
They also require `goal_hash`; missing goal binding records
`missing_goal_hash` and makes the receipt invalid input. When a DAG receipt has
an active goal hash, orchestration reliability rejects declared
course-correction artifacts that omit or mismatch that goal hash.
Coding-trigger corrections also require `node_id`, `agent`, and `attempt >= 1`;
missing attribution records `missing_node_id`, `missing_agent`, or
`missing_attempt`. This prevents anonymous worker or reviewer failures from
looking like admissible bounded retry evidence.
Worker validation receipts now emit a concrete course-correction artifact when
they block external worker output. Missing, malformed, prose-only, or
artifact-incomplete worker results map to `worker_result_missing`; changed files, result
artifacts, or test logs outside the repo/path boundary map to
`worker_changed_forbidden_path`; worker result goal drift maps to
`goal_hash_mismatch`; Herdr substrate failures map to `herdr_stale`; missing or
invalid sandbox/substrate receipts map to `receipt_timeout`. The worker receipt
records `course_correction_path`, `course_correction_artifacts`, and the
embedded `course_correction` payload so downstream orchestration can route the
blocked worker instead of inferring a repair path from raw alert codes.
When a correction is based on a concrete failed receipt, log, or evidence file,
pass it with `--observed-artifact`; Tau records `observed_artifact` with path,
existence, SHA-256, and byte count so the correction remains tied to the
triggering artifact.
Triggers that claim repeated failure, such as
`brave_search_required_after_two_attempts`, `test_failed_twice`, and
`two_failed_attempts`, require either `attempt >= 2` or
`observed_state.attempt_count >= 2`; otherwise Tau records
`attempt_evidence_below_required_threshold` so the receipt does not silently
launder an unsupported retry-budget claim.
`two_failed_attempts` routes to reviewer/debug/goal-guardian/human rather than
blind human-only escalation, forbids `retry_same_context` and unrelated test
churn, and requires `two_attempt_failure_receipt` plus
`replan_or_debug_receipt` before another attempt.

### LSP-Style Diagnostics And Rename Planning

`tau.lsp_diagnostics_receipt.v1` records local diagnostics evidence for a
workspace. Tau uses Ruff when available and falls back to Python AST parsing for
syntax evidence. The receipt records the adapter used, inspected files,
diagnostics, severity counts, whether the adapter was available, and
`inspected_artifacts` with resolved path, existence, SHA-256 hashes, and byte
counts for the inspected source files.

CLI:

```bash
uv run tau lsp-diagnostics --workspace . --out lsp-diagnostics-receipt.json
```

To compare a post-change workspace against a previous diagnostics receipt:

```bash
uv run tau lsp-diagnostics \
  --workspace . \
  --goal-hash sha256:... \
  --baseline-receipt before-diagnostics.json \
  --out after-diagnostics.json
```

The receipt records `baseline_severity_counts`, `diagnostic_delta`, and
`diagnostics_increased`. It also records `baseline_receipt_artifact` with path,
existence, SHA-256, and byte count for the exact baseline receipt inspected.
If any severity count increases relative to the baseline, Tau blocks the
receipt with `lsp_diagnostics_regressed`; this is a course-correction signal,
not proof that the code is semantically correct. Baseline receipts must be
`status:"PASS"` with `ok:true`; BLOCKED or failed baseline diagnostics receipts
are recorded as `baseline_receipt_not_pass` and do not produce a before/after
delta. Baseline receipts must also resolve under the same `--workspace`;
workspace-external baselines are blocked as `baseline_receipt_outside_workspace`
and cannot influence regression comparison.
When a diagnostics receipt carries `goal_hash`, the baseline diagnostics receipt
must carry the same `goal_hash`; missing or mismatched baseline goal hashes
block before/after comparison.

Use `--zero-trust --goal-hash sha256:... --policy-profile policy.json
--data-boundary boundary.json` when LSP evidence is part of a high-stakes coding
route. In zero-trust mode, Tau blocks diagnostics, symbol, and rename-plan
receipts that omit the active goal hash, policy profile, or data boundary. When
the supplied `tau.policy_profile.v1` or `tau.data_boundary.v1` object is
malformed, or the boundary classification is `classified-not-allowed`, Tau
blocks before the LSP-style evidence can support a route. When
`policy_profile.filesystem.read_denylist` is present, Tau filters matching files
before local diagnostics or symbol scanning, records `policy_read_denied_paths`,
and blocks the receipt with `policy_read_denied` instead of reading denied
source files.

`tau.lsp_symbol_receipt.v1` and `tau.lsp_rename_receipt.v1` provide read-only
symbol lookup and rename planning. Symbol lookup is token-based for Python
sources: Tau records exact identifier tokens, not substring matches in longer
identifiers, comments, or string literals. Invalid symbol queries block with
`invalid_query`. Rename planning does not apply edits by default; it records
references, planned edits, the hash-bound inspected source artifacts, and the
hash/byte count of the intermediate symbol receipt used to derive the rename
plan. Rename plans block when the symbol is absent, the source or target name is
not a valid identifier, or the requested rename is a no-op.
Because rename planning is write intent, `policy_profile.filesystem.write_allowlist`
is enforced when present: denied planned edit paths are recorded in
`policy_write_denied_paths`, each planned edit records `policy_write_allowed`,
and the receipt blocks with `policy_write_disallowed`.

CLI:

```bash
uv run tau lsp-symbols \
  --workspace . \
  --query Example \
  --goal-hash sha256:... \
  --out lsp-symbols.json
uv run tau lsp-rename-plan \
  --workspace . \
  --symbol Example \
  --new-name BetterExample \
  --goal-hash sha256:... \
  --out lsp-rename-plan.json
```

These receipts do not prove semantic correctness, complete language-server
parity, or that a rename is safe to apply.

### Focused Test-Run Receipts

`tau.test_run_receipt.v1` records a focused local pytest-shaped command for
coding work. It is intentionally not a general shell runner: Tau accepts
`python -m pytest`, `pytest`, or `uv run pytest` command forms and rejects other
commands with `disallowed_test_command`.

CLI:

```bash
uv run tau test-run \
  --repo . \
  --out test-run-receipt.json \
  --goal-hash sha256:... \
  --command python3 \
  --command -m \
  --command pytest \
  --command -q \
  --tested-path src/example.py
```

The receipt records the command, return code, timeout, stdout/stderr artifacts,
`tested_paths`, and `tests_passed`. `tested_paths` lets `tau commit-plan`
connect focused test evidence to changed source paths without inferring
semantic coverage from a passing command. Tested paths must be non-empty
relative paths inside the repo; absolute paths or `..` escapes block with
`invalid_tested_path` before the test command can run. In zero-trust mode, Tau
requires the active goal hash, `tau.policy_profile.v1`, and
`tau.data_boundary.v1` before running the command. The zero-trust receipt path
must also resolve under `--repo`; external receipt paths block with
`test_run_receipt_outside_repo` before command execution, so stdout/stderr
artifacts are not written outside the repository boundary.

This receipt proves only that Tau ran the named focused test command and
captured its artifacts. It does not prove semantic correctness, full-suite
health unless the full suite was the command, agent truthfulness, or
provider/model quality.

### Atomic Commit Planning

`tau.commit_plan_receipt.v1` inspects a Git working tree and proposes dry-run
commit groups for source, tests, docs, and lockfiles. It records changed files,
their SHA-256 hashes and byte counts when the file still exists, dependency
order, risk level, required evidence per group, lockfile handling, and approval
requirements. Deleted files are recorded with `exists:false`, `bytes:null`, and
`sha256:null` so reviewers can distinguish absent content from missing
evidence. When `policy_profile.filesystem.read_denylist` matches a changed
file, Tau records the path and status but withholds content inspection by
setting `policy_read_denied:true`, `exists:null`, `bytes:null`, and
`sha256:null`, then blocks the plan with `policy_read_denied`. When
`policy_profile.filesystem.write_allowlist` is present, every changed path must
match that allowlist; otherwise the plan blocks with `policy_write_disallowed`
and records `policy_write_allowed:false` for the affected file.
For source-only changes without changed tests, supported evidence receipts,
including `tau.test_run_receipt.v1`, must cover every changed source path.
Partial evidence coverage blocks with
`source_changes_lack_relevant_evidence` and records the uncovered source paths
in the alert `errors` field.

CLI:

```bash
uv run tau commit-plan --repo . --out commit-plan-receipt.json
```

For source-only changes, attach the receipts that justify the commit group:

```bash
uv run tau commit-plan \
  --repo . \
  --out commit-plan-receipt.json \
  --goal-hash sha256:... \
  --evidence-receipt code-patch-receipt.json \
  --evidence-receipt lsp-diagnostics-receipt.json \
  --evidence-receipt review-findings-receipt.json
```

To request apply eligibility, attach an approval-gate receipt:

```bash
uv run tau commit-plan \
  --repo . \
  --out commit-plan-receipt.json \
  --apply \
  --approval-receipt approval-gate-receipt.json
```

Tau blocks source changes that have neither changed tests nor explicit evidence
receipts. The receipt records each evidence artifact path, schema, status,
`ok`, `mocked`, `live`, `provider_live`, existence, and SHA-256 plus byte count
so a later reviewer can inspect the exact artifact and proof mode that
supported the proposed atomic commit. Evidence receipts only count when they report
`status:"PASS"` and `ok:true` from a supported Tau coding evidence schema;
BLOCKED, failed, mocked, non-live, or unknown-schema receipts are recorded but
cannot justify a source commit group.
Evidence receipt paths must be inside the repository being planned; external
paths block with `evidence_receipt_outside_repo` and do not count toward source
coverage.
For source-only changes, evidence must also cover at least one changed source
path. Tau derives `covered_paths` from common receipt fields such as
`target_file`, `changed_files`, `inspected_artifacts`, `findings[].file`, and
artifact descriptors, then blocks unrelated evidence with
`source_changes_lack_relevant_evidence`.
When `--goal-hash` is supplied, every evidence receipt must carry the same
`goal_hash`; missing or mismatched evidence goal hashes block the commit plan.

Commit plans also emit non-blocking `warnings` and `warning_codes` when the
working tree mixes independent commit-group classes, such as docs with runtime
or test changes, or lockfiles with unrelated files. These warnings are review
signals only. They do not authorize a commit, prove the grouping is
semantically correct, or replace the approval gate for high-risk paths.

Use `--zero-trust --goal-hash sha256:... --policy-profile policy.json
--data-boundary boundary.json` when planning commits for a high-stakes coding
route. In zero-trust mode, Tau blocks commit plans that omit the active goal
hash, policy metadata, or boundary metadata. It also validates the full
`tau.policy_profile.v1` and `tau.data_boundary.v1` objects and blocks malformed
metadata or `classified-not-allowed` boundaries before a commit plan can support
coding continuation.

The command is dry-run by default. `--apply` requires a valid
`tau.approval_gate_receipt.v1` with `requested_action:"working_tree_mutation"`
passed through `--approval-receipt`; mocked approval receipts do not make a
plan apply-eligible. The approval receipt must also bind
`packet_summary.target_id` to the planned repository as `repo:<absolute repo
path>`; approvals for a different repo block with
`approval_receipt_target_mismatch`. The receipt can then mark the plan
`apply_eligible:true`, but this commit-plan lane still does not run
`git commit`. High-risk paths such as `.github/`, `secrets/`, `.env`,
`pyproject.toml`, `uv.lock`, and `package-lock.json` are flagged for approval
unless a valid working-tree mutation approval receipt is supplied. Untracked
sensitive paths such as `.env`, `.env.*`, private-key files, and `secrets/**`
still block with `untracked_sensitive_files` so a commit plan cannot quietly
normalize accidental secret material into a proposed commit group.

### Debugger Evidence

`tau.debug_session_receipt.v1` records debugger/DAP evidence from a structured
local session packet. Supported adapter labels are `debugpy`, `lldb-dap`, `dlv`,
and `node`. The receipt records the goal hash, target, adapter availability,
breakpoints, stopped frame, variables, commands, stdout/stderr artifacts,
existence, SHA-256 hashes, byte counts, conclusion, and non-claims. It also
records the inspected debug session packet's `session_sha256` and
`session_bytes`, with `null` values when the packet is missing. The same packet
is exposed as a `session_artifact` descriptor with label, path, existence,
SHA-256, and byte count so debug evidence can be reviewed through the same
artifact pattern as other coding receipts.
Debugger variables with sensitive-looking names such as `token`, `password`,
`secret`, `api_key`, `credential`, or `auth` are redacted before receipt write;
Tau records `value_sha256`, `redacted:true`, and `variable_redaction_count`
instead of storing the raw value. This reduces debug-evidence leakage, but does
not prove every sensitive value was discovered.

Tau blocks debug receipts when the session packet omits the target command, uses
an unsupported adapter, refers to missing stdout/stderr artifacts, points
stdout/stderr outside the debug session packet directory, or provides malformed
structured evidence fields. `breakpoints`, `variables`, and `commands` must be
arrays; `stopped_frame` must be an object.

CLI:

```bash
uv run tau debug-session-receipt \
  --session debug-session.json \
  --out debug-session-receipt.json
```

Use `--zero-trust --goal-hash sha256:... --policy-profile policy.json
--data-boundary boundary.json` when debugger evidence is part of a high-stakes
coding route. In zero-trust mode, Tau blocks debug receipts that omit the
caller-supplied expected goal hash, policy, boundary, or session-packet
`goal_hash` metadata, carry invalid `tau.policy_profile.v1` /
`tau.data_boundary.v1` objects, or mark the boundary as
`classified-not-allowed`. Missing caller binding records
`missing_expected_goal_hash`; a packet/caller mismatch records
`goal_hash_mismatch`. When `policy_profile.filesystem.read_denylist` matches a
declared stdout/stderr log artifact, Tau blocks with `policy_read_denied` and
does not include a SHA-256 or byte count for that denied log. Debug session
packets may also declare `allowed_paths` and `forbidden_paths`; Tau checks
breakpoint and stopped-frame `file` entries against those boundaries and blocks
with `debug_evidence_path_disallowed` or `debug_evidence_path_forbidden` when
debug evidence points outside the declared coding scope. Absolute paths and
`..` escapes block with `debug_evidence_path_escape` instead of being ignored.
Zero-trust debug targets are also screened for shell-control syntax such as
`;`, `&&`, pipes, command substitution, redirects, and newlines. A target with
that syntax blocks with `unsafe_debug_target` so debugger evidence cannot carry
an unreviewed shell chain while still looking like a passive evidence packet.

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

The `commit://` form requires a short or full hexadecimal commit SHA. Branch
names, tag names, and other moving refs are rejected for this read scheme so a
commit evidence receipt stays tied to an immutable object.
All supported forms also validate GitHub owner and repo tokens before accepting
the target. Malformed names, including whitespace/control-like or shell-like
characters, block with `invalid_github_target` before execute mode can invoke
`gh`.

CLI:

```bash
uv run tau github-read \
  --uri issue://grahama1970/tau/67 \
  --goal-hash sha256:... \
  --out github-read-receipt.json
```

Projection is the default. Use `--execute` only when the run should perform the
bounded read-only `gh` command and persist stdout/stderr sidecars:

```bash
uv run tau github-read \
  --uri issue://grahama1970/tau/67 \
  --goal-hash sha256:... \
  --out github-read-receipt.json \
  --execute
```

In execute mode, `issue://`, `pr://`, and `commit://` reads are fail-closed
against malformed `gh` output: stdout must parse as a JSON object because those
commands are JSON-backed evidence reads. `diff://` remains a plain-text read and
does not require JSON stdout.

Use `--zero-trust --goal-hash sha256:... --policy-profile policy.json
--data-boundary boundary.json` when GitHub read projections are part of a
high-stakes coding route. In zero-trust mode, Tau blocks read receipts that
omit the active goal hash, policy metadata, or boundary metadata. It validates
the full `tau.policy_profile.v1` and `tau.data_boundary.v1` objects, blocks
malformed metadata with `invalid_policy_profile` or `invalid_data_boundary`,
and refuses `classified-not-allowed` boundaries before any `gh` command can
run. If the active data boundary sets `public_repo_allowed:false`, Tau blocks the read with
`public_repo_denied` and does not execute `gh`; the projection remains a local
review artifact and does not authorize external GitHub access. When the active
policy profile declares `github.allowed_repos`, Tau also blocks reads outside
that repo allowlist with `github_repo_not_allowed`; malformed allowlists block
with `invalid_github_allowed_repos`.

The receipt records the active goal hash, parsed target, a suggested `gh` read
command, blocked mutation verbs, and `mutation_allowed:false`. It also writes a
`tau.github_read_projection.v1` sidecar and records it as `projection_artifact`
with resolved path, existence, SHA-256, and byte count, so dry-run GitHub read
evidence is tied to a reviewable immutable projection artifact. In execute mode
it records the exact command, exit code, timeout state, stdout/stderr artifact
paths, existence, SHA-256 hashes, byte counts, and artifact descriptors. It does
not authorize mutation, prove semantic correctness of GitHub content, or prove
content freshness unless a real `gh` command completed for that target at that
time.

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
- existence plus SHA-256/byte-count binding for the DAG receipt and required receipts
- required receipt validity: required receipts must be `status:"PASS"`,
  `ok:true`, `mocked:false`, and `live:true`
- required receipt scope: required receipts must resolve under the active
  `--run-dir` or DAG receipt directory; external receipts are marked
  `outside_run_scope` and cannot support orchestration reliability
- course-correction emission and handling
- retry budget discipline
- terminal condition validity
- `agent_truthfulness: NOT_CLAIMED`

For orchestration reliability, a terminal condition can be either a clean
`PASS` route to a declared terminal node or a controlled `BLOCKED` stop with a
valid declared `tau.course_correction.v1` artifact. A controlled blocked run
does not mean the coding task succeeded; it means Tau stopped safely and left a
bounded next-action receipt.

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

### Static Run Reports

`tau.run_report_receipt.v1` records the HTML report artifact and the source
artifacts used to render it, including the DAG receipt and DAG contract when
present. The report receipt does not claim a SHA-256 hash of itself, because a
self-hash field would become stale as soon as it is written. Instead it records
`receipt_sha256_excludes_self:true` and `unsigned_receipt_preimage_sha256`,
which hashes the unsigned receipt body before those self-reference metadata
fields are added.

CLI:

```bash
uv run tau report run-dir --out report.html
```

The report is an inspection artifact. It does not prove memory truth, evidence
case sufficiency, code correctness, ITAR compliance, legal sufficiency, or that
an agent DAG/swarm is trustworthy.

### Aggregate Coding Sanity

`scripts/run-coding-capability-sanity.py` runs the current copyable coding
examples plus the focused coding receipt test set, then writes one
`tau.coding_capability_sanity_receipt.v1`:

```bash
scripts/run-coding-capability-sanity.py \
  --run-dir /tmp/tau-coding-capability-sanity
```

The receipt covers zero-trust policy/data-boundary preflight, hash-bound patch
receipts, course correction, structured review findings, LSP receipts, commit
planning, debugger evidence, GitHub reads, worker validation, dry-run worker
launch receipts, bounded apply-launch mechanics, memory intent/evidence-case
gates, Graph Memory acquisition receipts, compliance evidence package receipts,
run report generation, local API preflight surfaces, provenance/signing,
zero-trust adversarial red-team receipts, the ITAR-grade containment example,
Herdr observation gates, sandbox-run policy receipts, and orchestration
reliability. It records `mocked:"mixed"` and `live:"mixed"` because the worker
examples use fixture worker results and deterministic local apply fixtures, the
containment demo uses local fail-closed fixtures and package validation, and the
coding reliability example exercises local receipt-producing commands. It does
not prove live Graph Memory, live OMP/SciLLM semantic worker execution,
provider/model quality, semantic code correctness, GitHub mutation, human
acceptance, legal compliance, ITAR compliance, or full sandbox isolation on
every host.

## Intended Worker Adapters

External coding workers remain untrusted. Tau should wrap them with work orders,
allowed/forbidden paths, goal hashes, policy/data-boundary metadata, and required
result artifacts.

Current validation adapters:

- `tau.executor.omp.v1` work orders validated into `tau.omp_worker_receipt.v1`
- `tau.executor.omp.v1` work orders converted into dry-run or bounded apply
  `tau.omp_worker_launch_receipt.v1` launch requests for OMP RPC
- `tau.executor.scillm_worker.v1` work orders validated into
  `tau.scillm_worker_receipt.v1`
- `tau.executor.scillm_worker.v1` work orders converted into dry-run or
  bounded apply `tau.scillm_worker_launch_receipt.v1` launch requests for
  SciLLM OpenCode serve

These adapters reject missing results, invalid schemas, prose-only results,
goal-hash drift, disallowed file changes, missing result artifacts, missing
required artifacts, result artifacts outside the work-order allowlist or inside
forbidden paths, PASS test claims without durable logs, public GitHub mutation
without policy receipts, and external research without research-query/source
receipts. When a worker result
does declare a GitHub apply policy receipt, research-query safety receipt, or
research-source receipt, Tau resolves the receipt under the work-order repo,
requires the expected schema and `status:"PASS"`/`ok:true`, rejects mocked
receipts, and records SHA-256/byte descriptors in `side_effect_receipts` or
`research_receipts`. For GitHub mutations, Tau also checks that the apply
policy receipt target and actions match the worker's requested
`github:owner/repo#number` or `github:owner/repo:issue#number` target and
requested action. The policy receipt must also carry a `requirements` object
with `approval_packet:true` and `preflight:true`; public GitHub comments must
also carry `redaction:true`. A referenced policy or research receipt makes the
worker claim admissible for review; it does not prove live GitHub mutation,
research truth, source sufficiency, or worker trustworthiness. High-stakes work
orders must name an allowed execution substrate such as Herdr-visible execution
or a sandbox, and must carry `policy_profile` plus `data_boundary` metadata
before Tau accepts the worker result. The metadata must use and pass the current
schemas: `policy_profile.schema` must be `tau.policy_profile.v1`, and
`data_boundary.schema` must be `tau.data_boundary.v1`. Tau blocks malformed
policy or boundary objects with `invalid_policy_profile` or
`invalid_data_boundary`, and refuses `classified-not-allowed` boundaries before
worker validation or launch can support a high-stakes coding route. Sandbox
substrates must include an existing `tau.sandbox_run_receipt.v1` receipt with
`status:"PASS"`,
`ok:true`, `mocked:false`, and `live:true`; Herdr substrates must include both
`herdr_binding` and an existing `tau.herdr_observation_gate_receipt.v1` receipt
with `status:"PASS"`, `ok:true`, `mocked:false`, and `live:true`. Referenced
sandbox and Herdr receipts must also carry `goal_hash` matching the worker work
order and `work_order_sha256` matching the exact worker work-order artifact.
Missing or stale bindings block with `sandbox_receipt_missing_goal_hash`,
`herdr_receipt_missing_goal_hash`, `sandbox_receipt_goal_hash_mismatch`,
`herdr_receipt_goal_hash_mismatch`,
`sandbox_receipt_missing_work_order_sha256`,
`herdr_receipt_missing_work_order_sha256`,
`sandbox_receipt_work_order_sha256_mismatch`, or
`herdr_receipt_work_order_sha256_mismatch`.
Referenced sandbox and Herdr receipt paths must resolve inside the worker repo;
absolute paths outside the repo block with `sandbox_receipt_outside_repo` or
`herdr_receipt_outside_repo` and are not recorded as admissible substrate
descriptors.
Use `uv run tau sandbox-run --goal-hash sha256:...` and include the final
worker work-order SHA-256 when creating a sandbox receipt that will be
referenced by a high-stakes worker work order; otherwise the worker substrate
gate will reject it as unbound. `uv run tau sandbox-run`
writes a BLOCKED `tau.sandbox_run_receipt.v1` even when the policy profile or
data-boundary file is missing, invalid JSON, or not a JSON object. Those
preflight failures record `policy_profile_missing`,
`policy_profile_unreadable`, `policy_profile_not_object`,
`data_boundary_missing`, `data_boundary_unreadable`, or
`data_boundary_not_object`; command execution remains false.
Binding metadata alone is not an admissible high-stakes Herdr substrate. Validation
receipts record `work_order_sha256`, `result_sha256`, byte counts, and
`validated_artifacts` for the exact JSON artifacts Tau inspected. Worker result
artifacts are recorded in `result_artifact_descriptors` with resolved path,
existence, SHA-256, byte count, and original artifact string when they exist and
stay inside the worker repo. Launch
receipts record `work_order_sha256`, `work_order_bytes`, and
`work_order_artifact` before dry-run or apply launch so the process/HTTP
request is bound to the exact work order Tau preflighted. Each validated or
launch artifact descriptor records label, resolved path, existence, SHA-256,
and byte count. Validation and launch receipts also carry
`execution_substrate`, `sandbox_receipt_path`, `herdr_binding`,
`herdr_receipt_path`, `high_stakes`, `policy_profile`, `data_boundary`, and
`substrate_receipts` so the worker result or launch request remains tied to the
same containment metadata and the referenced sandbox/Herdr receipt content.
Inline `policy_profile` and `data_boundary` objects are hash-bound with
`policy_profile_sha256`, `policy_profile_bytes`, `policy_profile_artifact`,
`data_boundary_sha256`, `data_boundary_bytes`, and
`data_boundary_artifact`, using canonical JSON bytes for the exact metadata
Tau enforced.
Each substrate receipt descriptor records the referenced path, existence,
SHA-256, byte count, schema, status, `ok`, `mocked`, `live`, and
`provider_live` fields when the receipt can be read.

Worker `changed_files` are checked against the declared work-order repo before
allowed/forbidden path policy is applied. Absolute paths inside the repo are
normalized to repo-relative POSIX paths in `normalized_changed_files`; absolute
paths outside the repo block with `changed_file_outside_repo`.

Required artifacts are not satisfied by strings alone. When a work order names a
required artifact, the worker result must list that artifact and the referenced
file must exist under the work-order repo before Tau accepts the worker receipt.
Absolute paths outside the repo are blocked with `artifact_outside_repo`.
Accepted required artifacts are recorded in
`required_artifact_descriptors` with the declared artifact name, resolved path,
existence, SHA-256, and byte count.

PASS test claims are treated the same way. When a worker result lists
`tests_run[].status:"PASS"`, it must include an existing `log_path` or
`stdout_path` under the work-order repo. Absolute paths outside the repo are
blocked with `test_log_outside_repo`; accepted test logs are recorded in
`test_log_artifacts` with the test name/status, declared artifact path, resolved
path, existence, SHA-256, and byte count. This lets downstream reviewers
distinguish "the worker claimed pytest passed" from "Tau inspected the exact log
artifact behind that claim."

CLI:

```bash
uv run tau omp-worker-validate \
  --work-order omp-work-order.json \
  --result omp-result.json \
  --out omp-worker-receipt.json

uv run tau omp-worker-launch \
  --work-order omp-work-order.json \
  --out omp-worker-launch-receipt.json

uv run tau omp-worker-launch \
  --work-order omp-work-order.json \
  --out omp-worker-launch-receipt.json \
  --apply \
  --omp-bin omp \
  --timeout-s 600

uv run tau scillm-worker-validate \
  --work-order scillm-work-order.json \
  --result scillm-result.json \
  --out scillm-worker-receipt.json

uv run tau scillm-worker-launch \
  --work-order scillm-work-order.json \
  --out scillm-worker-launch-receipt.json

uv run tau scillm-worker-launch \
  --work-order scillm-work-order.json \
  --out scillm-worker-launch-receipt.json \
  --scillm-base-url http://localhost:4001 \
  --apply \
  --request-timeout-s 600
```

For OMP coding delegates, Tau uses the documented process-isolated RPC surface:
`omp --mode rpc --no-session` with NDJSON prompt frames. By default,
`omp-worker-launch` is a dry-run launcher receipt: it builds the command and
stdin JSONL frame, records the caller skill, and blocks incompatible OMP route
metadata before any external process launch.

With `--apply`, `omp-worker-launch` invokes the configured command, writes
captured stdout and stderr artifacts next to the receipt, and records
`process_executed`, `exit_code`, `timed_out`, `stdout_path`, `stderr_path`,
`stdout_sha256`, `stderr_sha256`, byte counts, and `log_artifacts`.
This proves only that Tau sent a bounded request to a local process and captured
the process result. It does not prove OMP accepted the request semantically, a
real `oh-my-pi` binary was used, the worker result artifact is valid, code
changed, or code is correct. A worker result must still pass
`omp-worker-validate`.

For SciLLM coding delegates, Tau uses the SciLLM proxy service, normally
`http://localhost:4001`, and the OpenCode serve surface
(`/v1/scillm/opencode/runs`) with an agent profile such as `build` or
`scillm-debugger`, not chat completions, raw OpenCode ports, direct provider
APIs, or `opencode-go/*` model strings as the `agent`. By default,
`scillm-worker-launch` is a dry-run launcher receipt: it builds the exact
`POST /v1/scillm/opencode/runs` payload, redacts the required auth header,
records `x_caller_skill`, and blocks wrong surfaces/endpoints before any
external call. The default request timeout is 600 seconds to match the SciLLM
OpenCode serve contract. Malformed base URLs are blocked before apply, and
known raw local OpenCode ports such as `127.0.0.1:4096` and `127.0.0.1:4098`
are blocked as `raw_opencode_base_url`; Tau must route through the SciLLM proxy
surface rather than around it.

With `--apply`, `scillm-worker-launch` posts the bounded request to the
configured SciLLM OpenCode-serve endpoint, writes the response JSON beside the
receipt, and records `http_executed`, `http_status`, `response_path`,
`response_sha256`, response byte count, `run_id`, `session_id`,
`scillm_run_status`, response artifacts, and `http_artifacts`. HTTP error
artifacts are also hash-bound when present. A generic HTTP 200 JSON object is
not accepted as an admissible launch result: Tau requires
`scillm_run_status:"completed"` and at least one of `run_id` or `session_id`,
otherwise the launch receipt is BLOCKED with `missing_scillm_run_status` or
`missing_scillm_run_identifier`. Apply mode requires bearer auth from one of
these sources:

- explicit `--auth-token`
- local `SCILLM_MASTER_KEY`, `SCILLM_API_KEY`, or `SCILLM_AUTH_TOKEN`
- a local env file selected by `SCILLM_ENV_PATH`
- the local Scillm project `.env` when the base URL is localhost

Tau records only `headers.authorization: REDACTED` plus
`headers.authorization_source`; it never writes the bearer token to the receipt.
If no auth source is available, Tau fails closed before issuing the HTTP
request. This proves only that Tau sent the bounded request to the configured
SciLLM endpoint and captured its response. It does not prove the OpenCode worker
result is truthful or sufficient for closure, a worker result artifact is valid,
code changed, or code is correct. A worker result must still pass
`scillm-worker-validate`.

Copyable examples:

```bash
examples/memory-evidence-case/run.sh /tmp/tau-memory-evidence-case
examples/coding-reliability-basic/run.sh /tmp/tau-coding-reliability-basic
examples/omp-worker/run.sh /tmp/tau-omp-worker-example
examples/scillm-worker/run.sh /tmp/tau-scillm-worker-example
examples/itar-grade-containment/run.sh /tmp/tau-itar-grade-containment-demo
```

`examples/memory-evidence-case` writes Graph Memory `/intent` and
`/create-evidence-case` shaped local artifacts, then emits
`tau.memory_intent_gate_receipt.v1` and `tau.evidence_case_gate_receipt.v1`.
It proves only that Tau can evaluate separate memory intent and evidence-case
gate inputs and write parseable receipts; it does not prove Memory truth,
evidence-case sufficiency, ITAR compliance, legal sufficiency, provider/model
quality, or semantic code correctness.

`examples/omp-worker` validates a bounded OMP-shaped worker result. By default
it uses a fixture result and marks the demo `mocked:true`, `live:false`; it
also writes a dry-run `omp-worker-launch-receipt.json` showing the exact OMP
RPC command and prompt frame Tau would send, plus a deterministic
`omp-worker-launch-apply-receipt.json` using a local `fake-omp` executable to
exercise process launch and stdout/stderr capture. Set
`OMP_WORKER_RESULT=/path/to/tau.omp_worker_result.v1.json` to validate an
external worker artifact. The launch receipt can also be generated directly:

```bash
uv run tau omp-worker-launch \
  --work-order omp-work-order.json \
  --out omp-worker-launch-receipt.json
```

Dry-run launch does not prove live OMP execution. Apply launch proves only that
Tau invoked the configured local process and captured stdout/stderr; it does not
replace result validation.

`examples/scillm-worker` validates a bounded SciLLM/OpenCode-serve-shaped
worker result. By default it uses a fixture result and marks the demo
`mocked:true`, `live:false`; it also writes a dry-run
`scillm-worker-launch-receipt.json` showing the exact OpenCode-serve request
Tau would send, plus a deterministic `scillm-worker-launch-apply-receipt.json`
using a local SciLLM-compatible fixture server to exercise HTTP post and
response capture. Set
`SCILLM_WORKER_RESULT=/path/to/tau.scillm_worker_result.v1.json` to validate an
external worker artifact. The work order records the correct coding-delegate
surface, `/v1/scillm/opencode/runs`, with an OpenCode agent profile such as
`build`; the launch receipt can also be generated directly:

```bash
uv run tau scillm-worker-launch \
  --work-order scillm-work-order.json \
  --out scillm-worker-launch-receipt.json
```

Dry-run launch does not prove live SciLLM execution. Apply launch proves only
that Tau posted to the configured SciLLM endpoint and captured the response; it
does not replace result validation.

## Packaging Coding Evidence

`uv run tau compliance-package <run-dir> --out <package-dir>` copies Tau-native
coding evidence receipts into `coding-evidence-receipts/` when a run produced
them. This includes patch, LSP, focused test-run, review findings, commit-plan,
debug-session, OMP worker, SciLLM worker, and orchestration reliability
receipts.

Packaging these receipts makes coding evidence easier to review. It does not
prove code correctness, worker truthfulness, live provider behavior, sandbox
isolation, legal compliance, or closure.

## Non-Claims

Tau does not claim:

- agents are trustworthy
- reviewer agents decide truth
- a passing patch receipt proves semantic correctness
- LSP or tests prove full safety
- worker adapters make OMP or SciLLM trusted
- worker validation proves Tau launched the worker
- OMP dry-run or fixture apply launch receipts prove real OMP execution
- SciLLM dry-run or fixture apply launch receipts prove live OpenCode execution
- GitHub read receipts authorize mutation
- policy/data-boundary gates are legal compliance

The design goal is narrower: make coding-agent output bounded, inspectable,
rejectable, and course-correctable.
