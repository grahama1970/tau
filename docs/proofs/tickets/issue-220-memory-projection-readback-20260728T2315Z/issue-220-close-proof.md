# Issue #220 Close Proof

Issue: https://github.com/grahama1970/tau/issues/220

Current main before this proof-bundle commit:

```text
af0e70ed758a177783d6e7d768f6d3c500cf53ba refs/heads/main
```

Implementation readback:

```text
git merge-base --is-ancestor c40e93d2 HEAD
ancestor:0
```

Focused deterministic proof:

```text
uv run pytest -q tests/test_dag_runtime_memory_projection.py tests/test_dag_viewer_static_package.py tests/test_dag_viewer_server.py
```

Observed result:

```text
28 passed in 9.88s
```

Live harness command:

```text
PYTHONPATH=src uv run python scripts/memory-projection-live.py docs/proofs/tickets/issue-220-memory-projection-readback-20260728T2315Z
```

Live receipt readback:

```text
schema: tau.memory_projection_harness_receipt.v1
mocked: false
live: true
ok: true
same_transaction_rollback: true
outage_degraded_not_blocked: true
execution_authority_intact_after_outage: true
recovery_projects_and_dedupes: true
run_status_after_outage: RUNNING
```

Retained raw artifacts:

```text
docs/proofs/tickets/issue-220-memory-projection-readback-20260728T2315Z/dag-run.sqlite3
docs/proofs/tickets/issue-220-memory-projection-readback-20260728T2315Z/memory-projection-receipt.json
docs/proofs/tickets/issue-220-memory-projection-readback-20260728T2315Z/issue-220-close-proof.md
```

Scope boundary:

This closes #220's idempotent Memory projection boundary proof. SQLite remains
the scheduler authority; Memory projection remains an observable outbox state
and cannot rewrite accepted Tau execution state.
