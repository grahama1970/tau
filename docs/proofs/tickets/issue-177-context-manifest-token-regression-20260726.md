# Issue 177 Regression Proof: Context Manifest Token Budget

Ticket: https://github.com/grahama1970/tau/issues/177

## Scope

Issue #177 was reopened because graph context manifest compaction replaced two
active messages with one manifest message but inflated the estimated active
context from `1365` tokens to `73832` tokens.

At current `main`, the reopened reproducer passes without a source change in
this ticket slice. This proof records the current behavior and keeps the ticket
close tied to deterministic local evidence.

## Changed Paths

- `docs/proofs/tickets/issue-177-context-manifest-token-regression-20260726.md`

## Deterministic Commands

Reopened reproducer:

```bash
uv run pytest -q \
  tests/test_coding_session.py::test_context_usage_recalculates_after_prompt_and_compaction \
  -vv
```

Result:

```text
tests/test_coding_session.py::test_context_usage_recalculates_after_prompt_and_compaction[asyncio] PASSED
1 passed in 0.67s
```

Focused context manifest/session suite:

```bash
uv run pytest -q \
  tests/test_context_window.py \
  tests/test_session.py::test_session_state_replays_compaction_as_context_summary \
  tests/test_session.py::test_session_state_inserts_partial_compaction_before_retained_messages \
  tests/test_coding_session.py::test_context_usage_recalculates_after_prompt_and_compaction \
  tests/test_coding_session.py::test_session_compact_persists_summary_and_rebuilds_context \
  tests/test_coding_session.py::test_session_auto_compacts_after_response_when_threshold_is_exceeded \
  tests/test_coding_session.py::test_session_auto_compacts_with_pi_style_default_threshold \
  tests/test_coding_session.py::test_session_compacts_and_retries_once_after_context_overflow
```

Result:

```text
18 passed in 3.29s
```

Token-count probe using the same fixture as the reopened test:

```text
initial_total_tokens: 1110
after_prompt_total_tokens: 1365
after_prompt_message_count: 2
after_compaction_total_tokens: 1296
after_compaction_message_count: 1
strictly_reduced: True
```

Lint:

```bash
uv run ruff check \
  src/tau_coding/context_window.py \
  src/tau_agent/session/memory.py \
  src/tau_coding/session.py \
  tests/test_context_window.py \
  tests/test_coding_session.py \
  tests/test_session.py
```

Result:

```text
All checks passed!
```

Compile:

```bash
uv run python -m py_compile \
  src/tau_coding/context_window.py \
  src/tau_agent/session/memory.py \
  src/tau_coding/session.py \
  tests/test_context_window.py \
  tests/test_coding_session.py \
  tests/test_session.py
```

Result: exit code `0`.

Diff hygiene:

```bash
git diff --check
```

Result: exit code `0`.

## Evidence Classification

- mocked: yes, for the fake provider session fixture and local Memory/context
  tests.
- live: no provider calls for this regression proof.
- exercised: active context token accounting, context manifest replay,
  compaction replacement of active messages, session compaction persistence,
  lint, compile, and diff hygiene.
- remains unverified: live Memory daemon recall behavior in this proof-only
  slice; that was covered by the earlier #177 live Memory receipt.
