# Issue #180 P4 Catalogue And Pre-Run Preview

This bundle covers the catalogue/pre-run preview slice for #180.

## Changed Surface

- `tau dag-template-catalog`
- `tau dag-template-preview --template <name> --params <json>` now includes a
  compiled `tau.dag_plan.v1` summary and source-to-plan preservation checks.

The catalogue exposes full descriptors, use/avoid guidance, required
parameters, resources, side effects, evidence contracts, and authority
boundaries. Preview remains non-executing.

## Proof Commands

```text
uv run python -m py_compile src/tau_coding/dag_template_registry.py src/tau_coding/cli.py tests/test_dag_template_registry.py
uv run ruff check src/tau_coding/dag_template_registry.py src/tau_coding/cli.py tests/test_dag_template_registry.py
uv run pytest tests/test_dag_template_registry.py -q
uv run tau dag-template-catalog
uv run tau dag-template-preview --template plan-execute-verify --params docs/proofs/tickets/issue-180-p3-high-value-templates-20260727/plan-execute-verify-params.json
uv run tau dag-template-preview --template roundtable --params docs/proofs/tickets/issue-131-dag-template-registry-20260726/roundtable-missing-params.json
jq empty docs/proofs/tickets/issue-180-p4-catalog-preview-20260727/*.json
```

## Observed Fields

```text
catalog tau.dag_template_catalog.v1 10 tau.dag_template_descriptor.v1
preview tau.dag_template_preview.v1 PASS tau.dag_plan.v1 True True
missing INTERVIEW_REQUIRED ['handlers[1]', 'join'] 1
```

The preview row confirms:

- preview schema is `tau.dag_template_preview.v1`
- status is `PASS`
- compiled plan summary schema is `tau.dag_plan.v1`
- source entry node is preserved in the plan
- required evidence is preserved in the plan

## Captured Artifacts

- `catalog.json`
- `plan-execute-verify-preview.json`
- `roundtable-missing-preview.json`
- `roundtable-missing-preview.exitcode`

## Evidence Boundary

mocked: no
live: yes for local Tau CLI entrypoint execution
provider_live: no

This slice proves the catalogue and pre-run preview data surfaces exist and fail
closed when parameters are incomplete. It does not prove viewer UI ergonomics,
live worker execution, provider/model quality, Memory service provenance
availability, or integrated #180 conformance.
