# Canonical Workflow Catalog

Tau exposes five packaged DAG workflows as the canonical developer ladder. This
is the workflow-first entry point for a new evaluator: list the catalog,
describe a workflow, launch it with normal CLI inputs, and open the same DAG
viewer used for generic Tau runs.

The authoritative workflow identity comes from
`src/tau_coding/workflows/definitions/*.json` through
`tau_coding.workflows.catalog`. The useful-output and proof boundaries come
from `src/tau_coding/workflows/definitions/ladder-manifest.json`. This document
is an operator map over those descriptors, not a second registry.

## Discovery Commands

List exactly the canonical ladder, ordered by rung:

```bash
uv run tau workflows list
uv run tau workflows list --json
uv run tau workflows ladder --json
```

Describe one workflow before launching it:

```bash
uv run tau workflows describe repository-readiness --json
uv run tau workflows describe tau-operator-reference --json
uv run tau workflows describe repository-evidence-map --json
uv run tau workflows describe approved-release-bundle --json
uv run tau workflows describe durable-repository-qualification --json
```

The JSON description is the CLI-visible descriptor: `workflow_id`,
`workflow_version`, `rung`, `title`, `summary`, `topology`, `input_schema`,
`result_schema`, `result_node_id`, package-relative `template`, `runtime`, and
`proof_boundary`. The ladder manifest binds those same workflow IDs and
topologies to useful result paths and acceptance boundaries.

## Launch Contract

All five workflows launch through one control:

```bash
uv run tau workflows run <workflow-id> --repo <repo> --run-dir <run-dir> [workflow inputs]
```

Common inputs:

| Input | Required for | Meaning |
| --- | --- | --- |
| `--repo <repo>` | all workflows | Local Git checkout to inspect or use as Tau source. |
| `--run-dir <run-dir>` | all workflows | Empty/new run directory where Tau writes the materialized DAG, journal, receipts, and results. |
| `--goal "<text>"` | all except `tau-operator-reference` | Human-owned goal text bound into the materialized DAG goal hash. |
| `--required-workflow <id>` | `tau-operator-reference` | Workflow that must appear in the captured catalog; defaults to `tau-operator-reference`. |
| `--require-clean` | `repository-readiness` | Block readiness publication when the target Git checkout is dirty. |
| `--require-tests` | `repository-evidence-map` | Block evidence-map publication when the target checkout has no tracked tests. |
| `--publish-path <path>` | rungs 4 and 5 | External publication destination; must be outside the inspected repository and must not already exist. |
| `--open-viewer --no-browser-open --viewer-hold-seconds <seconds>` | optional for all | Start the loopback viewer immediately, print its URL in the run receipt, and keep it alive for browser proof. |

Launch output is a JSON `tau.workflow_run_receipt.v1` object. It includes the
`workflow_id`, `workflow_version`, `run_id`, `goal`, `source_dag_path`,
`run_dir`, `run_receipt_path`, `result`, `result_artifact`, `progress_view`,
and `viewer`. The run ID is not inferred from the directory name: read `run_id`
from the launch receipt, `<run-dir>/current-state.json`, or the materialized
source DAG at `source_dag_path`. The stable progress deep link is:

```bash
uv run tau dag-view --run-dir <run-dir>
```

When `--open-viewer` is used, `run_id`, `run_dir`, and `progress_view` are
printed immediately before the final JSON receipt; `progress_view.url` is a
loopback browser URL for the active viewer process. The URL is intentionally
ephemeral; the durable link is the `tau dag-view --run-dir` command because it
reopens the authoritative SQLite journal from the run directory.

Missing prerequisites are preflight/interview blockers. Tau must report the
specific missing input or invalid path and must not infer that a prerequisite is
healthy. Examples include a missing `--goal`, a non-directory `--repo`, an
existing `--run-dir`, an in-repository or existing `--publish-path`, missing Tau
operator source files, missing local `tau` entry point for operator-reference
CLI probes, and workflow-specific fail-closed blockers such as
`dirty_repository`, `test_surface_missing`, `required_workflow_missing`,
`approval_packet_required`, and `targeted_repair_required`.

## Canonical Ladder

| Rung | Workflow/version | Title | Topology summary | Useful result |
| --- | --- | --- | --- | --- |
| 1 | `repository-readiness` v1 | Repository Readiness | `LINEAR`, 3 nodes: inspect, validate, publish | Hash-bound readiness report for one local Git checkout. |
| 2 | `tau-operator-reference` v1 | Tau Operator Reference | `MULTI_STEP_SEQUENTIAL`, 4 nodes: collect sources, capture CLI, compose, validate | Validated Tau operator reference from fixed local source files and public CLI probes. |
| 3 | `repository-evidence-map` v1 | Repository Evidence Map | `FAN_OUT_FAN_IN`, 5 nodes: inventory, documentation/tests/package branches, publish join | Validated evidence map joining repository docs, tests, and package metadata. |
| 4 | `approved-release-bundle` v1 | Approved Release Bundle | `MIXED_RETRY_APPROVAL`, 7 nodes: prepare, concurrent release work, assemble, approval-gated publish, finalize | Rollback-protected release bundle after exact human approval. |
| 5 | `durable-repository-qualification` v1 | Durable Repository Qualification | `DURABLE_MIXED_REPAIR_APPROVAL`, 7 nodes: capture, concurrent qualification branches, repair/approval-gated publish, finalize | Idempotent repository qualification result after durable recovery and exact approval. |

## Rung 1: Repository Readiness

| Field | Value |
| --- | --- |
| Workflow | `repository-readiness` version 1 |
| Registry title | Repository Readiness |
| Useful result | `results/repository-readiness.json` and `results/repository-readiness.md` report whether the requested checkout is ready under the requested clean-worktree policy. |
| Topology/node count | `LINEAR`; 3 nodes: `inspect-repository`, `validate-readiness`, `publish-readiness`. |
| Required inputs | `--repo`, `--run-dir`, `--goal`; optional `--require-clean`. |
| Completion criteria | Inspect the exact requested Git repository without mutation; apply the clean-worktree policy; publish only after validation passes. |
| Runtime/provider prerequisites | Local filesystem and Git checkout; no network, provider, or repository mutation. |
| Human approval requirements | None. The human-owned `--goal` is hashed into the run, but no approval packet is required. |
| Side-effect/rollback boundary | Writes only under `--run-dir`; inspected repository is read-only. No publication side effect. |
| Expected repair/recovery behavior | Dirty checkout with `--require-clean` blocks at `validate-readiness` with `dirty_repository` and publishes no result. |
| Launch command/control | `uv run tau workflows run repository-readiness --repo <repo> --goal "Determine whether this checkout is ready for focused work." --require-clean --run-dir <run-dir> --open-viewer --no-browser-open --viewer-hold-seconds 20` |
| Progress-view deep link pattern | `uv run tau dag-view --run-dir <run-dir>`; with `--open-viewer`, read `viewer.url` from launch output. |
| Final-result location/type | JSON and Markdown readiness reports under `<run-dir>/results/`; supporting evidence under `<run-dir>/receipts/` is secondary. |
| Proof boundaries/known limitations | Proves local non-mocked workflow execution and result publication. Does not prove provider/model quality, deployment readiness, or human acceptance of `GOAL.md`. |

## Rung 2: Tau Operator Reference

| Field | Value |
| --- | --- |
| Workflow | `tau-operator-reference` version 1 |
| Registry title | Tau Operator Reference |
| Useful result | `results/tau-operator-reference.json` and `results/tau-operator-reference.md` explain the installed Tau operator controls from fixed source files and public CLI probes. |
| Topology/node count | `MULTI_STEP_SEQUENTIAL`; 4 nodes: `collect-operator-sources`, `capture-operator-cli`, `compose-operator-reference`, `validate-operator-reference`. |
| Required inputs | `--repo`, `--run-dir`; optional `--required-workflow`, defaulting to `tau-operator-reference`. |
| Completion criteria | Expose the workflow through the packaged catalog; execute the four nodes sequentially; read fixed Tau source files; capture `tau workflows list --json`, `tau workflows run --help`, and `tau dag-view-capabilities --json`; publish only after validator recomputation. |
| Runtime/provider prerequisites | Local Tau source checkout containing `pyproject.toml`, `README.md`, `docs/getting-started.md`, `docs/live-dag-viewer.md`, and `docs/generic-dag-runner.md`; local `tau` executable on `PATH` or beside the running interpreter; no network or provider. |
| Human approval requirements | None. |
| Side-effect/rollback boundary | Writes only under `--run-dir`; source checkout is read-only. Draft JSON/Markdown stay under `intermediate/` until validation publishes final results. |
| Expected repair/recovery behavior | `--required-workflow deliberately-absent` blocks validation with `required_workflow_missing` and publishes no result. Missing source files or missing `tau` entry point fail preflight with actionable messages. |
| Launch command/control | `uv run tau workflows run tau-operator-reference --repo <tau-checkout> --required-workflow tau-operator-reference --run-dir <run-dir> --open-viewer --no-browser-open --viewer-hold-seconds 20` |
| Progress-view deep link pattern | `uv run tau dag-view --run-dir <run-dir>`; with `--open-viewer`, read `viewer.url` from launch output. |
| Final-result location/type | JSON and Markdown operator reference under `<run-dir>/results/`; CLI/source probe receipts remain supporting evidence. |
| Proof boundaries/known limitations | Proves the fixed local source/probe path and catalog readback. Does not prove provider/model quality or that operator prose is accepted by a human. |

## Rung 3: Repository Evidence Map

| Field | Value |
| --- | --- |
| Workflow | `repository-evidence-map` version 1 |
| Registry title | Repository Evidence Map |
| Useful result | `results/repository-evidence-map.json` and `results/repository-evidence-map.md` summarize repository documentation, test, and package evidence against one inventory hash. |
| Topology/node count | `FAN_OUT_FAN_IN`; 5 nodes: `inventory-repository`, `analyze-documentation`, `analyze-tests`, `analyze-package`, `publish-evidence-map`. |
| Required inputs | `--repo`, `--run-dir`, `--goal`; optional `--require-tests`. |
| Completion criteria | Inventory the exact requested Git repository without mutation; analyze documentation, tests, and package metadata concurrently; publish only after every required branch is accepted. |
| Runtime/provider prerequisites | Local Git checkout; no network, provider, or repository mutation. |
| Human approval requirements | None. |
| Side-effect/rollback boundary | Writes only under `--run-dir`; result directory is atomically published after the join validates accepted branch outputs. |
| Expected repair/recovery behavior | With `--require-tests`, a checkout with no tracked tests blocks `analyze-tests` with `test_surface_missing`; the publisher is not dispatched and no result exists. Missing branch artifacts block the join. |
| Launch command/control | `uv run tau workflows run repository-evidence-map --repo <repo> --goal "Map this repository for focused work." --require-tests --run-dir <run-dir> --open-viewer --no-browser-open --viewer-hold-seconds 20` |
| Progress-view deep link pattern | `uv run tau dag-view --run-dir <run-dir>`; with `--open-viewer`, read `viewer.url` from launch output. |
| Final-result location/type | JSON and Markdown evidence map under `<run-dir>/results/`; receipts are supporting branch evidence, not the product result. |
| Proof boundaries/known limitations | Proves local concurrent branch/join workflow behavior and result publication. Does not prove provider/model quality or repository production readiness. |

## Rung 4: Approved Release Bundle

| Field | Value |
| --- | --- |
| Workflow | `approved-release-bundle` version 1 |
| Registry title | Approved Release Bundle |
| Useful result | `results/approved-release-bundle.json` and `results/approved-release-bundle.md`, copied to `--publish-path` only after exact approval and post-write verification. |
| Topology/node count | `MIXED_RETRY_APPROVAL`; 7 nodes: `prepare-release`, `draft-release-notes`, `build-release-manifest`, `verify-release-policy`, `assemble-release-bundle`, `publish-approved-release`, `finalize-approved-release`. |
| Required inputs | `--repo`, `--run-dir`, `--goal`, `--publish-path`. |
| Completion criteria | Prepare the repository without mutation; accept revised release notes, release manifest, and release policy; stop before publication until the exact accepted bundle is approved; publish one hash-bound bundle with rollback on failed verification. |
| Runtime/provider prerequisites | Local Git checkout; `--publish-path` must be outside the inspected repo and must not already exist; no network or provider. |
| Human approval requirements | Required before publication. Use `uv run tau workflows approve <run-dir> --approval-packet <approval.json>` and then `uv run tau workflows resume <run-dir>`. |
| Side-effect/rollback boundary | Pre-approval writes stay under `--run-dir`; publication writes to `--publish-path` once approved. Failed publication verification removes the published target and records `receipts/publication-rollback.json`. |
| Expected repair/recovery behavior | Initial run blocks at the approval-gated `publish-approved-release` transaction with `approval_packet_required`; resume after approval preserves accepted work and performs the continuation once. |
| Launch command/control | `uv run tau workflows run approved-release-bundle --repo <repo> --goal "Publish an approved local release bundle." --publish-path <outside-repo-output> --run-dir <run-dir> --open-viewer --no-browser-open --viewer-hold-seconds 20` |
| Progress-view deep link pattern | `uv run tau dag-view --run-dir <run-dir>`; with `--open-viewer`, read `viewer.url` from launch output. |
| Final-result location/type | JSON and Markdown approved release bundle under `<run-dir>/results/` and copied to `--publish-path`; receipts and rollback ledger are supporting evidence. |
| Proof boundaries/known limitations | Proves exact approval gating and local side-effect boundary for the packaged workflow. Does not prove real deployment readiness or human acceptance of release content. |

## Rung 5: Durable Repository Qualification

| Field | Value |
| --- | --- |
| Workflow | `durable-repository-qualification` version 1 |
| Registry title | Durable Repository Qualification |
| Useful result | `results/durable-repository-qualification.json` and `results/durable-repository-qualification.md`, copied to `--publish-path` after durable repair/recovery and exact approval. |
| Topology/node count | `DURABLE_MIXED_REPAIR_APPROVAL`; 7 nodes: `capture-repository`, `qualify-documentation`, `qualify-tests`, `qualify-package`, `reconcile-qualification`, `publish-qualification`, `finalize-qualification`. |
| Required inputs | `--repo`, `--run-dir`, `--goal`, `--publish-path`; diagnostic proof may use `--inject-test-branch-failure`. |
| Completion criteria | Capture the exact repository without mutation; qualify documentation, tests, and package metadata concurrently; repair only a blocked qualification branch while preserving accepted work; publish one idempotent result after exact human approval. |
| Runtime/provider prerequisites | Local Git checkout; `--publish-path` must be outside the inspected repo and must not already exist; no network or provider. |
| Human approval requirements | Required for targeted repair when `qualify-tests` is blocked and required again for publication. Use `uv run tau workflows repair <run-dir> --node qualify-tests --approval-packet <approval.json>`, `uv run tau workflows resume <run-dir>`, `uv run tau workflows approve <run-dir> --approval-packet <approval.json>`, then `uv run tau workflows resume <run-dir>`. |
| Side-effect/rollback boundary | Accepted branch work is preserved under `--run-dir`; only the approved publication writes to `--publish-path`; `receipts/qualification-publication-ledger.json` proves the side effect is recorded once. |
| Expected repair/recovery behavior | Injected test-branch failure blocks downstream work with `targeted_repair_required`; approved repair is goal/request-bound; resume reuses accepted branches and avoids duplicate publication. |
| Launch command/control | `uv run tau workflows run durable-repository-qualification --repo <repo> --goal "Qualify this repository through durable recovery." --publish-path <outside-repo-output> --run-dir <run-dir> --open-viewer --no-browser-open --viewer-hold-seconds 20` |
| Progress-view deep link pattern | `uv run tau dag-view --run-dir <run-dir>`; with `--open-viewer`, read `viewer.url` from launch output. |
| Final-result location/type | JSON and Markdown qualification reports under `<run-dir>/results/` and copied to `--publish-path`; receipts and publication ledger are supporting evidence. |
| Proof boundaries/known limitations | Proves durable local workflow mechanics, targeted repair boundary, and exact-once publication evidence when the full proof path runs. Does not prove provider/model quality or full product acceptance. |

## Viewer Catalog Entry Point

The viewer entry point is the same for every canonical workflow:

```bash
uv run tau dag-view --run-dir <run-dir>
```

The viewer reads the authoritative `dag-run.sqlite3`, `workflow/dag.json`,
`current-state.json`, receipts, and workflow result from that run directory. It
does not maintain a separate workflow list, dispatch work, approve gates, edit
DAGs, or infer state from browser text. The catalog-to-viewer identity check is:

1. `tau workflows describe <id> --json` reports `workflow_id`,
   `workflow_version`, `topology`, `result_schema`, and `result_node_id`.
2. `tau workflows run <id> ...` prints a workflow receipt with the same
   `workflow_id` and `workflow_version`, plus the run `goal` and `run_dir`.
3. Read `<run-dir>/current-state.json` or `source_dag_path` for the authoritative
   `run_id`.
4. `tau dag-view --run-dir <run-dir>` opens the viewer for that run directory.
5. The viewer API and UI show the materialized source DAG and current run state
   for the same `run_id`, goal hash, workflow ID, and workflow version.

The run-specific viewer landing uses the workflow metadata embedded in the
materialized DAG extension. For pre-run discovery, the catalog landing is
`tau workflows list` / `tau workflows describe` and the TUI workflow selector;
after launch, `/api/v1/manifest.workflow` is the browser-side readback that the
viewer is showing the same selected canonical descriptor rather than a
browser-maintained second list.

Completion links should lead first to `result_artifact.path`, the useful output
under `<run-dir>/results/`. Receipts under `<run-dir>/receipts/`,
`run-receipt.json`, and `workflow-receipt.json` are supporting evidence and
should not be presented as the product result.

## Proof Boundary

The catalog path is live and non-mocked when exercised through the public Tau
entry point against a real local checkout:

- `mocked: false`
- `live: true`
- `provider_live: false` for the five packaged workflows unless a separate
  provider-readiness proof is being run

This catalog proves discoverability, launch controls, authoritative identity
continuity, preflight/blocker reporting, viewer deep links, and result-link
orientation. It does not prove provider/model semantic quality, production
deployment readiness, or human acceptance of the full `GOAL.md` outcome.
