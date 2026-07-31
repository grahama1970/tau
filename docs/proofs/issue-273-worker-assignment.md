# Issue 273 Worker Assignment Proof

Ticket: <https://github.com/grahama1970/tau/issues/273>

Lease: `20260731T013506Z-codex-273`

## What Changed

Tau now has a deterministic worker assignment gate on the canonical scheduler path:

- `tau.worker_capability.v1` capability descriptors.
- `tau.worker_requirement.v1` requirements compiled from a DAG node runtime contract and policy/data boundary.
- Model-free capability matching with stable eligible/rejected candidate reasons.
- Deterministic selection by priority, worker id, and capability hash.
- Attempt-bound worker resource leases through `ResourceLeaseManager`.
- `tau.worker_assignment_receipt.v1` durable receipts admitted before adapter dispatch.
- Fail-closed behavior when no worker matches or explicit worker assignment lacks durable run-store/resource-lease support.
- Legacy direct adapters retain compatibility descriptors when a resource lease manager is present.

Workers remain dispatch targets, not evidence sources.

## Local Proof

```text
uv run ruff check src/tau_coding/dag_runtime/worker_assignment.py src/tau_coding/dag_runtime/scheduler.py tests/test_worker_assignment.py
All checks passed!

uv run ruff format --check src/tau_coding/dag_runtime/worker_assignment.py src/tau_coding/dag_runtime/scheduler.py tests/test_worker_assignment.py
3 files already formatted

uv run pytest tests/test_worker_assignment.py tests/test_dag_runtime_scheduler.py tests/test_dag_runtime_run_store.py tests/test_runtime_backend_contracts.py -q
95 passed in 4.67s

uv run pytest -q
3419 passed in 495.28s (0:08:15)
```

## Live E2E Proof

```text
TAU_ISSUE_273_LIVE_E2E=1 uv run python <inline live worker assignment proof>
PASS
```

Artifact:

```text
/tmp/tau-issue-273-worker-assignment-proof-20260731T015506Z-2341765/summary.json
```

The live e2e used real `run_dag_plan`, `SqliteDagRunStore`, and
`ResourceLeaseManager`. It exercised:

- selected worker receipt admission before adapter dispatch;
- deterministic rejected candidates for provider mismatch and missing capability;
- attempt-bound worker lease acquisition and release;
- replay preserving the admitted assignment count without redispatch;
- no-match failure before adapter dispatch.

## Resource Lease Regression Proof

```text
uv run python - <<'PY'
from pathlib import Path
from tau_coding.resource_lease_conformance import write_resource_lease_conformance
payload = write_resource_lease_conformance(Path('/tmp/tau-issue273-live-proof-20260731T014539Z-2139388/resource-lease-conformance.json'), allow_live_filesystem=True)
print(payload['status'])
print(payload['checks'])
print(payload['event_counts'])
PY
PASS
```

Artifact:

```text
/tmp/tau-issue273-live-proof-20260731T014539Z-2139388/resource-lease-conformance.json
```

The conformance run preserved scheduler concurrency and resource lease
acquire/release/denial/expiry behavior after adding compatibility worker leases.
