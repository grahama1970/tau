# Issue 180 Browser Proof Refresh

Date: 2026-07-27

This bundle records a live, non-mocked DAG viewer proof for the creator/reviewer
bounded-transaction scenario after the memory path/hop fix.

## Scope

Proves:

- DAG viewer renders the execution graph.
- Source DAG and compiled DAG plan are inspectable from the TUI/web viewer.
- A reviewer `REVISE` result is visible before a second attempt.
- A later producer/reviewer `PASS` claim is not accepted until the receipt
  admission turns green.
- A dependent node is released after acceptance.
- Browser refresh reconstructs scheduler/admission state.
- The viewer makes read-only HTTP requests.
- Desktop and mobile layouts render without graph/inspector/timeline overlap.

Does not prove:

- Provider semantic correctness.
- Runtime text proves node completion.
- Source DAG correctness for future route behavior.
- Full immutable goal acceptance; issue #180 remains the active integration
  ticket until the complete product checklist is accepted.

## Live Browser Runs

Desktop:

```bash
PYTHONPATH=src uv run python scripts/run-dag-viewer-browser-proof.py \
  --out docs/proofs/tickets/issue-180-browser-proof-20260727/browser-desktop-proof.json \
  --screenshot docs/proofs/tickets/issue-180-browser-proof-20260727/browser-desktop-proof.png \
  --run-root docs/proofs/tickets/issue-180-browser-proof-20260727/run-desktop \
  --step-delay-seconds 0.25
```

Result: `status=PASS`, `mocked=false`, `live=true`, viewport `1440x1000`.

Mobile:

```bash
TAU_DAG_VIEWPORT_WIDTH=430 TAU_DAG_VIEWPORT_HEIGHT=900 TAU_DAG_DEVICE_SCALE_FACTOR=1 \
PYTHONPATH=src uv run python scripts/run-dag-viewer-browser-proof.py \
  --out docs/proofs/tickets/issue-180-browser-proof-20260727/browser-mobile-proof.json \
  --screenshot docs/proofs/tickets/issue-180-browser-proof-20260727/browser-mobile-proof.png \
  --run-root docs/proofs/tickets/issue-180-browser-proof-20260727/run-mobile \
  --step-delay-seconds 0.25
```

Result: `status=PASS`, `mocked=false`, `live=true`, viewport `430x900`.

The generated screenshots were visually inspected:

- `browser-desktop-proof.png`: graph, bounded transaction attempts, inspector,
  compare panel, and event timeline are visible without overlap.
- `browser-mobile-proof.png`: stacked mobile graph, transaction attempts,
  inspector, proof boundary, compare panel, and event timeline are visible
  without overlap.

## Focused Checks

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_dag_viewer_static_package.py \
  tests/test_dag_viewer_server.py \
  tests/test_dag_viewer_live_smoke.py
```

Result: `24 passed in 10.84s`.

```bash
npm --prefix web/dag-viewer ci
npm --prefix web/dag-viewer run typecheck
npm --prefix web/dag-viewer run build
npm --prefix web/dag-viewer run test -- --run
```

Results:

- `npm ci`: added 161 packages, audited 162 packages, `0 vulnerabilities`.
- `typecheck`: exit 0.
- `build`: exit 0, Vite built the static viewer bundle.
- `test -- --run`: `6 passed (6)` test files, `26 passed (26)` tests.

