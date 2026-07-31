# Issue #270 Proof: Typed Context Projection And Node Input Manifest

## Change

Tau now resolves declared `DagPlanContextBinding` records into a
`tau.node_input_manifest.v1` before each node adapter runs. The manifest records
the run, plan, node, attempt, binding policy, activated control edge, selected
schema/hash, disposition, omission/invalid reason, and canonical manifest hash.

The default compiled binding is backward compatible:

- `accepted_source_schemas: ["*"]`
- `selector_kind: accepted_output`
- `materialization_mode: by_value`
- `on_missing: omit`
- `on_invalid: omit`

Bindings can opt into schema selection with `artifact_by_schema` or
`receipt_by_schema`. Missing or invalid inputs with `block` or `fail` policy
settle the attempt without calling the adapter.

## Deterministic Proof

```text
uv run ruff check --select F,E9 src/tau_coding/dag_runtime/model.py src/tau_coding/dag_runtime/node_input_manifest.py src/tau_coding/dag_runtime/scheduler.py src/tau_coding/dag_runtime/compiler.py src/tau_coding/dag_runtime/run_store.py tests/test_node_input_manifest.py tests/test_dag_runtime_admission_table.py
All checks passed!

uv run python -m py_compile src/tau_coding/dag_runtime/model.py src/tau_coding/dag_runtime/node_input_manifest.py src/tau_coding/dag_runtime/scheduler.py src/tau_coding/dag_runtime/compiler.py src/tau_coding/dag_runtime/run_store.py tests/test_node_input_manifest.py tests/test_dag_runtime_admission_table.py
exit 0

uv run pytest tests/test_node_input_manifest.py tests/test_dag_runtime_scheduler.py tests/test_dag_plan.py tests/test_dag_runtime_run_store.py -q
75 passed in 4.68s

uv run pytest tests/test_dag_runtime_admission_table.py::test_enforcement_blocks_pass_claim_with_torn_receipt tests/test_node_input_manifest.py -q
6 passed in 0.69s

uv run ruff check --select F,E9 src/tau_coding/dag_runtime/model.py src/tau_coding/dag_runtime/node_input_manifest.py src/tau_coding/dag_runtime/scheduler.py src/tau_coding/dag_runtime/compiler.py src/tau_coding/dag_runtime/run_store.py tests/test_node_input_manifest.py tests/test_dag_runtime_admission_table.py
All checks passed!

uv run ruff format --check src/tau_coding/dag_runtime/model.py src/tau_coding/dag_runtime/node_input_manifest.py src/tau_coding/dag_runtime/scheduler.py src/tau_coding/dag_runtime/compiler.py src/tau_coding/dag_runtime/run_store.py tests/test_node_input_manifest.py tests/test_dag_runtime_admission_table.py
7 files already formatted

uv run pytest tests/test_node_input_manifest.py tests/test_dag_runtime_admission_table.py::test_enforcement_blocks_pass_claim_with_torn_receipt tests/test_dag_runtime_scheduler.py::test_scheduler_preserves_declared_accepted_input_order -q
7 passed in 1.58s

uv run pytest -q
3401 passed in 477.82s (0:07:57)
```

## Live Non-Mocked Scheduler Sanity

Receipt:

```text
/tmp/tau-issue-270-context-manifest-proof-20260731T000005Z-4042741/summary.json
```

Readback summary:

- `mocked: false`
- `live: true`
- pass case: status `PASS`, verdict `PASS`, 3 manifest admissions,
  2 consumer inputs, manifest hash matched, hidden artifact not exposed
- block case: status `BLOCKED`, verdict `NODE_INPUT_MISSING`,
  consumer adapter was not called, consumer binding reason
  `accepted_output_missing`
