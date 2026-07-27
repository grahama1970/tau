# Issue 193 Proof

Implemented `tau worker-controlled-data-conformance` as a live filesystem command that launches an available local Python worker subprocess, validates its structured worker result through Tau's existing OMP worker adapter, denies controlled-data access for an unverified actor, and then exercises a verified correction path.

## Commands

- `PYTHONPATH=src uv run tau worker-controlled-data-conformance --allow-live-worker --allow-live-filesystem --output docs/proofs/tickets/issue-193-worker-controlled-data-conformance-20260727/worker-controlled-data-conformance.json`
  - Exit code: 0
  - Receipt status: `PASS`
  - `mocked=false`, `live=true`, `provider_live=false`
  - `failed_checks=[]`

- `PYTHONPATH=src uv run python -m py_compile src/tau_coding/worker_controlled_data_conformance.py src/tau_coding/cli.py`
  - Exit code: 0

- `PYTHONPATH=src uv run ruff check src/tau_coding/worker_controlled_data_conformance.py src/tau_coding/cli.py`
  - Exit code: 0
  - Output: `All checks passed!`

- `PYTHONPATH=src uv run pytest tests/test_coding_worker_adapters.py tests/test_itar_boundary.py tests/test_policy_profile.py`
  - Exit code: 0
  - Result: 132 passed

- `rg -n "TAU_CONTROLLED_DATA_NEVER_LEAK_193" docs/proofs/tickets/issue-193-worker-controlled-data-conformance-20260727/artifacts/unauthorized-outputs docs/proofs/tickets/issue-193-worker-controlled-data-conformance-20260727/artifacts/worker-repo || true`
  - Matches: 0

## Required #193 Checks

- `worker_readiness_receipt_present=true`
- `worker_execution_process_ran=true`
- `worker_result_artifact_validated=true`
- `controlled_data_denial_receipt_present=true`
- `correction_authorization_receipt_present=true`
- `correction_action_receipt_present=true`
- `correction_validation_receipt_present=true`
- `no_restricted_data_leaked_into_unauthorized_outputs=true`

## Independent Readback

- `artifacts/receipts/worker-validation.json`: `status=PASS`, `mocked=false`, `live=true`, `alert_codes=[]`
- `artifacts/receipts/controlled-data-denial.json`: `status=BLOCKED` with actor/access alert codes
- `artifacts/receipts/correction-authorization.json`: `status=PASS`
- `artifacts/receipts/correction-validation.json`: `status=PASS`, `unauthorized_leak_count=0`

## Artifacts

- `worker-controlled-data-conformance.json`
- `artifacts/receipts/worker-readiness.json`
- `artifacts/receipts/worker-execution.json`
- `artifacts/receipts/worker-validation.json`
- `artifacts/receipts/controlled-data-denial.json`
- `artifacts/receipts/correction-authorization.json`
- `artifacts/receipts/correction-action.json`
- `artifacts/receipts/correction-validation.json`
- `closure-evidence.json`
