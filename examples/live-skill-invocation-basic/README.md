# Live Skill Invocation Basic

This example proves Tau can execute one real local `agent-skills` skill through
`tau.skill_invocation_receipt.v1`.

The example uses `skills/clean-text/run.sh` because it is deterministic,
local-only, and provider-free.

```bash
examples/live-skill-invocation-basic/run.sh /tmp/tau-live-skill-invocation-basic
```

The run writes:

- `input.txt`
- `clean-output.txt`
- `skill-invocation-request.json`
- `skill-invocation-receipt.json`
- `demo-receipt.json`

This proves a real local skill command executed, produced a repo-contained
artifact, and Tau hash-bound that artifact to a skill invocation receipt. It
does not prove the skill output is semantically sufficient for arbitrary tasks,
provider/model quality, future route correctness, or adapter-specific
admissibility beyond the generic invocation receipt.
