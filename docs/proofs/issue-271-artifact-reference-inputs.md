# Issue #271 Proof: Hash-Addressed By-Reference Artifact Inputs

## Change

Tau now supports `materialization_mode="by_reference"` for declared DAG context
bindings. A by-reference binding resolves selected upstream receipt descriptors
through Tau's receipt admission ledger and passes a small
`tau.artifact_reference.v1` object to the downstream adapter instead of copying
the artifact bytes into node context.

Reference construction is fail-closed:

- requires a matching admitted upstream artifact;
- verifies file hash and byte size against the admission row;
- rejects path escape and symlink escape by resolving the real path under the
  run evidence root;
- enforces schema compatibility and configured byte budget;
- records the selected reference and disposition in `tau.node_input_manifest.v1`;
- exposes dereference through `tau_coding.dag_runtime.artifact_reference`, not
  arbitrary model-supplied filesystem paths.

## Deterministic Proof

```text
uv run pytest tests/test_node_input_manifest.py -q
12 passed in 0.88s

uv run ruff check --select F,E9 src/tau_coding/dag_runtime/artifact_reference.py src/tau_coding/dag_runtime/model.py src/tau_coding/dag_runtime/node_input_manifest.py src/tau_coding/dag_runtime/run_store.py src/tau_coding/dag_runtime/scheduler.py tests/test_node_input_manifest.py
All checks passed!

uv run ruff format --check src/tau_coding/dag_runtime/artifact_reference.py src/tau_coding/dag_runtime/model.py src/tau_coding/dag_runtime/node_input_manifest.py src/tau_coding/dag_runtime/run_store.py src/tau_coding/dag_runtime/scheduler.py tests/test_node_input_manifest.py
6 files already formatted

uv run pytest tests/test_node_input_manifest.py tests/test_dag_runtime_scheduler.py tests/test_dag_plan.py tests/test_dag_runtime_run_store.py tests/test_dag_runtime_admission_table.py -q
91 passed in 5.25s

uv run pytest tests/test_tui_app.py::test_tui_prompt_ctrl_c_twice_quits_from_empty_prompt -q
1 passed in 0.94s

uv run pytest -q
3408 passed in 506.75s (0:08:26)
```

The first full-suite run produced one unrelated TUI Ctrl-C flake:
`test_tui_prompt_ctrl_c_twice_quits_from_empty_prompt`. The isolated rerun
passed, and the second full-suite run passed.

## Live Non-Mocked Scheduler Sanity

Receipt:

```text
/tmp/tau-issue-271-artifact-reference-proof-20260731T002040Z-433417/summary.json
```

Readback summary:

- `mocked: false`
- `live: true`
- positive case: status `PASS`, reference schema `tau.artifact_reference.v1`,
  consumer dereferenced only selected JSON key `section`, and hidden payload was
  not embedded in context
- missing admission: status `BLOCKED`, verdict
  `NODE_INPUT_REFERENCE_ADMISSION_MISSING`
- hash mismatch after admission: status `BLOCKED`, verdict
  `NODE_INPUT_REFERENCE_HASH_MISMATCH`
- path escape: status `BLOCKED`, verdict `NODE_INPUT_REFERENCE_PATH_ESCAPE`
