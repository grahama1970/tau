# Tau issue #156 proof notes

Ticket: <https://github.com/grahama1970/tau/issues/156>

Implemented Tau-side contract:

- `write_codebase_ingest_receipt()` writes `tau.codebase_ingest_receipt.v1`.
- Tau records a resumable codebase ingest state marker.
- Tau detects changed files by content hash and skips unchanged files.
- Tau prepares the existing `/ingest-code rescan --treesitter --code-index`
  command instead of implementing a second Memory client or extractor.
- Tau does not block the interactive path unless `start=true` is explicitly
  requested.

Focused checks:

```text
uv run pytest -q tests/test_codebase_ingest.py
...
3 passed in 0.46s

uv run ruff check src/tau_coding/codebase_ingest.py tests/test_codebase_ingest.py
All checks passed!

uv run python -m py_compile src/tau_coding/codebase_ingest.py
exit 0
```

Generated proof artifact:

```text
docs/proofs/tickets/issue-156-codebase-ingest-20260726/first-pass-receipt.json
status: QUEUED
changed_files: ["pkg/__init__.py", "pkg/mod.py"]

docs/proofs/tickets/issue-156-codebase-ingest-20260726/second-pass-receipt.json
status: SKIPPED
changed_files: []

docs/proofs/tickets/issue-156-codebase-ingest-20260726/edit-pass-receipt.json
status: QUEUED
changed_files: ["pkg/mod.py"]
```

Evidence boundary:

- This proves Tau's non-blocking coordinator, resumable state marker, and
  incremental change detection.
- This does not prove Memory graph write completeness, Tree-sitter extraction,
  point-in-time Memory recall, or TUI idle-scheduler integration.
