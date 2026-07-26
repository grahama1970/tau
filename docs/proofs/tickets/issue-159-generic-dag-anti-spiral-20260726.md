# Issue #159 Proof: Generic DAG Anti-Spiral Escalation

Timestamp: 2026-07-26T22:25:53Z

## Scope

Ticket: https://github.com/grahama1970/tau/issues/159

Implemented a bounded detector in `src/tau_coding/generic_dag.py` for generic
artifact transaction nodes. When two reviewer `REVISE` attempts carry the same
canonical revision signature, Tau now writes a `tau.course_correction.v1`
receipt and blocks before consuming another same-context attempt.

This is the detector/fail-closed slice discussed on the ticket. It does not
implement the full future Memory/github-search/dogpile persistence ladder.

## Files Changed

- `src/tau_coding/generic_dag.py`
- `tests/test_generic_artifact_transaction.py`

## Deterministic Proof

```text
$ uv run pytest -q tests/test_generic_artifact_transaction.py::test_transaction_blocks_third_identical_revision_with_course_correction
.                                                                        [100%]
1 passed in 0.67s
```

What this exercised:

- real `run_generic_dag` execution path
- real local subprocess producer and reviewer commands
- `max_attempts=3`
- two identical reviewer `REVISE` results
- physical course-correction receipt read back from disk
- producer attempt counter proving no third producer attempt ran

Assertions included:

- run status is `BLOCKED`
- run verdict is `COURSE_CORRECTION_REQUIRED`
- node `attempt_count` is `2`
- attempts list is exactly `[1, 2]`
- error includes `brave_search_required_after_two_attempts`
- `tau.course_correction.v1` receipt exists and is readable
- required action is `run_brave_search_then_retry`
- advisory evidence flags are present and do not satisfy acceptance

```text
$ uv run pytest -q tests/test_generic_artifact_transaction.py
..................                                                       [100%]
18 passed in 6.46s
```

```text
$ uv run ruff check src/tau_coding/generic_dag.py tests/test_generic_artifact_transaction.py
All checks passed!
```

```text
$ uv run python -m py_compile src/tau_coding/generic_dag.py tests/test_generic_artifact_transaction.py && git diff --check
```

Exit code: 0.

## Evidence Classification

mocked: no

live: no external provider calls

What was actually exercised: deterministic local subprocess DAG execution,
transaction retry behavior, receipt writing, and receipt readback.

What remains unverified: the future full escalation ladder that invokes
Memory, registered documentation sources, brave-search, github-search, dogpile,
and Memory episode persistence.
