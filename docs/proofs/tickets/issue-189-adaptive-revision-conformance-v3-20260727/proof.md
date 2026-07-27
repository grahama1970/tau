# Issue 189 Adaptive Revision Conformance Proof

Ticket: grahama1970/tau#189

## Live E2E Gate

Command:

```bash
PYTHONPATH=src uv run tau adaptive-revision-conformance --allow-live-filesystem --output docs/proofs/tickets/issue-189-adaptive-revision-conformance-v3-20260727/adaptive-revision-conformance.json
```

Result: exit code 0.

Receipt:

- `docs/proofs/tickets/issue-189-adaptive-revision-conformance-v3-20260727/adaptive-revision-conformance.json`
- `status=PASS`
- `mocked=false`
- `live=true`
- `provider_live=false`
- `failed_checks=[]`
- `source_plan_sha256=sha256:db1f0e80e9674fada8ce57ceabe5cda821164add82113401c830b9da8504608b`
- `revised_plan_sha256=sha256:87b773e2d3059b980b6b4627b06f5d63b51d5f7ef29b99de49f386ba197d9271`
- `superseded_nodes=["reviewer"]`
- `accepted_work_preserved=true`
- `unauthorized_expansion_denied=true`

The live receipt records:

- source run blocked at `SAFE_ADAPTIVE_REVISION_CHECKPOINT`
- source `coder` completed while `reviewer` remained pending
- bounded expansion validation, policy, and apply receipts
- revised run completed `coder`, `validator`, and `reviewer`
- viewer-state artifact for checkpoint/revision transparency
- unauthorized proposal denied fail-closed

## Focused Checks

Commands:

```bash
PYTHONPATH=src uv run python -m py_compile src/tau_coding/adaptive_revision_conformance.py src/tau_coding/cli.py
uv run ruff check src/tau_coding/adaptive_revision_conformance.py src/tau_coding/cli.py
PYTHONPATH=src uv run pytest tests/test_dag_expansion.py
```

Results:

- `py_compile`: exit code 0
- `ruff`: exit code 0, `All checks passed!`
- `pytest tests/test_dag_expansion.py`: exit code 0, `24 passed`

## Proof Scope

Proves:

- Tau can checkpoint a source DAG before dispatching a pending node.
- Tau can validate, policy-check, and apply a bounded adaptive DAG expansion into a new DAG artifact.
- Tau records old and new plan hashes and explicit superseded pending nodes.
- Tau preserves accepted checkpoint work across the revised run.
- Tau denies an unauthorized expansion proposal fail-closed.
- Tau emits a viewer-state artifact that can show the checkpoint and revision receipts.

Does not prove:

- In-place mutation of an already-running scheduler route.
- Provider/model semantic quality.
- Distributed scheduler coordination across hosts.
- Human approval UX for selecting between multiple revision proposals.
