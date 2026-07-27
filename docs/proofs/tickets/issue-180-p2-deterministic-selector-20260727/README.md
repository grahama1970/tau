# Issue #180 P2 Deterministic Template Selector

This bundle covers the deterministic selector slice for #180.

## Changed Surface

- `tau dag-template-select --facts <json>`

Selection is based on closed typed facts only. The receipt records
`request_hash`, `goal_hash`, `policy_hash`, `capability_hash`, and target
identity. Missing or ambiguous facts return `INTERVIEW_REQUIRED`. Diagnostic
model-confidence fields are explicitly ignored and cannot override selection.

## Proof Commands

```text
uv run python -m py_compile src/tau_coding/dag_template_registry.py src/tau_coding/cli.py
uv run ruff check src/tau_coding/dag_template_registry.py src/tau_coding/cli.py
uv run tau dag-template-select --facts docs/proofs/tickets/issue-180-p2-deterministic-selector-20260727/competition-facts.json
uv run tau dag-template-select --facts docs/proofs/tickets/issue-180-p2-deterministic-selector-20260727/ambiguous-facts.json
jq empty docs/proofs/tickets/issue-180-p2-deterministic-selector-20260727/*.json
```

## Captured Artifacts

- `competition-facts.json`: closed facts for a competition/winner request, with
  a conflicting diagnostic `model_confidence.template: roundtable` field.
- `competition-selection.json`: schema
  `tau.dag_template_selection_receipt.v1`, `status: PASS`,
  `selected_template: compete`, `diagnostic_model_confidence_ignored: true`.
- `ambiguous-facts.json`: closed facts with both `needs_review` and
  `wants_winner`.
- `ambiguous-selection.json`: schema
  `tau.dag_template_selection_receipt.v1`, `status: INTERVIEW_REQUIRED`,
  eligible templates `compete` and `reflection-loop`, no selected template.
- `ambiguous-selection.exitcode`: `1`.

## Evidence Boundary

mocked: no
live: yes for local Tau CLI entrypoint execution
provider_live: no

This slice proves deterministic selection or fail-closed refusal from typed
facts. It does not prove runtime execution, provider/model quality, complete
template params, catalogue UI, viewer inspection, Memory provenance, or the
integrated #180 conformance package.
