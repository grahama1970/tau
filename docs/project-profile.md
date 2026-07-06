# Tau Project Profile

`tau.project_profile.v1` is the project-specific operating contract for Tau
orchestration.

The first slice is validation-only. It does not apply profile policy to a live
DAG run. It gives project agents a strict artifact for the operating envelope
that later DAG, Herdr, course-correction, sandbox, and research gates can
reference.

## Command

```bash
uv run tau project-profile-validate \
  --profile project-profile.json \
  --out project-profile-validation.json
```

The command exits non-zero when required policy fields are missing or invalid.

## Minimum Shape

```json
{
  "schema": "tau.project_profile.v1",
  "project_id": "tau-self-fix",
  "memory": {
    "scope": "project:tau",
    "intent_required": true,
    "evidence_case_required": true
  },
  "retries": {
    "max_attempts_per_node": 2,
    "after_two_failures": "require_research_or_goal_guardian"
  },
  "herdr": {
    "receipt_timeout_seconds": 300,
    "stale_pane_seconds": 180,
    "auth_required_action": "route_human",
    "crashed_action": "retry_node",
    "interstitial_action": "route_human"
  },
  "course_correction": {
    "allowed_actions": [
      "send_reminder",
      "retry_node",
      "route_reviewer",
      "route_goal_guardian",
      "route_human",
      "block_run"
    ],
    "forbid_retry_same_context_after": 2
  }
}
```

## Boundary

The profile validator proves only that Tau can parse and validate a
project-specific orchestration policy artifact. It does not prove the profile
has been applied to a DAG run, that a correction action was executed, or that a
future route is correct.
