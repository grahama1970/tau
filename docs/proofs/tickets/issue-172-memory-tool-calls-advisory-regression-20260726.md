# Issue 172 Regression Proof: Memory Tool Calls Are Advisory

Ticket: https://github.com/grahama1970/tau/issues/172

## Scope

Issue #172 was reopened because the content-trust repair rejected a valid
Memory intent plus evidence-case pair in
`tests/test_project_dag.py::test_project_dag_memory_gate_allows_valid_intent_and_evidence_case`.

Root cause: the file-backed project DAG gate treated any Memory-supplied
`tool_calls` as a blocking error. That was too strict. The trust boundary must
prevent Memory from authorizing executable tool calls, but a Memory response can
still preserve proposed calls as advisory data when the route and separate
evidence case pass validation.

This patch changes the gate contract to:

- preserve Memory `tool_calls` under `advisory_tool_calls`
- expose executable `tool_calls` as an empty list
- allow dispatch when route, confidence, goal/target, and evidence-case checks
  pass
- keep existing blockers for inline evidence, missing intent, low confidence,
  route blockers, and evidence-case mismatch

## Changed Paths

- `src/tau_coding/memory_evidence_gate.py`
- `tests/test_memory_evidence_gate.py`
- `tests/test_project_dag.py`
- `docs/proofs/tickets/issue-172-memory-tool-calls-advisory-regression-20260726.md`

## Deterministic Commands

Reopened reproducer before repair:

```bash
uv run pytest -q \
  tests/test_project_dag.py::test_project_dag_memory_gate_allows_valid_intent_and_evidence_case \
  -vv
```

Result before repair:

```text
FAILED tests/test_project_dag.py::test_project_dag_memory_gate_allows_valid_intent_and_evidence_case
E  assert False is True
```

The failing receipt contained:

```text
memory_tool_calls_rejected
memory_intent_gate_not_passed
```

Reopened reproducer after repair:

```bash
uv run pytest -q \
  tests/test_project_dag.py::test_project_dag_memory_gate_allows_valid_intent_and_evidence_case \
  -vv
```

Result after repair:

```text
tests/test_project_dag.py::test_project_dag_memory_gate_allows_valid_intent_and_evidence_case PASSED
1 passed in 0.88s
```

Project DAG memory-gate subset:

```bash
uv run pytest -q tests/test_project_dag.py -k 'memory_gate'
```

Result:

```text
4 passed, 91 deselected in 0.69s
```

Content-trust, Memory auth, and self-fix focused suite:

```bash
uv run pytest -q \
  tests/test_memory_evidence_gate.py \
  tests/test_dag_route_memory.py \
  tests/test_self_fix_ticket_repair.py
```

Result:

```text
30 passed in 1.34s
```

Lint:

```bash
uv run ruff check \
  src/tau_coding/memory_evidence_gate.py \
  src/tau_coding/content_trust.py \
  src/tau_coding/self_fix_ticket_repair.py \
  src/tau_coding/self_fix_repair_loop.py \
  src/tau_coding/dag_route_memory.py \
  src/tau_coding/cli.py \
  tests/test_memory_evidence_gate.py \
  tests/test_dag_route_memory.py \
  tests/test_self_fix_ticket_repair.py \
  tests/test_project_dag.py
```

Result:

```text
All checks passed!
```

Compile:

```bash
uv run python -m py_compile \
  src/tau_coding/memory_evidence_gate.py \
  src/tau_coding/content_trust.py \
  src/tau_coding/self_fix_ticket_repair.py \
  src/tau_coding/self_fix_repair_loop.py \
  src/tau_coding/dag_route_memory.py \
  src/tau_coding/cli.py \
  tests/test_memory_evidence_gate.py \
  tests/test_dag_route_memory.py \
  tests/test_self_fix_ticket_repair.py \
  tests/test_project_dag.py
```

Result: exit code `0`.

Diff hygiene:

```bash
git diff --check
```

Result: exit code `0`.

## Evidence Mapping

- Valid evidence-backed Memory intent is admitted:
  `tests/test_project_dag.py::test_project_dag_memory_gate_allows_valid_intent_and_evidence_case`.
- Memory-supplied tool calls do not become executable tool calls:
  `tests/test_memory_evidence_gate.py::test_memory_evidence_gate_treats_memory_supplied_tool_calls_as_advisory`.
- Inline untrusted evidence in Memory intent remains blocked:
  `tests/test_project_dag.py::test_project_dag_memory_gate_blocks_inline_intent_evidence`.
- Authenticated Memory writes remain covered by:
  `tests/test_dag_route_memory.py::test_route_memory_sync_apply_posts_to_memory_with_approval`.
- Unauthenticated Memory writes remain covered by:
  `tests/test_dag_route_memory.py::test_route_memory_sync_apply_rejects_unauthenticated_memory_write`.
- GitHub issue text remains labeled as untrusted content and not human-authored:
  `tests/test_self_fix_ticket_repair.py::test_ticket_repair_labels_issue_text_as_untrusted_content`.

## Evidence Classification

- mocked: yes, for local pytest fixtures, local HTTP Memory test server, and
  monkeypatched repair-loop wiring.
- live: no provider calls for this regression proof.
- exercised: file-backed project DAG memory gate, compatibility memory evidence
  gate, advisory tool-call recording, existing content-trust blockers,
  authenticated/unauthenticated Memory write tests, lint, compile, and diff
  hygiene.
- remains unverified: Memory service-side enforcement of bearer tokens and
  model semantic resistance to injected untrusted content.
