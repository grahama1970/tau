# Issue #206 Close Proof

Issue: https://github.com/grahama1970/tau/issues/206

Proof directory:

```text
docs/proofs/tickets/issue-206-subprocess-receipt-staging-readback-20260728T2325Z/
```

## Workflow Transcript

The retained transcript uses a concrete fixture repo under the proof directory,
not a placeholder. Command files, stdout, stderr, and exit codes are retained:

```text
01-run.command.txt              exit 1
02-repair.command.txt           exit 0
03-resume.command.txt           exit 1
04-approve.command.txt          exit 0
05-final-resume.command.txt     exit 0
```

The final resume receipt reports:

```text
mocked: false
live: true
provider_live: false
ok: true
status: PASS
```

## Admission Readback

SQLite command:

```text
sqlite3 run/dag-run.sqlite3 "SELECT DISTINCT node_id FROM receipt_admissions ORDER BY node_id;"
```

Observed distinct node ids:

```text
capture-repository
finalize-qualification
publish-qualification
qualify-documentation
qualify-package
qualify-tests
reconcile-qualification
```

The required six issue nodes are all present:

```text
capture-repository
publish-qualification
qualify-documentation
qualify-package
qualify-tests
reconcile-qualification
```

`finalize-qualification` is an extra admitted workflow node on current `main`,
not a missing required node.

## Absence Classification Probe

Command:

```text
uv run tau dag-run docs/proofs/tickets/issue-206-subprocess-receipt-staging-readback-20260728T2325Z/absence-probe/timeout-spec.json
```

Observed retained readbacks:

```text
absence-probe/02-absence-classifications.txt:
dag_diagnostic_event_appended|killed-child|attempted_and_swallowed|[124]

absence-probe/03-node-attempts.txt:
killed-child|SETTLED|NONE

absence-probe/04-admission-row-count.txt:
0
```

This proves the killed/timed-out child is classified as
`attempted_and_swallowed` and does not produce an accepted receipt admission.

## SIGKILL Regression Output

Command:

```text
uv run pytest -q tests/test_durable_repository_qualification_workflow.py::test_sigkill_before_staging_fails_closed_without_rerunning tests/test_durable_repository_qualification_workflow.py::test_staged_result_crash_resumes_without_rerunning_accepted_branches tests/test_durable_repository_qualification_workflow.py::test_sigkill_after_staged_result_resumes_without_duplicate_publication
```

Observed output:

```text
...                                                                      [100%]
3 passed in 40.78s
```

## Scope Boundary

This closes #206's missing proof gap for parent-side receipt staging/promotion,
admission readback, block/repair/resume, and killed-child absence
classification. It does not close #72 or prove human acceptance for #221.
