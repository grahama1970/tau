# Issue 170 Proof: Self-Fix Issue Body Shell Gate

Ticket: https://github.com/grahama1970/tau/issues/170

## Scope

Issue #170 reported that a GitHub issue body could provide
`verification_commands` that flowed through the Tau self-fix repair path into
`subprocess.run(..., shell=True)`.

This repair treats issue-provided repair JSON as untrusted data:

- Repair execution requires a trusted GitHub issue author association.
- A body edited after the routing label gate blocks repair.
- Issue-provided verification commands must match a repo-controlled allowlist.
- Allowed verification commands are executed as argv arrays with `shell=False`.

## Changed Paths

- `src/tau_coding/self_fix_ticket_repair.py`
- `src/tau_coding/self_fix_repair_loop.py`
- `src/tau_coding/cli.py`
- `tests/test_self_fix_ticket_repair.py`
- `docs/proofs/tickets/issue-170-self-fix-shell-gate-20260726/*`

## Gates Added

Trusted author gate:

- Accepted GitHub author associations: `OWNER`, `MEMBER`, `COLLABORATOR`.
- Missing or untrusted author metadata blocks before the coder/reviewer loop.

Body edit gate:

- `tau self-fix tick` fetches issue REST metadata and issue events.
- If the issue body has an `edited` event after the matched routing label event,
  the repair payload gets `bodyEditedAfterRoutingLabel=true`.
- `run_ticket_repair` blocks that payload before repair.

Verification command gate:

- `verification_commands` are parsed with `shlex.split`.
- Commands must match the committed allowlist:
  - `python -m py_compile {target_file}`
  - `uv run python -m py_compile {target_file}`
- Non-allowlisted commands make `extract_repair_request` return `None`, so the
  repair path fails closed with `repair_request_contract_missing`.

Shell removal:

- `_run_verification_commands` now executes argv arrays and does not pass
  `shell=True`.

## Deterministic Commands

```bash
uv run ruff check \
  src/tau_coding/self_fix_ticket_repair.py \
  src/tau_coding/self_fix_repair_loop.py \
  src/tau_coding/cli.py \
  tests/test_self_fix_ticket_repair.py

uv run python -m py_compile \
  src/tau_coding/self_fix_ticket_repair.py \
  src/tau_coding/self_fix_repair_loop.py \
  src/tau_coding/cli.py \
  tests/test_self_fix_ticket_repair.py

uv run pytest -q tests/test_self_fix_ticket_repair.py
```

Results:

- `ruff check`: `All checks passed!`
- `py_compile`: exit code `0`
- `pytest`: `5 passed in 0.45s`

## Security Regression Coverage

Focused tests assert:

- A repair request with non-allowlisted `verification_commands` is rejected
  fail-closed.
- A repair request from an untrusted issue author is rejected before the repair
  loop can run.
- An issue marked as edited after routing-label eligibility is rejected before
  the repair loop can run.
- Verification commands are sent to `subprocess.run` as argv arrays and the
  `shell` kwarg is not `True`.
- The existing rollback behavior still restores the target file if commit fails.

## Source Proof

Command:

```bash
rg -n "shell=True" src/tau_coding/self_fix_repair_loop.py \
  > docs/proofs/tickets/issue-170-self-fix-shell-gate-20260726/shell-true-grep.txt
printf '%s\n' "$?" \
  > docs/proofs/tickets/issue-170-self-fix-shell-gate-20260726/shell-true-grep-exitcode.txt
```

Result:

- `shell-true-grep-exitcode.txt`: `1`
- `shell-true-grep.txt`: `0` bytes

This means there is no `shell=True` match in the self-fix repair loop source.

## Live GitHub Metadata Proof

Command:

```bash
uv run python - <<'PY'
import json
from pathlib import Path
from tau_coding.cli import (
    _fetch_github_issue,
    _issue_body_edited_after_routing_label,
    _issue_labels,
    _self_fix_eligibility,
)

issue, fetch = _fetch_github_issue(repo="grahama1970/tau", issue=170)
labels = _issue_labels(issue)
eligibility = _self_fix_eligibility(
    labels, ("route:security_or_compliance", "security", "goal-lock")
)
edit_gate = _issue_body_edited_after_routing_label(
    repo="grahama1970/tau",
    issue=170,
    routing_labels=set(eligibility.get("matched_labels", [])),
)
out = {
    "mocked": False,
    "live": True,
    "fetch_ok": fetch.get("ok"),
    "number": issue.get("number"),
    "authorAssociation": issue.get("authorAssociation"),
    "authorLogin": issue.get("authorLogin"),
    "updatedAt": issue.get("updatedAt"),
    "restUpdatedAt": issue.get("restUpdatedAt"),
    "eligibility": eligibility,
    "body_edit_gate": edit_gate,
}
path = Path(
    "docs/proofs/tickets/issue-170-self-fix-shell-gate-20260726/"
    "live-github-security-metadata.json"
)
path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(out, indent=2, sort_keys=True))
PY
```

Receipt:

- `docs/proofs/tickets/issue-170-self-fix-shell-gate-20260726/live-github-security-metadata.json`

Result summary:

- `mocked: false`
- `live: true`
- `fetch_ok: true`
- `authorAssociation: OWNER`
- matched routing labels: `goal-lock`, `route:security_or_compliance`, `security`
- `body_edit_gate.ok: true`
- `body_edit_gate.edited_after_routing_label: false`

## Evidence Classification

- `mocked: no` for the live GitHub metadata proof.
- `mocked: yes` for monkeypatched unit assertions that verify no repair loop
  runs after fail-closed gates.
- `live: yes` for GitHub issue metadata and event fetch.
- Actual behavior exercised: request normalization, command allowlist, author
  trust gate, post-routing edit gate, argv verification execution, source grep
  for `shell=True`.
- Remaining unverified claim: secure-executor execution of issue-supplied
  commands is not implemented because this repair forbids issue-supplied
  arbitrary commands instead.
