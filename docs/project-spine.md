# Tau Project Spine

`tau.project_spine.v1` is a domain-neutral control artifact extracted from the
persona-dream hardening work.

It does not know about Embry, Kling, storyboard panels, or video providers. It
captures the reusable control grammar:

```text
goal revision -> change events -> lineage -> active work queue
-> leases -> accepted evidence -> derived progress -> side-effect gate
```

The checker is observational only. It writes a receipt and course-correction
records; it does not mutate a DAG, route, Memory, Herdr, providers, files, or
side-effect targets.

## Command

```bash
uv run tau project check-spine \
  --spine examples/project-spine-basic/project-spine.json \
  --out /tmp/tau-project-spine-check-receipt.json
```

The command exits non-zero when Tau detects stale lineage, false progress, or a
forbidden side effect.

## What It Checks

- Active work items are bound to the active goal revision.
- Accepted evidence has lineage.
- Accepted evidence lineage is current for the active revision.
- Accepted evidence does not depend on unresolved change events.
- Mutating or provider-pending work has an active revision-bound lease.
- Reported progress does not exceed receipt-derived progress.
- Requested, submitted, or executed side effects have a passing final gate or a
  human-accepted exception.

## Course-Correction Triggers

The checker emits `tau.course_correction.v1` records for:

- `stale_lineage`: recompute a replan plan before promoting or continuing.
- `false_progress`: derive progress from accepted receipts before readiness
  claims.
- `forbidden_side_effect`: block provider/mutation side effects until a final
  gate or human boundary exists.

## Non-Claims

`tau.project_spine_check_receipt.v1` does not prove:

- semantic correctness of project artifacts;
- provider/model semantic quality;
- human approval;
- that the proposed correction has been executed;
- future route correctness.
