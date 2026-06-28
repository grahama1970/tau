Tau watchdog apply proof issue for the fresh same-run COMPLIANCE chat-to-command-loop handoff.

project-watchdog-action:tau-handoff-dispatch start=experiments/goal-locked-subagents/proofs/tau-same-run-compliance-20260628T222531Z/command-loop/start-handoff.json max_steps=1 active_goal_hash=sha256:0000000000000000000000000000000000000000000000000000000000000000 apply_transport=true

Expected behavior: global project-watchdog cron picks this up as one bounded local Tau tick, selects reviewer, writes receipts, applies terminal GitHub transport to create a next:human proof issue, comments evidence, and closes or routes the source issue without leaving it active.
