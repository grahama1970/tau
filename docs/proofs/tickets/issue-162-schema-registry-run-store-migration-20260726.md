# Issue #162 Proof: Schema Registry And DAG Run Store Migration

Issue: https://github.com/grahama1970/tau/issues/162
Lease: `20260726T184423Z-codex-162`
Commit target: `grahama1970/tau@main`

## Changed Scope

- Added `src/tau_coding/schema_registry.py` for canonical `tau.<name>.v<N>`
  parsing and same-family version-skew errors.
- Routed runtime backend contract schema checks through the registry helper so
  future same-family payloads fail with schema-version skew before unknown-field
  checks.
- Bumped the SQLite DAG run store to schema version 2 with
  `dag_store_migrations`, `PRAGMA user_version`, and a bounded v1-to-v2 upgrade
  loop.
- Preserved compatible read access for v1/v2 stores while future store versions
  fail closed as `dag_run_store_schema_mismatch` with `actual` and `expected`.
- Updated run-status DAG viewer summaries so journal schema skew is reported as
  schema skew, not as an absent/missing journal.
- Repaired runtime-event journal redaction so redacted runtime payloads refresh
  their inner hashes before persistence and duplicate comparison.

## Deterministic Proof

All commands were run from `/tmp/tau-main-issue-137.7nchbf`.

```text
uv run ruff check \
  src/tau_coding/schema_registry.py \
  src/tau_coding/dag_runtime/run_store.py \
  src/tau_coding/runtime_backends/contracts.py \
  src/tau_coding/run_status.py \
  tests/test_schema_registry.py \
  tests/test_dag_runtime_run_store.py \
  tests/test_runtime_backend_contracts.py \
  tests/test_run_status.py \
  tests/test_runtime_event_bridge.py
```

Result: `All checks passed!`

```text
uv run python -m py_compile \
  src/tau_coding/schema_registry.py \
  src/tau_coding/dag_runtime/run_store.py \
  src/tau_coding/runtime_backends/contracts.py \
  src/tau_coding/run_status.py \
  tests/test_schema_registry.py \
  tests/test_dag_runtime_run_store.py \
  tests/test_runtime_backend_contracts.py \
  tests/test_run_status.py \
  tests/test_runtime_event_bridge.py
```

Result: exit code 0.

```text
uv run pytest -q \
  tests/test_schema_registry.py \
  tests/test_dag_runtime_run_store.py \
  tests/test_runtime_backend_contracts.py \
  tests/test_run_status.py \
  tests/test_runtime_event_bridge.py \
  tests/test_dag_runtime_replay.py \
  tests/test_dag_viewer_historical.py \
  tests/test_dag_live_projection.py
```

Result: `177 passed in 7.10s`.

```text
git diff --check
```

Result: exit code 0.

## What Was Exercised

- `test_parse_tau_schema_id_returns_name_and_version` proves schema parsing
  extracts namespace, name, version, and family.
- `test_schema_acceptance_reports_same_family_version_skew` proves future
  same-family schema versions report version skew.
- `test_schema_acceptance_allows_registered_legacy_versions` proves explicit
  legacy acceptance works for known migrations.
- `test_store_migrates_v1_journal_and_preserves_resume_state` proves a v1
  SQLite DAG journal reopens under the current store, records a 1-to-2 migration,
  preserves a running run, and remains resumable.
- `test_store_rejects_future_journal_with_version_details` proves a future
  journal fails closed with `actual=999 expected=2`.
- `test_run_status_reports_dag_viewer_store_schema_skew` proves run status
  exposes `dag_run_store_schema_mismatch` instead of collapsing skew into a
  missing journal.
- `test_runtime_contract_parsers_report_future_schema_version_skew_before_extras`
  proves a newer runtime requirement payload with extra fields reports schema
  skew before unknown-property rejection.
- `tests/test_runtime_event_bridge.py` additionally proves runtime-event journal
  redaction remains hash-consistent after the run-store persistence change.

## Non-Closure Signal Recorded

I also ran an over-broad adjacent command:

```text
uv run pytest -q \
  tests/test_dag_runtime_replay.py \
  tests/test_runtime_event_bridge.py \
  tests/test_dag_viewer_historical.py \
  tests/test_dag_live_projection.py \
  tests/test_cli.py
```

Result: `101 failed, 191 passed in 27.24s`.

The actionable store-adjacent failure from that run was
`test_sensitive_observation_is_redacted_without_hash_oracle`; it was repaired
and is covered by the passing `tests/test_runtime_event_bridge.py` run above.
The remaining failures were concentrated in `tests/test_cli.py` argument/parser
and JSON-output baseline failures and were not used as closure proof for #162.

## Evidence Classification

- mocked: no provider/service mocks used as proof
- live: no external provider or network service exercised
- exercised: real local SQLite journals, real schema parsing, real run-status
  projection, real pytest filesystem stores
- unverified: full repository test suite health and unrelated CLI baseline
  failures
