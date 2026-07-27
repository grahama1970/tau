# Proof for grahama1970/tau#186

## Changes

- Registered a Tau-owned native skill DAG provider pair: `runtime_handshake` / `tau`.
- Extended the canonical scheduler conformance command to exercise:
  - native generic DAG skill nodes via Tau runtime handshake
  - a canonical map workload with 3 deterministic items
  - 3 item-specific child DAGs launched through `uv run tau dag-run`
  - exact join cardinality over the child DAG results

## Live proof

Command:

```bash
PYTHONPATH=src uv run tau canonical-scheduler-conformance --allow-live-filesystem --output docs/proofs/tickets/issue-186-canonical-scheduler-conformance-20260727T1652Z/canonical-scheduler-conformance.json
```

Result:

- exit code: 0
- receipt status: `PASS`
- mocked: `false`
- live: `true`
- provider_live: `false`
- missing_surfaces: `[]`
- required surfaces true: `command_node`, `validator_node`, `transaction_node`, `skill_node`, `human_boundary`, `join`, `retry`, `durable_resume`, `targeted_repair`, `map_node`, `child_dag`, `conditional_route`

Map/child aggregate:

- artifact: `runs/map-child-dag/map-run/map-child-dag-conformance.json`
- status: `PASS`
- item_count: 3
- deterministic_child_ids: `child-alpha`, `child-bravo`, `child-charlie`
- exact_join_cardinality: `true`
- child_count: 3
- all_children_passed: `true`
- child `tau dag-run` exits: 0, 0, 0
- errors: `[]`

Native skill node:

- status: `PASS`
- skill_provider: `tau`
- capability: `runtime_handshake`
- skill_live: `true`

## Supporting checks

```bash
PYTHONPATH=src uv run python -m py_compile src/tau_coding/canonical_scheduler_conformance.py src/tau_coding/skill_dag_adapter.py
PYTHONPATH=src uv run ruff check src/tau_coding/canonical_scheduler_conformance.py src/tau_coding/skill_dag_adapter.py
PYTHONPATH=src uv run pytest tests/test_skill_dag_adapter.py tests/test_cli.py --tb=short
```

Results:

- py_compile: exit 0
- ruff: exit 0, all checks passed
- pytest: 245 passed in 15.84s

## Boundary

This proves the live local scheduler conformance workload for #186. It does not prove provider/model semantic quality, secure sandbox enforcement, resource lease enforcement, or a first-class dynamic map-node schema beyond this canonical workload.

