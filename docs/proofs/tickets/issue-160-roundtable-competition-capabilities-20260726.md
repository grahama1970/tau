# Issue #160 Proof: Roundtable And Competition Capability Contracts

Timestamp: 2026-07-26T22:29:21Z

## Scope

Ticket: https://github.com/grahama1970/tau/issues/160

Implemented the declarative capability and course-correction contract slice:

- `goal_not_met_after_failure_report` resolves to `convene_roundtable`.
- `wide_solution_space_after_failure_report` resolves to `run_competition`.
- `roundtable_deliberation` maps to the `ask` skill.
- `competitive_bakeoff` maps to the `battle` skill.
- Both capabilities are explicitly advisory and do not satisfy the immutable goal.

This does not implement the future panel runner, per-seat dispatch payloads,
round iteration, convergence detector, or dissent synthesis.

## Files Changed

- `src/tau_coding/course_correction.py`
- `src/tau_coding/project_profile.py`
- `src/tau_coding/skill_capability_registry.py`
- `tests/test_course_correction.py`
- `tests/test_project_profile.py`
- `tests/test_skill_capability_registry.py`

## Deterministic Proof

```text
$ uv run pytest -q tests/test_course_correction.py tests/test_skill_capability_registry.py tests/test_project_profile.py
..............................................                           [100%]
46 passed in 0.66s
```

What this exercised:

- typed goal-not-met trigger routes to `convene_roundtable`
- typed wide-solution trigger routes to `run_competition`
- skill route resolution through the capability registry
- project profile validation for both new actions
- default registry validation for advisory flags and trigger names
- explicit forbidden routes preventing goal proof from panel consensus or a winning candidate

```text
$ uv run ruff check src/tau_coding/course_correction.py src/tau_coding/project_profile.py src/tau_coding/skill_capability_registry.py tests/test_course_correction.py tests/test_skill_capability_registry.py tests/test_project_profile.py
All checks passed!
```

```text
$ uv run python -m py_compile src/tau_coding/course_correction.py src/tau_coding/project_profile.py src/tau_coding/skill_capability_registry.py tests/test_course_correction.py tests/test_skill_capability_registry.py tests/test_project_profile.py && git diff --check
```

Exit code: 0.

```text
$ uv run tau skill-capability-registry-default --out /tmp/tau-160-registry.EYZJSF/registry.json
$ uv run tau skill-capability-registry-validate --registry /tmp/tau-160-registry.EYZJSF/registry.json --out /tmp/tau-160-registry.EYZJSF/receipt.json --skills-root /home/graham/workspace/experiments/agent-skills/skills
```

Validation receipt summary:

```text
status: PASS
capability_count: 10
skill_names: ask, battle, code-runner, create-architecture, create-evidence-case, debugger, dogpile, review-code, scillm, webgpt
```

## Evidence Classification

mocked: no

live: no external provider calls

What was actually exercised: deterministic Python policy/registry validation
and CLI registry validation against the real local skills root.

What remains unverified: real roundtable execution, identical per-seat context
payloads across rounds, three-round cap, convergence detection, dissent
preservation in an emitted synthesis artifact, and integration from an actual
goal-not-met failure report into a launched panel.
