# DAG 3: Concurrent Fan-out/Fan-in

Run from the repository root:

```bash
uv run python examples/canonical-dags/run.py --dag 3 --run-root /tmp/tau-03 --view
```

`source` releases `docs`, `tests`, and `risks` concurrently. `integrate` waits
for all three branches.
