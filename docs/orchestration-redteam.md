# Orchestration Red-Team

`tau.orchestration_redteam_receipt.v1` is a deterministic local suite for Tau's
project-orchestration control loop. It attacks whether Tau emits useful
course-correction and reliability receipts when a run is stale, blocked, or
unsafe to continue normally.

It is separate from the zero-trust containment red-team suite. Zero-trust
red-team checks policy/data/research/access/package gates. Orchestration
red-team checks whether a project agent gets a course-correction path instead of
a vague blocked state.

## Command

```bash
uv run tau orchestration-redteam --run-dir /tmp/tau-orchestration-redteam
```

The command writes:

```text
/tmp/tau-orchestration-redteam/orchestration-redteam-receipt.json
```

## Current Attempts

The first suite covers:

- `herdr_stale`
- `provider_auth_required`
- `provider_interstitial`
- `provider_crashed`
- `receipt_timeout`
- `provider_receipt_wrong_pane`
- `blocked_run_without_course_correction`
- `unhandled_herdr_observation_block`

Each attempt passes only when Tau emits the expected fail-closed trigger or
reliability alert.

## Proof Boundary

This suite proves:

- Tau can classify local adversarial orchestration states.
- Tau emits `tau.course_correction.v1` through Herdr observation gates.
- Tau detects blocked orchestration receipts with no correction path.
- Tau detects blocked Herdr gates that lack embedded course correction.

It does not prove:

- exhaustive orchestration failure coverage;
- provider/model semantic quality;
- that a course-correction action was executed;
- future route correctness;
- live Herdr monitor snapshot availability.
