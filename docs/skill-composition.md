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

## Next Layers

The next composition layers should be implemented as separate slices:

1. Skill-specific adapters from native skill artifacts into Tau receipts.
2. Project-profile capability provider requirements.
3. Course-correction routing through validated capability providers.
4. A skill-composition red-team suite that proves Tau does not blindly trust
   skill outputs.
