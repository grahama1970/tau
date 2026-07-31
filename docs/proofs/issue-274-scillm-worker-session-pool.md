# Issue 274: SciLLM Worker Session Pool Proof

Issue: https://github.com/grahama1970/tau/issues/274
Lease: `20260731T021154Z-codex-274`

## Implementation Scope

Tau scheduler worker assignment now supports a Tau-owned SciLLM worker session
pool after `tau.worker_assignment_receipt.v1` selection and before node
dispatch. The pool records lifecycle/reset/cleanup/benchmark receipts and
requires a fresh attempt-scoped worker resource lease for every use.

The implementation is intentionally bounded to the SciLLM adapter contract. It
does not claim provider semantic quality, browser/Herdr/CLI pooling, worktree
pooling, or broad performance improvement.

## Non-Mocked Conformance Receipt

Command:

```bash
uv run python -m tau_coding.scillm_worker_pool_conformance \
  --output /tmp/tau-issue274-scillm-worker-pool-proof-20260731T023250Z-3183799/summary.json \
  --allow-live-filesystem
```

Receipt:

```text
/tmp/tau-issue274-scillm-worker-pool-proof-20260731T023250Z-3183799/summary.json
```

Result:

```text
status=PASS
mocked=false
live=true
provider_live=false
failed_checks=[]
```

Checks:

```text
assignment_receipts_admitted=true
benchmark_receipt_written=true
cleanup_receipt_written=true
distinct_attempt_ids=true
full_slot_blocked_before_dispatch=true
lifecycle_receipts_admitted=true
reset_receipts_admitted=true
restart_recovery_quarantined_inflight_session=true
run_store_integrity_pass=true
same_generation_reused=true
scheduler_status_pass=true
second_pre_claim_context_empty=true
two_attempts_observed=true
worker_leases_acquired=true
worker_leases_released=true
```

## Focused Checks

Command:

```bash
uv run pytest tests/test_scillm_worker_session_pool.py tests/test_worker_assignment.py -q
```

Result:

```text
12 passed in 0.90s
```

Command:

```bash
uv run pytest tests/test_scillm_worker_session_pool.py \
  tests/test_worker_assignment.py \
  tests/test_dag_runtime_scheduler.py \
  tests/test_dag_runtime_run_store.py \
  tests/test_runtime_backend_contracts.py -q
```

Result:

```text
103 passed in 5.26s
```

Command:

```bash
uv run ruff check src/tau_coding/scillm_worker_pool_conformance.py \
  src/tau_coding/dag_runtime/worker_session_pool.py \
  src/tau_coding/dag_runtime/scheduler.py \
  tests/test_scillm_worker_session_pool.py
uv run ruff format --check src/tau_coding/scillm_worker_pool_conformance.py \
  src/tau_coding/dag_runtime/worker_session_pool.py \
  src/tau_coding/dag_runtime/scheduler.py \
  tests/test_scillm_worker_session_pool.py
```

Result:

```text
All checks passed.
4 files already formatted.
```

## Full Suite

Command:

```bash
uv run pytest -q
```

Result:

```text
3427 passed in 496.59s (0:08:16)
```

Note: an earlier full-suite run reported one TUI prompt Ctrl-C timing failure
outside the scheduler/worker-session change path:
`tests/test_tui_app.py::test_tui_prompt_ctrl_c_twice_quits_from_empty_prompt`.
The focused rerun passed (`1 passed in 0.95s`) and the subsequent full rerun
passed as shown above.
