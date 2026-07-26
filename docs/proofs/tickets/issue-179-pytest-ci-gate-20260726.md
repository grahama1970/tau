# Issue #179 Pytest CI Gate Proof

Date: 2026-07-26

## Scope

Added `.github/workflows/tests.yml` so pushes to `main`, pull requests, and manual
dispatches run the repository pytest suite through:

```text
uv run pytest -q
```

The current suite is still red. This proof demonstrates the #179 gate invariant:
the branch now runs the full suite in CI and the build fails when pytest fails.
It does not claim the reopened regression tickets are repaired.

## Changed File

```text
.github/workflows/tests.yml
```

The workflow:

- checks out the repository;
- installs uv with the repository's existing `astral-sh/setup-uv@v7` convention;
- sets up Python from `.python-version`;
- runs `uv sync --group dev --frozen`;
- runs `uv run pytest -q`.

## Local Proof

Clean detached worktree:

```text
/tmp/tau-issue-179.4VMywm
```

Source ref before the workflow commit:

```text
318fc336f9f75a73c4bf79f2b996bad2910bf49c
```

Workflow YAML parse/check:

```text
uv run python - <<'PY'
from pathlib import Path
import yaml
path = Path('.github/workflows/tests.yml')
payload = yaml.safe_load(path.read_text())
assert payload['name'] == 'Tests'
assert payload[True]['push']['branches'] == ['main']
assert payload['jobs']['pytest']['steps'][-1]['run'] == 'uv run pytest -q'
print('workflow_yaml_ok')
PY
```

Result:

```text
workflow_yaml_ok
```

Whitespace check:

```text
git diff --check
```

Result: exit code 0.

Full suite command required by #179:

```text
uv run pytest -q > /tmp/tau-issue-179-pytest.log 2>&1
```

Pytest summary from `/tmp/tau-issue-179-pytest.log`:

```text
239 failed, 2952 passed, 29 skipped in 494.97s (0:08:14)
```

## Remote Proof

Workflow commit pushed to `main`:

```text
84d74e6bd230d881ea3952ad72304a42bbfbcc1a
```

Remote ref verification:

```text
git ls-remote origin refs/heads/main
```

Result:

```text
84d74e6bd230d881ea3952ad72304a42bbfbcc1a refs/heads/main
```

GitHub Actions run:

```text
https://github.com/grahama1970/tau/actions/runs/30220802126
```

Job URL:

```text
https://github.com/grahama1970/tau/actions/runs/30220802126/job/89842801980
```

Run inspection:

```text
gh run view 30220802126 -R grahama1970/tau --json databaseId,displayTitle,headSha,status,conclusion,url,workflowName,createdAt,updatedAt,jobs
```

Relevant result:

```text
workflowName: Tests
headSha: 84d74e6bd230d881ea3952ad72304a42bbfbcc1a
status: completed
conclusion: failure
job: uv run pytest -q
step: Run pytest suite
step conclusion: failure
annotation: Process completed with exit code 1.
```

## Evidence Classification

- mocked: no
- live: yes, local full pytest run and GitHub Actions run on `main`
- provider_live: no
- exercised: workflow trigger on push to `main`, dependency install, Python setup from `.python-version`, `uv sync --group dev --frozen`, `uv run pytest -q`, and CI failure propagation
- remains unverified: repairing the 239 failing tests; those failures are tracked by reopened regression tickets listed on #179
