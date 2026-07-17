# Tau DAG Scheduler Examples

These five scripts are developer-facing scheduler examples retained for focused
runtime experiments. The packaged product workflows defined by `GOAL.md` are
the authoritative operator path: discover them with
`uv run tau workflows list --json` and launch them with
`uv run tau workflows run <workflow-id> ...`. Both surfaces use Tau's durable
receipt-gated scheduler and the same polling React Flow viewer, but these
examples are not substitutes for installed-wheel or packaged-workflow proof.

| DAG | Topology | Command |
| --- | --- | --- |
| 1 | Two-node linear | `uv run python examples/canonical-dags/01-simple-linear/workflow.py run --run-root /tmp/tau-01 --view` |
| 2 | Four-node sequential | `uv run python examples/canonical-dags/run.py --dag 2 --run-root /tmp/tau-02 --view` |
| 3 | Fan-out/fan-in, three concurrent branches | `uv run python examples/canonical-dags/run.py --dag 3 --run-root /tmp/tau-03 --view` |
| 4 | Mixed topology, retry, human release gate | `uv run python examples/canonical-dags/run.py --dag 4 --run-root /tmp/tau-04 --approve --view` |
| 5 | Mixed topology, blocked repair, durable resume | See the two commands below. |

For DAGs 2-5, `--fail-node NODE_ID` provides a deterministic fail-closed path
without editing a DAG document. DAG 1 fails closed when `GOAL.md` is missing a
required topology contract. DAG 4 can be launched without `--approve` to watch
the release node stop at the human decision boundary; `--approve --resume`
reuses accepted upstream work and releases the final artifact. Approval-gated
release writes `rollback/release.json` before the side effect.

DAG 5 intentionally blocks at reconciliation on its first run:

```bash
uv run python examples/canonical-dags/run.py --dag 5 --run-root /tmp/tau-05 --approve --view
```

Authorize the targeted repair and resume. Tau reuses already accepted node
receipts and runs only the blocked remainder:

```bash
uv run python examples/canonical-dags/run.py --dag 5 --run-root /tmp/tau-05 \
  --approve --repair --resume --view
```

These workflows are deterministic and local: `mocked:false`, `live:true`, and
`provider_live:false`. They prove scheduler, receipt, recovery, and viewer
behavior without claiming external model-provider execution.

Run the repeatable desktop/mobile recovery proof from a clean checkout with:

```bash
uv run python scripts/run-canonical-dag-resume-browser-proof.py \
  --run-root /tmp/tau-canonical-resume-proof \
  --out /tmp/tau-canonical-resume-proof.json \
  --desktop-screenshot /tmp/tau-canonical-resume-desktop.png \
  --mobile-screenshot /tmp/tau-canonical-resume-mobile.png
```

The proof keeps one browser page open while DAG 5 runs, blocks for targeted
repair, resumes as a new durable generation, reuses four accepted nodes, and
finishes at the human release boundary. It fails unless React Flow updates
without reload and both viewport checks pass.
