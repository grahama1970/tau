# DAG 4: Mixed Retry and Approval

Run from the repository root:

```bash
uv run python examples/canonical-dags/run.py --dag 4 --run-root /tmp/tau-04 --approve --view
```

Implementation and testing run concurrently. Review returns a first-attempt
failure and passes on Tau's bounded retry. Release requires the explicit human
authorization created by `--approve`.

Omit `--approve` to observe the precise blocked approval boundary. Resume the
same run with `--approve --resume`; Tau reuses the four accepted upstream nodes.
The release worker records `rollback/release.json` before writing its output.
