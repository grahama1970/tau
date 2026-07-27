# Issue #180 P5 Viewer Inspection Capability Contract

This bundle covers the authority-separated viewer inspection slice for #180.

## Changed Surface

- `tau dag-view-capabilities --json`
- `/api/v1/capabilities` from the DAG viewer server

The capability contract now explicitly names the inspection surfaces already
provided by the read model and viewer:

- diagnostic activity
- artifact workspace
- accepted evidence
- retry/revision overlays
- Memory provenance in degraded mode when Memory chain products are unavailable
- route/join projection
- receipt inspection
- cross-template conformance support

The viewer remains read-only.

## Proof Commands

```text
uv run python -m py_compile src/tau_coding/dag_viewer/contracts.py src/tau_coding/cli.py
uv run ruff check src/tau_coding/dag_viewer/contracts.py src/tau_coding/cli.py
uv run pytest tests/test_dag_viewer_server.py tests/test_cli.py::test_cli_dag_view_capabilities_is_read_only -q
uv run tau dag-view-capabilities --json
jq empty docs/proofs/tickets/issue-180-p5-viewer-inspection-20260727/dag-view-capabilities.json
```

## Observed Fields

```text
read_only True
supports_diagnostic_activity True
supports_artifact_workspace True
supports_accepted_evidence True
supports_retry_revision_overlays True
supports_memory_provenance DEGRADED_WHEN_MEMORY_CHAIN_UNAVAILABLE
supports_cross_template_conformance True
supports_route_join_projection True
supports_receipt_inspection True
authority read-only projection
```

## Captured Artifact

- `dag-view-capabilities.json`

## Evidence Boundary

mocked: no
live: yes for local Tau CLI and local viewer-server tests
provider_live: no

This slice proves the viewer capability contract explicitly exposes the #180
inspection surfaces while preserving read-only authority. It does not prove
Memory service provenance availability, browser screenshot ergonomics, or
integrated #180 conformance.
