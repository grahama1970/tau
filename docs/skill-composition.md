# Skill Composition

Tau should not reimplement every capability in `agent-skills`. The operating
split is:

```text
agent-skills = capability providers
Tau = admissibility, DAG authority, policy, receipts, course correction
```

The first layer is a read-only capability registry. It maps Tau-required
capabilities to existing skills and names the Tau receipt schema that can make
the skill output admissible later.

## Registry

Create a default registry:

```bash
uv run tau skill-capability-registry-default \
  --out /tmp/tau-skill-capability-registry.json
```

Validate a registry:

```bash
uv run tau skill-capability-registry-validate \
  --registry /tmp/tau-skill-capability-registry.json \
  --out /tmp/tau-skill-capability-registry-validation.json
```

The registry schema is `tau.skill_capability_registry.v1`. The validation
receipt schema is `tau.skill_capability_registry_validation_receipt.v1`.

## Relationship To Memory

Memory already composes skills through proven `skill_chain` recall and `/intent`
routing. Tau should use that as routing evidence, not replace it.

The registry has a different job: it is Tau's local admissibility map. It says
which skill is allowed to satisfy a Tau capability and which Tau receipt schema
must bind the result before a DAG, gate, or course-correction action can treat
the skill output as evidence.

```text
Memory: what skill chain or route has worked before?
Tau registry: is that capability allowed here, and what receipt makes it count?
```

That keeps skill selection adaptive without letting recalled chains bypass Tau's
goal, policy, boundary, artifact, and non-claim checks.

## Default Capability Map

The default registry starts with these Tau capabilities:

| Capability | Skill | Tau receipt binding |
| --- | --- | --- |
| `debug_runtime_state` | `debugger` | `tau.debug_session_receipt.v1` |
| `bounded_code_fix` | `code-runner` | `tau.code_patch_receipt.v1` |
| `code_review` | `review-code` | `tau.review_findings.v1` |
| `deep_research` | `dogpile` | `tau.research_source_receipt.v1` |
| `evidence_case` | `create-evidence-case` | `tau.evidence_case_gate_receipt.v1` |
| `model_worker` | `scillm` | `tau.scillm_worker_receipt.v1` |

## Boundaries

Registry validation proves:

- Tau parsed the registry artifact.
- Declared skill names exist under the configured skills root.
- Each capability names a Tau receipt schema.
- Required triggers are from Tau's known course-correction vocabulary.

It does not prove:

- any skill was executed;
- skill output semantic correctness;
- adapter acceptance of native skill artifacts;
- provider/model quality;
- future route correctness.

## Skill Invocation Receipts

The generic invocation wrapper records a bounded skill call or an ingested skill
artifact under Tau context:

```bash
uv run tau skill-invocation \
  --request /tmp/tau-skill-request.json \
  --out /tmp/tau-skill-invocation-receipt.json \
  --repo-root /path/to/repo
```

The request schema is `tau.skill_invocation_request.v1`. The receipt schema is
`tau.skill_invocation_receipt.v1`.

Supported modes:

| Mode | Behavior |
| --- | --- |
| `dry_run` | Record the command and bindings without executing the skill. |
| `execute` | Run the command locally, capture stdout/stderr/exit code, and record bindings. |
| `ingest_existing` | Hash existing repo-contained artifacts without running a command. |

Artifact bindings use `tau.skill_artifact_binding.v1` and fail closed if an
artifact path escapes the configured repo root.

The invocation receipt still does not make a native skill artifact admissible by
itself. Skill-specific adapters must validate debugger, code-runner, review,
evidence-case, research, or model-worker artifacts before a DAG can treat those
outputs as evidence.

## Debugger Adapter

The first skill-specific adapter ingests `debugger.proof.v1` and projects it
into Tau's existing `tau.debug_session_receipt.v1` validator.

```bash
uv run tau debugger-skill-adapter \
  --proof /tmp/debugger-proof.json \
  --out /tmp/debugger-adapter-receipt.json \
  --debug-session-out /tmp/debug-session-receipt.json \
  --repo-root /path/to/repo \
  --goal-hash sha256:...
```

```text
debugger.proof.v1
  -> tau.debugger_skill_adapter_receipt.v1
  -> tau.debug_session_packet.v1
  -> tau.debug_session_receipt.v1
```

The adapter checks goal hash, target command, adapter label, structured
breakpoints/frame/variables, and log artifact path boundaries. If the proof is
missing or malformed, the adapter emits a course-correction payload requiring
debugger evidence before retry.

This still does not prove the bug is fixed or that the debugger conclusion is
semantically complete.

## Code-Runner Adapter

The code-runner adapter ingests `code_runner.result.v1` and requires three
artifact classes before Tau accepts the worker result:

- a patch artifact;
- a deterministic definition-of-done artifact;
- a test or log artifact.

The adapter then dry-run validates the patch through `tau.code_patch_receipt.v1`.
It rejects patches outside the worker allowlist and emits a course-correction
payload when evidence is missing or the worker reports a blocked result.

```bash
uv run tau code-runner-skill-adapter \
  --result /tmp/code-runner-result.json \
  --out /tmp/code-runner-worker-receipt.json \
  --repo-root /path/to/repo \
  --goal-hash sha256:...
```

This still does not apply the patch, prove semantic correctness, or make the
worker model truthful.

## Review-Code Adapter

The review-code adapter ingests `review_code.result.v1`, normalizes advisory
findings into `tau.review_findings.v1`, and validates them through Tau's review
findings gate.

```bash
uv run tau review-code-skill-adapter \
  --review /tmp/review_result.json \
  --out /tmp/review-code-adapter-receipt.json \
  --repo-root /path/to/repo \
  --goal-hash sha256:...
```

The adapter maps review-code verdicts into Tau's `PASS`, `REVISE`, and
`BLOCKED` routing vocabulary. P0/P1 findings still require evidence, a PASS
verdict cannot hide blocking findings, and BLOCKED/REVISE outputs emit
course-correction payloads.

This still does not prove the reviewer is correct, the code is semantically
correct, or reviewer consensus is proof.

## Evidence-Case Adapter

The evidence-case adapter ingests `create_evidence_case.result.v1`, writes a
separate normalized `memory.evidence_case.v1` artifact, and validates it through
Tau's existing `tau.evidence_case_gate_receipt.v1`.

```bash
uv run tau evidence-case-skill-adapter \
  --case /tmp/create-evidence-case-result.json \
  --out /tmp/evidence-case-adapter-receipt.json \
  --repo-root /path/to/repo \
  --goal-hash sha256:... \
  --policy-profile /tmp/policy-profile.json \
  --data-boundary /tmp/data-boundary.json
```

The adapter preserves the separation between intent and evidence: a
create-evidence-case result can become admissible only after Tau writes and
validates the evidence-case gate receipt. Support artifact paths are checked
against the repo root before the adapter passes.

This still does not prove evidence semantic completeness, task closure, Memory
truth, or provider/model quality.

## Research Adapter

The research adapter ingests a Dogpile/Brave/ArXiv-style research artifact only
after Tau has a passing `tau.research_query_safety_receipt.v1`. It converts the
research artifact into `tau.research_source_packet.v1`, then validates it with
`tau.research_source_receipt.v1`.

```bash
uv run tau research-skill-adapter \
  --report /tmp/dogpile-report.json \
  --query-safety /tmp/research-query-safety-receipt.json \
  --out /tmp/research-adapter-receipt.json \
  --repo-root /path/to/repo \
  --method dogpile \
  --source-type web \
  --classification design_input
```

```text
tau.research_query_safety_receipt.v1
  -> dogpile/brave/arxiv/fetcher report artifact
  -> tau.research_skill_adapter_receipt.v1
  -> tau.research_source_packet.v1
  -> tau.research_source_receipt.v1
```

The adapter compares the research report query to the safety receipt hash, hashes
the source report, and marks the resulting research as review-required design
input. It does not call external research services itself.

This still does not prove cited sources are true, the research is closure proof,
or that the research is sufficient for implementation.

## Project Profile Capability Providers

Project profiles can now declare which skill provider must satisfy a Tau
capability:

```json
{
  "schema": "tau.project_profile.v1",
  "project_id": "tau-self-fix",
  "capability_providers": {
    "debug_runtime_state": "debugger",
    "bounded_code_fix": "code-runner",
    "code_review": "review-code",
    "deep_research": "dogpile",
    "evidence_case": "create-evidence-case",
    "model_worker": "scillm"
  },
  "course_correction": {
    "allowed_actions": ["route_reviewer", "run_brave_search_then_retry"],
    "forbid_retry_same_context_after": 2,
    "action_capabilities": {
      "route_reviewer": "code_review",
      "run_brave_search_then_retry": "deep_research"
    }
  }
}
```

```bash
uv run tau project-profile-validate \
  --profile /tmp/project-profile.json \
  --registry /tmp/skill-capability-registry.json \
  --out /tmp/project-profile-validation-receipt.json
```

This is a binding and validation layer only. It does not invoke skills or trust
skill output. Tau still requires invocation receipts, adapter receipts, and
downstream DAG/course-correction validation before a skill result counts.

## Course-Correction Skill Routes

`tau.course_correction.v1` can now include a `skill_routes` block when Tau has a
capability registry or project-profile providers. The block answers which skill
provider may satisfy the required next action.

Examples:

- `debug_or_route_reviewer` maps to `debugger` or `review-code`;
- `route_reviewer` maps to `review-code`;
- `run_brave_search_then_retry` maps to `dogpile`;
- `retry_node` maps to `code-runner` or `scillm`.

Missing or registry-mismatched providers fail closed with
`skill_capability_route_unavailable`. The route map is not execution proof; a
skill still needs a Tau invocation receipt and an adapter receipt before its
artifact is admissible.

## Skill-Composition Red Team

The local red-team suite feeds malicious or under-specified skill artifacts into
Tau wrappers/adapters and requires each one to fail closed:

```python
from pathlib import Path
from tau_coding.skill_composition_redteam import run_skill_composition_redteam

receipt = run_skill_composition_redteam(run_dir=Path("/tmp/tau-skill-redteam"))
```

Operator command:

```bash
uv run tau skill-composition-redteam --run-dir /tmp/tau-skill-redteam
```

The command writes:

```text
/tmp/tau-skill-redteam/skill-composition-redteam-receipt.json
```

Current fixtures cover:

- debugger proof missing goal hash;
- review-code PASS with a blocking finding;
- code-runner patch outside the allowlist;
- Dogpile/research report without query-safety receipt;
- create-evidence-case data-boundary mismatch;
- skill invocation artifact outside the repo;
- mocked skill invocation when high-stakes live execution is required.

The suite proves only deterministic local fail-closed behavior. It does not run
the skills, call providers, or prove semantic correctness.

The suite also has deterministic non-claim checks. It must keep these claims in
`proof_scope.does_not_prove`, and tests fail if they move into
`proof_scope.proves`:

- Live skill execution.
- Provider/model semantic quality.
- Exhaustive skill attack coverage.
- Future route correctness.
- Skill output correctness without Tau adapter validation.

`tau run-status <run-dir>` includes
`tau.skill_composition_redteam_receipt.v1` under `coding_evidence` when the
receipt is present in the run directory. The summary reports the receipt path,
hash, status, and attempt counts without re-running the red-team suite.

## Next Layers

The next composition layers should be implemented as separate slices:

1. Model-worker adapter.
