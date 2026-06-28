Tau watchdog proof issue for the fresh COMPLIANCE UI handoff command-loop path.

project-watchdog-action:tau-handoff-dispatch start=experiments/goal-locked-subagents/proofs/fresh-compliance-ui-handoff-command-loop-20260628T142600Z/start-handoff.json max_steps=1 active_goal_hash=sha256:0000000000000000000000000000000000000000000000000000000000000000 apply_transport=false

Expected behavior: global project-watchdog cron picks this up as one bounded local Tau tick, selects the `reviewer` command spec from the handoff, writes a receipt, comments evidence, and closes or routes the issue without leaving it active.
