# Canonical DAG Launch Surface

Tau's active product goal requires five canonical DAGs that a new evaluator can
discover and launch without repository archaeology. This page documents the
launch surface only. It does not claim the full immutable goal is complete.

List the canonical DAGs:

```bash
uv run tau canonical-dags --json
```

Launch one DAG and read back its run record, output receipt, and viewer URL:

```bash
uv run tau canonical-dag-launch simple-linear --run-root /tmp/tau-canonical-dags
uv run tau canonical-dag-launch multi-step-sequential --run-root /tmp/tau-canonical-dags
uv run tau canonical-dag-launch fanout-fanin --run-root /tmp/tau-canonical-dags
uv run tau canonical-dag-launch mixed-retry-approval --run-root /tmp/tau-canonical-dags
uv run tau canonical-dag-launch durable-recovery --run-root /tmp/tau-canonical-dags
```

Run the live launch proof:

```bash
scripts/prove-canonical-dag-launch.py \
  --run-root /tmp/tau-canonical-dag-launch-proof
```

The proof reports `mocked: false` and `live: true`. It proves discovery and
launch ergonomics only:

- exactly five canonical DAGs are listed;
- each selected DAG launches through Tau CLI without editing JSON;
- each launch reads back a run id, run directory, output receipt path, and DAG
  viewer URL.

The proof does not prove final useful-output quality, dynamic browser rendering,
crash-safe durable resume, exact approval rollback, or human acceptance.
