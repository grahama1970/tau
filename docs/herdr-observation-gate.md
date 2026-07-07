# Tau Herdr Observation Gate

`tau.herdr_observation_gate_receipt.v1` is Tau's admissibility gate over Herdr
runtime observations.

It is not a Herdr dashboard and it is not a replacement for Herdr monitoring.

```text
Herdr observes workspace/pane/process/session state.
Tau compares those facts with DAG, work-order, receipt, and retry policy.
Tau emits a gate receipt plus a course-correction payload when normal
continuation is unsafe.
```

## Command

```bash
uv run tau herdr-observation-gate \
  --snapshot herdr-monitor-snapshot.json \
  --out herdr-observation-gate.json \
  --expected-receipt receipts/attempt-01-coder.json \
  --expected-workspace-id w1 \
  --expected-pane-id w1:p1 \
  --expected-terminal-id term-abc \
  --dag-id dag-1 \
  --node-id coder \
  --agent coder \
  --attempt 1 \
  --receipt-overdue
```

The command exits non-zero when the gate blocks continuation.

## Fail-Closed Cases

The gate blocks when:

- expected Herdr workspace, pane, or terminal identity does not match;
- expected node receipt is missing and overdue;
- Herdr state is `waiting_on_input` and the expected node receipt is missing
  and overdue;
- Herdr state is `auth_required`;
- Herdr state is `interstitial`;
- Herdr state is `crashed`, `exited`, or `stale`.

The blocked receipt embeds `tau.course_correction.v1`, so project agents get a
bounded next action such as `retry_node_or_route_goal_guardian`, `route_human`,
`send_reminder_or_route_human`, or `block_run`.

## Non-Claims

This gate does not prove Herdr pane output is true, provider/model semantic
quality, production route correctness, or that the required correction action
has been executed. It proves Tau consumed Herdr runtime evidence and applied the
DAG/work-order admissibility policy without mutating the DAG or goal.
