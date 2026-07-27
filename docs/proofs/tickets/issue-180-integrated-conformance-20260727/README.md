# Issue #180 Integrated Conformance Snapshot

This bundle reconciles the current #180 fleet after P0-P5 local Tau slices.

## Captured Artifacts

- `open-tau-issues.json`: `$ticket lookup --repo grahama1970/tau --search "state:open" --limit 50`
- `remote-main.txt`: `git ls-remote origin refs/heads/main`
- `catalog.json`: `uv run tau dag-template-catalog`
- `dag-view-capabilities.json`: `uv run tau dag-view-capabilities --json`
- `memory-recall-brief.json`: live Memory `/recall` brief probe

## Observed Summary

```text
open_tau_issues [180, 150, 72]
catalog_templates 10
viewer_read_only True
viewer_memory_provenance DEGRADED_WHEN_MEMORY_CHAIN_UNAVAILABLE
memory_skill_chain True provenance True path False hop False
```

## Local #180 Slice Status

- P0 adoption boundary and foundation reconciliation: recorded at
  `87716a25aea8c0dfd57b8bf67bca89c8b4afbd09`.
- P1 descriptor, validation, and preview surfaces: recorded at
  `fe7d2b42ba33dd9897e5fa801792d4d51b3a362a`.
- P2 deterministic selector: recorded at
  `0e8f08332db84515495e6c3ebb26d4408d3da4f4`.
- P3 five high-value native templates: recorded at
  `46ff4036a4e7579d1f6416ce17b2443b39065afa`.
- P4 catalogue and DagPlan preview metadata: recorded at
  `a3be52b142def15506e76f081d67a9517e169867`.
- P5 viewer inspection capability contract: recorded at
  `29b97df63c44c7a30d36bcc4b21f82ecd7fac1d3`.

## Remaining Blocker

#180 depends on #150 for governed Memory provenance. #150 is still open because
Memory now returns top-level `skill_chain` / `tool_chain` products and
provenance, but does not yet return an inspectable traversal path or hop count.

Remaining upstream blocker:
`https://github.com/grahama1970/graph-memory-operator/issues/62`

## Evidence Boundary

mocked: no
live: yes for local Tau CLI, `$ticket` lookup, Git remote-ref read, and Memory
HTTP recall
provider_live: no

This snapshot proves Tau's local #180 catalogue/template/viewer slices have
current receipts on `main`. It does not prove #180 closure because Memory
path/hop provenance is still absent and #150 remains open.
