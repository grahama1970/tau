# Tau testing policy

Tau tests are regression guards, not proof that Tau works.

## Rule

`$agentic-evals` is required for any claim that Tau works as expected.

A passing `pytest` suite may support a change by proving deterministic mechanics did not regress, but it cannot close an agentic workflow, ticket, feature, or product-readiness claim by itself.

## Keep unit tests only when they guard a deterministic contract

Keep small tests for:

- schema validation
- pure parser behavior
- hash-chain and signing math
- deterministic path/resource resolution
- fail-closed policy predicates
- small serialization/deserialization contracts

Do not add or keep broad unit tests that only re-state the implementation, mock the real boundary, or prove a fixture the test author invented.

## Required proof layers

For Tau work, report proof in this order:

1. `$agentic-evals` report with `readiness=READY`, `mocked=false`, and `live=true` for the relevant capability claim.
2. Live read-back artifact from the thing under test: run receipt, CLI JSON, browser screenshot receipt, ledger verification output, or service response.
3. Deterministic regression gate: targeted `pytest`, `ruff`, or static validation for code touched.

If layer 1 is missing, the work is not proven. If layer 2 is missing, the eval is not enough. If only layer 3 exists, report it as unproven regression coverage.

## Test diet target

The long legacy pytest suite remains a migration backlog. New work should move coverage from large self-serving unit suites into declared agentic-eval capability claims. When touching a large test module, either reduce it to deterministic contract cases or replace the workflow-level assertions with an agentic eval fixture.
