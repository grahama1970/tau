# Issue #180 P3 High-Value Native Templates

This bundle covers the #180 slice adding the five high-value native Tau DAG
templates named by the P0 reconciliation.

## Added Templates

- `plan-execute-verify`
- `claim-chain-verification`
- `specialist-fanout-join`
- `dry-run-human-approval`
- `memory-recalled-workflow`

The templates compile to existing `tau.dag_contract.v1` contracts. No scheduler,
viewer reducer, provider runtime, or external source dependency was added.

## Proof Commands

```text
uv run python -m py_compile src/tau_coding/dag_template_registry.py src/tau_coding/cli.py tests/test_dag_template_registry.py
uv run ruff check src/tau_coding/dag_template_registry.py src/tau_coding/cli.py tests/test_dag_template_registry.py
uv run pytest tests/test_dag_template_registry.py -q
uv run tau dag-template-compile --template <new-template> --params <params.json> --out <dag.json> --receipt <compile-receipt.json>
uv run tau dag-template-preview --template <new-template> --params <params.json>
uv run tau dag-template-select --facts docs/proofs/tickets/issue-180-p3-high-value-templates-20260727/memory-selector-facts.json
jq empty docs/proofs/tickets/issue-180-p3-high-value-templates-20260727/*.json
```

## Observed Fields

```text
plan-execute-verify PASS PASS PLAN_EXECUTE_VERIFY 3 3
claim-chain-verification PASS PASS CLAIM_CHAIN_VERIFICATION 3 3
specialist-fanout-join PASS PASS SPECIALIST_FAN_OUT_JOIN 3 3
dry-run-human-approval PASS PASS DRY_RUN_HUMAN_APPROVAL 2 2
memory-recalled-workflow PASS PASS MEMORY_RECALLED_WORKFLOW 2 2
selector PASS memory-recalled-workflow
```

Each row is:

```text
template compile_status preview_status topology node_count edge_count
```

## Captured Artifacts

For each new template, this directory contains:

- `<template>-params.json`
- `<template>-dag.json`
- `<template>-compile-receipt.json`
- `<template>-compile.stdout.json`
- `<template>-preview.json`

The selector proof also includes:

- `memory-selector-facts.json`
- `memory-selector-receipt.json`

## Evidence Boundary

mocked: no
live: yes for local Tau CLI entrypoint execution
provider_live: no

This slice proves the five named templates are present, compile to valid Tau DAG
contracts, preview without dispatch, and participate in deterministic selection
where applicable. It does not prove provider/model quality, real worker
execution for every template, UI catalogue ergonomics, viewer inspection,
Memory service provenance availability, or integrated #180 conformance.
