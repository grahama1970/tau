# tau#298 Closure Proof

Issue: https://github.com/grahama1970/tau/issues/298

## Result

Tau now has a focused offline adversarial contract-conformance lane:

- retained manifest: `tests/contract_conformance/fixtures/mutation_manifest.json`
- executable lane: `tests/contract_conformance/test_adversarial_contract_conformance.py`
- focused command: `uv run python scripts/run_contract_conformance_live.py`
- machine-readable summary:
  `docs/proofs/tickets/issue-298-adversarial-contract-conformance/conformance-summary.json`

The lane covers source contract identities, numeric/string/boolean/non-finite
source mutations, route/join/context-binding mutations, attempt-result
admission, transition effects/deadlines, artifact-reference provenance,
source/exported payload mutation isolation, replay determinism, and validator
sensitivity.

## Proof

- `uv run python scripts/run_contract_conformance_live.py`
  - PASS; wrote `conformance-summary.json`
  - categories: 9 executed, 9 passed, 0 failed, 0 not exercised
  - cases: 13 executed, 13 passed
  - mocked: false
  - live: true
  - provider_live: false
- `uv run pytest -q tests/contract_conformance`
  - 13 passed in 0.64s
- `uv run pytest -q tests/contract_conformance tests/test_dag_contract_immutability.py tests/test_public_dag_contracts.py tests/test_generic_dag.py tests/test_project_dag.py tests/test_dag_plan.py tests/test_dag_transition_validation.py tests/test_node_input_manifest.py tests/test_node_completion_boundary.py`
  - 226 passed in 17.09s
- `uv run pytest -q`
  - 3558 passed in 544.71s (0:09:04)
- `uv run ruff check scripts/run_contract_conformance.py scripts/run_contract_conformance_live.py tests/contract_conformance/test_adversarial_contract_conformance.py`
  - All checks passed.

## Side-Effect Assertions

The conformance lane asserts:

- invalid source contracts do not create run/dispatch artifacts;
- invalid context binding output does not dispatch the consumer and records no
  consumer admission;
- invalid attempt result does not release the successor and records no accepted
  output admission;
- invalid transition payloads reject before commit;
- artifact-reference admission mismatch rejects dereference;
- repeated replay returns stable node, edge, and terminal state;
- monkeypatching the generic public validator lets a malformed source pass,
  proving the validator case is not vacuous.

## Remaining Scope

This structural conformance lane does not prove provider/model semantic quality,
browser/Memory/Herdr integration, or absence of every possible future validation
defect.
