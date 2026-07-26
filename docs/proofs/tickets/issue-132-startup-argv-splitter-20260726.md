# Issue #132 Startup Argv Splitter Proof

Date: 2026-07-26

Issue: https://github.com/grahama1970/tau/issues/132

## Scope

Fixed the CLI startup extension-flag splitter so it only consumes extension
flags before the first positional token. After a command token is present, the
command and every following argument are passed unchanged to the command parser.

Also removed the hardcoded command allowlist that left non-allowlisted commands
broken, added help handling for affected manual parsers, kept empty extension
flag dictionaries out of TUI/print runner calls, kept DAG diagnostic logs out of
JSON CLI stdout, and made handoff command dispatch stdout match its persisted
redacted receipt.

## Changed Files

```text
src/tau_coding/cli.py
docs/proofs/tickets/issue-132-startup-argv-splitter-20260726.md
```

## Deterministic Proof

Clean worktree:

```text
/tmp/tau-issue-132.cyVJhE
```

Source ref before patch:

```text
a5b24683300455c87b4699a90b5042a88417987c
```

Pre-patch live repro:

```text
uv run tau test-run --repo . --out /tmp/tau-issue-132-before-test-run.json
```

Result:

```text
status=2
Invalid value: Usage: tau test-run --repo <repo> --out <receipt>
receipt_exists=no
```

Pre-patch help repro:

```text
uv run tau lsp-diagnostics --help
```

Result:

```text
status=2
Invalid value: Usage: tau lsp-diagnostics --workspace <path> --out <receipt>
```

Post-patch help matrix:

```text
uv run tau test-run --help
uv run tau lsp-diagnostics --help
uv run tau sandbox-run --help
uv run tau review-findings --help
uv run tau dag-run --help
uv run tau run --help
uv run tau workflows --help
uv run tau workflows run --help
```

Result:

```text
test-run status=0
lsp-diagnostics status=0
sandbox-run status=0
review-findings status=0
dag-run status=0
run status=0
workflows status=0
workflows-run status=0
```

No hardcoded command allowlist remains:

```text
python - <<'PY'
from pathlib import Path
text=Path('src/tau_coding/cli.py').read_text()
print('has_raw_command_allowlist=' + str('raw_positional_args[:1] in (' in text))
PY
```

Result:

```text
has_raw_command_allowlist=False
```

Required reopened-ticket family:

```text
uv run pytest -q \
  tests/test_cli.py \
  tests/test_lsp_receipts.py \
  tests/test_sandbox_policy.py \
  tests/test_review_findings.py \
  tests/test_test_run_receipt.py
```

Result:

```text
332 passed in 18.14s
```

Live bounded `test-run` receipt:

```text
uv run tau test-run --repo . \
  --out /tmp/tau-issue-132-live-test-run.json \
  --command uv --command run --command pytest --command -q \
  --command tests/test_cli.py::test_cli_dag_view_capabilities_is_read_only
```

Result:

```text
status=0
receipt_exists=true
status=PASS
ok=True
live=True
```

Live `lsp-diagnostics` receipt:

```text
uv run tau lsp-diagnostics --workspace . --out /tmp/tau-issue-132-live-lsp.json
```

Result:

```text
status=0
receipt_exists=true
status=PASS
ok=True
live=True
```

Live `review-findings` receipt:

```text
uv run tau review-findings \
  --findings /tmp/tau-issue-132-findings-valid.json \
  --out /tmp/tau-issue-132-live-review-valid.json \
  --goal-hash sha256:goal
```

Result:

```text
status=0
receipt_exists=true
status=PASS
ok=True
live=True
derived_verdict=PASS
```

Live `sandbox-run` parser/receipt probe:

```text
uv run tau sandbox-run \
  --policy-profile /tmp/tau-issue-132-policy-valid.json \
  --data-boundary /tmp/tau-issue-132-boundary-valid.json \
  --out /tmp/tau-issue-132-live-sandbox-valid.json \
  -- python -c 'print("sandbox ok")'
```

Result:

```text
status=1
receipt_exists=true
status=BLOCKED
ok=False
live=True
command_executed=False
alert_codes=["sandbox_backend_unavailable"]
```

The sandbox live probe proves argv reaches `sandbox-run` and a live receipt is
written. It does not prove Bubblewrap availability in this container.

Original documented rung-1 workflow command against a clean temp Git repo:

```text
uv run tau workflows run repository-readiness \
  --repo /tmp/tau-issue-132-rung1-repo.94Ik24 \
  --goal 'Determine whether this checkout is ready for focused work.' \
  --require-clean \
  --run-dir /tmp/tau-issue-132-rung1-run.kGteKE \
  --no-browser-open
```

Result:

```text
status=0
receipt_exists=true
receipt_status=PASS
workflow_id=repository-readiness
```

Syntax and whitespace checks:

```text
uv run python -m py_compile src/tau_coding/cli.py
git diff --check
```

Result:

```text
both exited 0
```

## Evidence Boundary

- mocked: no for subprocess CLI probes and workflow run; yes only in parts of
  the existing pytest family that intentionally use test fixtures
- live: yes for CLI subprocess probes, receipt writes, and packaged workflow run
- provider_live: no
- proves: the startup splitter no longer consumes subcommand options after the
  command token; the command allowlist is removed; affected command help paths
  exit 0; the reopened issue's named test families are green; the documented
  repository-readiness workflow command writes a PASS receipt
- does_not_prove: Bubblewrap is available in this container; the full repo test
  suite is green; provider/model behavior; the full immutable Tau product goal
