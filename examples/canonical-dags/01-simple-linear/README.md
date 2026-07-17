# Canonical DAG 1: Simple Linear

This is the first rung of Tau's immutable product goal. Two sequential local
nodes turn the repository's `GOAL.md` into a concise, validated goal summary:

```text
extract-goal -> validate-goal
```

Run it and open the dynamic React Flow view with one command:

```bash
uv run python examples/canonical-dags/01-simple-linear/workflow.py run \
  --run-root /tmp/tau-canonical-01 \
  --view
```

Tau prints the loopback viewer URL as soon as the durable scheduler journal is
available. The nodes intentionally pause briefly so their sequential state
changes are visible. The viewer remains available for 15 seconds after the DAG
finishes unless `--serve-after-seconds` is changed.

The useful output is:

```text
/tmp/tau-canonical-01/artifacts/tau-goal-summary.md
```

The run is deterministic and local: `mocked:false`, `live:true`, and
`provider_live:false`. It proves this simple linear workflow and dynamic local
viewer path only. It does not prove the later concurrent, conditional,
human-gated, or recovery DAGs.
