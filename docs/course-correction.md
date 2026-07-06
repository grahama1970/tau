# Tau Course Correction

`tau.course_correction.v1` is the shared artifact for blocked or drifting
orchestration states.

It is not a success receipt. It tells a project agent what Tau observed, why
normal continuation is unsafe, and which bounded next routes remain allowed.

## Boundary

Herdr observes runtime facts such as pane, terminal, process, visible log, and
agent state. Tau consumes those facts with DAG, goal, work-order, receipt, and
retry policy context.

```text
Herdr observes.
Tau adjudicates.
Tau emits tau.course_correction.v1.
```

## Current Command

```bash
uv run tau course-correction \
  --trigger receipt_timeout \
  --out /tmp/course-correction.json \
  --run-id run-1 \
  --dag-id dag-1 \
  --goal-hash sha256:goal \
  --node-id coder \
  --agent coder \
  --attempt 2 \
  --observed-state-json '{"receipt_missing":true}' \
  --error 'node_receipt_timeout: coder receipt did not appear before timeout'
```

The command exits non-zero when `next_allowed:false`, because the generated
receipt is a fail-closed correction gate, not normal continuation.

## Normalized Fields

Every receipt includes:

- `trigger`: closed trigger code such as `receipt_timeout`, `invalid_receipt`,
  `provider_auth_required`, `provider_crashed`, `pointless_unit_test_drift`, or
  `brave_search_required_after_two_attempts`.
- `observed_state`: the runtime or DAG facts Tau used.
- `why_normal_retry_is_unsafe`: the reason blind continuation is unsafe.
- `required_next_action`: bounded next action selected by policy.
- `allowed_next_routes`: routes the orchestrator may consider.
- `forbidden_next_routes`: routes Tau should not take.
- `required_evidence_before_retry`: artifacts needed before another attempt.

Legacy DAG course-correction receipts also preserve `code`, `required_action`,
and `blocked_report_required` fields so existing run-status and proof summaries
remain compatible.

## Non-Claims

`tau.course_correction.v1` does not prove:

- the agent is truthful;
- the task is complete;
- the proposed correction is semantically sufficient;
- the required next action has been executed.

It proves only that Tau classified a blocked or drifting state and selected a
bounded next action without mutating the DAG, goal, route, work order, or
provider state.
