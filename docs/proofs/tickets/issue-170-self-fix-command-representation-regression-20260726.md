# Issue 170 Regression Proof: Self-Fix Command Representation

Ticket: https://github.com/grahama1970/tau/issues/170

## Scope

Issue #170 was reopened because the P0 shell-gate repair correctly normalized
issue-body `verification_commands` to argv lists, but the self-fix poll-side
test still expected shell command strings.

This repair keeps the secure argv-list representation and migrates the
poll-side regression coverage to that contract. It also adds a poll-side
negative assertion that a command containing shell metacharacters is rejected
fail-closed by `extract_repair_request`.

## Changed Paths

- `tests/test_self_fix_poll.py`
- `docs/proofs/tickets/issue-170-self-fix-command-representation-regression-20260726.md`

## Representation Contract

Accepted issue-body command:

```json
["python -m py_compile tests/fixtures/self_fix_ticket_probe.py"]
```

Normalized Tau repair request command:

```json
[["python", "-m", "py_compile", "tests/fixtures/self_fix_ticket_probe.py"]]
```

Rejected issue-body command:

```json
["python -m py_compile tests/fixtures/self_fix_ticket_probe.py; touch /tmp/owned"]
```

The rejection preserves the #170 security invariant: GitHub issue text must not
be able to introduce arbitrary shell execution.

## Deterministic Commands

Reopened reproducer before repair:

```bash
uv run pytest -q tests/test_self_fix_poll.py::test_extract_repair_request_from_issue_body -vv
```

Result before repair:

```text
FAILED tests/test_self_fix_poll.py::test_extract_repair_request_from_issue_body
E  At index 0 diff: ['python', '-m', 'py_compile', 'tests/fixtures/self_fix_ticket_probe.py']
                != 'python -m py_compile tests/fixtures/self_fix_ticket_probe.py'
```

Reopened reproducer after repair:

```bash
uv run pytest -q tests/test_self_fix_poll.py::test_extract_repair_request_from_issue_body -vv
```

Result after repair:

```text
tests/test_self_fix_poll.py::test_extract_repair_request_from_issue_body PASSED
1 passed in 0.61s
```

Focused self-fix family:

```bash
uv run pytest -q tests/test_self_fix_poll.py tests/test_self_fix_ticket_repair.py
```

Result:

```text
11 passed in 0.98s
```

Lint:

```bash
uv run ruff check \
  tests/test_self_fix_poll.py \
  tests/test_self_fix_ticket_repair.py \
  src/tau_coding/self_fix_ticket_repair.py \
  src/tau_coding/self_fix_repair_loop.py
```

Result:

```text
All checks passed!
```

Compile:

```bash
uv run python -m py_compile \
  src/tau_coding/self_fix_ticket_repair.py \
  src/tau_coding/self_fix_repair_loop.py \
  src/tau_coding/cli.py \
  tests/test_self_fix_poll.py \
  tests/test_self_fix_ticket_repair.py
```

Result: exit code `0`.

Diff hygiene:

```bash
git diff --check
```

Result: exit code `0`.

Shell execution source check:

```bash
rg -n "shell=True" src/tau_coding/self_fix_repair_loop.py
```

Result: exit code `1`, no output. There is no `shell=True` match in the
self-fix repair loop source.

## Evidence Classification

- mocked: yes, for the pytest fixture assertions that exercise synthetic issue
  bodies.
- live: no, for this regression proof; the reopened defect is a deterministic
  local contract mismatch in tests and extractor shape.
- exercised: repair request extraction, argv-list normalization expectation,
  shell-metacharacter rejection, focused self-fix poll/repair test family,
  lint, compile, diff hygiene, and source grep for `shell=True`.
- remains unverified: live GitHub repair execution; this patch did not change
  production repair execution and only repairs the reopened representation
  regression.
