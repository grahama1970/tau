# Issue #134 Proof: Canonical Workflow Proof Retention

Ticket: https://github.com/grahama1970/tau/issues/134

## Summary

Tau now retains the immutable-goal audit receipt, supplied proof JSON, and
desktop/mobile screenshots under:

`experiments/goal-locked-subagents/proofs/canonical-workflows/issue-134-20260726T134804Z/`

The retained manifest reports `PASS`, `mocked: false`, `live: true`,
`provider_live: false`, `artifact_count: 27`, and source ref
`669f833de65189e9c14c02f77c1df04da1ddf84e`.

## Commands And Results

```text
uv run ruff check scripts/run-immutable-goal-audit.py scripts/run-approved-release-browser-proof.py scripts/run-durable-qualification-browser-proof.py scripts/verify-durable-qualification-wheel.py
-> All checks passed!

uv run python -m py_compile scripts/run-immutable-goal-audit.py scripts/run-approved-release-browser-proof.py scripts/run-durable-qualification-browser-proof.py scripts/verify-durable-qualification-wheel.py
-> exit 0

uv run python scripts/run-repository-readiness-browser-proof.py ...
-> status PASS, mocked false, live true, provider_live false

uv run python scripts/run-operator-reference-browser-proof.py ...
-> status PASS, mocked false, live true, provider_live false

uv run python scripts/run-repository-evidence-map-browser-proof.py ...
-> status PASS, mocked false, live true, provider_live false

uv run python scripts/run-approved-release-browser-proof.py ...
-> status PASS, mocked false, live true, provider_live false

uv run python scripts/run-durable-qualification-browser-proof.py ...
-> status PASS, mocked false, live true, provider_live false

uv build --wheel --out-dir /tmp/tau-issue-134-canonical-proof-final-20260726/wheel
-> Successfully built tau-0.1.0-py3-none-any.whl

uv run python scripts/verify-durable-qualification-wheel.py ...
-> status PASS, publication_effect_count 1, repeated_resume_status PASS

uv run python scripts/run-immutable-goal-audit.py --repo . --ref 669f833de65189e9c14c02f77c1df04da1ddf84e ... --retention-root experiments/goal-locked-subagents/proofs/canonical-workflows/issue-134-20260726T134804Z
-> audit.status PASS, retention.status PASS, retained artifact_count 27
```

## Retained Artifacts

- Manifest:
  `experiments/goal-locked-subagents/proofs/canonical-workflows/issue-134-20260726T134804Z/manifest.json`
- Audit receipt:
  `experiments/goal-locked-subagents/proofs/canonical-workflows/issue-134-20260726T134804Z/audit/immutable-goal-audit.json`
- Supplied proof JSON:
  `experiments/goal-locked-subagents/proofs/canonical-workflows/issue-134-20260726T134804Z/supplied-proofs/`
- Desktop/mobile screenshots:
  `experiments/goal-locked-subagents/proofs/canonical-workflows/issue-134-20260726T134804Z/screenshots/`

## Proof Boundary

mocked: no
live: yes

This proves the clean-checkout audit and retention path for the canonical
workflow proof bundle. It does not prove human acceptance of the immutable goal,
provider/model semantic quality, or future proof runs without rerunning the
audit.
