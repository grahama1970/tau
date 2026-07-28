Repair grahama1970/tau#181: TEST: project-watchdog live dispatch probe (safe to close)

Allowed paths: docs/watchdog-live-probe.md

Commit to the current branch only. Do not push, do not merge, do not switch branches. Whether this reaches main is the reviewer's decision and a human's, not the creator's.

The creator seat implements the fix and commits it. The reviewer seat checks it against the ticket's acceptance criterion and required proof, and answers VERDICT: PASS, VERDICT: FAIL, or VERDICT: NEEDS_ATTENTION.

--- ticket body ---
## Type

bug

## Target

docs/watchdog-live-probe.md

## Target paths

- docs/watchdog-live-probe.md

## Current state

project-watchdog has never dispatched a real ticket; 3000/3000 receipts are NOOP and 0 issues have ever been handled

## Requested outcome

the cron leases this issue, runs one bounded tau self-fix tick, and writes a receipt with handled_count >= 1

## Required proof

a project-watchdog receipt.json with status COMPLETED or NEEDS_ATTENTION and a non-empty handled_issues array

## Route

backend_python_or_skill_runtime

## Maintainer route

backend_python_or_skill_runtime

## Requested repair agent

Not specified.

## Non-goals

No unrelated refactors or scope expansion.

## Ticket type details

- **Observed failure:** project-watchdog has never dispatched a real ticket; 3000/3000 receipts are NOOP and 0 issues have ever been handled
- **Expected behavior:** the cron leases this issue, runs one bounded tau self-fix tick, and writes a receipt with handled_count >= 1
- **Reproduction or artifact:** file this ticket with agent-work, then run: skills/project-watchdog/run.sh tick --apply --project tau

<!-- ticket-skill
type: bug
target: docs/watchdog-live-probe.md
route: backend_python_or_skill_runtime
agent: unspecified
-->

This ticket must be resolved under `best-practices-github-ticket`. Closure requires deterministic proof; WebGPT review or CI green alone is not closure proof.
