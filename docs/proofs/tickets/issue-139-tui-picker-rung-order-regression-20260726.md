# Issue #139 TUI Picker Rung Order Regression Proof

Date: 2026-07-26

Issue: https://github.com/grahama1970/tau/issues/139

## Scope

The catalog is intentionally rung-ordered. The reopened regression was that the
TUI workflow picker tests still relied on old positional expectations after the
catalog changed from alphabetical order to rungs 1-5.

This repair updates the picker tests to assert the selected visible row identity
instead of obsolete row positions, and updates inserted-command expectations to
the current rung order and shared viewer-hold behavior.

## Changed Files

```text
tests/test_tui_app.py
docs/proofs/tickets/issue-139-tui-picker-rung-order-regression-20260726.md
```

## Deterministic Proof

Pre-repair reproduction:

```text
uv run pytest -q \
  tests/test_tui_app.py::test_tui_app_workflows_picker_selection_opens_detail_modal \
  tests/test_tui_app.py::test_tui_app_workflows_picker_inserts_runnable_command \
  tests/test_tui_app.py::test_tui_app_workflows_picker_uses_configured_pi_select_keybindings \
  -vv
```

Result:

```text
3 failed
selection expected durable-repository-qualification but row 1 is tau-operator-reference
insert expected approved-release-bundle but default row is repository-readiness
```

Post-repair picker subset:

```text
uv run pytest -q \
  tests/test_tui_app.py::test_tui_app_workflows_command_opens_picker \
  tests/test_tui_app.py::test_tui_app_workflows_picker_selection_opens_detail_modal \
  tests/test_tui_app.py::test_tui_app_workflows_picker_search_filters_visible_workflows \
  tests/test_tui_app.py::test_tui_app_workflows_picker_filtered_selection_uses_filtered_workflow \
  tests/test_tui_app.py::test_tui_app_workflows_picker_uses_configured_pi_select_keybindings \
  tests/test_tui_app.py::test_tui_app_workflows_picker_inserts_runnable_command \
  tests/test_tui_app.py::test_tui_app_workflows_picker_inserts_operator_reference_without_goal \
  tests/test_tui_app.py::test_tui_app_workflows_picker_filtered_insert_uses_filtered_workflow \
  -vv
```

Result:

```text
8 passed in 5.30s
```

Catalog and workflow CLI tests:

```text
uv run pytest -q tests/test_workflow_catalog.py tests/test_workflow_cli.py
```

Result:

```text
9 passed in 23.77s
```

Workflow list order:

```text
uv run tau workflows list
```

Result:

```text
rung 1	repository-readiness	LINEAR	Repository Readiness
rung 2	tau-operator-reference	MULTI_STEP_SEQUENTIAL	Tau Operator Reference
rung 3	repository-evidence-map	FAN_OUT_FAN_IN	Repository Evidence Map
rung 4	approved-release-bundle	MIXED_RETRY_APPROVAL	Approved Release Bundle
rung 5	durable-repository-qualification	DURABLE_MIXED_REPAIR_APPROVAL	Durable Repository Qualification
```

Workflow JSON order:

```text
uv run tau workflows list --json | python -c 'import json,sys; data=json.load(sys.stdin); print([w["rung"] for w in data["workflows"]]); print([w["workflow_id"] for w in data["workflows"]])'
```

Result:

```text
[1, 2, 3, 4, 5]
['repository-readiness', 'tau-operator-reference', 'repository-evidence-map', 'approved-release-bundle', 'durable-repository-qualification']
```

Docs build:

```text
uv run --group docs mkdocs build --clean
```

Result:

```text
INFO - Documentation built in 2.13 seconds
```

Syntax and whitespace checks:

```text
uv run python -m py_compile tests/test_tui_app.py
git diff --check
```

Result:

```text
both exited 0
```

## Evidence Boundary

- mocked: yes for the Textual app tests' fake session harness
- live: yes for local CLI workflow-list commands and docs build
- provider_live: no
- proves: the workflow catalog remains rung-ordered; TUI picker tests now bind
  selection and inserted commands to the visible/current workflow identity; docs
  build with the canonical workflow page
- does_not_prove: browser-rendered visual proof; provider/model behavior; the
  full immutable Tau product goal
