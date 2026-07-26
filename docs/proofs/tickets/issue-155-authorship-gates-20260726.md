# Issue #155 Proof: Approval Authorship Gates

Ticket: https://github.com/grahama1970/tau/issues/155

## Repair

- Approval gates now use positive authorship classification instead of a one-case denylist.
- `manual` approval packets are classified as `machine_originated_unverified_manual` and cannot satisfy a human gate.
- Verifiable `local-signature` packets still pass when the signature digest matches the packet content.
- `github-comment` packets require a GitHub attestation shape before they can satisfy the gate.
- `tau workflows repair` now accepts `--approval-packet` and fails closed with `approval_packet_required` when missing.
- Durable workflow repair packets are machine-originated and bind to a passing `tau.approval_gate_receipt.v1` instead of minting a fake human authorization.
- Replacement-harness sanity now emits a machine-origin packet and treats production human-gate rejection as the expected sanity result.
- The legacy callback parser now preserves options for `commit-plan`, `herdr-cleanup`, `replacement-harness-sanity`, and workflow repair approval packets.

## Deterministic Proof

Artifacts are under:

`docs/proofs/tickets/issue-155-authorship-gates-20260726/`

Command results:

- `uv run ruff format --check src/tau_coding/approval_gate.py src/tau_coding/cli.py src/tau_coding/workflows/runner.py tests/test_approval_gate.py tests/test_cli.py tests/test_durable_repository_qualification_workflow.py tests/test_generic_artifact_transaction.py tests/test_workflow_cli.py`
  - Result: `8 files already formatted`
- `uv run ruff check src/tau_coding/approval_gate.py src/tau_coding/cli.py src/tau_coding/workflows/runner.py tests/test_approval_gate.py tests/test_cli.py tests/test_durable_repository_qualification_workflow.py tests/test_generic_artifact_transaction.py tests/test_workflow_cli.py`
  - Result: `All checks passed!`
- `uv run python -m py_compile src/tau_coding/approval_gate.py src/tau_coding/cli.py src/tau_coding/workflows/runner.py tests/test_approval_gate.py tests/test_cli.py tests/test_durable_repository_qualification_workflow.py tests/test_generic_artifact_transaction.py tests/test_workflow_cli.py`
  - Result: exit `0`
- `uv run pytest -q tests/test_approval_gate.py tests/test_durable_repository_qualification_workflow.py tests/test_workflow_cli.py tests/test_cli.py -k 'approval_gate or repair_approve_and_resume_durable_qualification or targeted_repair_preserves or replacement_harness_sanity'`
  - Result: `12 passed, 238 deselected`
- `uv run pytest -q tests/test_approval_gate.py tests/test_generic_artifact_transaction.py tests/test_durable_repository_qualification_workflow.py tests/test_workflow_cli.py tests/test_cli.py tests/test_herdr_cleanup.py tests/test_commit_plan.py -k 'approval or workflow or replacement_harness_sanity or repair_approve_and_resume_durable_qualification or targeted_repair_preserves'`
  - Result: `36 passed, 297 deselected`

Proof receipts:

- `proof-summary.json`
- `pytest-focused.txt`
- `pytest-wide-approval-slice.txt`
- `ruff-check.txt`
- `ruff-format-check.txt`
- `py-compile.txt`

## Evidence Limits

mocked: no

live: no

What was exercised: deterministic local approval classification, workflow repair CLI/function gates, generic transaction approval continuation, replacement-harness sanity classification, callback parser option preservation, lint, compile, and focused pytest slices.

What remains unverified: live GitHub comment attestation fetch, external signature-key infrastructure, and live production mutation after approval.
