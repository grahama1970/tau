# Tau Generic DAG Runner

Tau now has a generic local DAG runner for productizing the provider-DAG proof
without baking Codex/OpenCode or coder/reviewer assumptions into the scheduler.

## CLI

```bash
uv run tau run path/to/dag-spec.json
uv run tau dag-run path/to/dag-spec.json
uv run tau dag-inspect path/to/run-dir
uv run tau dag-resume path/to/run-dir
uv run tau generic-provider-dag-node --node-id provider-task --receipt-path /tmp/node.json --provider-run-root /tmp/provider-runs
```

`run` is the product-facing convenience command for DAG execution. `dag-run` is
the explicit implementation command and accepts the same DAG execution options.

Use `--no-resume` with `dag-run` to force commands to execute even when a valid
node receipt already exists.

Use `dag-resume <run-dir>` when the operator has a run directory but not the
original DAG spec path at hand. Tau reads `spec_path` from
`current-state.json`, `checkpoint.json`, or `run-receipt.json`, then reruns the
recorded spec with resume enabled.

## Spec

```json
{
  "schema": "tau.generic_dag_spec.v1",
  "run_id": "example-run",
  "run_dir": "/tmp/tau-generic-dag-example",
  "events_jsonl": "/tmp/tau-generic-dag-example/events.jsonl",
  "nodes": [
    {
      "node_id": "planner",
      "role": "planner",
      "depends_on": [],
      "command": ["python3", "write-planner-receipt.py"],
      "receipt_path": "/tmp/tau-generic-dag-example/receipts/planner.json",
      "timeout_seconds": 60,
      "max_attempts": 1
    },
    {
      "node_id": "coder",
      "role": "coder",
      "depends_on": ["planner"],
      "command": ["python3", "write-coder-receipt.py"],
      "receipt_path": "/tmp/tau-generic-dag-example/receipts/coder.json",
      "timeout_seconds": 120,
      "max_attempts": 2
    }
  ]
}
```

Each node command must write a receipt at `receipt_path`:

```json
{
  "schema": "tau.generic_dag_node_receipt.v1",
  "node_id": "coder",
  "status": "PASS",
  "verdict": "PASS",
  "artifacts": [],
  "commands_run": [],
  "handoff_summary": "What this node completed.",
  "errors": [],
  "policy_exceptions": []
}
```

## Semantics

- Tau validates the DAG spec before execution.
- Tau rejects unknown dependencies and cycles before dispatch.
- Nodes run in dependency order.
- A downstream node starts only after all dependencies have valid `PASS`
  receipts.
- A valid existing `PASS` receipt is treated as a checkpoint when resume is
  enabled.
- If a node declares `work_order_path`, its node receipt must include
  `work_order_sha256` matching the current work-order file. Stale work-order
  receipts are not resumed.
- Timeout, non-zero exit, missing receipt, invalid receipt, or blocked verdict
  fails closed.

## Checkpoint Artifacts

Each run writes:

```text
run-receipt.json
events.jsonl
checkpoint.json
current-state.json
```

`checkpoint.json` and `current-state.json` use
`tau.generic_dag_checkpoint.v1` and record:

- `status` and `verdict`
- `active_node_id`
- `completed_nodes`
- `ready_nodes`
- `blocked_nodes`
- compact `node_statuses`
- receipt paths that resume will reuse
- `spec_path`, so `tau dag-resume <run-dir>` can recover from checkpoint
  metadata

`tau dag-inspect <run-dir>` includes a compact checkpoint summary so a project
agent can see whether a run is resumable, blocked, or finished without parsing
the full event log. It also reports `resumed_node_count`,
`dispatched_node_count`, `blocked_node_count`, and `event_kind_counts` so
resume behavior is visible without opening every node record. Node summaries
include `work_order_path` and `work_order_sha256` when present, so stale
work-order resume failures can be audited from the inspect surface. Node
records also include `started_at`, `finished_at`, and `duration_seconds`, so a
project agent can diagnose timeout, error, retry, and resume behavior from the
receipt, `tau dag-inspect`, or `tau run-status` before opening raw subprocess
logs.

Run receipts also include `resume_requested` and `resume_source`. A direct
`tau dag-run <spec>` records `resume_source.mode:"spec_path"`, while
`tau dag-resume <run-dir>` records `resume_source.mode:"run_metadata"` plus the
source `run_dir`, metadata file used, and recovered `spec_path`. `tau
dag-inspect`, `tau run-status`, and real-world sanity summaries expose these
fields so recovery provenance is visible from the normal operator surfaces.

## Hash-Bound Artifact Transactions

A node may opt into `tau.generic_artifact_transaction.v1` with an immutable
`work_order_path`, an artifact root, separate producer and reviewer identities,
and an independent reviewer command. Tau writes attempt and review contexts and
passes their paths and hashes through `TAU_GENERIC_DAG_CONTEXT` or
`TAU_GENERIC_DAG_REVIEW_CONTEXT`.

The producer writes `tau.media_artifact_manifest.v1`. Tau independently checks
each file path, size, and SHA-256 before invoking the reviewer. A structured
`REVISE` result is hash-bound into the next attempt context without changing the
work order. Only a validated reviewer `PASS` causes Tau to write the authoritative
`tau.accepted_artifact_manifest.v1` used by dependent nodes.

Resume authority comes from the Tau transaction receipt and accepted manifest,
not the producer's PASS claim. Missing or modified accepted artifacts block with
`STALE_ACCEPTED_STATE`; Tau does not silently regenerate them. Optional
continuations can require `generic_dag_transaction_continue` approval bound to
the exact run, transaction, accepted-manifest hash, and command hash.

Run the live two-stage acceptance canary against the local Scillm proxy:

```bash
uv run python scripts/run-generic-artifact-transaction-canary.py \
  --out /tmp/tau-issue-71-live-canary \
  --reference docs/assets/tau-header.webp \
  --model gpt-5.5 \
  --approve-synthetic-continuation
```

The approval flag authorizes only the canary's local marker write. Its declared
manual signature is not cryptographic or legal authority. The expected first
run is `BLOCKED/APPROVAL_REQUIRED`; the resumed run is PASS only after exact
approval binding. `provider_live:true` proves real Scillm calls occurred, not
reviewer truthfulness, model quality, or future route correctness.

## Proof Boundary

`mocked:false` means the runner executed real local subprocesses and consumed
real files. `live:false` means this generic layer does not itself contact live
provider CLIs, Herdr, GitHub, Tailscale, or production repositories.

The live provider path remains `provider-dag-poc`, which can use this generic
runner contract as the scheduler layer after provider adapters are separated.

## Provider Adapter Node

`tau generic-provider-dag-node` is the first provider adapter for the generic
runner. A generic DAG node can call it as its `command`; the adapter runs the
existing visible Herdr Codex/OpenCode provider-DAG path, then translates the
provider run receipt into `tau.generic_dag_node_receipt.v1`.

Provider-specific status is preserved in `provider_status` and
`provider_verdict`. The generic node uses `status: PASS` / `verdict: PASS` only
when the provider subrun passes. Any provider failure becomes a generic
`status: BLOCKED` / `verdict: BLOCKED` node receipt so downstream generic DAG
nodes fail closed without needing to know every provider-specific verdict.

If the generic DAG node declares `work_order_path`, pass the same file to the
adapter with `--work-order-path`. The adapter records `work_order_path` and
`work_order_sha256` in the emitted node receipt, allowing the generic runner to
resume provider-backed nodes only when the current work order still matches the
receipt.

When any generic node receipt reports `provider_live: true`, the generic run
receipt also reports `provider_live: true` and `live: true`, and its proof scope
records that Tau carried live provider-backed node evidence through the generic
DAG receipt.

`tau dag-inspect` and `tau run-status` summarize each generic node's provider
evidence with `artifact_count` and an `artifacts` map keyed by artifact kind,
for example `run_dir`, `runtime_manifest`, `events_jsonl`,
`herdr_cleanup_receipt`, and `orchestration_evidence_receipt`. This lets a
project agent jump from the generic scheduler receipt to the nested provider run
without parsing the raw node receipt.

This adapter still does not approve production repository mutation, close
tickets, or prove Tailscale monitoring. It is only a bridge from the generic
receipt-gated scheduler to Tau's existing provider-DAG proof path.
