# Project Spine Basic

This example shows how Tau can apply persona-dream lessons without importing
persona-dream domain terms into Tau core.

Run the clean fixture:

```bash
uv run tau project check-spine \
  --spine examples/project-spine-basic/project-spine.json \
  --out /tmp/tau-project-spine-pass.json
```

Run the blocked fixture:

```bash
uv run tau project check-spine \
  --spine examples/project-spine-basic/blocked-project-spine.json \
  --out /tmp/tau-project-spine-blocked.json
```

The blocked fixture emits `tau.course_correction.v1` records for stale lineage,
false progress, and a forbidden side effect. It does not execute the correction.
