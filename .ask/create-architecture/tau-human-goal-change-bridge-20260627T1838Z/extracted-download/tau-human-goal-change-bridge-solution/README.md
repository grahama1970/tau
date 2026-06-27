# tau-human-goal-change-bridge-solution

Apply `patches/0001-human-goal-change-bridge.patch` at the Tau repo root, then run:

```bash
uv run pytest tests/test_human_goal_change.py tests/test_handoff_dispatch.py tests/test_cli.py -q
```

This bundle is a local/dry-run implementation slice. It adds a trusted-human bridge from `tau.human_goal_change.v1` to a normal `tau.agent_handoff.v1` start handoff for `goal-guardian`.
