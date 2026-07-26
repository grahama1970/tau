# tau#131 Native DAG Template Registry Proof

Ticket: https://github.com/grahama1970/tau/issues/131

## Change

Tau now has a native DAG template registry in
`src/tau_coding/dag_template_registry.py` with CLI access through:

- `tau dag-template-list`
- `tau dag-template-compile --template <name> --params <json> --out <dag.json> --receipt <receipt.json>`

The registry currently includes five explicit workflow patterns:

- `single-call`
- `prompt-chain`
- `reflection-loop`
- `roundtable`
- `compete`

Each template expands into a `tau.dag_contract.v1` contract with explicit nodes,
edges, required evidence, retry limits, and fail-closed conditions. Template
compilation performs no provider or model calls.

## Deterministic Checks

Commands run from `/tmp/tau-main-issue-137.7nchbf`:

```bash
uv run python -m py_compile src/tau_coding/dag_template_registry.py src/tau_coding/cli.py
uv run ruff check src/tau_coding/dag_template_registry.py src/tau_coding/cli.py tests/test_dag_template_registry.py
uv run pytest tests/test_dag_template_registry.py -q
```

Observed results:

- `py_compile`: exit 0
- `ruff check`: `All checks passed!`
- `pytest`: `4 passed`

## Template Compile Receipts

Artifact directory:

`docs/proofs/tickets/issue-131-dag-template-registry-20260726/`

Compile receipts:

- `single-call-compile-receipt.json`: `ok=true`, `status=PASS`, nodes=1, edges=1
- `prompt-chain-compile-receipt.json`: `ok=true`, `status=PASS`, nodes=2, edges=2
- `reflection-loop-compile-receipt.json`: `ok=true`, `status=PASS`, nodes=2, edges=2
- `roundtable-compile-receipt.json`: `ok=true`, `status=PASS`, nodes=3, edges=3
- `compete-compile-receipt.json`: `ok=true`, `status=PASS`, nodes=3, edges=3

Missing-field path:

- `roundtable-missing-compile.exitcode`: `1`
- `roundtable-missing-fields.json`: `status=INTERVIEW_REQUIRED`
- Missing fields: `handlers[1]`, `join`

## Local Fixture Execution

The generated `single-call` template was executed with Tau's local DAG runner:

```bash
uv run tau dag-run docs/proofs/tickets/issue-131-dag-template-registry-20260726/single-call-run-dag.json \
  --receipt-dir docs/proofs/tickets/issue-131-dag-template-registry-20260726/single-call-run \
  --agents-root docs/proofs/tickets/issue-131-dag-template-registry-20260726/agents
```

Terminal receipt:

`docs/proofs/tickets/issue-131-dag-template-registry-20260726/single-call-run/dag-receipt.json`

Receipt summary:

- `ok=true`
- `status=PASS`
- `mocked=false`
- `live=true`
- `provider_live=false`
- `selected_agents=["handler"]`
- observed edge: `handler` -> `human`

## Evidence Scope

- mocked: no
- live: yes, local Tau subprocess/DAG execution only
- provider/model calls: no
- proves: registry selection, required-field gating, template-to-DAG compilation,
  missing-field interview packet generation, and one local fixture execution of a
  compiled DAG
- does not prove: provider semantic quality, remote model execution, or human
  completion of the missing-field interview
