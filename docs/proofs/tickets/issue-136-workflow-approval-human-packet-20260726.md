# Issue 136 Proof: Workflow Approval Requires Human Packet

Issue: https://github.com/grahama1970/tau/issues/136

## Change

`tau workflows approve <run-dir>` no longer fabricates
`tau.human_approval_packet.v1`. The command requires `--approval-packet`, checks
the packet against the transaction gate's exact `expected_target`, copies a
valid packet into the DAG's expected input path, and writes
`receipts/workflow-approval.json`.

The approval gate now labels packet authorship in `packet_summary` and rejects
Tau's legacy self-generated marker:

- actor id: `human:tau-operator`
- signature: `declared-manual-approval`

## Deterministic Commands

```text
uv run python -m py_compile src/tau_coding/approval_gate.py src/tau_coding/workflows/runner.py src/tau_coding/cli.py tests/test_approval_gate.py tests/test_workflow_cli.py tests/test_durable_repository_qualification_workflow.py
exit 0
```

```text
uv run ruff format src/tau_coding/approval_gate.py src/tau_coding/workflows/runner.py src/tau_coding/cli.py tests/test_approval_gate.py tests/test_workflow_cli.py tests/test_durable_repository_qualification_workflow.py
6 files reformatted
```

```text
uv run pytest -q tests/test_approval_gate.py tests/test_workflow_cli.py tests/test_durable_repository_qualification_workflow.py
18 passed in 46.96s
```

One earlier post-format full run reported a crash-resume timing failure in
`test_staged_result_crash_resumes_without_rerunning_accepted_branches`. The same
test passed in isolation, and the subsequent full focused rerun passed.

## Real CLI Smoke

Command family used a temporary real git repository and real Tau workflow CLI,
with no mocked service responses:

```text
tmp_root=/tmp/tau-issue-136-live.Amno2W
run_ec=1
approve_without_ec=1
```

Observed user-facing behavior:

```text
tau workflows run approved-release-bundle ... -> BLOCKED at approval gate
tau workflows approve <run-dir> -> exit 1
approve-without-packet.status -> BLOCKED
approve-without-packet.errors -> approval_packet_required
tau workflows approve <run-dir> --approval-packet <human-approval.json> -> PASS
tau workflows resume <run-dir> -> PASS / APPROVED
approval-gate packet_summary.authorship -> human_declared_packet
approval-gate packet_summary.machine_fabricated -> false
```

## Evidence Scope

mocked: no
live: yes, local Tau CLI workflow execution against a real temporary git repo
provider_live: no

This proves the previous no-input workflow approval path now fails closed, an
explicit packet is required, legacy Tau-generated packets are rejected, and the
successful path binds the provided packet to the exact transaction target.

This does not prove cryptographic non-repudiation or external human identity.
The approval-gate contract still treats `manual` packets as declared human
approval unless backed by a stronger `local-signature` or `github-comment`
workflow.
