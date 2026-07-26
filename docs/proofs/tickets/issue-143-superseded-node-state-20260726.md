# Issue #143 Proof: Superseded Node State

Ticket: https://github.com/grahama1970/tau/issues/143

## Change

- Added `superseded` to the DAG live projection vocabulary.
- `superseded` projects as scheduler state `superseded`, admission state
  `not_applicable`, and `accepted: false`.
- The React DAG node exposes `data-state="superseded"` and keeps the existing
  `data-node-state="superseded"` attribute.
- Added a distinct viewer tone: `tau-node--superseded`.
- Rebuilt the static viewer bundle served by Tau.

## Proof Commands

```bash
uv run python -m py_compile \
  src/tau_coding/dag_viewer/projection.py \
  tests/test_dag_live_projection.py
```

Result: exit 0.

```bash
uv run pytest \
  tests/test_dag_live_projection.py::test_superseded_node_state_is_distinct_from_settled_and_skipped \
  -q
```

Result: `1 passed in 0.46s`.

```bash
npm ci
```

Result: installed 161 packages; audited 162 packages; found 0 vulnerabilities.

```bash
npm test -- --run src/tests/DagWorkspace.test.tsx
```

Result: `Test Files 1 passed (1)` and `Tests 4 passed (4)`.

```bash
npm run build
```

Result: Vite built the production static viewer into
`src/tau_coding/dag_viewer/static` with assets `index-BjagBDU3.css` and
`index-DIAShRSq.js`.

```bash
uv run pytest tests/test_dag_live_projection.py \
  tests/test_dag_viewer_server.py::test_default_viewer_follows_new_run_generation \
  -q
```

Result: `22 passed in 1.57s`.

```bash
uv run ruff check src/tau_coding/dag_viewer/projection.py tests/test_dag_live_projection.py
```

Result: `All checks passed!`

```bash
git diff --check
```

Result: exit 0.

## Evidence Boundary

- mocked: no
- live: yes, local deterministic Python projection, React render test, and production
  viewer build.
- exercised: projection state mapping, React rendered class/data attributes, and served
  static asset regeneration.
- not exercised: a full browser screenshot; this ticket required projection and viewer
  rendering tests, and no live browser route was changed.
