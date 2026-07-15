# Live DAG Viewer

Tau's DAG viewer is a read-only projection of the canonical scheduler journal. The immutable
source DAG, compiled `DagPlan`, scheduler transitions, runtime observations, and admitted receipts
remain separate evidence surfaces.

Child A provides the authoritative backend read model:

```bash
tau dag-view-capabilities --json
tau dag-view-snapshot --run-dir <run-dir> --output -
tau dag-view-events --run-dir <run-dir> --after-sequence 0 --limit 200 --output -
```

The run directory must contain `dag-run.sqlite3`. When the store contains multiple run generations,
pass `--run-id`; Tau does not silently choose a generation.

## Authority Boundary

- SQLite journal sequence is authoritative replay order.
- The scheduler and viewer consume the same replay reducer.
- Runtime observations are diagnostic and never accept a node.
- A node is accepted only after a committed successful scheduler transition.
- The query-only reader cannot acquire a lease or mutate SQLite.
- Browser-facing values are recursively redacted and size bounded.
- Older runs without `source-dag.json` report `SOURCE_DAG_NOT_RETAINED`; Tau does not synthesize it.

## Read-only server

Child B adds a loopback-only standard-library HTTP surface over the authoritative
Child A projection:

```bash
uv run tau dag-view-serve \
  --run-dir /path/to/run \
  --host 127.0.0.1 \
  --port 0 \
  --json
```

The startup receipt prints the assigned port. The server exposes only `GET`
endpoints for capabilities, manifest, full state snapshots, bounded events, and
allowlisted receipts. It opens SQLite read-only, uses one snapshot transaction
per projection, never acquires a scheduler lease, and rejects non-loopback
hosts. Receipt paths come only from committed journal references and are
path-checked, symlink-checked, and hash-checked on every fetch.

`/api/v1/state` supports `ETag` and `If-None-Match`. A `304` means the last
Tau-authored replacement snapshot is still current; clients must not infer
transitions locally.

## Packaged application

The Tau wheel contains the built React Flow application and needs no Node
runtime after installation:

```bash
tau dag-view --run-dir /path/to/run
```

`dag-view` uses an ephemeral loopback port by default and opens the browser in
an interactive terminal. Use `--no-open` for scripts. The browser polls full
Tau-authored snapshots with ETags and never reduces scheduler events locally.
The source DAG remains immutable and the UI has no mutation controls.

The graph keeps scheduler, runtime, and admission state separate. A generic
artifact transaction expands into creator, validator, reviewer, revision, and
acceptance phases without inventing a cycle in the source DAG. Diagnostic
transaction events are bounded and journaled, but cannot activate an edge,
satisfy a terminal, or accept a node.

Run the deterministic non-provider smoke and browser proof:

```bash
uv run python scripts/run-dag-viewer-live-smoke.py \
  --out /tmp/tau-dag-viewer-live-smoke.json

uv run python scripts/run-dag-viewer-browser-proof.py \
  --out /tmp/tau-dag-viewer-browser-proof.json \
  --screenshot /tmp/tau-dag-viewer-browser-proof.png
```

The browser proof receipt records the exact screenshot SHA-256, requires all
13 named checks to pass, and accepts only observed GET requests for a PASS.
These prove real local subprocess execution and browser rendering with
`mocked:false`, `live:true`, and `provider_live:false`. They do not prove model
or provider semantic quality, production deployment, or legal/compliance
authority.
