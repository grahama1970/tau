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
hash-bound patch receipts, LSP diagnostics, structured review findings, dry-run
commit planning, and course-correction receipts for blocked routes.

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
disallowed, explicitly forbidden, or generated paths, goal-hash mismatches,
malformed patch operations, and post-hash mismatches. The receipt records the
active `policy_profile`, `data_boundary`, `allowed_paths`, `forbidden_paths`,
built-in generated-path patterns, and the inspected patch artifact's
`patch_sha256` plus `patch_bytes`. When the target file exists, the receipt also
records `target_artifact_before` and `target_artifact_after` descriptors with
label, resolved path, existence, SHA-256, and byte count for the file state
before and after the attempted apply. Passing the receipt proves only that the
deterministic patch gate ran against that exact patch artifact and the
before/after hashes matched. It does not prove semantic correctness, test
success, production safety, or agent truthfulness.

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

Use `--zero-trust --policy-profile policy.json --data-boundary boundary.json`
when reviewer findings are part of a high-stakes coding route. In zero-trust
mode, Tau blocks review finding receipts that omit policy or boundary metadata.

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

Course-correction receipts include `input_valid`, `alerts`, and `alert_codes`.
They also require `goal_hash`; missing goal binding records
`missing_goal_hash` and makes the receipt invalid input. When a DAG receipt has
an active goal hash, orchestration reliability rejects declared
course-correction artifacts that omit or mismatch that goal hash.
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

### LSP-Style Diagnostics And Rename Planning

`tau.lsp_diagnostics_receipt.v1` records local diagnostics evidence for a
workspace. Tau uses Ruff when available and falls back to Python AST parsing for
syntax evidence. The receipt records the adapter used, inspected files,
diagnostics, severity counts, whether the adapter was available, and
`inspected_artifacts` with SHA-256 hashes and byte counts for the inspected
source files.

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
This is a regression signal for Tau course correction, not proof that the code
is semantically correct. Baseline receipts must be `status:"PASS"` with
`ok:true`; BLOCKED or failed baseline diagnostics receipts are recorded as
`baseline_receipt_not_pass` and do not produce a before/after delta.
When a diagnostics receipt carries `goal_hash`, the baseline diagnostics receipt
must carry the same `goal_hash`; missing or mismatched baseline goal hashes
block before/after comparison.

Use `--zero-trust --goal-hash sha256:... --policy-profile policy.json
--data-boundary boundary.json` when LSP evidence is part of a high-stakes coding
route. In zero-trust mode, Tau blocks diagnostics, symbol, and rename-plan
receipts that omit the active goal hash, policy profile, or data boundary.

`tau.lsp_symbol_receipt.v1` and `tau.lsp_rename_receipt.v1` provide read-only
symbol lookup and rename planning. Rename planning does not apply edits by
default; it records references, planned edits, the hash-bound inspected source
artifacts, and the hash/byte count of the intermediate symbol receipt used to
derive the rename plan. Rename plans block when the symbol is absent, the source
or target name is not a valid identifier, or the requested rename is a no-op.

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

### Atomic Commit Planning

`tau.commit_plan_receipt.v1` inspects a Git working tree and proposes dry-run
commit groups for source, tests, docs, and lockfiles. It records changed files,
their SHA-256 hashes and byte counts when the file still exists, dependency
order, risk level, required evidence per group, lockfile handling, and approval
requirements. Deleted files are recorded with `exists:false`, `bytes:null`, and
`sha256:null` so reviewers can distinguish absent content from missing
evidence.

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

Tau blocks source changes that have neither changed tests nor explicit evidence
receipts. The receipt records each evidence artifact path, schema, status,
`ok`, `mocked`, `live`, `provider_live`, and SHA-256 plus byte count so a later
reviewer can inspect the exact artifact and proof mode that supported the
proposed atomic commit. Evidence receipts only count when they report
`status:"PASS"` and `ok:true` from a supported Tau coding evidence schema;
BLOCKED, failed, or unknown-schema receipts are recorded but cannot justify a
source commit group.
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
hash, policy metadata, or boundary metadata.

The command is dry-run by default. `--apply` is intentionally blocked unless a
future approval lane authorizes commit application. High-risk paths such as
`.github/`, `secrets/`, `.env`, `pyproject.toml`, `uv.lock`, and
`package-lock.json` are flagged for approval.

### Debugger Evidence

`tau.debug_session_receipt.v1` records debugger/DAP evidence from a structured
local session packet. Supported adapter labels are `debugpy`, `lldb-dap`, `dlv`,
and `node`. The receipt records the goal hash, target, adapter availability,
breakpoints, stopped frame, variables, commands, stdout/stderr artifacts,
SHA-256 hashes, byte counts, conclusion, and non-claims. It also records the
inspected debug session packet's `session_sha256` and `session_bytes`, with
`null` values when the packet is missing. The same packet is exposed as a
`session_artifact` descriptor with label, path, existence, SHA-256, and byte
count so debug evidence can be reviewed through the same artifact pattern as
other coding receipts.

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

Use `--zero-trust --policy-profile policy.json --data-boundary boundary.json`
when debugger evidence is part of a high-stakes coding route. In zero-trust
mode, Tau blocks debug receipts that omit policy, boundary, or `goal_hash`
metadata. Use `--goal-hash sha256:...` when the caller needs to bind the
session packet to an expected active goal hash; a mismatch records
`goal_hash_mismatch`.

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

Use `--zero-trust --goal-hash sha256:... --policy-profile policy.json
--data-boundary boundary.json` when GitHub read projections are part of a
high-stakes coding route. In zero-trust mode, Tau blocks read receipts that
omit the active goal hash, policy metadata, or boundary metadata.

The receipt records the active goal hash, parsed target, a suggested `gh` read
command, blocked mutation verbs, and `mutation_allowed:false`. It also writes a
`tau.github_read_projection.v1` sidecar and records it as `projection_artifact`
with path, SHA-256, and byte count, so dry-run GitHub read evidence is tied to a
reviewable immutable projection artifact. In execute mode it records the exact
command, exit code, timeout state, stdout/stderr artifact paths, SHA-256 hashes,
byte counts, and artifact descriptors. It does not authorize mutation, prove
semantic correctness of GitHub content, or prove content freshness unless a real
`gh` command completed for that target at that time.

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
- SHA-256/byte-count binding for the DAG receipt and required receipts
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
goal-hash drift, disallowed file changes, missing required artifacts, PASS test
claims without durable logs, public GitHub mutation without policy receipts, and
external research without research-query/source receipts. High-stakes work
orders must name an allowed execution substrate such as Herdr-visible execution
or a sandbox, and must carry `policy_profile` plus `data_boundary` metadata
before Tau accepts the worker result. The metadata must use the current schemas:
`policy_profile.schema` must be `tau.policy_profile.v1`, and
`data_boundary.schema` must be `tau.data_boundary.v1`. Sandbox substrates must
include an existing `tau.sandbox_run_receipt.v1` receipt with `status:"PASS"`
and `ok:true`; Herdr substrates must include `herdr_binding` or an existing
`tau.herdr_observation_gate_receipt.v1` receipt with `status:"PASS"` and
`ok:true`. Validation receipts record
`work_order_sha256`, `result_sha256`, byte counts, and `validated_artifacts`
for the exact JSON artifacts Tau inspected. Validation and launch receipts also
carry `execution_substrate`, `sandbox_receipt_path`, `herdr_binding`,
`herdr_receipt_path`, `high_stakes`, `policy_profile`, `data_boundary`, and
`substrate_receipts` so the worker result or launch request remains tied to the
same containment metadata and the referenced sandbox/Herdr receipt content.
Each substrate receipt descriptor records the referenced path, SHA-256, byte
count, schema, status, `ok`, `mocked`, `live`, and `provider_live` fields when
the receipt can be read.

Required artifacts are not satisfied by strings alone. When a work order names a
required artifact, the worker result must list that artifact and the referenced
file must exist under the repo or at its absolute path before Tau accepts the
worker receipt. Accepted required artifacts are recorded in
`required_artifact_descriptors` with the declared artifact name, resolved path,
SHA-256, and byte count.

PASS test claims are treated the same way. When a worker result lists
`tests_run[].status:"PASS"`, it must include an existing `log_path` or
`stdout_path`; accepted test logs are recorded in `test_log_artifacts` with the
test name/status, declared artifact path, resolved path, SHA-256, and byte
count. This lets downstream reviewers distinguish "the worker claimed pytest
passed" from "Tau inspected the exact log artifact behind that claim."

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
  --request-timeout-s 650
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

For SciLLM coding delegates, Tau should use the OpenCode serve surface
(`/v1/scillm/opencode/runs`) with an agent profile such as `build` or
`scillm-debugger`, not chat completions, raw OpenCode ports, or `opencode-go/*`
model strings as the `agent`. By default, `scillm-worker-launch` is a dry-run
launcher receipt: it builds the exact `POST /v1/scillm/opencode/runs` payload,
redacts the required auth header, records `x_caller_skill`, and blocks wrong
surfaces/endpoints before any external call.

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
examples/coding-reliability-basic/run.sh /tmp/tau-coding-reliability-basic
examples/omp-worker/run.sh /tmp/tau-omp-worker-example
examples/scillm-worker/run.sh /tmp/tau-scillm-worker-example
examples/itar-grade-containment/run.sh /tmp/tau-itar-grade-containment-demo
```

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
