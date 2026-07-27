# Issue 192 Proof

Implemented `tau project-profile-conformance` as a live filesystem conformance command for authoritative project profile and project spine dispatch gates.

## Commands

- `PYTHONPATH=src uv run tau project-profile-conformance --allow-live-filesystem --output docs/proofs/tickets/issue-192-project-profile-conformance-20260727/project-profile-conformance.json`
  - Exit code: 0
  - Receipt status: `PASS`
  - `mocked=false`, `live=true`, `provider_live=false`
  - `failed_checks=[]`

- `PYTHONPATH=src uv run python -m py_compile src/tau_coding/project_profile_conformance.py src/tau_coding/cli.py`
  - Exit code: 0

- `PYTHONPATH=src uv run ruff check src/tau_coding/project_profile_conformance.py src/tau_coding/cli.py`
  - Exit code: 0
  - Output: `All checks passed!`

- `PYTHONPATH=src uv run pytest tests/test_project_profile.py tests/test_project_spine.py`
  - Exit code: 0
  - Result: 18 passed

## Required #192 Checks

- `valid_profile_permits_dispatch=true`
- `parent_policy_broadening_denied=true`
- `stale_lineage_denied_with_receipt=true`
- `incompatible_capability_provider_denied=true`
- `accepted_dispatch_records_profile_spine_hashes=true`

## Artifacts

- `project-profile-conformance.json`
- `artifacts/valid-dispatch-receipt.json`
- `artifacts/parent-policy-broadening-receipt.json`
- `artifacts/stale-lineage-receipt.json`
- `artifacts/incompatible-provider-receipt.json`
- `closure-evidence.json`
