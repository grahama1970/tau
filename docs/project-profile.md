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
  --registry skill-capability-registry.json \
  --out project-profile-validation.json
```

The command exits non-zero when required policy fields are missing or invalid.
`--registry` is optional, but when supplied Tau checks that every declared
project capability provider matches the skill capability registry.

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
  },
  "capability_providers": {
    "debug_runtime_state": "debugger",
    "bounded_code_fix": "code-runner",
    "code_review": "review-code",
    "deep_research": "dogpile",
    "evidence_case": "create-evidence-case",
    "model_worker": "scillm"
  }
}
```

## Skill Providers

`capability_providers` lets a project profile require which agent-skills
provider Tau should use for a capability. The field does not invoke a skill and
does not trust the skill output. It only binds the project operating profile to
a declared provider so later course-correction and DAG dispatch code can route
through known capabilities.

When a registry is supplied, profile validation checks that:

- each profile capability exists in `tau.skill_capability_registry.v1`;
- the profile's provider matches the registry's `skill`;
- course-correction action mappings reference declared capabilities.

Example action mapping:

```json
{
  "course_correction": {
    "allowed_actions": ["route_reviewer", "run_brave_search_then_retry"],
    "forbid_retry_same_context_after": 2,
    "action_capabilities": {
      "route_reviewer": "code_review",
      "run_brave_search_then_retry": "deep_research"
    }
  },
  "capability_providers": {
    "code_review": "review-code",
    "deep_research": "dogpile"
  }
}
```

## Boundary

The profile validator proves only that Tau can parse and validate a
project-specific orchestration policy artifact and, when provided, compare
declared capability providers against the registry. It does not prove the
profile has been applied to a DAG run, that a skill was invoked, that a
correction action was executed, or that a future route is correct.
