# Issue #180 P1 Template Descriptor Surfaces

This bundle covers the first implementation slice after the #180 P0
reconciliation: native Tau DAG template descriptor, validation, and pre-run
preview surfaces.

## Changed Surface

- `tau dag-template-describe --template <name>`
- `tau dag-template-validate --template <name> --params <json>`
- `tau dag-template-preview --template <name> --params <json>`

These commands are non-executing. They do not dispatch workers, providers, or
the scheduler. They expose existing native templates through machine-readable
metadata and return `INTERVIEW_REQUIRED` when typed required fields are missing.

## Proof Commands

```text
uv run python -m py_compile src/tau_coding/dag_template_registry.py src/tau_coding/cli.py
uv run ruff check src/tau_coding/dag_template_registry.py src/tau_coding/cli.py
uv run tau dag-template-describe --template compete
uv run tau dag-template-validate --template compete --params docs/proofs/tickets/issue-131-dag-template-registry-20260726/compete-params.json
uv run tau dag-template-preview --template compete --params docs/proofs/tickets/issue-131-dag-template-registry-20260726/compete-params.json
uv run tau dag-template-preview --template roundtable --params docs/proofs/tickets/issue-131-dag-template-registry-20260726/roundtable-missing-params.json
jq empty docs/proofs/tickets/issue-180-p1-template-descriptors-20260727/*.json
```

## Captured Artifacts

- `compete-describe.json`: descriptor schema
  `tau.dag_template_descriptor.v1`
- `compete-validate.json`: validation receipt schema
  `tau.dag_template_validation_receipt.v1`, `status: PASS`
- `compete-preview.json`: preview schema `tau.dag_template_preview.v1`,
  `status: PASS`
- `roundtable-missing-preview.json`: preview schema
  `tau.dag_template_preview.v1`, `status: INTERVIEW_REQUIRED`,
  `missing_fields: ["handlers[1]", "join"]`
- `roundtable-missing-preview.exitcode`: `1`

## Evidence Boundary

mocked: no
live: yes for local Tau CLI entrypoint execution
provider_live: no

This slice proves Tau has descriptor, validation, and preview surfaces for the
existing native templates, with fail-closed incomplete-input behavior. It does
not prove runtime execution, provider/model quality, human acceptance of a
preview, deterministic selection among templates, or the remaining #180 viewer
and Memory provenance work.
