# Issue #180 P0 Reconciliation

This proof bundle reconciles the #180 parent fleet against current `main` and
freezes the external adoption boundary before implementation work.

## Current Main

- Remote/main commit used for this reconciliation:
  `a2e608d2d6a25e0c0a96f71cea93df02756df834`
- Worktree used:
  `/tmp/tau-remaining-tickets-20260727.8FsbQ8`
- Parent ticket:
  `https://github.com/grahama1970/tau/issues/180`

## Captured Proof Artifacts

- `dag-template-list.json`: `uv run tau dag-template-list`
- `workflows-list.json`: `uv run tau workflows list --json`
- `dag-view-capabilities.json`: `uv run tau dag-view-capabilities --json`
- `tau-issues-state.json`: `gh issue list --repo grahama1970/tau --state all --limit 250 --json number,title,state,closedAt,updatedAt,labels`
- `docs/adoption/tau-agentic-patterns-adoption-manifest.json`: machine-readable
  external source and adoption boundary manifest

## Foundation Status

The ticket body says #180 builds on #105, #131, #139, #143, #144, #174, and
#179. Current issue-state proof shows all seven are `CLOSED`.

Current product substrate observed from local commands:

- Native template registry exposes five templates:
  `single-call`, `prompt-chain`, `reflection-loop`, `roundtable`, `compete`.
- Workflow catalogue exposes five available canonical workflows in rung order.
- DAG viewer capabilities report `read_only: true`, live/replay support,
  source JSON, receipt inspection, causal explanations, route/join projection,
  attention items, bounded query, and exactly-two comparison.

## External Adoption Boundary

The adoption manifest records two pinned research/design inputs:

- `FareedKhan-dev/all-agentic-architectures`
  at `cf9d620a8cc55d59589399c30f305e6dfaa428ec`,
  MIT license SHA-256
  `3af7509fdf483718aa1b8ab030d8ed639b9e43ae273321d9e7b75ddbf0e07f13`.
- `theaiautomators/agentic-architectures`
  at `eeeff664308e2c7c337b9fe36245cc4780024951`,
  MIT license SHA-256
  `cb65589aa3aba75f701a0827d507cf03dcfc05448978d225e544373dd2ab23d4`.

No external files are copied by this P0 slice. Both sources are recorded as
research inputs only. Any later copy/adaptation must update the manifest with
source file, local destination, transformation notes, and focused proof that Tau
authority boundaries remain intact.

## Missing Behavior Split For Child Work

Do not reopen the completed foundation tickets without a deterministic
regression reproduction. The remaining #180 fleet should be split into these
narrow implementation children:

1. Descriptor contract: add `tau.dag_template_descriptor.v1` metadata over the
   existing registry, with list/describe/validate/preview surfaces and
   `INTERVIEW_REQUIRED` behavior.
2. Deterministic selector: map closed typed input facts and policy/capability
   hashes to eligible templates, with adversarial proof that model confidence
   cannot override deterministic selection.
3. Missing high-value templates: add only `plan-execute-verify`,
   `claim-chain-verification`, `specialist-fanout-join`,
   `dry-run-human-approval`, and `memory-recalled-workflow` unless a child
   proves equivalence already exists.
4. Catalogue and pre-run preview: expose use/avoid guidance, parameters,
   resources, side effects, evidence, immutable goal, source DAG preview,
   compiled `DagPlan`, source-to-plan diff, and interview questions.
5. Authority-separated viewer inspection: add diagnostic activity, artifact
   workspace, accepted evidence, Memory provenance, route/join/retry/revision
   overlays, and cross-template conformance while preserving read-only
   scheduler authority.
6. Memory provenance: consume #150's governed Memory boundary when available,
   show `DEGRADED` when unavailable, and fail closed on empty/invalid chain
   products. This leg remains blocked by
   `grahama1970/graph-memory-operator#61`.
7. Integrated conformance package: retain clean-checkout Python/web proof,
   screenshots, trace, conformance matrix, and mocked/live/provider boundaries.

## Disposition

#180 is not closable from this P0 reconciliation. It is a parent implementation
fleet and closes only after all child issues are closed with their named proof
and the integrated product proof passes.

mocked: no
live: yes for local Tau CLI probes and GitHub issue-state reconciliation
provider_live: no

This P0 slice proves the adoption boundary and current foundation status. It
does not prove any descriptor, selector, new pattern, viewer, Memory provenance,
or conformance implementation.
