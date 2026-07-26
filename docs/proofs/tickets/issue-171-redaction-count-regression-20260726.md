# Issue 171 Regression Proof: Redaction Count Contract

Ticket: https://github.com/grahama1970/tau/issues/171

## Scope

Issue #171 was reopened because the expanded GitHub projection redactor changed
the receipt count in
`tests/test_github_handoff.py::test_github_projection_redaction_writes_redacted_artifact_and_receipt`
from `2` to `3`.

The third redaction is legitimate. The fixture contains:

- `linux_home_path` at `$.comment.body`
- `github_token` at `$.comment.body`
- `sensitive_key` at `$.context.api_key`

This patch updates the regression test to assert the explicit redaction
entries, not only the raw count, so the contract records which class fired and
where.

## Changed Paths

- `tests/test_github_handoff.py`
- `docs/proofs/tickets/issue-171-redaction-count-regression-20260726.md`

## Deterministic Commands

Reopened reproducer before repair:

```bash
uv run pytest -q \
  tests/test_github_handoff.py::test_github_projection_redaction_writes_redacted_artifact_and_receipt \
  -vv
```

Result before repair:

```text
FAILED tests/test_github_handoff.py::test_github_projection_redaction_writes_redacted_artifact_and_receipt
E  assert 3 == 2
```

Reopened reproducer after repair:

```bash
uv run pytest -q \
  tests/test_github_handoff.py::test_github_projection_redaction_writes_redacted_artifact_and_receipt \
  -vv
```

Result after repair:

```text
tests/test_github_handoff.py::test_github_projection_redaction_writes_redacted_artifact_and_receipt PASSED
1 passed in 0.51s
```

Focused redaction and self-fix GitHub gate family:

```bash
uv run pytest -q \
  tests/test_self_fix_ticket_repair.py \
  tests/test_cli.py \
  tests/test_github_handoff.py \
  -k 'github_redact_projection or ticket_repair or github_projection_redaction'
```

Result:

```text
10 passed, 255 deselected in 0.80s
```

Lint:

```bash
uv run ruff check \
  tests/test_github_handoff.py \
  tests/test_cli.py \
  tests/test_self_fix_ticket_repair.py \
  src/tau_coding/github_handoff.py \
  src/tau_coding/self_fix_ticket_repair.py \
  src/tau_coding/cli.py
```

Result:

```text
All checks passed!
```

Compile:

```bash
uv run python -m py_compile \
  tests/test_github_handoff.py \
  tests/test_cli.py \
  tests/test_self_fix_ticket_repair.py \
  src/tau_coding/github_handoff.py \
  src/tau_coding/self_fix_ticket_repair.py \
  src/tau_coding/cli.py
```

Result: exit code `0`.

Diff hygiene:

```bash
git diff --check
```

Result: exit code `0`.

## Evidence Classification

- mocked: yes, for pytest fixture assertions and monkeypatched GitHub/self-fix
  transport paths.
- live: no, for this regression proof; the reopened defect is a deterministic
  local redaction receipt contract mismatch.
- exercised: GitHub projection redaction receipt shape, explicit redaction
  kinds and paths, self-fix redaction gate tests, CLI redaction tests, lint,
  compile, and diff hygiene.
- remains unverified: live GitHub mutation content after close and exhaustive
  detection of all possible secret formats.
