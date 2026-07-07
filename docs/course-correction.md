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

Repeated-failure triggers are evidence-gated. `two_failed_attempts` requires
`attempt >= 2` or `observed_state.attempt_count >= 2`, forbids
`retry_same_context`, and routes to reviewer/debug/goal-guardian/human with
`two_attempt_failure_receipt` and `replan_or_debug_receipt` required before a
new attempt.

Provider-auth failures are also fail-closed. `provider_auth_required` routes to
`repair_provider_auth_then_retry_or_route_human`, allows only `auth-repair`,
`provider-readiness`, or `human`, forbids normal same-context retry and artifact
regeneration before auth repair, and requires `provider_auth_repair_receipt`
plus `provider_readiness_receipt` before retry. Project DAGs classify nested
provider errors such as `401 Unauthorized`, stale/invalid OAuth, and
`403 PERMISSION_DENIED` leaked-key failures into this trigger before treating
missing evidence as a normal reviewer repair.

For ScillM-backed provider nodes, Tau attempts the auth repair before spending
another node attempt. The bounded ready-queue writes
`provider-auth-repair/<node>-attempt-NNN-scillm-auth-preflight.json`, runs the
ScillM auth preflight with repair enabled, refreshes child-process
`SCILLM_PROXY_KEY` and `LITELLM_MASTER_KEY` from the active Docker proxy key
without recording the secret, and retries the same node when both the preflight
and environment refresh pass and retry budget remains. If repair fails or the
retry budget is exhausted, Tau emits the normal `tau.course_correction.v1`
block instead of regenerating artifacts blindly.

Legacy DAG course-correction receipts also preserve `code`, `required_action`,
and `blocked_report_required` fields so existing run-status and proof summaries
remain compatible.

## Skill Capability Routes

Course-correction receipts may include `skill_routes` when Tau is given a
`tau.skill_capability_registry.v1` registry or project-profile capability
providers. This maps bounded correction actions to agent-skills providers:

| Correction action | Capability provider route |
| --- | --- |
| `debug_or_route_reviewer` | `debug_runtime_state` / `debugger`, or `code_review` / `review-code` |
| `route_reviewer` | `code_review` / `review-code` |
| `route_reviewer_or_debug` | `code_review` / `review-code`, or `debug_runtime_state` / `debugger` |
| `run_brave_search_then_retry` | `deep_research` / `dogpile` |
| `retry_node` | `bounded_code_fix` / `code-runner`, or `model_worker` / `scillm` |
| `retry_node_or_route_goal_guardian` | `bounded_code_fix` / `code-runner`, or `model_worker` / `scillm` |

If a required provider is missing or conflicts with the registry, Tau marks the
course-correction input invalid and adds a `skill_capability_route_unavailable`
alert. This is still only routing evidence. It does not invoke the skill and it
does not make a skill result admissible.

## Non-Claims

`tau.course_correction.v1` does not prove:

- the agent is truthful;
- the task is complete;
- the proposed correction is semantically sufficient;
- the required next action has been executed.

It proves only that Tau classified a blocked or drifting state and selected a
bounded next action without mutating the DAG, goal, route, work order, or
provider state.
