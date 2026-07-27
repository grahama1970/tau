# Issue 194 Proof

Implemented `tau security-audit-conformance` as a live filesystem command that exercises asymmetric Ed25519 signing through local OpenSSL, action-bound approval gates, RBAC/API mutating request receipts, and a hash-chained audit ledger.

## Commands

- `PYTHONPATH=src uv run tau security-audit-conformance --allow-live-filesystem --output docs/proofs/tickets/issue-194-security-audit-conformance-20260727/security-audit-conformance.json`
  - Exit code: 0
  - Receipt status: `PASS`
  - `mocked=false`, `live=true`, `provider_live=false`
  - `failed_checks=[]`

- `PYTHONPATH=src uv run python -m py_compile src/tau_coding/security_audit_conformance.py src/tau_coding/cli.py`
  - Exit code: 0

- `PYTHONPATH=src uv run ruff check src/tau_coding/security_audit_conformance.py src/tau_coding/cli.py`
  - Exit code: 0
  - Output: `All checks passed!`

- `PYTHONPATH=src uv run pytest tests/test_receipt_signing.py tests/test_approval_gate.py tests/test_github_apply_policy.py tests/test_handoff_dispatch.py`
  - Exit code: 0
  - Result: 54 passed

- `find docs/proofs/tickets/issue-194-security-audit-conformance-20260727 -type f \( -name '*private*' -o -name 'ed25519-private.pem' \) -print`
  - Matches: 0

## Required #194 Checks

- `signature_verification_pass=true`
- `tamper_negative_control_denied=true`
- `approval_bound_action_accepted=true`
- `wrong_target_approval_denied=true`
- `expired_approval_denied=true`
- `unauthorized_api_mutating_request_denied=true`
- `authorized_request_accepted_with_receipt=true`
- `audit_ledger_verifies=true`

## Independent Readback

- `artifacts/signing/signature-verification.json`: `status=PASS`, OpenSSL exit code 0
- `artifacts/signing/tamper-negative-verification.json`: `status=BLOCKED`, OpenSSL exit code 1
- `artifacts/approvals/approval-bound-action.json`: `status=PASS`
- `artifacts/approvals/wrong-target-denial.json`: `status=BLOCKED`
- `artifacts/approvals/expired-denial.json`: `status=BLOCKED`
- `artifacts/api/unauthorized-mutating-request.json`: `status=BLOCKED`
- `artifacts/api/authorized-mutating-request.json`: `status=PASS`
- `artifacts/audit/audit-ledger-verification.json`: `status=PASS`, `entry_count=7`, `errors=[]`

## Artifacts

- `security-audit-conformance.json`
- `artifacts/signing/asymmetric-signature-receipt.json`
- `artifacts/signing/signature-verification.json`
- `artifacts/signing/tamper-negative-verification.json`
- `artifacts/approvals/approval-bound-action.json`
- `artifacts/approvals/wrong-target-denial.json`
- `artifacts/approvals/expired-denial.json`
- `artifacts/api/unauthorized-mutating-request.json`
- `artifacts/api/authorized-mutating-request.json`
- `artifacts/audit/audit-ledger.jsonl`
- `artifacts/audit/audit-ledger-verification.json`
- `closure-evidence.json`
