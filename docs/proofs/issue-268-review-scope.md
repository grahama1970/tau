# Issue #268 Review Scope Proof

## Scope

This proof covers GitHub issue #268:

> Bind reviewer findings to the exact DAG plan, attempts, and artifact hashes
> reviewed.

## Implementation

- Added companion schema `tau.review_scope.v1` in `src/tau_coding/review_findings.py`.
- Added canonical `scope_sha256` generation and stale-scope validation.
- Added `SqliteDagRunStore.review_scope_snapshot()` and
  `SqliteDagRunReader.review_scope_snapshot()` over the durable run journal and
  `receipt_admissions` table.
- Added `tau review-findings --current-review-scope <scope.json>` so an
  operator can compare reviewer claims against current run state.

## Deterministic Checks

```bash
uv run ruff check --select F,E9 \
  src/tau_coding/review_findings.py \
  src/tau_coding/dag_runtime/run_store.py \
  src/tau_coding/cli.py \
  tests/test_review_findings.py \
  tests/test_dag_runtime_admission_table.py
```

Result: `All checks passed!`

```bash
uv run python -m py_compile \
  src/tau_coding/review_findings.py \
  src/tau_coding/dag_runtime/run_store.py \
  src/tau_coding/cli.py \
  tests/test_review_findings.py \
  tests/test_dag_runtime_admission_table.py
```

Result: exit 0.

```bash
uv run pytest tests/test_review_findings.py tests/test_dag_runtime_admission_table.py -q
```

Result: `44 passed in 0.91s`.

```bash
uv run pytest -q
```

Result: `3386 passed in 518.18s (0:08:38)`.

## Non-Mocked CLI Readback

Proof bundle:

```text
/tmp/tau-issue-268-review-scope-proof-20260730T221955Z-1949381
```

Artifacts:

- `current-review-scope.json`
- `stale-current-review-scope.json`
- `review-findings-pass.json`
- `pass-receipt.json`
- `stale-receipt.json`
- `stale-exit-code.txt`
- `summary.json`

Summary readback:

```json
{
  "live": true,
  "mocked": false,
  "pass_scope_status": "PASS",
  "pass_status": "PASS",
  "provider_live": false,
  "stale_alert_codes": ["review_scope_stale"],
  "stale_reason_codes": [
    "review_scope_attempts_changed",
    "review_scope_artifacts_changed",
    "review_scope_journal_advanced",
    "review_scope_hash_changed"
  ],
  "stale_status": "BLOCKED"
}
```

`stale-exit-code.txt` contains `1`, proving the real CLI exits blocked when a
previous reviewer `PASS` is replayed against a newer current scope.

## Proof Boundary

Proves:

- unchanged review scope validates and replays;
- adding required nodes, replacing admitted artifacts, accepting newer attempts,
  or advancing the journal blocks old reviewer output;
- canonical scope hash ignores input ordering;
- a reviewer-authored `PASS` cannot override stale-scope failure;
- historical unscoped `tau.review_findings.v1` receipts remain readable but do
  not satisfy a scoped gate.

Does not prove:

- reviewer semantic correctness;
- provider-live model quality;
- every future scheduler integration path;
- the whole Tau immutable goal.
