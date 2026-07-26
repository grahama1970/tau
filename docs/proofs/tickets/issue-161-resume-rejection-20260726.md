# Issue 161 Proof: Resume Rejection Is Typed And Fail-Closed

Issue: https://github.com/grahama1970/tau/issues/161

## Scope

This patch changes generic DAG resume behavior so an existing node receipt that
fails validation is not silently treated as absent and rerun. Invalid resume
receipts now emit `node_resume_rejected` and block the DAG with
`verdict=INVALID_RECEIPT` and `attempt_count=0`.

## Changed Behavior

- Command-node resume rejects any invalid prior receipt, including schema,
  work-order hash, goal hash, and local execution evidence failures.
- Skill-node resume now uses the shared generic node receipt validation for
  schema, node id, status/verdict, work-order hash, and goal hash, plus
  skill-provider/capability/artifact checks.
- `node_resume_rejected` events record expected schema, observed schema,
  validation errors, receipt path, and `resume_action=blocked_no_rerun`.
- Goal-hash mismatches fail closed without dispatching the node command.

## Proof Commands

```text
uv run python -m py_compile src/tau_coding/generic_dag.py tests/test_generic_dag.py tests/test_skill_dag_adapter.py
exit 0
```

```text
uv run pytest tests/test_generic_dag.py tests/test_skill_dag_adapter.py -q
...................................
35 passed in 3.32s
exit 0
```

```text
uv run ruff check src/tau_coding/generic_dag.py tests/test_generic_dag.py tests/test_skill_dag_adapter.py
All checks passed!
exit 0
```

```text
git diff --check
exit 0
```

## Evidence Mapping

- Schema-mismatched command receipt emits `node_resume_rejected` and no
  `node_dispatch`:
  `tests/test_generic_dag.py::test_generic_dag_rejects_schema_mismatched_resume_without_rerun`.
- Goal-hash mismatch blocks with `INVALID_RECEIPT`, `attempt_count=0`, and no
  rerun:
  `tests/test_generic_dag.py::test_generic_dag_rejects_resumed_receipt_from_changed_goal`.
- Stale work-order receipt no longer reruns:
  `tests/test_generic_dag.py::test_generic_dag_rejects_stale_work_order_receipt_on_resume`.
- Skill-node schema mismatch uses the same rejection event and no rerun:
  `tests/test_skill_dag_adapter.py::test_generic_dag_resume_rejects_schema_mismatched_skill_receipt_without_rerun`.

mocked: yes, for the skill adapter WebGPT transport fixture.

live: no provider calls. Deterministic local DAG execution, receipt validation,
event-log writes, and run-directory receipts were exercised.

What remains unverified: an explicit operator opt-in rerun mode. Current
operator rerun remains `resume=False`.
