# tau#174 Storage Redaction Proof

Ticket: https://github.com/grahama1970/tau/issues/174

## Change

Tau now applies a shared storage redactor before the main durable write
boundaries that can carry command lines, stdout, stderr, tool arguments, or tool
results:

- local runtime capture/artifact writes
- handoff dispatch receipt writes
- DAG run-store staged results and event payloads
- session JSONL and HTML exports
- project DAG receipt/progress JSON helpers

The viewer redactor still omits raw output for browser projection, but the
storage redactor preserves useful text while masking credential substrings.

## Deterministic Checks

Commands run from `/tmp/tau-main-issue-137.7nchbf`:

```bash
uv run ruff check src/tau_coding/dag_viewer/redaction.py src/tau_coding/runtime_backends/local.py src/tau_coding/handoff_dispatch.py src/tau_coding/dag_runtime/run_store.py src/tau_coding/session_export.py src/tau_coding/project_dag.py tests/test_dag_viewer_redaction.py tests/test_storage_redaction_boundaries.py
uv run pytest tests/test_dag_viewer_redaction.py tests/test_storage_redaction_boundaries.py -q
uv run python -m py_compile src/tau_coding/dag_viewer/redaction.py src/tau_coding/runtime_backends/local.py src/tau_coding/handoff_dispatch.py src/tau_coding/dag_runtime/run_store.py src/tau_coding/session_export.py src/tau_coding/project_dag.py tests/test_dag_viewer_redaction.py tests/test_storage_redaction_boundaries.py
git diff --check
```

Observed results:

- `ruff check`: `All checks passed!`
- `pytest`: `7 passed`
- `py_compile`: exit 0
- `git diff --check`: exit 0

## Live Local Proof Bundle

Artifact directory:

`docs/proofs/tickets/issue-174-storage-redaction-20260726/`

Generated proof receipt:

`docs/proofs/tickets/issue-174-storage-redaction-20260726/proof-receipt.json`

Receipt summary:

- `ok=true`
- `mocked=false`
- `live=true`
- `provider_live=false`
- `dispatch_status=COMPLETED`
- `secret_leak_paths=[]`

Additional proof artifacts:

- `dispatch/dispatch-receipt.json`
- `dispatch/command-artifacts/runtime/runtime-capture.json`
- `sqlite-query-receipt.json`
- `session.jsonl`
- `session.html`

The proof generator used a token in the command line and in subprocess stdout,
queried the SQLite journal rows after staging a command result, exported session
JSONL/HTML with tool arguments and tool result bodies, and then scanned the
artifact directory for the raw token. No committed proof artifact contains the
raw proof token.

## Evidence Scope

- mocked: no
- live: yes, local subprocess/dispatch and local SQLite journal
- provider/model calls: no
- proves: storage redaction before the targeted durable artifact boundaries for
  command args, stdout, stderr, staged SQLite results/events, and session exports
- does not prove: external provider/service behavior or redaction coverage for
  every future artifact writer outside these patched boundaries
