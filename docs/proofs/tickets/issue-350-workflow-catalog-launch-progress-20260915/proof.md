# tau#350 clean-checkout workflow catalog, launch, and progress deep-link proof

Lease: `20260915T153347Z-codex-worker-tau-350-20260915-350` (codex-worker-tau-350-20260915).
Machine trace: `trace.json` in this directory (built by re-running every assertion; builder exits non-zero on any failure).

## Provenance

- Clean shallow clone: `git clone --depth 1 https://github.com/grahama1970/tau /tmp/tau-350-clean`
- `git rev-parse HEAD` = `f9ff1158d0fd4b9816252cec09b4c8bcc72f581e` (= origin/main at proof time); `git status --porcelain` empty.
- Environment: `uv sync` in the clone; every command below is `uv run tau ...` from the clone root. No local import state, no edited JSON, no source reading for inputs — launch inputs came from `docs/canonical-dag-launch.md` and `tau workflows run --help`.

## Step 1 — discover: exactly five ordered rungs

`uv run tau workflows list`:

```text
rung 1	repository-readiness	LINEAR	Repository Readiness
rung 2	tau-operator-reference	MULTI_STEP_SEQUENTIAL	Tau Operator Reference
rung 3	repository-evidence-map	FAN_OUT_FAN_IN	Repository Evidence Map
rung 4	approved-release-bundle	MIXED_RETRY_APPROVAL	Approved Release Bundle
rung 5	durable-repository-qualification	DURABLE_MIXED_REPAIR_APPROVAL	Durable Repository Qualification
```

`tau workflows list --json` asserted: ids `[repository-readiness, tau-operator-reference, repository-evidence-map, approved-release-bundle, durable-repository-qualification]`, rungs `[1,2,3,4,5]`. The docs' catalog readback (`workflow_catalog_payload()` + `dag_ladder_manifest_payload()`) agrees.

## Step 2 — describe matches the authoritative registry

For each of the five: `uv run tau workflows describe <id> --json` compared against
`src/tau_coding/workflows/definitions/<id>.json` and `ladder-manifest.json`:
`workflow_id`, `workflow_version` (1), `rung`, `title`, `topology`, `result_node_id` all equal;
template node counts equal the canonical ladder table (3/4/5/7/7).

## Step 3 — launch all five through the one documented control

`uv run tau workflows run <workflow-id> ... --open-viewer --no-browser-open --viewer-hold-seconds 150`
against the clean clone itself (`--repo /tmp/tau-350-clean`), publish paths under `/tmp/tau350-proof/`.
No JSON hand-editing at any point. Each launch printed `run_id:`, `run_dir:`, and
`progress_view: <loopback URL>` immediately, then a final `tau.workflow_run_receipt.v1`.

| Rung | run_id | progress_view | Final state |
| --- | --- | --- | --- |
| 1 | `repository-readiness-7d0ddc00e604` | `http://127.0.0.1:44499/` | PASS, `results/repository-readiness.{json,md}` published |
| 2 | `tau-operator-reference-19fb46c220ed` | `http://127.0.0.1:32951/` | PASS, `results/tau-operator-reference.{json,md}` published |
| 3 | `repository-evidence-map-300023a18bdb` | `http://127.0.0.1:45615/` | PASS, `results/repository-evidence-map.{json,md}` published |
| 4 | `approved-release-bundle-58002a3dc695` | `http://127.0.0.1:35307/` | BLOCKED `APPROVAL_REQUIRED` (documented approval gate; no result published) |
| 5 | `durable-repository-qualification-c06e1c65e623` | `http://127.0.0.1:42799/` | BLOCKED `APPROVAL_REQUIRED` (documented approval gate; no result published) |

Every receipt's `progress_view.command` is `["tau", "dag-view", "--run-dir", "<run-dir>"]`, and `result_artifact.path` (rungs 1–3) points under `<run-dir>/results/` — the useful output, not a receipt directory.

## Step 4 — each deep link resolves the authoritative run/workflow/goal

During each viewer hold window: `curl <progress_view>/api/v1/manifest` and `/api/v1/state`.
Asserted per rung: `run_id` identical across launch stdout, viewer manifest, viewer state,
`<run-dir>/current-state.json`, and `<run-dir>/workflow/dag.json`; manifest `workflow`
`workflow_id`/`workflow_version`/`topology` equal the described registry descriptor;
manifest goal `summary` and `goal_hash` equal the materialized DAG goal.

## Step 5 — missing-prerequisite negatives (actionable blockers, no traceback)

1. Missing `--goal` on `repository-readiness`: exit 2,
   `workflows run missing required option: --goal (required by workflow repository-readiness)`;
   no run dir created.
2. `--require-tests` against a repo with no tracked tests: exit 1, receipt `status=BLOCKED`,
   `receipts/analyze-tests.json` `errors=["test_surface_missing"]`, `verdict=BLOCKED`;
   no `results/` directory published.

## Deterministic suite

`uv run pytest tests/test_workflow_catalog.py tests/test_workflow_cli.py --tau-suite=all -q` → `10 passed`.

Note: `docs/canonical-dag-launch.md` previously listed this command without `--tau-suite=all`;
on a clean checkout the default `contract` suite deselects all 10 tests and exits 0 (false pass).
Fixed in this proof bundle (one line) and re-verified from a fresh clone.

## Proof boundaries

- `mocked: false`, `live: true`, `provider_live: false`.
- Live: CLI against real local Git checkouts; scheduler executed all 23 nodes across the five
  rungs; loopback HTTP viewer read via curl during each hold window; SQLite journal, receipts,
  and results written under each run dir.
- Not exercised here: provider/model calls (the five packaged workflows are provider-free),
  desktop/mobile browser UI screenshots (owned by the viewer live-state tickets; API-level
  deep-link resolution proven above), and post-approval resume paths for rungs 4–5
  (runs stop at the documented approval gate).
