# Issue #136 Approved Release Human Packet Regression Proof

Date: 2026-07-26

Issue: https://github.com/grahama1970/tau/issues/136

## Scope

The original #136 repair correctly made workflow approval require an explicit
human approval packet. The reopened regression was that the
approved-release-bundle workflow tests still used the old no-packet approval
path, so the approval/resume and rollback cycles stayed blocked.

This change updates `tests/test_approved_release_bundle_workflow.py` to preserve
the human boundary:

- no-packet approval is asserted to block with `approval_packet_required`;
- successful approval uses a local-signature `tau.human_approval_packet.v1`
  bound to the exact `publish-approved-release` gate target;
- resume then exercises the real continuation and rollback paths.

## Changed Files

```text
tests/test_approved_release_bundle_workflow.py
docs/proofs/tickets/issue-136-approved-release-human-packet-regression-20260726.md
```

## Deterministic Proof

Clean worktree:

```text
/tmp/tau-issue-132.cyVJhE
```

Pre-repair reproduction:

```text
uv run pytest -q \
  tests/test_approved_release_bundle_workflow.py::test_release_bundle_revises_waits_for_approval_and_resumes_once \
  tests/test_approved_release_bundle_workflow.py::test_failed_post_write_verification_rolls_back_publication \
  -vv
```

Result:

```text
2 failed
approval["status"] == "BLOCKED", expected "PASS"
FileNotFoundError: receipts/publication-rollback.json
```

Post-repair reopened-ticket tests:

```text
uv run pytest -q \
  tests/test_approved_release_bundle_workflow.py::test_release_bundle_revises_waits_for_approval_and_resumes_once \
  tests/test_approved_release_bundle_workflow.py::test_failed_post_write_verification_rolls_back_publication \
  -vv
```

Result:

```text
2 passed in 14.22s
```

Focused approval/workflow suite:

```text
uv run pytest -q \
  tests/test_approval_gate.py \
  tests/test_workflow_cli.py \
  tests/test_durable_repository_qualification_workflow.py \
  tests/test_approved_release_bundle_workflow.py
```

Result:

```text
25 passed in 87.26s (0:01:27)
```

Real CLI smoke against a temporary Git repo:

```text
uv run tau workflows run approved-release-bundle \
  --repo /tmp/tau-issue-136-live.ga6Sbg/repo \
  --goal 'Publish an approved release bundle.' \
  --run-dir /tmp/tau-issue-136-live.ga6Sbg/run \
  --publish-path /tmp/tau-issue-136-live.ga6Sbg/published \
  --no-browser-open

uv run tau workflows approve /tmp/tau-issue-136-live.ga6Sbg/run

uv run tau workflows approve /tmp/tau-issue-136-live.ga6Sbg/run \
  --approval-packet /tmp/tau-issue-136-live.ga6Sbg/run/human-approval.json

uv run tau workflows resume /tmp/tau-issue-136-live.ga6Sbg/run
```

Result:

```text
run_ec=1
approve_without_ec=1
approve_with_ec=0
resume_ec=0
run_status=PASS
approval_status=PASS
gate_status=PASS
gate_authorship=human_local_signature
gate_machine_fabricated=False
published_exists=true
```

Syntax and whitespace checks:

```text
uv run python -m py_compile \
  tests/test_approved_release_bundle_workflow.py \
  src/tau_coding/workflows/runner.py \
  src/tau_coding/approval_gate.py \
  src/tau_coding/cli.py

git diff --check
```

Result:

```text
both exited 0
```

## Evidence Boundary

- mocked: no for the CLI smoke; existing pytest fixtures use local deterministic
  workflow test fixtures only
- live: yes for the CLI workflow/approval/resume smoke against a real temporary
  Git repository
- provider_live: no
- proves: no-packet approval still blocks; local-signature human packet approval
  resumes approved-release-bundle; post-write verification failure produces the
  rollback receipt; the focused approval/workflow suite is green
- does_not_prove: external identity proof beyond local-signature packet
  validation; provider/model behavior; full repository test-suite health; full
  immutable Tau product goal
