# Issue #139 Proof: Canonical Workflow Ladder Order

Issue: https://github.com/grahama1970/tau/issues/139

## Change

Packaged workflow definitions now carry an explicit `rung` field. The catalog
sorts by `(rung, workflow_id)`, `tau workflows list` prints the rung ordinal,
the public JSON catalog includes rung numbers, and the docs include a
`canonical-workflows.md` page in mkdocs nav. The rung-2 README command now uses
`--open-viewer`.

## Deterministic Proof

Commands were run from clean main worktree after the patch:

```bash
uv run pytest -q tests/test_workflow_catalog.py tests/test_workflow_cli.py
```

Result:

```text
9 passed in 20.40s
```

```bash
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

```bash
uv run tau workflows list --json | python -c 'import json,sys; data=json.load(sys.stdin); print([w["rung"] for w in data["workflows"]]); print([w["workflow_id"] for w in data["workflows"]])'
```

Result:

```text
[1, 2, 3, 4, 5]
['repository-readiness', 'tau-operator-reference', 'repository-evidence-map', 'approved-release-bundle', 'durable-repository-qualification']
```

```bash
uv run --group docs mkdocs build --clean
```

Result:

```text
INFO - Building documentation to directory: /tmp/tau-main-issue-137.7nchbf/site
INFO - Documentation built in 1.78 seconds
```

```bash
rg -n "Canonical Workflows: canonical-workflows.md|tau workflows run tau-operator-reference|--open-viewer|rung 1|rung 5" mkdocs.yml README.md docs/getting-started.md docs/canonical-workflows.md
```

Result excerpt:

```text
mkdocs.yml:50:  - Canonical Workflows: canonical-workflows.md
README.md:466:uv run tau workflows run tau-operator-reference \
README.md:469:  --open-viewer
docs/canonical-workflows.md:23:Run rung 1:
docs/canonical-workflows.md:67:Run rung 5:
```

Additional checks:

```bash
uv run pytest -q tests/test_tui_app.py::test_tui_app_workflows_command_opens_picker
uv run python -m py_compile src/tau_coding/workflows/catalog.py src/tau_coding/workflows/contracts.py src/tau_coding/cli.py tests/test_workflow_catalog.py tests/test_workflow_cli.py
git diff --check
```

Result: TUI picker check reports `1 passed in 1.61s`; compile and diff-check
both exit 0.

## Evidence Boundary

mocked: no
live: local CLI and docs build; no external service calls

This proves the workflow catalog and docs expose the five-rung ladder in order.
It does not prove the full immutable five-DAG runtime goal or browser rendering.
