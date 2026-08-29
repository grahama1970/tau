# Issue #331 proof: canonical DAG ladder manifest and rung-1 clean-checkout proof

## Change

Tau now has a committed `tau.canonical_dag_ladder_manifest.v1` naming the five packaged workflow rungs, their topology classes, acceptance boundaries, and proof requirements.

Tau also has a retained agentic eval for rung 1. The eval clones the current source commit into a separate clean checkout, runs `tau workflows run repository-readiness --require-clean`, reads back the workflow result and generic DAG run receipt, verifies artifact digests, and proves negative controls detect mutated and missing artifact references.

## Proof boundary

This proves the ladder contract and rung 1 clean-checkout execution. It does not prove rungs 2-5 clean-checkout completion, provider-live execution, dynamic React Flow progress, or human acceptance of the full GOAL.md outcome.

## Validation

- `uv run python -m py_compile scripts/agentic-eval-tau-dag-ladder-rung1.py` passed.
- `uv run pytest tests/test_canonical_dag_examples.py -q --tau-suite=all` passed: 9 tests.
- `/home/graham/workspace/experiments/agent-skills/skills/agentic-evals/run.sh run evals/tau_dag_ladder_rung1_agentic_eval.json --output local/agentic-evals/tau-dag-ladder-rung1-agentic-evals-report.json` returned `readiness=READY`, `live=true`, `mocked=false`, claim `tau.dag_ladder.rung1_clean_checkout: PROVEN`, 2 cases, 4 trials, 0 failures.
- `/home/graham/workspace/experiments/agent-skills/skills/agentic-evals/run.sh run evals/tau_feature_coverage_agentic_eval.json --output local/agentic-evals/tau-feature-coverage-agentic-evals-report.json` returned `readiness=READY`; readback of `local/agentic-evals/tau-feature-coverage-proof.json` shows 14 manifests, 14 claims, and no findings.
- `uv run tau project-status build --out /tmp/tau-current-after-rung1.json --github-snapshot docs/status/github-snapshot.json && uv run tau project-status verify --status /tmp/tau-current-after-rung1.json --github-snapshot docs/status/github-snapshot.json` returned PASS.

## Retained artifacts

- `docs/proofs/acceptance/canonical-dag-ladder-manifest.json`
- `evals/tau_dag_ladder_rung1_agentic_eval.json`
- `scripts/agentic-eval-tau-dag-ladder-rung1.py`
- `local/agentic-evals/tau-dag-ladder-rung1-agentic-evals-report.json`
- `local/agentic-evals/tau-dag-ladder-rung1-proof.json`
- `local/agentic-evals/tau-dag-ladder-rung1-negative-proof.json`
- `local/agentic-evals/tau-feature-coverage-agentic-evals-report.json`
- `local/agentic-evals/tau-feature-coverage-proof.json`
