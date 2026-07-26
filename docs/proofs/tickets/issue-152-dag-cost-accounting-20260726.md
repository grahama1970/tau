# Issue #152 Proof: Per-Run Token/Cost Accounting And Budget Blocker

Issue: https://github.com/grahama1970/tau/issues/152

## Scope

Implemented provider-reported estimate accounting for generic DAG runs and packaged workflow receipts:

- per-node `cost_accounting`
- aggregate run `cost_accounting`
- provider-reported estimate language via `estimated_cost_is_billing_truth: false`
- optional spec budget ceiling using `budget.estimated_cost_usd`
- run-local raised ceiling via `budget-override.json` so resume does not mutate the source DAG
- fail-closed `BUDGET_EXCEEDED` blocker with consumed vs allowed estimate
- viewer snapshot projection for node and run accounting
- redaction safe-list for aggregate usage-counter keys such as `input_tokens` and `output_tokens`

Changed files:

- `src/tau_coding/generic_dag.py`
- `src/tau_coding/workflows/runner.py`
- `src/tau_coding/dag_viewer/projection.py`
- `src/tau_coding/dag_viewer/redaction.py`
- `tests/test_dag_cost_accounting.py`
- `tests/test_dag_live_projection.py`

## Verification

Commands run from `/tmp/tau-main-issue-137.7nchbf`:

```text
$ uv run ruff check src/tau_coding/generic_dag.py src/tau_coding/workflows/runner.py src/tau_coding/dag_viewer/projection.py src/tau_coding/dag_viewer/redaction.py tests/test_dag_cost_accounting.py tests/test_dag_live_projection.py
All checks passed!
```

```text
$ uv run python -m py_compile src/tau_coding/generic_dag.py src/tau_coding/workflows/runner.py src/tau_coding/dag_viewer/projection.py src/tau_coding/dag_viewer/redaction.py tests/test_dag_cost_accounting.py tests/test_dag_live_projection.py
exit 0
```

```text
$ uv run pytest -q tests/test_dag_cost_accounting.py
..                                                                       [100%]
2 passed in 0.86s
```

```text
$ uv run pytest -q tests/test_dag_cost_accounting.py tests/test_dag_viewer_redaction.py tests/test_dag_live_projection.py tests/test_dag_viewer_compare.py tests/test_dag_viewer_historical.py
.....................................                                    [100%]
37 passed in 6.63s
```

```text
$ git diff --check
exit 0
```

## Evidence Details

`tests/test_dag_cost_accounting.py` proves:

- a run with two local receipt-backed nodes records per-node token/cost attribution
- aggregate totals equal the sum of node values
- `estimated_cost_is_billing_truth` is false
- viewer snapshot exposes run total and per-node accounting
- a low budget stops the second node with `BUDGET_EXCEEDED`
- the blocker records consumed estimate and allowed estimate
- previous accepted work remains accepted
- writing `budget-override.json` with a higher ceiling lets resume reuse accepted receipts and reach `PASS`

`tests/test_dag_viewer_redaction.py` and adjacent viewer tests prove the usage-counter redaction change preserves projected counters while maintaining the existing viewer/redaction contract.

## Mock/Live Boundary

- mocked: provider/model calls are stubbed by local receipt-writing workers with known usage values
- live: yes for local subprocess DAG execution, durable SQLite journal/replay, receipt aggregation, budget blocker behavior, run-local budget override, and viewer projection
- provider_live: no

This proves Tau's DAG accounting, viewer projection, and budget-gated resume mechanics. It does not prove provider billing accuracy, live paid-provider token reporting, or that estimated cost equals invoice truth.
