# Issue #277 Selected-Node Inspector Proof

## Scope

Ticket: <https://github.com/grahama1970/tau/issues/277>

Tau now serves a backend-generated, versioned, read-only selected-node inspector
projection for DAG viewer nodes. The browser does not join arbitrary receipts
or infer authority locally; it requests `/api/v1/nodes/{node_id}/inspector` and
uses the returned projection only when the run, plan, node, attempt, and journal
sequence still match the selected live state.

## Changed Surfaces

- `src/tau_coding/dag_viewer/http.py`
- `src/tau_coding/dag_viewer/projection.py`
- `src/tau_coding/dag_viewer/server.py`
- `scripts/dag-viewer-browser-proof.mjs`
- `scripts/run-dag-viewer-browser-proof.py`
- `tests/test_dag_viewer_server.py`
- `web/dag-viewer/src/App.tsx`
- `web/dag-viewer/src/api.ts`
- `web/dag-viewer/src/components/SelectedNodeInspector.tsx`
- `web/dag-viewer/src/styles.css`
- `web/dag-viewer/src/tests/App.test.tsx`
- `web/dag-viewer/src/tests/fixtures.ts`
- `web/dag-viewer/src/types.ts`
- packaged static viewer assets under `src/tau_coding/dag_viewer/static/`

## Projection Contract

The endpoint returns schema `tau.selected_node_inspector_projection.v1` with:

- stable identity fields: `run_id`, `plan_id`, `plan_sha256`, `node_id`,
  `attempt`, `attempt_id`, `journal_sequence`, `projection_key`, and
  `projection_sha256`;
- sectioned backend state: Contract, Accepted Inputs, Completion Boundary,
  Review Scope, Workspace Freshness, Worker, Accepted Evidence and Artifacts,
  and Diagnostics;
- optional missing sections reported as `not_available` or `not_enforced`;
- diagnostics marked `authority=diagnostic_only` and `can_settle_node=false`;
- `read_only=true` and `mutation_controls=[]`.

## Deterministic Checks

Command:

```bash
uv run ruff check \
  scripts/run-dag-viewer-browser-proof.py \
  src/tau_coding/dag_viewer/projection.py \
  src/tau_coding/dag_viewer/server.py \
  src/tau_coding/dag_viewer/http.py \
  tests/test_dag_viewer_server.py
```

Readback:

- `All checks passed!`

Command:

```bash
uv run pytest \
  tests/test_dag_viewer_server.py \
  tests/test_dag_viewer_historical.py \
  tests/test_dag_viewer_query.py \
  tests/test_dag_viewer_receipt_index.py \
  tests/test_dag_viewer_static_package.py \
  -q
```

Readback:

- `50 passed in 11.22s`

Command:

```bash
npm run typecheck && npm test -- --run && npm run build
```

Readback:

- TypeScript compile passed.
- Vitest: `6 passed`, `28 passed`.
- Vite built packaged assets:
  - `src/tau_coding/dag_viewer/static/index.html`
  - `src/tau_coding/dag_viewer/static/assets/index-B6QvK191.css`
  - `src/tau_coding/dag_viewer/static/assets/index-CRteFZ9d.js`

Command:

```bash
uv run pytest tests/test_dag_viewer_static_package.py -q
```

Readback:

- `2 passed in 0.43s`

## Live Browser Proof

Desktop command:

```bash
uv run python scripts/run-dag-viewer-browser-proof.py \
  --run-root /tmp/tau-issue277-selected-node-inspector-20260731T0507Z/run-root \
  --out /tmp/tau-issue277-selected-node-inspector-20260731T0507Z/browser-proof.json \
  --screenshot /tmp/tau-issue277-selected-node-inspector-20260731T0507Z/selected-node-inspector.png \
  --step-delay-seconds 0.35
```

Durable readback:

- Receipt: `docs/proofs/issue-277-selected-node-inspector/desktop-browser-proof.json`
- Screenshot: `docs/proofs/issue-277-selected-node-inspector/desktop-selected-node-inspector.png`
- `status`: `PASS`
- `mocked`: `false`
- `live`: `true`
- `request_methods`: `["GET"]`
- viewport: `1440x1000`
- screenshot sha256:
  `sha256:1df46c1efcbbd18641ebfa2b0d3628a8e0e9b2a05b88dfd3ca70629706c8e683`
- selected-node checks:
  `selected_node_inspector_visible=true`,
  `selected_node_sections_visible=true`,
  `selected_node_read_only=true`,
  `selected_node_no_mutation_controls=true`

Narrow command:

```bash
TAU_DAG_VIEWPORT_WIDTH=760 TAU_DAG_VIEWPORT_HEIGHT=1100 \
uv run python scripts/run-dag-viewer-browser-proof.py \
  --run-root /tmp/tau-issue277-selected-node-inspector-mobile-20260731T0508Z/run-root \
  --out /tmp/tau-issue277-selected-node-inspector-mobile-20260731T0508Z/browser-proof.json \
  --screenshot /tmp/tau-issue277-selected-node-inspector-mobile-20260731T0508Z/selected-node-inspector.png \
  --step-delay-seconds 0.35
```

Durable readback:

- Receipt: `docs/proofs/issue-277-selected-node-inspector/narrow-browser-proof.json`
- Screenshot: `docs/proofs/issue-277-selected-node-inspector/narrow-selected-node-inspector.png`
- `status`: `PASS`
- `mocked`: `false`
- `live`: `true`
- `request_methods`: `["GET"]`
- viewport: `760x1100`
- screenshot sha256:
  `sha256:ce7a03893c66c7f84421cddf9ae83f08c1de45fd982d5154aa89829eea877d1f`
- selected-node checks:
  `selected_node_inspector_visible=true`,
  `selected_node_sections_visible=true`,
  `selected_node_read_only=true`,
  `selected_node_no_mutation_controls=true`

Visual inspection confirmed the desktop and narrow screenshots show the Node
tab open for `creator-reviewer`, with wrapped attention/copy controls inside
the inspector pane and no visible mutation controls.

## Proof Boundaries

This proves the local DAG viewer can expose backend-projected selected-node
state for a real local Tau DAG through a real browser. It does not prove
provider/model semantic quality, remote multi-user authorization policy, or a
full production deployment.
