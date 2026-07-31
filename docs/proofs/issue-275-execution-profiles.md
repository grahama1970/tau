# Issue 275: Execution Profiles Proof

Issue: https://github.com/grahama1970/tau/issues/275
Lease: `20260731T030229Z-codex-275`

## Implementation Scope

Tau now resolves `tau.execution_profile.v1` before canonical `DagPlan`
hashing. The supported profile IDs are exactly:

```text
interactive
standard
assurance
```

The resolution is stored in hash-bound plan fields:

- `execution_limits.execution_profile`
- `source_extensions.execution_profile_resolution`

Profile-less contracts use an explicit compatibility mapping to `standard`
with `compatibility_default=true` and `historical_profile_omitted=true`; no
historical run is rewritten.

The implementation does not create a second scheduler. All conformance runs use
the existing `dag_plan_ready_queue` scheduler.

## Non-Mocked Conformance Receipt

Command:

```bash
uv run python -m tau_coding.execution_profile_conformance \
  --output /tmp/tau-issue275-execution-profile-proof-20260731T030944Z-3938373/summary.json \
  --allow-live-filesystem
```

Receipt:

```text
/tmp/tau-issue275-execution-profile-proof-20260731T030944Z-3938373/summary.json
```

Result:

```text
status=PASS
mocked=false
live=true
provider_live=false
failed_checks=[]
```

Checks:

```text
all_plan_hashes_distinct=true
all_runs_passed=true
all_three_profiles_ran_same_scheduler=true
approved_strengthening_requires_new_plan=true
compile_receipts_surface_profile=true
mid_run_downgrade_rejected=true
run_receipts_bind_profiled_plan_hash=true
run_receipts_surface_profile=true
```

## Focused Checks

Command:

```bash
uv run ruff check src/tau_coding/execution_profile_conformance.py \
  src/tau_coding/generic_dag.py \
  src/tau_coding/tui/app.py \
  tests/test_dag_plan.py \
  tests/test_tui_app.py \
  tests/test_execution_profiles.py \
  src/tau_coding/dag_runtime/execution_profile.py \
  src/tau_coding/dag_runtime/compiler.py
```

Result:

```text
All checks passed.
```

Command:

```bash
uv run ruff check src/tau_coding/tui/app.py tests/test_tui_app.py
for i in 1 2 3 4 5; do \
  uv run pytest tests/test_tui_app.py::test_tui_prompt_ctrl_c_twice_quits_from_empty_prompt -q || exit 1; \
done
```

Result:

```text
All checks passed.
5 consecutive focused TUI Ctrl-C runs passed.
```

Command:

```bash
uv run pytest tests/test_dag_plan.py::test_source_extensions_are_preserved_and_hash_bound \
  tests/test_execution_profiles.py -q
```

Result:

```text
9 passed in 1.78s
```

Command:

```bash
uv run pytest tests/test_execution_profiles.py \
  tests/test_generic_dag.py \
  tests/test_dag_plan.py \
  tests/test_dag_runtime_scheduler.py \
  tests/test_dag_runtime_run_store.py \
  tests/test_dag_viewer_historical.py -q
```

Result:

```text
109 passed in 10.92s
```

## Full Suite

Command:

```bash
uv run pytest -q
```

Result:

```text
3435 passed in 496.47s (0:08:16)
```

Note: the full suite twice exposed a pre-existing TUI timing flake in
`tests/test_tui_app.py::test_tui_prompt_ctrl_c_twice_quits_from_empty_prompt`.
The repair moved Ctrl-C clear/quit to a dedicated
`CLEAR_PROMPT_QUIT_WINDOW_SECONDS=2.0` window while leaving the Escape
double-press behavior unchanged. The focused TUI test passed 5 consecutive runs
before the final full-suite pass above.

## Proof Boundaries

This proves deterministic profile resolution, preview/run receipt surfacing,
negative downgrade/broadening behavior, and same-scheduler execution for one
local subprocess DAG per profile.

This does not prove provider/model semantic quality, compliance certification,
or future optional evidence schemas that do not yet exist.
