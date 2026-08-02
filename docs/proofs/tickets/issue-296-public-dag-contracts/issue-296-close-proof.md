# tau#296 Close Proof: strict public DAG contract validation

Ticket: https://github.com/grahama1970/tau/issues/296

## Result

Tau now applies an explicit strict public boundary to both public DAG source
formats:

- `tau.dag_contract.v1`
- `tau.generic_dag_spec.v1`

Unknown public fields are rejected unless they are declared contract fields or
placed under `extensions`. Numeric/string/bool coercion paths for public
attempt counts, timeout values, roles/executors, paths, and node command-spec
fields are rejected visibly before dispatch. Explicit extensions are preserved
in compiled `DagPlan.source_extensions` and change the canonical plan hash.

## Live / Mock Status

- mocked: no
- live: yes
- provider_live: no

No paid provider calls were required. The live readback used real local Tau DAG
runners and subprocess workers.

## Proof Commands

```bash
uv run python docs/proofs/tickets/issue-296-public-dag-contracts/issue-296-live-readback.py
```

Result:

```text
{"receipt": "/home/graham/workspace/experiments/tau/docs/proofs/tickets/issue-296-public-dag-contracts/live-readback.json", "status": "PASS"}
```

```bash
uv run python scripts/check_public_dag_contract_schema.py
```

Result:

```text
PASS /home/graham/workspace/experiments/tau/experiments/goal-locked-subagents/schemas/tau.public_dag_contract_keys.v1.json
```

```bash
uv run pytest -q tests/test_public_dag_contracts.py tests/test_generic_dag.py tests/test_project_dag.py tests/test_project_dag_join_policies.py tests/test_dag_template_registry.py tests/test_dag_plan.py tests/test_execution_profiles.py tests/test_runtime_backend_contracts.py tests/test_browser_dag_handler.py
```

Result:

```text
231 passed in 24.83s
```

```bash
uv run pytest -q
```

Result:

```text
3540 passed in 546.52s (0:09:06)
```

```bash
uv run ruff check src/tau_coding/public_dag_contracts.py src/tau_coding/project_dag.py src/tau_coding/generic_dag.py src/tau_coding/dag_runtime/compiler.py src/tau_coding/dag_template_registry.py src/tau_coding/dag_runtime/artifact_reference.py tests/test_public_dag_contracts.py tests/test_project_dag_join_policies.py tests/test_dag_plan.py tests/test_coding_session.py scripts/check_public_dag_contract_schema.py docs/proofs/tickets/issue-296-public-dag-contracts/issue-296-live-readback.py
```

Result:

```text
All checks passed!
```

```bash
uv run mypy src/tau_coding/public_dag_contracts.py src/tau_coding/dag_runtime/compiler.py scripts/check_public_dag_contract_schema.py docs/proofs/tickets/issue-296-public-dag-contracts/issue-296-live-readback.py
```

Result:

```text
Success: no issues found in 4 source files
```

```bash
git diff --check
```

Result: exit code 0.

## Live Readback Checks

Receipt: `docs/proofs/tickets/issue-296-public-dag-contracts/live-readback.json`

All checks in that receipt are true:

- `valid_project_dag_passed`
- `valid_project_extensions_hash_bound`
- `valid_generic_dag_passed`
- `valid_generic_json_yaml_plan_parity`
- `invalid_project_rejected_before_dispatch`
- `invalid_generic_rejected_before_dispatch`
- `schema_drift_check_passed`

## Notes

Broad mypy over legacy `project_dag.py`/`generic_dag.py` still reports existing
typing debt unrelated to the new strict contract module. The narrow typed
surface added for this ticket is clean, and the full runtime test suite passes.
