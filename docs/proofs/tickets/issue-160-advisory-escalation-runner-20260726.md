# Issue #160 Advisory Escalation Runner Proof

Ticket: https://github.com/grahama1970/tau/issues/160

## Scope

Added deterministic advisory roundtable and competition receipt runners:

- `tau.roundtable_advisory_receipt.v1`
- `tau.competition_advisory_receipt.v1`

The runners require typed goal-not-met or wide-solution course-correction
receipts, preserve advisory-only status, and explicitly do not satisfy the
immutable goal or human release boundary.

## Deterministic Checks

```bash
uv run pytest -q \
  tests/test_advisory_escalation.py \
  tests/test_course_correction.py \
  tests/test_skill_capability_registry.py \
  tests/test_project_profile.py
```

Result:

```text
49 passed in 0.71s
```

```bash
uv run ruff check \
  src/tau_coding/advisory_escalation.py \
  tests/test_advisory_escalation.py \
  src/tau_coding/course_correction.py \
  src/tau_coding/skill_capability_registry.py \
  tests/test_course_correction.py \
  tests/test_skill_capability_registry.py \
  tests/test_project_profile.py
```

Result:

```text
All checks passed!
```

```bash
uv run python -m py_compile \
  src/tau_coding/advisory_escalation.py \
  tests/test_advisory_escalation.py
git diff --check
```

Result: exit code 0.

## Regression Added

`tests/test_advisory_escalation.py` asserts:

- every roundtable seat receives an identical dispatch payload per round
- dispatch payload hashes match across seats
- roundtable stops at convergence when all seats agree without dissent
- roundtable stops at the three-round cap when dissent remains
- surviving dissent is preserved in the advisory receipt
- a roundtable PASS does not mark the immutable goal met
- competition candidates are independently produced and judged
- a competition PASS does not mark the immutable goal met

## Evidence Classification

mocked: yes, by design for stubbed seats requested by the ticket.

live: no external provider calls.

What was actually exercised: deterministic trigger validation, same-context
dispatch payload construction, round iteration/cap behavior, convergence
detection, dissent preservation, advisory-only receipts, and existing
course-correction/capability registry routing.

What remains unverified: live `/ask` roundtable calls, live `$battle` execution,
provider quality, and human release.
