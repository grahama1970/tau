# Issue #154 Tau-Side Route-Memory Provenance Proof

Date: 2026-07-26

## Scope

This repair covers the Tau-owned side of #154:

- DAG signal receipts now pass `run_id` into route-memory candidates.
- Route-memory candidate receipts carry source signal receipt path/hash, source DAG receipt path/hash, run id, DAG id, and goal hash.
- Projected Memory documents carry episode id, run id, node ids, goal hash, source receipt links/hashes, candidate receipt link/hash, and a nested provenance object.
- Route-memory sync refuses to project or upsert unattributed candidate receipts.

The remaining recommendation expansion and precise episode retraction behavior is graph-side Memory work per the owner correction on #154. Tau does not implement Memory AQL or graph retraction inside `src/tau_coding/dag_route_memory.py`.

## Proof Commands

```text
uv run ruff check src/tau_coding/dag_route_memory.py src/tau_coding/dag_signals.py tests/test_dag_route_memory.py tests/test_run_status.py
```

Result:

```text
All checks passed!
```

```text
uv run python -m py_compile src/tau_coding/dag_route_memory.py src/tau_coding/dag_signals.py tests/test_dag_route_memory.py
```

Result: exit code 0.

```text
uv run pytest -q tests/test_dag_route_memory.py tests/test_run_status.py
```

Result:

```text
57 passed in 1.35s
```

```text
git diff --check
```

Result: exit code 0.

## Evidence Classification

- mocked: no
- live: yes, local deterministic filesystem receipt generation and local HTTP Memory `/upsert` test server
- provider_live: no
- exercised: candidate receipt generation, sync document projection, apply-mode HTTP upsert path, and fail-closed rejection of missing provenance
- remains unverified in Tau: Memory graph-side recommendation expansion and precise retraction of a prior episode's derived facts

## Disposition

Tau-side provenance write/rejection behavior is repaired. Full #154 should remain blocked or be split until Memory implements the graph-side expansion/retraction acceptance criteria.
