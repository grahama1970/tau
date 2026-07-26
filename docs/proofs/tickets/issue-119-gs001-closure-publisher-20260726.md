# Issue #119 Proof: GS001 Closure Publisher Current Goal Replay

Issue: https://github.com/grahama1970/tau/issues/119

## Scope

Changed Tau only:

- `src/tau_coding/gs001_closure_publisher.py`
- `src/tau_coding/cli.py`
- `src/tau_coding/project_dag.py`
- `tests/test_gs001_closure_publisher.py`
- committed proof receipts under `docs/proofs/tickets/`

## What Changed

- Added `tau gs001-closure-publish`, a narrow closure-publisher replay command
  for committed GS001 closure-state bundles.
- The command reads the Tau DAG contract, closure-state JSON, terminal receipt,
  visual receipt, closure HTML page, and screenshot, then writes a Tau terminal
  replay receipt with hash-bound artifact references.
- It preserves current goal-hash continuity across the DAG contract,
  closure-state record, and terminal receipt.
- It marks stale expected goal hashes as `BLOCKED` with
  `verdict: STALE_GOAL_HASH` while still writing the blocked receipt.
- Added the GS001 fail-closed invariant codes used by the cited pdf_oxide DAG
  contract to Tau's project-DAG registry:
  `anti_overfit_inspection_failed`, `expected_contract_not_locked`, and
  `patch_path_violation`.

## Deterministic Proof

Command:

```bash
uv run ruff format src/tau_coding/project_dag.py tests/test_gs001_closure_publisher.py src/tau_coding/gs001_closure_publisher.py src/tau_coding/cli.py
```

Result:

```text
1 file reformatted, 3 files left unchanged
```

Command:

```bash
uv run python -m py_compile src/tau_coding/project_dag.py src/tau_coding/gs001_closure_publisher.py src/tau_coding/cli.py tests/test_gs001_closure_publisher.py
```

Result: exit 0.

Command:

```bash
uv run pytest -q tests/test_gs001_closure_publisher.py tests/test_project_dag.py::test_cli_dag_run_dispatches_project_dag_contract tests/test_project_dag.py::test_project_dag_blocks_reviewer_goal_hash_mismatch tests/test_project_dag.py::test_project_dag_evidence_manifest_blocks_missing_required_kind
```

Result:

```text
5 passed in 0.87s
```

## Live Local Replay Against Cited pdf_oxide Commit

Clean checkout:

```text
/tmp/pdf_oxide-gs001-119
HEAD 09dd3b183a5184ffa5e22b83704f1b871c35543a
```

Current-goal command:

```bash
uv run tau gs001-closure-publish \
  --repo-root /tmp/pdf_oxide-gs001-119 \
  --dag .tau/gs001-execution-dag.json \
  --closure-state artifacts/pdf-lab/loop-runs/gs001-closure-publication-20260720T2335Z/gs001-closure-state.json \
  --terminal-receipt artifacts/pdf-lab/loop-runs/gs001-closure-publication-20260720T2335Z/terminal-receipt.json \
  --visual-receipt artifacts/pdf-lab/loop-runs/gs001-closure-publication-20260720T2335Z/receipts/gs001-closure-page-visual-receipt.json \
  --out docs/proofs/tickets/issue-119-gs001-current-replay-receipt.json \
  --expected-goal-hash sha256:ca56881acd36f5fdffffafc2a2ee73bbfd806df820f2fe0bd50ec52e794308ca
```

Result:

```text
ok True
status PASS
verdict PASS
terminal_status pending_human
goal_hash sha256:ca56881acd36f5fdffffafc2a2ee73bbfd806df820f2fe0bd50ec52e794308ca
source_commit 09dd3b183a5184ffa5e22b83704f1b871c35543a
```

Committed receipt:

- `docs/proofs/tickets/issue-119-gs001-current-replay-receipt.json`

The receipt references the committed pdf_oxide closure-state JSON, closure HTML,
terminal receipt, visual receipt JSON, and screenshot with SHA-256 hashes.

Stale-goal command:

```bash
uv run tau gs001-closure-publish \
  --repo-root /tmp/pdf_oxide-gs001-119 \
  --dag .tau/gs001-execution-dag.json \
  --closure-state artifacts/pdf-lab/loop-runs/gs001-closure-publication-20260720T2335Z/gs001-closure-state.json \
  --terminal-receipt artifacts/pdf-lab/loop-runs/gs001-closure-publication-20260720T2335Z/terminal-receipt.json \
  --visual-receipt artifacts/pdf-lab/loop-runs/gs001-closure-publication-20260720T2335Z/receipts/gs001-closure-page-visual-receipt.json \
  --out docs/proofs/tickets/issue-119-gs001-stale-goal-receipt.json \
  --expected-goal-hash sha256:old-goal
```

Result:

```text
exit 1
ok False
status BLOCKED
verdict STALE_GOAL_HASH
terminal_status stale_goal_hash
errors ['stale_goal_hash']
```

Committed stale receipt:

- `docs/proofs/tickets/issue-119-gs001-stale-goal-receipt.json`

## Evidence Classification

- mocked: no
- live: yes, local CLI execution against a clean checkout of the cited
  `pdf_oxide` commit
- provider_live: no
- What was exercised: Tau CLI parsing, GS001 DAG contract validation,
  fail-closed invariant registry lookup, goal-hash continuity checks,
  artifact hash references, current-hash receipt publication, and stale-hash
  rejection.
- What remains unverified: provider/model quality and human acceptance are not
  part of this ticket.
