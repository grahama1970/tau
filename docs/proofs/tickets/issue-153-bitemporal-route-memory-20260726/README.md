# Tau issue #153 proof notes

Ticket: <https://github.com/grahama1970/tau/issues/153>

Implemented Tau-side contract:

- Route-memory candidates now carry `valid_from`, `valid_to`, `supersedes`,
  `superseded_by`, and `temporal_status`.
- Route-memory Memory documents project the same fields at top level and inside
  provenance.
- Superseded facts are projected as historical documents with
  `temporal_status: "superseded"` rather than being deleted.
- Current facts are projected with `valid_to: null` and
  `temporal_status: "current"`.

Focused checks:

```text
uv run pytest -q tests/test_dag_route_memory.py
.................
17 passed in 1.13s

uv run ruff check src/tau_coding/dag_route_memory.py tests/test_dag_route_memory.py
All checks passed!

uv run python -m py_compile src/tau_coding/dag_route_memory.py
exit 0
```

Generated dry-run artifact:

```text
docs/proofs/tickets/issue-153-bitemporal-route-memory-20260726/sync-dry-run-receipt.json
candidate_status: PASS
sync_status: PASS
projected_document_count: 2
temporal_statuses: ["superseded", "current"]
valid_to: ["2026-07-27T00:00:00Z", null]
```

Evidence boundary:

- This proves Tau's write-side projection contract for temporal validity and
  invalidation-not-deletion.
- This does not implement Memory-owned graph ranking, point-in-time recall, AQL,
  or current-validity ranking inside the Memory service.
