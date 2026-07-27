# Issue #186 Canonical Scheduler Conformance V4

Command:

```bash
PYTHONPATH=src uv run tau canonical-scheduler-conformance \
  --allow-live-filesystem \
  --output docs/proofs/tickets/issue-186-canonical-scheduler-conformance-v4-20260727/canonical-scheduler-conformance.json
```

Result: `BLOCKED`.

This is a non-mocked live filesystem conformance receipt. It exercises the
canonical local DAG examples, the transaction viewer smoke, and a materialized
project DAG route/join fixture through Tau's bounded ready-queue scheduler.

Passed surfaces:

- `command_node`
- `validator_node`
- `transaction_node`
- `human_boundary`
- `join`
- `retry`
- `durable_resume`
- `targeted_repair`
- `conditional_route`

Remaining blocked surfaces:

- `skill_node`
- `map_node`
- `child_dag`

Important readback:

- `mocked=false`
- `live=true`
- `provider_live=false`
- `project_route_join.exit_code=0`
- `project_route_join.status=PASS`
- `route_decision_receipts=1`
- `join_decision_receipts=1`
- `terminal_contribution_receipts=2`
- `transaction_viewer_smoke.status=PASS`
- `transaction_viewer_smoke.snapshot_count=93`

This receipt proves Tau now reports unsupported or unexercised canonical
scheduler surfaces as blockers instead of false-green closure.
