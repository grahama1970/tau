# Canonical DAG Launch And Proof

This page is the runnable proof playbook for the canonical five workflow
catalog in `docs/canonical-workflows.md`. The normal developer path is the
`tau workflows` CLI. The older `tau canonical-dags` surface remains a
low-level launch/corpus proof for the same DAG viewer, but it is not the
workflow catalog a new evaluator should start from.

## Normal Path

1. Discover the catalog without reading source files:

   ```bash
   uv run tau workflows list --json
   uv run tau workflows ladder --json
   ```

2. Describe the target workflow and read the exact launch contract:

   ```bash
   uv run tau workflows describe <workflow-id> --json
   ```

3. Launch through the documented control. Use `--open-viewer
   --no-browser-open --viewer-hold-seconds 20` when the proof needs an
   immediate loopback URL in `viewer.url`; omit those flags when the durable
   `tau dag-view --run-dir <run-dir>` deep link is enough.

   ```bash
   uv run tau workflows run <workflow-id> --repo <repo> --run-dir <run-dir> [workflow inputs]
   ```

4. Open progress for the run:

   ```bash
   uv run tau dag-view --run-dir <run-dir>
   ```

5. After completion or a terminal blocker, follow the useful result path under
   `<run-dir>/results/` when a result exists. Use receipts as supporting
   evidence only.

## Launch Commands

Rung 1:

```bash
uv run tau workflows run repository-readiness \
  --repo /path/to/repository \
  --goal "Determine whether this checkout is ready for focused work." \
  --require-clean \
  --run-dir /tmp/tau-repository-readiness \
  --open-viewer --no-browser-open --viewer-hold-seconds 20
```

Rung 2:

```bash
uv run tau workflows run tau-operator-reference \
  --repo /path/to/tau \
  --required-workflow tau-operator-reference \
  --run-dir /tmp/tau-operator-reference \
  --open-viewer --no-browser-open --viewer-hold-seconds 20
```

Rung 3:

```bash
uv run tau workflows run repository-evidence-map \
  --repo /path/to/repository \
  --goal "Map this repository for focused work." \
  --require-tests \
  --run-dir /tmp/tau-repository-evidence-map \
  --open-viewer --no-browser-open --viewer-hold-seconds 20
```

Rung 4:

```bash
uv run tau workflows run approved-release-bundle \
  --repo /path/to/repository \
  --goal "Publish an approved local release bundle." \
  --publish-path /tmp/tau-approved-release-published \
  --run-dir /tmp/tau-approved-release-bundle \
  --open-viewer --no-browser-open --viewer-hold-seconds 20
uv run tau workflows approve /tmp/tau-approved-release-bundle \
  --approval-packet /path/to/human-approval.json
uv run tau workflows resume /tmp/tau-approved-release-bundle
```

Rung 5:

```bash
uv run tau workflows run durable-repository-qualification \
  --repo /path/to/repository \
  --goal "Qualify this repository through durable recovery." \
  --publish-path /tmp/tau-durable-qualified \
  --run-dir /tmp/tau-durable-qualification \
  --open-viewer --no-browser-open --viewer-hold-seconds 20
uv run tau workflows repair /tmp/tau-durable-qualification \
  --node qualify-tests \
  --approval-packet /path/to/repair-approval.json
uv run tau workflows resume /tmp/tau-durable-qualification
uv run tau workflows approve /tmp/tau-durable-qualification \
  --approval-packet /path/to/publication-approval.json
uv run tau workflows resume /tmp/tau-durable-qualification
```

## Readback Checklist

For every launch receipt, verify:

- `schema` is `tau.workflow_run_receipt.v1`;
- `workflow_id` and `workflow_version` match `tau workflows describe`;
- receipt `run_id`, `<run-dir>/current-state.json`, and
  `<run-dir>/workflow/dag.json` all carry the same authoritative `run_id`;
- `run_dir` contains `workflow/dag.json`, `dag-run.sqlite3`,
  `current-state.json`, `run-receipt.json`, and `workflow-receipt.json`;
- `progress_view.command` is `["tau", "dag-view", "--run-dir", "<run-dir>"]`;
- when `--open-viewer` was used, launch stdout first prints `run_id`, `run_dir`,
  and `progress_view`; `progress_view.url` is a loopback HTTP URL and opens the
  same authoritative run;
- successful rungs link to useful output via `result_artifact.path` under
  `<run-dir>/results/`;
- blocked rungs identify an actionable blocker and do not publish a result.

## Missing-Prerequisite Cases

Use these negative checks to prove Tau blocks with actionable preflight or
interview output instead of a traceback or guessed healthy state:

```bash
uv run tau workflows run repository-readiness \
  --repo /path/to/repository \
  --run-dir /tmp/tau-missing-goal
```

Expected blocker: `workflows run missing required option: --goal`.

```bash
uv run tau workflows run repository-evidence-map \
  --repo /path/to/repository-without-tests \
  --goal "Map this repository for focused work." \
  --require-tests \
  --run-dir /tmp/tau-no-tests
```

Expected blocker: `test_surface_missing`; no result directory is published.

```bash
uv run tau workflows run tau-operator-reference \
  --repo /path/to/tau \
  --required-workflow deliberately-absent \
  --run-dir /tmp/tau-missing-required-workflow
```

Expected blocker: `required_workflow_missing`; no operator-reference result is
published.

## Clean-Checkout Proof

The ticket-level proof must be run from a clean checkout or from an installed
wheel so the evaluator does not rely on repository knowledge or local import
state. Use one JSON artifact for the workflow catalog/launch proof and one JSON
artifact for the viewer/browser corpus proof.

Minimum deterministic proof commands:

```bash
uv run python - <<'PY'
import json
from pathlib import Path
from tau_coding.workflows.catalog import dag_ladder_manifest_payload, workflow_catalog_payload

catalog = workflow_catalog_payload()
ladder = dag_ladder_manifest_payload()
ids = [item["workflow_id"] for item in catalog["workflows"]]
assert ids == [
    "repository-readiness",
    "tau-operator-reference",
    "repository-evidence-map",
    "approved-release-bundle",
    "durable-repository-qualification",
]
assert [item["rung"] for item in catalog["workflows"]] == [1, 2, 3, 4, 5]
assert [item["workflow_id"] for item in ladder["rungs"]] == ids
Path("/tmp/tau-workflow-catalog-readback.json").write_text(
    json.dumps({"catalog": catalog, "ladder": ladder}, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
uv run pytest tests/test_workflow_catalog.py tests/test_workflow_cli.py --tau-suite=all -q
```

Minimum live proof commands:

```bash
uv run tau workflows acceptance-proof \
  --repo /home/graham/workspace/experiments/tau \
  --output /tmp/tau-workflows-acceptance-proof.json \
  --provider-url http://127.0.0.1:4001 \
  --model scillm-provider-boundary
uv run tau workflows verify-acceptance-proof \
  /tmp/tau-workflows-acceptance-proof.json \
  --repo /home/graham/workspace/experiments/tau
```

This proves a freshly installed wheel exposes and launches all five packaged
workflows through the public `tau workflows` entry point and records
`mocked: false`, `live: true`, and provider-boundary status in the receipt.

Browser/deep-link proof for the shared viewer:

```bash
uv run python scripts/prove-canonical-dag-viewer-corpus.py \
  --repo /home/graham/workspace/experiments/tau \
  --run-root /tmp/tau-canonical-viewer-runs \
  --receipt /tmp/tau-canonical-viewer-proof.json
```

That proof launches each canonical DAG, starts the packaged read-only viewer,
opens the loopback deep link in desktop and mobile Chromium/Puppeteer
viewports, reads `/api/v1/manifest`, `/api/v1/state`, and `/api/v1/events`,
and retains screenshots plus one JSON browser proof per DAG.

## Required Ticket Coverage

The retained proof bundle must explicitly cover:

| Requirement | Proof source |
| --- | --- |
| List exactly five ordered entries | `tau workflows list --json`, catalog readback, and `tests/test_workflow_catalog.py`. |
| Describe each and compare identity/version/result/topology against registry | `tau workflows describe <id> --json` plus descriptor/ladder comparison. |
| Launch each without editing JSON or searching the repo | `tau workflows acceptance-proof` run commands. |
| Read back run ID and progress deep link | Each workflow run receipt: `goal`, `run_dir`, `run_receipt_path`, and `viewer.command`; `<run-dir>/current-state.json` and `<run-dir>/workflow/dag.json` for `run_id`; `viewer.url` when `--open-viewer` is used. |
| Open each deep link and prove correct run/workflow/goal | Browser/viewer proof reads manifest/state/events for each run and compares `/api/v1/manifest.workflow` with the described canonical descriptor. |
| Missing prerequisite blocker | Negative command such as missing `--goal`, missing tests, or missing required workflow. |
| Final result link points to useful output | Workflow receipt `result` and files under `<run-dir>/results/`, not merely a receipt directory. |
| Desktop and mobile catalog/launch navigation | Browser proof screenshots and checks for desktop and mobile viewer navigation. |
| Retained browser trace and boundaries | Viewer proof JSON plus screenshots; receipt states `mocked` and `live` boundaries. |

## Proof Limits

Passing catalog and viewer navigation proof does not claim that all workflow
outputs are semantically useful to a human, that providers are intelligent, or
that the full `GOAL.md` product is accepted. It proves the narrow #180 P4 child
slice: canonical-five discovery, launch, progress deep links, preflight
blockers, and result-link orientation through existing Tau authority.
