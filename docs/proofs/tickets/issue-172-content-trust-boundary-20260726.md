# Issue 172 Proof: Content Trust And Memory Auth Boundary

Issue: https://github.com/grahama1970/tau/issues/172

## Scope

This patch adds a deterministic content-trust boundary for GitHub issue intake,
prevents Memory intent from authorizing tool calls, corrects the Memory gate
proof scope, and requires an auth token before Tau writes route-memory documents
to Memory `/upsert`.

## Changed Behavior

- External GitHub issue text is wrapped as `tau.content_block.v1` with
  `trust=untrusted` and `instruction_authority=false`.
- Ticket-derived self-fix handoffs use `previous_subagent=ticket-intake`, not
  `human`.
- Memory intent `tool_calls` are preserved as `advisory_tool_calls`, while
  executable `tool_calls` is empty and the gate blocks with
  `memory_tool_calls_rejected`.
- Route-memory sync with `--apply` fails closed without
  `memory_auth_token` or `TAU_MEMORY_AUTH_TOKEN`; successful writes send a
  bearer token but never record the token in receipts.
- The threat model now names ingested-content instruction laundering as a Tau
  threat with an explicit remaining boundary.

## Proof Commands

```text
uv run python -m py_compile src/tau_coding/content_trust.py src/tau_coding/generated_ticket.py src/tau_coding/self_fix_ticket_repair.py src/tau_coding/self_fix_repair_loop.py src/tau_coding/memory_evidence_gate.py src/tau_coding/dag_route_memory.py src/tau_coding/cli.py
exit 0
```

```text
uv run ruff check src/tau_coding/content_trust.py src/tau_coding/generated_ticket.py src/tau_coding/self_fix_ticket_repair.py src/tau_coding/self_fix_repair_loop.py src/tau_coding/memory_evidence_gate.py src/tau_coding/dag_route_memory.py src/tau_coding/cli.py tests/test_memory_evidence_gate.py tests/test_dag_route_memory.py tests/test_self_fix_ticket_repair.py
All checks passed!
exit 0
```

```text
uv run pytest tests/test_memory_evidence_gate.py tests/test_dag_route_memory.py tests/test_self_fix_ticket_repair.py -q
.............................
29 passed in 1.30s
exit 0
```

```text
git diff --check
exit 0
```

## Evidence Mapping

- Ingested content is labeled untrusted and not human-authored:
  `tests/test_self_fix_ticket_repair.py::test_ticket_repair_labels_issue_text_as_untrusted_content`.
- Memory response tool calls cannot become executable tool calls:
  `tests/test_memory_evidence_gate.py::test_memory_evidence_gate_rejects_memory_supplied_tool_calls`.
- Gate proof scope no longer claims an unproven prompt-grounding boundary:
  `tests/test_memory_evidence_gate.py::test_memory_evidence_gate_proof_scope_matches_enforced_boundary`.
- Unauthenticated Memory writes are rejected:
  `tests/test_dag_route_memory.py::test_route_memory_sync_apply_rejects_unauthenticated_memory_write`.
- Authenticated Memory writes send an authorization header without recording the
  token:
  `tests/test_dag_route_memory.py::test_route_memory_sync_apply_posts_to_memory_with_approval`.

mocked: yes, for local HTTP Memory test server and monkeypatched repair loop
wiring checks.

live: no provider calls. Deterministic local Python code, CLI parsing, receipt,
and local HTTP behavior were exercised.

What remains unverified: Memory service-side enforcement of the bearer token and
model semantic resistance to injected untrusted content.
