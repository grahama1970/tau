# Issue #146 Proof: Pending Human Decision Inbox

Issue: https://github.com/grahama1970/tau/issues/146

## Scope

Implemented a TUI-visible pending-decision inbox backed by durable Tau run
artifacts:

- `src/tau_coding/pending_decisions.py` detects registered runs whose transaction
  receipts are in `APPROVAL_REQUIRED` and whose approval-gate receipt has not
  passed.
- `TauTuiApp` refreshes the inbox at mount time, after terminal commands, and
  through `/decisions` or `/decision-inbox`.
- Pending decisions render in the existing prompt-region widget surface with
  required action, target, and the exact command shape to satisfy the approval.
- New pending approvals emit both an in-app warning and a terminal desktop
  notification through the existing `TerminalNotificationController`.
- Approved runs clear from the inbox when `workflow-approval.json` reports
  `ok: true`.

## Deterministic Checks

```text
$ uv run python -m py_compile src/tau_coding/pending_decisions.py src/tau_coding/tui/app.py src/tau_coding/tui/terminal_notification.py tests/test_pending_decisions.py tests/test_tui_app.py tests/test_terminal_notification.py
exit 0
```

```text
$ uv run ruff check src/tau_coding/pending_decisions.py src/tau_coding/tui/app.py src/tau_coding/tui/terminal_notification.py tests/test_pending_decisions.py tests/test_tui_app.py tests/test_terminal_notification.py
All checks passed!
exit 0
```

```text
$ uv run pytest tests/test_pending_decisions.py::test_pending_decision_inbox_tracks_approval_boundary_and_clears tests/test_tui_app.py::test_tui_app_pending_decision_refresh_updates_widget_and_notifies tests/test_terminal_notification.py::test_terminal_notification_controller_writes_pending_decision_message -q
...                                                                      [100%]
3 passed in 9.65s
exit 0
```

```text
$ uv run pytest tests/test_workflow_cli.py::test_workflows_approve_and_resume_release_bundle tests/test_approved_release_bundle_workflow.py::test_release_bundle_revises_waits_for_approval_and_resumes_once -q
.F                                                                       [100%]
1 failed, 1 passed in 17.51s
```

Adjacent residual failure signature:

```text
tests/test_approved_release_bundle_workflow.py::test_release_bundle_revises_waits_for_approval_and_resumes_once
assert approval["status"] == "PASS"
actual: "BLOCKED"
```

That legacy direct-runner test calls `approve_approved_release_bundle(run_dir=...)`
without a human approval packet. The CLI workflow proof path with a signed
approval packet passed and is the path used by the new inbox clearing test.

```text
$ uv run pytest tests/test_terminal_notification.py -q
.........                                                                [100%]
9 passed in 0.44s
exit 0
```

```text
$ git diff --check
exit 0
```

## Proof Meaning

mocked: no for the approval-boundary collector test; yes for the narrow TUI
widget fixture.

live: yes, local deterministic workflow execution only.

provider_live: no.

The main inbox test runs the real `approved-release-bundle` workflow through the
Tau CLI until it blocks at the approval boundary, verifies the inbox entry and
required command, runs a non-approval blocked workflow and verifies it does not
appear, then applies a signed human approval packet and verifies the inbox
clears.

The TUI test verifies prompt-region widget state, in-app notification, terminal
OSC notification emission, notification dedupe, and clearing after approval.

This proves the requested pending-decision inbox behavior for local Tau workflow
approval gates. It does not prove terminal notification support on every
terminal emulator or provider/model quality.
