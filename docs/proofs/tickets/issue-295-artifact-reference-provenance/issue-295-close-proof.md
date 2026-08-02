# tau#295 Close Proof: Artifact Reference Provenance Validation

Issue: https://github.com/grahama1970/tau/issues/295
Lease: 20260802T182516Z-codex-295
Generated: 2026-08-02T19:46:00Z

## Scope

This repair hardens `tau.artifact_reference.v1` dereference so a correct file
hash is not enough to consume an artifact. Dereference now requires an active
admission reader plus the expected run, producer attempt, consumer node, binding
policy, and data-boundary identity.

The contract now validates:

- complete `reference_sha256` over the reference envelope
- exact admission row by admitted artifact ID
- receipt kind, path, URI, size, and SHA-256 against the admission row
- producer run, node, and attempt identity
- expected consumer node identity
- binding policy hash
- data-boundary hash
- embedded JSON schema compatibility when present
- selector policy before returning selected content

## Code Paths Changed

- `src/tau_coding/dag_runtime/artifact_reference.py`
- `src/tau_coding/dag_runtime/node_input_manifest.py`
- `tests/test_node_input_manifest.py`
- `docs/proofs/tickets/issue-295-artifact-reference-provenance/issue-295-live-readback.py`

## Deterministic Proof

Focused artifact-reference/input-manifest slice:

```text
uv run pytest -q tests/test_node_input_manifest.py
30 passed in 1.72s
```

Broader DAG runtime slice:

```text
uv run pytest -q tests/test_node_input_manifest.py tests/test_dag_runtime_scheduler.py tests/test_dag_runtime_run_store.py tests/test_dag_runtime_replay.py tests/test_dag_transition_validation.py
95 passed in 6.29s
```

Full repository test suite:

```text
uv run pytest -q
3535 passed in 527.32s (0:08:47)
```

Formatting/static checks:

```text
uv run ruff check src/tau_coding/dag_runtime/artifact_reference.py src/tau_coding/dag_runtime/node_input_manifest.py tests/test_node_input_manifest.py docs/proofs/tickets/issue-295-artifact-reference-provenance/issue-295-live-readback.py
All checks passed!
```

```text
uv run mypy src/tau_coding/dag_runtime/artifact_reference.py src/tau_coding/dag_runtime/node_input_manifest.py docs/proofs/tickets/issue-295-artifact-reference-provenance/issue-295-live-readback.py
Success: no issues found in 3 source files
```

```text
git diff --check
exit 0
```

## Live Non-Mocked Sanity

Command:

```text
uv run python docs/proofs/tickets/issue-295-artifact-reference-provenance/issue-295-live-readback.py
```

Artifact:

```text
docs/proofs/tickets/issue-295-artifact-reference-provenance/live-readback.json
```

Readback summary:

```json
{
  "schema": "tau.issue_295.live_readback.v1",
  "mocked": false,
  "live": true,
  "valid_dereference": {
    "dereference_receipt_schema": "tau.artifact_dereference_receipt.v1",
    "manifest_records_dereference_receipt": true
  },
  "mutation_matrix": {
    "reference_sha256": "ARTIFACT_REFERENCE_ENVELOPE_HASH_MISMATCH",
    "admitted_artifact_id": "ARTIFACT_REFERENCE_ADMISSION_MISSING",
    "size_bytes": "ARTIFACT_REFERENCE_SIZE_MISMATCH",
    "uri": "ARTIFACT_REFERENCE_URI_MISMATCH",
    "producer": "ARTIFACT_REFERENCE_PRODUCER_MISMATCH",
    "consumer": "ARTIFACT_REFERENCE_CONSUMER_MISMATCH",
    "receipt_kind": "ARTIFACT_REFERENCE_RECEIPT_KIND_MISMATCH",
    "policy_sha256": "ARTIFACT_REFERENCE_POLICY_MISMATCH",
    "data_boundary_sha256": "ARTIFACT_REFERENCE_DATA_BOUNDARY_MISMATCH",
    "artifact_schema": "ARTIFACT_REFERENCE_EMBEDDED_SCHEMA_MISMATCH",
    "selector": "ARTIFACT_REFERENCE_SELECTOR_POLICY_MISMATCH"
  },
  "wrong_context_reuse": {
    "wrong_run": "ARTIFACT_REFERENCE_ADMISSION_MISSING",
    "wrong_attempt": "ARTIFACT_REFERENCE_PRODUCER_MISMATCH",
    "wrong_consumer": "ARTIFACT_REFERENCE_CONSUMER_MISMATCH"
  },
  "embedded_schema_block": {
    "status": "BLOCKED",
    "verdict": "NODE_INPUT_REFERENCE_EMBEDDED_SCHEMA_MISMATCH",
    "calls": [
      "producer"
    ]
  },
  "admission_delete_denied": "receipt_admissions is append-only",
  "admission_ambiguity": "eliminated_by_exact_admitted_artifact_id_lookup"
}
```

The live check is `mocked:false` and `live:true`. It does not call a paid model
provider; it exercises the real Tau scheduler, SQLite journal, admitted receipt
table, input-manifest path, receipt files, and artifact dereference path.

## What This Proves

- A valid admitted by-reference artifact still resolves and can return a
  selected JSON field.
- Successful dereference emits a typed `tau.artifact_dereference_receipt.v1`
  receipt and the input manifest records it.
- Mutating each ticket-listed provenance, policy, boundary, schema, and selector
  field fails before adapter use.
- Correct byte hash cannot compensate for altered provenance or context.
- Wrong-run, wrong-attempt, and wrong-consumer reuse fail closed.
- Claimed schema versus embedded schema mismatch blocks before the consumer
  node runs.
- Receipt admissions remain append-only and exact-ID lookup eliminates ambiguous
  admission reuse.

## What This Does Not Prove

- The full Tau immutable goal is not accepted by this ticket alone.
- This does not prove provider/model semantic correctness.
- This does not close unrelated open Tau issues.
- This does not prove remote object storage behavior, which is a non-goal for
  this ticket.

## Worktree Boundary

Worktree audit before close:

```text
/home/graham/workspace/experiments/agent-skills/skills/best-practices-github-ticket/scripts/audit-worktrees.sh --repo /home/graham/workspace/experiments/tau --json
{"ok":false,"repo":"/home/graham/workspace/experiments/tau","total":30,"tmp":1,"detached":3,"prunable":0,"dirty_secondary":2,"tmp_paths":["/tmp/tau-immutable-goal-main-20260721T000650Z"],"prunable_paths":[],"dirty_secondary_paths":["/home/graham/workspace/experiments/tau-causal-replay","/home/graham/workspace/experiments/tau-gs001"]}
```

Those worktrees pre-existed this ticket and are retained rather than removed.
The unrelated local `.ask_artifacts/` directory also remains unstaged.
