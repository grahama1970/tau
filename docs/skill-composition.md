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

## Next Layers

The next composition layers should be implemented as separate slices:

1. `tau.skill_invocation_receipt.v1` for bounded dry-run, execute, and
   ingest-existing skill calls.
2. Skill-specific adapters from native skill artifacts into Tau receipts.
3. Project-profile capability provider requirements.
4. Course-correction routing through validated capability providers.
5. A skill-composition red-team suite that proves Tau does not blindly trust
   skill outputs.
