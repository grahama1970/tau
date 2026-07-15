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

This layer does not provide HTTP or React assets. Those are later fleet children built on this read
model.
