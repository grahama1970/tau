# Issue 188 Resource Lease Proof

Commands:

```bash
PYTHONPATH=src uv run python -m py_compile src/tau_coding/dag_runtime/resource_leases.py src/tau_coding/dag_runtime/scheduler.py src/tau_coding/resource_lease_conformance.py src/tau_coding/cli.py
uv run ruff check src/tau_coding/dag_runtime/resource_leases.py src/tau_coding/dag_runtime/scheduler.py src/tau_coding/resource_lease_conformance.py src/tau_coding/cli.py
PYTHONPATH=src uv run tau resource-lease-conformance --allow-live-filesystem --output docs/proofs/tickets/issue-188-resource-lease-conformance-v2-20260727/resource-lease-conformance.json
PYTHONPATH=src uv run pytest tests/test_dag_runtime_scheduler.py tests/test_dag_runtime_run_store.py tests/test_dag_runtime_replay.py tests/test_dag_runtime_correction.py tests/test_git_worktree_leases.py
```

Readback:

- `resource-lease-conformance.json`: `status=PASS`
- `mocked=false`, `live=true`, `provider_live=false`
- `failed_checks=[]`
- scheduler max observed concurrency: `2`
- durable event counts: `resource_lease_acquired=4`, `resource_lease_released=3`, `resource_lease_denied=1`, `resource_lease_expired=1`
- focused regression tests: `134 passed`

