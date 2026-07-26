# Issue #144 Proof: DAG Viewer Elapsed And Duration Timing

Ticket: https://github.com/grahama1970/tau/issues/144

## Changes

- `src/tau_coding/dag_viewer/projection.py` now projects active node `started_at`
  from the authoritative `attempt_dispatched` journal event when a result
  payload has not been staged yet.
- `web/dag-viewer/src/components/TauNode.tsx` renders compact node timing:
  completed nodes show `duration`; in-flight nodes show ticking `elapsed`.
- `web/dag-viewer/src/tests/DagWorkspace.test.tsx` asserts both completed
  duration and ticking in-flight elapsed text.
- Packaged DAG viewer static assets were rebuilt.

## Deterministic Proof

Mocked: mixed.
Live: yes for the browser proof and local DAG/viewer server proof.

Commands run:

```text
uv run python -m py_compile src/tau_coding/dag_viewer/projection.py tests/test_dag_viewer_historical.py
exit 0

uv run ruff check src/tau_coding/dag_viewer/projection.py tests/test_dag_viewer_historical.py
All checks passed!

uv run pytest tests/test_dag_viewer_historical.py::test_exact_historical_prefix_is_stable_and_excludes_future_state tests/test_dag_live_projection.py tests/test_dag_viewer_server.py::test_default_viewer_follows_new_run_generation -q
23 passed in 2.12s

npm test -- --run src/tests/DagWorkspace.test.tsx
1 test file passed, 5 tests passed

npm run build
tsc -b && vite build completed; packaged assets written under src/tau_coding/dag_viewer/static

git diff --check
exit 0
```

Browser proof:

- Receipt: `docs/proofs/tickets/issue-144-browser/timing-browser-proof.json`
- Screenshot: `docs/proofs/tickets/issue-144-browser/timing-browser-proof.png`
- Screenshot SHA-256:
  `sha256:edc7e58028d014f8119a891f2d7c3eeb71b35521a8fc53b0402102db81295448`

Browser receipt checks:

```json
{
  "final_duration_visible": true,
  "final_duration_from_snapshot": true,
  "historical_inflight_started_at_projected": true,
  "historical_elapsed_visible": true,
  "historical_elapsed_ticks": true,
  "read_only_requests": true
}
```

Observed browser timing text:

```text
final_timing_text: duration1s
initial_elapsed_text: elapsed4m 38s
changed_elapsed_text: elapsed4m 39s
```

## Scope

This proves the DAG viewer can render node duration from existing result timing
fields, can derive in-flight `started_at` from the durable journal, and can show
a ticking elapsed value in the packaged browser viewer. It does not prove
provider semantic correctness or mutate DAG state.
