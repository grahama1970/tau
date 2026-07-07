# Coding Reliability Basic

This example shows Tau-native coding evidence without trusting a coding agent.
It creates a temporary repository, blocks a stale hash-bound patch, applies a
valid hash-bound patch, collects local diagnostics, validates structured review
findings for `PASS`, `REVISE`, and `BLOCKED` routes, proposes a dry-run commit
plan from the PASS review receipt, and summarizes orchestration reliability from
a synthetic DAG receipt.

Run:

```bash
examples/coding-reliability-basic/run.sh
```

Optional output directory:

```bash
examples/coding-reliability-basic/run.sh /tmp/tau-coding-reliability-basic
```

The final artifact is:

```text
<out>/demo-receipt.json
```

## What This Proves

- Tau can block a stale code patch by base-file hash.
- Tau can apply a valid exact-replacement patch with before/after hashes.
- Tau can write LSP-style diagnostics, structured PASS/REVISE/BLOCKED review,
  commit-plan, and orchestration reliability receipts.

## Non-Claims

This example does not prove semantic code correctness, agent truthfulness,
provider/model quality, full DAG execution, GitHub mutation, or legal
compliance.
