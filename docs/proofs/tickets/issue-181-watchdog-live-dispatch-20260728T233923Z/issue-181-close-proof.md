# Tau #181 Close Proof

Ticket: `grahama1970/tau#181`

## What Was Proved

The watchdog live dispatch probe produced a persisted project-watchdog receipt
with:

- `status`: `NEEDS_ATTENTION`
- `handled_count`: `1`
- `handled_issues[0].issue_number`: `181`
- `handled_issues[0].action`: `ticket_repair`
- `handled_issues[0].status`: `NEEDS_ATTENTION`
- timeout command captured as `exit_code=124`, `timed_out=true`,
  `timeout_seconds=20`

This satisfies #181's required proof: a project-watchdog `receipt.json` with
status `COMPLETED` or `NEEDS_ATTENTION` and a non-empty `handled_issues` array.

## Proof Artifacts

- `docs/proofs/tickets/issue-181-watchdog-live-dispatch-20260728T233923Z/project-watchdog-receipt.json`
- `docs/proofs/tickets/issue-181-watchdog-live-dispatch-20260728T233923Z/readback.json`
- `docs/proofs/tickets/issue-181-watchdog-live-dispatch-20260728T233923Z/tick.stdout.json`
- `docs/proofs/tickets/issue-181-watchdog-live-dispatch-20260728T233923Z/tick.stderr.txt`
- `docs/proofs/tickets/issue-181-watchdog-live-dispatch-20260728T233923Z/repair-task.md`
- `docs/watchdog-live-probe.md`

## Commands Run

```bash
uv run --project skills/project-watchdog pytest -q skills/project-watchdog/tests
```

Result:

```text
149 passed in 1.81s
```

```bash
python3 scripts/check_mock_evidence_claims.py
```

Result:

```text
OK: checked 466 test file(s); no mock+proof claim violations
```

```bash
PROJECT_WATCHDOG_STATE_ROOT=/tmp/project-watchdog-issue181-20260728T2343Z/state \
PROJECT_WATCHDOG_PROJECTS_PATH=/tmp/project-watchdog-issue181-20260728T2343Z/registry/projects.json \
/tmp/agent-skills-watchdog-181-20260728/skills/project-watchdog/run.sh tick --apply --project tau --max-tickets 1
```

Result:

```text
exit_code=1
status NEEDS_ATTENTION ok False handled_count 1
handled [(181, 'NEEDS_ATTENTION', '$ask tau-dag failed for grahama1970/tau#181')]
```

## Shared Runtime Patch

The crash root cause was in `agent-skills`: project-watchdog `run_cmd` let
`subprocess.TimeoutExpired` escape, so no receipt was written. That patch is
pushed to:

```text
grahama1970/agent-skills@9bd330ddb528652c9dfc4bf7fef91a47cc1ecc75
```

## Scope

`mocked`: no

`live`: yes

This proves watchdog dispatch and receipt persistence for the #181 probe. It
does not prove the underlying #181 repair DAG completed successfully; the
ticket's accepted bar explicitly allows `NEEDS_ATTENTION`.
