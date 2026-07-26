# Issue #166 Proof: DAG Run Retention And Archive-Before-Delete

Issue: https://github.com/grahama1970/tau/issues/166

## Scope

Implemented bounded local retention for Tau DAG run directories:

- `tau dag-retention-expire --root <dir> --archive-dir <dir>`
- count policy via `--keep-count`
- age policy via `--older-than-days`
- dry-run mode via `--dry-run`
- JSON receipt output via `--receipt`
- archive-before-delete tarball and archive manifest with SHA-256 hashes
- `.gitignore` entries for newly generated local run journals and transient run artifacts

Changed files:

- `.gitignore`
- `src/tau_coding/cli.py`
- `src/tau_coding/dag_runtime/retention.py`
- `tests/test_dag_retention.py`

## Verification

Commands run from `/tmp/tau-main-issue-137.7nchbf`:

```text
$ uv run ruff check src/tau_coding/dag_runtime/retention.py src/tau_coding/cli.py tests/test_dag_retention.py
All checks passed!
```

```text
$ uv run python -m py_compile src/tau_coding/dag_runtime/retention.py src/tau_coding/cli.py tests/test_dag_retention.py
exit 0
```

```text
$ uv run pytest -q tests/test_dag_retention.py
..                                                                       [100%]
2 passed in 0.97s
```

```text
$ git diff --check
exit 0
```

## Evidence Details

`tests/test_dag_retention.py` creates real local generic DAG run directories with:

- `dag-run.sqlite3`
- `run-receipt.json`
- `events.jsonl`
- node receipts
- source DAG spec

The focused regression checks:

- three runs are discovered under a configured retention root
- `keep_count=2` expires only the oldest run
- the expired run directory is removed after an archive is written
- the archive tar contains `dag-run.sqlite3` and `run-receipt.json`
- the archive manifest contains non-empty `archive_sha256`, `journal_sha256`, and `receipt_sha256`
- the retention receipt is written to disk
- retained run directories still inspect successfully
- retained run directories resume through Tau replay with `replayed_event_count > 0`
- the public `tau dag-retention-expire` CLI writes a receipt and expires the expected run

## Mock/Live Boundary

- mocked: no provider/model calls
- live: yes for local filesystem, SQLite DAG run journals, tar archive creation, CLI invocation, generic DAG execution through local subprocesses, and replay/inspect against retained run artifacts
- provider_live: no

This proves local Tau DAG retention behavior and archive-before-delete mechanics. It does not prove a scheduled background GC service, remote Herdr workspace cleanup, or semantic correctness of archived provider/model outputs.

`.gitignore` now prevents new generated run journals/transient artifacts from being added accidentally. Existing historical tracked journal/proof files remain tracked until a separate cleanup ticket removes or migrates them.
