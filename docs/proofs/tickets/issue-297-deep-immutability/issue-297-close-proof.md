# tau#297 Closure Proof

Issue: https://github.com/grahama1970/tau/issues/297

## Result

Tau now stores validated public DAG/domain contract data without caller-owned
mutable aliases:

- Project DAG validation freezes JSON-bearing fields with immutable dict/list
  compatible containers and wraps the node map.
- Generic DAG command vectors are stored as tuples.
- Transition, node-input-manifest, and node-completion-boundary result objects
  defensively freeze nested JSON payloads at construction.
- `to_payload()` paths still return fresh mutable JSON values for serialization
  and consumer mutation without altering source objects.

## Proof

- `uv run python docs/proofs/tickets/issue-297-deep-immutability/issue-297-live-readback.py`
  - PASS; wrote `docs/proofs/tickets/issue-297-deep-immutability/live-readback.json`
  - mocked: false
  - live: true
  - provider_live: false
- `uv run pytest -q tests/test_dag_contract_immutability.py`
  - 5 passed in 0.64s
- `uv run pytest -q tests/test_dag_contract_immutability.py tests/test_public_dag_contracts.py tests/test_generic_dag.py tests/test_project_dag.py tests/test_dag_plan.py tests/test_dag_transition_validation.py tests/test_node_input_manifest.py tests/test_node_completion_boundary.py`
  - 213 passed in 16.67s
- `uv run pytest -q`
  - 3545 passed in 549.92s (0:09:09)
- `uv run ruff check docs/proofs/tickets/issue-297-deep-immutability/issue-297-live-readback.py tests/test_dag_contract_immutability.py src/tau_coding/public_dag_contracts.py src/tau_coding/project_dag.py src/tau_coding/generic_dag.py src/tau_coding/dag_runtime/transition.py src/tau_coding/dag_runtime/node_input_manifest.py src/tau_coding/node_completion_boundary.py`
  - All checks passed.

## Readback

`live-readback.json` records eight positive checks:

- project contract immutable after source mutation
- nested project contract mutation blocked
- project plan `to_payload()` isolation
- project plan hash round trip
- generic command tuple immutable after source mutation
- generic live local DAG passed
- transition completion raw result immutable
- transition completion nested mutation blocked

## Remaining Scope

This does not prove paid provider behavior or every possible future DAG field.
