# DAG 5: Durable Resume and Repair

Run without `--repair` to preserve a real blocked reconciliation receipt, then
authorize the repair and resume:

```bash
uv run python examples/canonical-dags/run.py --dag 5 --run-root /tmp/tau-05 --approve --view
uv run python examples/canonical-dags/run.py --dag 5 --run-root /tmp/tau-05 --approve --repair --resume --view
```

The resumed run must reuse the accepted discovery/build/test/document receipts
and execute only reconciliation and release.
