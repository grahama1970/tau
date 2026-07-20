"""Apply-gated GitHub transport for Tau agent handoff projections."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CommandRunner = Callable[[list[str], str | None], subprocess.CompletedProcess[str]]

GITHUB_PROJECTION_REDACTION_RECEIPT_SCHEMA = "tau.github_projection_redaction_receipt.v1"
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
}
_LOCAL_PATH_PATTERN = re.compile(r"/home/[^\s\"'`<>),\]]+")
_TOKEN_PATTERN = re.compile(
    r"(gh[pousr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9]{8,})"
)


@dataclass(frozen=True, slots=True)
class GitHubHandoffTransportResult:
    """Receipt for a dry-run or applied GitHub handoff transport."""

    ok: bool
    dry_run: bool
    applied: bool
    target: dict[str, Any] | None = None
    commands: tuple[list[str], ...] = ()
    command_results: tuple[dict[str, Any], ...] = ()
    preflight_results: tuple[dict[str, Any], ...] = ()
    receipt_path: str | None = None
    errors: tuple[str, ...] = ()
    schema: str = "tau.github_handoff_transport_receipt.v1"

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable transport receipt."""

        return {
            "schema": self.schema,
            "ok": self.ok,
            "status": "PASS" if self.ok else "BLOCKED",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "dry_run": self.dry_run,
            "applied": self.applied,
            "target": self.target,
            "commands": [list(command) for command in self.commands],
            "command_results": list(self.command_results),
            "preflight_results": list(self.preflight_results),
            "receipt_path": self.receipt_path,
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class GitHubTicketSourceFetchResult:
    """Receipt for rendering or executing a read-only GitHub issue list fetch."""

    ok: bool
    dry_run: bool
    executed: bool
    repo: str
    command: list[str]
    command_result: dict[str, Any] | None = None
    ticket_source_path: str | None = None
    ticket_source: dict[str, Any] | None = None
    receipt_path: str | None = None
    errors: tuple[str, ...] = ()
    schema: str = "tau.github_ticket_source_fetch_receipt.v1"

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable fetch receipt."""

        return {
            "schema": self.schema,
            "ok": self.ok,
            "dry_run": self.dry_run,
            "executed": self.executed,
            "repo": self.repo,
            "command": list(self.command),
            "command_result": self.command_result,
            "ticket_source_path": self.ticket_source_path,
            "ticket_source": self.ticket_source,
            "receipt_path": self.receipt_path,
            "errors": list(self.errors),
        }


def fetch_goal_guardian_ticket_source_from_github(
    *,
    repo: str,
    output_path: Path,
    execute: bool = False,
    state: str = "open",
    limit: int = 100,
    receipt_path: Path | None = None,
    runner: CommandRunner | None = None,
) -> GitHubTicketSourceFetchResult:
    """Render or execute a read-only GitHub issue list fetch for goal-guardian."""

    errors: list[str] = []
    command = _github_issue_list_command(repo=repo, state=state, limit=limit, errors=errors)
    if errors:
        result = GitHubTicketSourceFetchResult(
            ok=False,
            dry_run=not execute,
            executed=False,
            repo=repo,
            command=command,
            errors=tuple(errors),
        )
        return _write_ticket_source_fetch_receipt(result, receipt_path)

    if not execute:
        result = GitHubTicketSourceFetchResult(
            ok=True,
            dry_run=True,
            executed=False,
            repo=repo,
            command=command,
        )
        return _write_ticket_source_fetch_receipt(result, receipt_path)

    command_runner = runner or _run_gh_command
    completed = command_runner(command, None)
    command_result = _completed_process_result(command, completed)
    if completed.returncode != 0:
        result = GitHubTicketSourceFetchResult(
            ok=False,
            dry_run=False,
            executed=True,
            repo=repo,
            command=command,
            command_result=command_result,
            errors=(f"GitHub issue list failed: {_completed_process_detail(completed)}",),
        )
        return _write_ticket_source_fetch_receipt(result, receipt_path)

    try:
        issues = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as exc:
        result = GitHubTicketSourceFetchResult(
            ok=False,
            dry_run=False,
            executed=True,
            repo=repo,
            command=command,
            command_result=command_result,
            errors=(f"GitHub issue list stdout was not JSON: {exc}",),
        )
        return _write_ticket_source_fetch_receipt(result, receipt_path)
    if not isinstance(issues, list):
        result = GitHubTicketSourceFetchResult(
            ok=False,
            dry_run=False,
            executed=True,
            repo=repo,
            command=command,
            command_result=command_result,
            errors=("GitHub issue list stdout root must be a list",),
        )
        return _write_ticket_source_fetch_receipt(result, receipt_path)

    try:
        ticket_source = _ticket_source_from_gh_issues(issues)
    except ValueError as exc:
        result = GitHubTicketSourceFetchResult(
            ok=False,
            dry_run=False,
            executed=True,
            repo=repo,
            command=command,
            command_result=command_result,
            errors=(str(exc),),
        )
        return _write_ticket_source_fetch_receipt(result, receipt_path)
    resolved_output = output_path.expanduser().resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(
        json.dumps(ticket_source, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result = GitHubTicketSourceFetchResult(
        ok=True,
        dry_run=False,
        executed=True,
        repo=repo,
        command=command,
        command_result=command_result,
        ticket_source_path=str(resolved_output),
        ticket_source=ticket_source,
    )
    return _write_ticket_source_fetch_receipt(result, receipt_path)


def transport_handoff_projection_to_github(
    projection: Mapping[str, Any],
    *,
    apply: bool = False,
    require_preflight: bool = False,
    receipt_path: Path | None = None,
    runner: CommandRunner | None = None,
) -> GitHubHandoffTransportResult:
    """Render or apply GitHub commands for one validated handoff projection."""

    errors: list[str] = []
    commands = _github_transport_commands(projection, errors)
    if errors:
        result = GitHubHandoffTransportResult(
            ok=False,
            dry_run=not apply,
            applied=False,
            target=_target_dict(projection),
            commands=tuple(commands),
            errors=tuple(errors),
        )
        return _write_transport_receipt(result, receipt_path)

    if not apply:
        result = GitHubHandoffTransportResult(
            ok=True,
            dry_run=True,
            applied=False,
            target=_target_dict(projection),
            commands=tuple(commands),
        )
        return _write_transport_receipt(result, receipt_path)

    command_runner = runner or _run_gh_command
    preflight_results: list[dict[str, Any]] = []
    if require_preflight:
        preflight_commands = _github_preflight_commands(projection, errors)
        if errors:
            result = GitHubHandoffTransportResult(
                ok=False,
                dry_run=False,
                applied=False,
                target=_target_dict(projection),
                commands=tuple(commands),
                errors=tuple(errors),
            )
            return _write_transport_receipt(result, receipt_path)
        for command in preflight_commands:
            completed = command_runner(command, None)
            preflight_results.append(_completed_process_result(command, completed))
            if completed.returncode != 0:
                detail = _completed_process_detail(completed)
                result = GitHubHandoffTransportResult(
                    ok=False,
                    dry_run=False,
                    applied=False,
                    target=_target_dict(projection),
                    commands=tuple(commands),
                    preflight_results=tuple(preflight_results),
                    errors=(f"GitHub preflight failed: {' '.join(command)}: {detail}",),
                )
                return _write_transport_receipt(result, receipt_path)

    command_results: list[dict[str, Any]] = []
    for command in commands:
        completed = command_runner(command, _stdin_for_command(command, projection))
        command_results.append(_completed_process_result(command, completed))
        if completed.returncode != 0:
            detail = _completed_process_detail(completed)
            result = GitHubHandoffTransportResult(
                ok=False,
                dry_run=False,
                applied=False,
                target=_target_dict(projection),
                commands=tuple(commands),
                command_results=tuple(command_results),
                preflight_results=tuple(preflight_results),
                errors=(f"GitHub command failed: {' '.join(command)}: {detail}",),
            )
            return _write_transport_receipt(result, receipt_path)

    result = GitHubHandoffTransportResult(
        ok=True,
        dry_run=False,
        applied=True,
        target=_target_dict(projection),
        commands=tuple(commands),
        command_results=tuple(command_results),
        preflight_results=tuple(preflight_results),
    )
    return _write_transport_receipt(result, receipt_path)


def redact_github_projection(
    *,
    projection_path: Path,
    output_path: Path,
    receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Write a redacted projection artifact and receipt before public GitHub transport."""

    resolved_projection = projection_path.expanduser().resolve()
    resolved_output = output_path.expanduser().resolve()
    projection = _read_json_object(resolved_projection, label="GitHub projection")
    redactions: list[dict[str, str]] = []
    redacted_projection = _redact_value(projection, path="$", redactions=redactions)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(
        json.dumps(redacted_projection, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    receipt = {
        "schema": GITHUB_PROJECTION_REDACTION_RECEIPT_SCHEMA,
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "projection": str(resolved_projection),
        "projection_sha256": _file_sha256(resolved_projection),
        "redacted_projection": str(resolved_output),
        "redacted_projection_sha256": _file_sha256(resolved_output),
        "redaction_count": len(redactions),
        "redactions": redactions,
        "review_required": bool(redactions),
        "proof_scope": {
            "proves": [
                "A GitHub projection artifact was inspected deterministically.",
                "Sensitive-key values, local absolute paths, and known token patterns "
                "were redacted.",
                "A separate redacted projection artifact was written for public GitHub "
                "transport review.",
            ],
            "does_not_prove": [
                "Live GitHub mutation.",
                "Human approval for posting.",
                "Semantic safety of the public comment body.",
                "Exhaustive secret detection beyond the configured redaction patterns.",
            ],
        },
        "errors": [],
    }
    if receipt_path is not None:
        resolved_receipt = receipt_path.expanduser().resolve()
        resolved_receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt["receipt_path"] = str(resolved_receipt)
        resolved_receipt.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        receipt["receipt_path"] = None
    return receipt


def transport_generated_ticket_to_github(
    *,
    repo: str,
    github_create: Mapping[str, Any],
    dedupe_projection: Mapping[str, Any] | None = None,
    require_dedupe_preflight: bool = False,
    apply: bool = False,
    receipt_path: Path | None = None,
    runner: CommandRunner | None = None,
) -> GitHubHandoffTransportResult:
    """Render or apply GitHub commands for one generated-ticket projection."""

    errors: list[str] = []
    target = _generated_ticket_transport_target(repo, dedupe_projection)
    schema = "tau.github_generated_ticket_transport_receipt.v1"
    if apply and require_dedupe_preflight and dedupe_projection is None:
        result = GitHubHandoffTransportResult(
            ok=False,
            dry_run=False,
            applied=False,
            target=target,
            commands=(),
            errors=("dedupe preflight projection is required before applying generated tickets",),
            schema=schema,
        )
        return _write_transport_receipt(result, receipt_path)
    commands = _generated_ticket_transport_commands(
        repo=repo,
        github_create=github_create,
        dedupe_projection=dedupe_projection,
        errors=errors,
    )
    if errors:
        result = GitHubHandoffTransportResult(
            ok=False,
            dry_run=not apply,
            applied=False,
            target=target,
            commands=tuple(commands),
            errors=tuple(errors),
            schema=schema,
        )
        return _write_transport_receipt(result, receipt_path)

    if not apply:
        result = GitHubHandoffTransportResult(
            ok=True,
            dry_run=True,
            applied=False,
            target=target,
            commands=tuple(commands),
            schema=schema,
        )
        return _write_transport_receipt(result, receipt_path)

    command_runner = runner or _run_gh_command
    command_results: list[dict[str, Any]] = []
    for command in commands:
        completed = command_runner(
            command,
            _stdin_for_generated_ticket_command(command, github_create, dedupe_projection),
        )
        command_results.append(_completed_process_result(command, completed))
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            stdout = (completed.stdout or "").strip()
            detail = stderr or stdout or f"exit_code={completed.returncode}"
            result = GitHubHandoffTransportResult(
                ok=False,
                dry_run=False,
                applied=False,
                target=target,
                commands=tuple(commands),
                command_results=tuple(command_results),
                errors=(f"GitHub command failed: {' '.join(command)}: {detail}",),
                schema=schema,
            )
            return _write_transport_receipt(result, receipt_path)

    result = GitHubHandoffTransportResult(
        ok=True,
        dry_run=False,
        applied=True,
        target=target,
        commands=tuple(commands),
        command_results=tuple(command_results),
        schema=schema,
    )
    return _write_transport_receipt(result, receipt_path)


def transport_command_loop_terminal_to_github(
    loop_receipt: Mapping[str, Any],
    *,
    apply: bool = False,
    receipt_path: Path | None = None,
    runner: CommandRunner | None = None,
) -> GitHubHandoffTransportResult:
    """Render or apply GitHub commands for a command-loop terminal projection."""

    schema = "tau.github_command_loop_terminal_transport_receipt.v1"
    errors: list[str] = []
    projection = _terminal_command_loop_projection(loop_receipt, errors)
    if projection is None:
        result = GitHubHandoffTransportResult(
            ok=False,
            dry_run=not apply,
            applied=False,
            target=None,
            commands=(),
            errors=tuple(errors),
            schema=schema,
        )
        return _write_transport_receipt(result, receipt_path)
    transport = transport_handoff_projection_to_github(
        projection,
        apply=apply,
        require_preflight=apply,
        runner=runner,
    )
    result = GitHubHandoffTransportResult(
        ok=transport.ok,
        dry_run=transport.dry_run,
        applied=transport.applied,
        target=transport.target,
        commands=transport.commands,
        command_results=transport.command_results,
        preflight_results=transport.preflight_results,
        errors=transport.errors,
        schema=schema,
    )
    return _write_transport_receipt(result, receipt_path)


def transport_goal_guardian_reconciliation_to_github(
    receipt: Mapping[str, Any],
    *,
    apply: bool = False,
    receipt_path: Path | None = None,
    runner: CommandRunner | None = None,
) -> GitHubHandoffTransportResult:
    """Render or apply GitHub commands for a goal-guardian reconciliation receipt."""

    schema = "tau.github_goal_guardian_reconciliation_transport_receipt.v1"
    errors: list[str] = []
    projection = _goal_guardian_reconciliation_projection(receipt, errors)
    if projection is None:
        result = GitHubHandoffTransportResult(
            ok=False,
            dry_run=not apply,
            applied=False,
            target=None,
            commands=(),
            errors=tuple(errors),
            schema=schema,
        )
        return _write_transport_receipt(result, receipt_path)
    transport = transport_handoff_projection_to_github(
        projection,
        apply=apply,
        require_preflight=apply,
        runner=runner,
    )
    result = GitHubHandoffTransportResult(
        ok=transport.ok,
        dry_run=transport.dry_run,
        applied=transport.applied,
        target=transport.target,
        commands=transport.commands,
        command_results=transport.command_results,
        preflight_results=transport.preflight_results,
        errors=transport.errors,
        schema=schema,
    )
    return _write_transport_receipt(result, receipt_path)


def _github_transport_commands(
    projection: Mapping[str, Any],
    errors: list[str],
) -> list[list[str]]:
    if projection.get("ok") is not True:
        errors.append("handoff projection must be ok before GitHub transport")
        return []

    target = _target_dict(projection)
    repo = _non_empty_string(target, "repo", "target", errors)
    target_ref = _non_empty_string(target, "target", "target", errors)

    comment = projection.get("comment")
    body = comment.get("body") if isinstance(comment, Mapping) else None
    if not isinstance(body, str) or not body.strip():
        errors.append("projection.comment.body must be a non-empty string")

    labels = projection.get("labels")
    add_labels: list[str] = []
    remove_labels: list[str] = []
    if isinstance(labels, Mapping):
        add_labels = _string_list(labels.get("add"), "labels.add", errors)
        remove_labels = _string_list(labels.get("remove"), "labels.remove", errors)
    else:
        errors.append("projection.labels must be an object")

    if errors:
        return []

    if target_ref == "new":
        command = [
            "gh",
            "issue",
            "create",
            "--repo",
            repo,
            "--title",
            _new_handoff_issue_title(projection),
            "--body-file",
            "-",
        ]
        if add_labels:
            command.extend(["--label", ",".join(add_labels)])
        return [command]

    ticket_kind, ticket_number = _parse_ticket_ref(target_ref, errors)
    if errors:
        return []

    command_kind = "pr" if ticket_kind == "pr" else "issue"
    commands: list[list[str]] = [
        ["gh", command_kind, "comment", ticket_number, "--repo", repo, "--body-file", "-"]
    ]
    edit_command = ["gh", command_kind, "edit", ticket_number, "--repo", repo]
    if add_labels:
        edit_command.extend(["--add-label", ",".join(add_labels)])
    if remove_labels:
        edit_command.extend(["--remove-label", ",".join(remove_labels)])
    if len(edit_command) > 6:
        commands.append(edit_command)
    return commands


def _github_issue_list_command(
    *,
    repo: str,
    state: str,
    limit: int,
    errors: list[str],
) -> list[str]:
    if not isinstance(repo, str) or not repo.strip():
        errors.append("repo must be a non-empty string")
    if state not in {"open", "closed", "all"}:
        errors.append("state must be one of: open, closed, all")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        errors.append("limit must be a positive integer")
    safe_limit = max(limit, 1) if isinstance(limit, int) and not isinstance(limit, bool) else 1
    return [
        "gh",
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        state,
        "--limit",
        str(safe_limit),
        "--json",
        "number,title,state,url,labels,body",
    ]


def _generated_ticket_transport_target(
    repo: str,
    dedupe_projection: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(dedupe_projection, Mapping):
        target = dedupe_projection.get("target")
        if isinstance(target, Mapping):
            return dict(target)
    return {"repo": repo, "target": "new"}


def _generated_ticket_transport_commands(
    *,
    repo: str,
    github_create: Mapping[str, Any],
    dedupe_projection: Mapping[str, Any] | None,
    errors: list[str],
) -> list[list[str]]:
    if dedupe_projection is None:
        return _generated_ticket_create_commands(repo, github_create, errors)
    if dedupe_projection.get("schema") != "tau.generated_ticket_dedupe_projection.v1":
        errors.append("dedupe_projection.schema must be tau.generated_ticket_dedupe_projection.v1")
        return []
    if dedupe_projection.get("ok") is not True:
        errors.append("dedupe_projection must be ok before generated-ticket GitHub transport")
        return []
    decision = dedupe_projection.get("decision")
    if decision == "create_new":
        projected_create = dedupe_projection.get("github_create")
        if not isinstance(projected_create, Mapping):
            errors.append("dedupe_projection.github_create must be present for create_new")
            return []
        return _generated_ticket_create_commands(repo, projected_create, errors)
    if decision != "update_existing_issue":
        errors.append("dedupe_projection.decision must be create_new or update_existing_issue")
        return []

    target = dedupe_projection.get("target")
    if not isinstance(target, Mapping):
        errors.append("dedupe_projection.target must be an object")
        return []
    target_repo = _non_empty_string(target, "repo", "dedupe_projection.target", errors)
    target_ref = _non_empty_string(target, "target", "dedupe_projection.target", errors)
    comment = dedupe_projection.get("comment")
    body = comment.get("body") if isinstance(comment, Mapping) else None
    if not isinstance(body, str) or not body.strip():
        errors.append("dedupe_projection.comment.body must be a non-empty string")
    labels = _string_list(dedupe_projection.get("labels"), "dedupe_projection.labels", errors)
    if errors:
        return []
    ticket_kind, ticket_number = _parse_ticket_ref(target_ref, errors)
    if ticket_kind != "issue":
        errors.append("generated-ticket dedupe updates currently support issue#<number> only")
    if errors:
        return []
    commands: list[list[str]] = [
        ["gh", "issue", "comment", ticket_number, "--repo", target_repo, "--body-file", "-"]
    ]
    if labels:
        commands.append(
            [
                "gh",
                "issue",
                "edit",
                ticket_number,
                "--repo",
                target_repo,
                "--add-label",
                ",".join(labels),
            ]
        )
    return commands


def _generated_ticket_create_commands(
    repo: str,
    github_create: Mapping[str, Any],
    errors: list[str],
) -> list[list[str]]:
    if not isinstance(repo, str) or not repo.strip():
        errors.append("github.repo must be a non-empty string")
    kind = _non_empty_string(github_create, "kind", "github_create", errors)
    title = _non_empty_string(github_create, "title", "github_create", errors)
    body = _non_empty_string(github_create, "body", "github_create", errors)
    labels = _string_list(github_create.get("labels"), "github_create.labels", errors)
    if kind and kind != "issue":
        errors.append("GitHub generated-ticket create currently supports kind='issue' only")
    if errors or not title or not body:
        return []
    command = [
        "gh",
        "issue",
        "create",
        "--repo",
        repo,
        "--title",
        title,
        "--body-file",
        "-",
    ]
    if labels:
        command.extend(["--label", ",".join(labels)])
    return [command]


def _github_preflight_commands(
    projection: Mapping[str, Any],
    errors: list[str],
) -> list[list[str]]:
    target = _target_dict(projection)
    repo = _non_empty_string(target, "repo", "target", errors)
    target_ref = _non_empty_string(target, "target", "target", errors)
    if target_ref == "new":
        return [["gh", "auth", "status", "--hostname", "github.com"]] if repo else []
    ticket_kind, ticket_number = _parse_ticket_ref(target_ref, errors)
    if errors or not repo:
        return []
    command_kind = "pr" if ticket_kind == "pr" else "issue"
    return [
        ["gh", "auth", "status", "--hostname", "github.com"],
        ["gh", command_kind, "view", ticket_number, "--repo", repo, "--json", "number"],
    ]


def _terminal_command_loop_projection(
    loop_receipt: Mapping[str, Any],
    errors: list[str],
) -> Mapping[str, Any] | None:
    if loop_receipt.get("schema") != "tau.agent_handoff_command_loop_receipt.v1":
        errors.append("loop receipt schema must be tau.agent_handoff_command_loop_receipt.v1")
    if loop_receipt.get("ok") is not True:
        errors.append("command loop receipt must be ok before GitHub transport")
    if loop_receipt.get("terminal_agent") != "human":
        errors.append("command loop terminal_agent must be human before terminal transport")
    dispatches = loop_receipt.get("dispatches")
    if not isinstance(dispatches, list) or not dispatches:
        errors.append("command loop receipt dispatches must be a non-empty list")
        return None
    last_dispatch = dispatches[-1]
    if not isinstance(last_dispatch, Mapping):
        errors.append("command loop last dispatch must be an object")
        return None
    projection = last_dispatch.get("response_projection")
    if not isinstance(projection, Mapping):
        errors.append("command loop last dispatch requires response_projection")
        return None
    if projection.get("ok") is not True:
        errors.append("command loop terminal response_projection must be ok")
    if projection.get("next_agent") != "human":
        errors.append("command loop terminal response_projection.next_agent must be human")
    if errors:
        return None
    return projection


def _goal_guardian_reconciliation_projection(
    receipt: Mapping[str, Any],
    errors: list[str],
) -> Mapping[str, Any] | None:
    if receipt.get("schema") != "tau.goal_guardian_reconciliation_receipt.v1":
        errors.append(
            "reconciliation receipt schema must be tau.goal_guardian_reconciliation_receipt.v1"
        )
    if receipt.get("ok") is not True:
        errors.append("reconciliation receipt must be ok before GitHub transport")
    if receipt.get("next_agent") != "human":
        errors.append("reconciliation receipt next_agent must be human before GitHub transport")
    github = receipt.get("github")
    if not isinstance(github, Mapping):
        errors.append("reconciliation receipt github must be an object")
        return None
    repo = _non_empty_string(github, "repo", "github", errors)
    target_ref = _non_empty_string(github, "target", "github", errors)
    if target_ref and target_ref != "new":
        _parse_ticket_ref(target_ref, errors)
    reconciliation = receipt.get("open_ticket_reconciliation")
    if not isinstance(reconciliation, Mapping):
        errors.append("reconciliation receipt open_ticket_reconciliation must be an object")
        return None
    if errors or repo is None or target_ref is None:
        return None
    return {
        "schema": "tau.agent_handoff_projection_receipt.v1",
        "ok": True,
        "dry_run": True,
        "next_agent": "human",
        "target": {"repo": repo, "target": target_ref},
        "labels": {
            "add": ["agent-work", "next:human", "executor:human", "goal-change"],
            "remove": ["next:goal-guardian", "agent-active"],
        },
        "comment": {"body": _goal_guardian_reconciliation_comment(receipt, reconciliation)},
        "errors": [],
    }


def _goal_guardian_reconciliation_comment(
    receipt: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
) -> str:
    counts = reconciliation.get("counts")
    counts_text = _counts_text(counts if isinstance(counts, Mapping) else {})
    new_goal = receipt.get("new_goal")
    goal_text = ""
    if isinstance(new_goal, Mapping):
        value = new_goal.get("text")
        if isinstance(value, str) and value.strip():
            goal_text = value.strip()
    body = [
        "## Tau Goal-Guardian Reconciliation",
        "",
        "- Decision: `REQUIRES_HUMAN_GOAL_VERSION`",
        "- Next agent: `human`",
        f"- Ticket reconciliation: `{reconciliation.get('status')}`",
        f"- Counts: {counts_text}",
    ]
    if goal_text:
        body.extend(["", "### Proposed Goal", "", goal_text])
    body.extend(
        [
            "",
            "### Reconciliation Receipt",
            "",
            "<!-- tau-goal-guardian-reconciliation:v1 -->",
            "```json",
            json.dumps(receipt, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )
    return "\n".join(body)


def _counts_text(counts: Mapping[str, Any]) -> str:
    ordered = []
    for name in ("keep", "close", "migrate", "regenerate"):
        value = counts.get(name, 0)
        ordered.append(f"{name}={value if isinstance(value, int) else 0}")
    return ", ".join(ordered)


def _target_dict(projection: Mapping[str, Any]) -> dict[str, Any] | None:
    target = projection.get("target")
    return dict(target) if isinstance(target, Mapping) else None


def _parse_ticket_ref(target_ref: str | None, errors: list[str]) -> tuple[str, str]:
    if target_ref is None:
        return "", ""
    prefix, separator, number = target_ref.partition("#")
    if separator != "#" or prefix not in {"issue", "pr"} or not number.isdigit():
        errors.append("target.target must be issue#<number> or pr#<number>")
        return "", ""
    return prefix, number


def _new_handoff_issue_title(projection: Mapping[str, Any]) -> str:
    next_agent = projection.get("next_agent")
    if isinstance(next_agent, str) and next_agent.strip():
        return f"Tau handoff: {next_agent.strip()}"
    return "Tau handoff"


def _non_empty_string(
    payload: Mapping[str, Any] | None,
    field: str,
    label: str,
    errors: list[str],
) -> str | None:
    value = payload.get(field) if isinstance(payload, Mapping) else None
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}.{field} must be a non-empty string")
        return None
    return value


def _string_list(value: object, label: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{label} must be a list")
        return []
    strings = [item for item in value if isinstance(item, str) and item.strip()]
    if len(strings) != len(value):
        errors.append(f"{label} must contain only non-empty strings")
    return strings


def _ticket_source_from_gh_issues(issues: list[object]) -> dict[str, Any]:
    tickets: list[dict[str, Any]] = []
    for index, issue in enumerate(issues):
        if not isinstance(issue, Mapping):
            raise ValueError(f"issue[{index}] must be an object")
        number = issue.get("number")
        title = issue.get("title")
        state = issue.get("state")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ValueError(f"issue[{index}].number must be a positive integer")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"issue[{index}].title must be a non-empty string")
        if not isinstance(state, str) or not state.strip():
            raise ValueError(f"issue[{index}].state must be a non-empty string")
        labels = issue.get("labels")
        ticket_labels: list[str] = []
        if isinstance(labels, list):
            for label in labels:
                if isinstance(label, Mapping):
                    name = label.get("name")
                    if isinstance(name, str) and name.strip():
                        ticket_labels.append(name.strip())
                elif isinstance(label, str) and label.strip():
                    ticket_labels.append(label.strip())
        tickets.append(
            {
                "id": f"issue#{number}",
                "kind": "issue",
                "number": number,
                "status": state.lower(),
                "title": title,
                "url": issue.get("url") if isinstance(issue.get("url"), str) else None,
                "labels": ticket_labels,
                "body": issue.get("body") if isinstance(issue.get("body"), str) else "",
            }
        )
    return {"schema": "tau.goal_guardian_ticket_source.v1", "tickets": tickets}


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be a JSON object: {path}")
    return payload


def _redact_value(value: Any, *, path: str, redactions: list[dict[str, str]]) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            if _is_sensitive_key(key_text):
                redactions.append({"path": child_path, "kind": "sensitive_key"})
                redacted[key_text] = f"<redacted:{key_text}>"
            else:
                redacted[key_text] = _redact_value(
                    child,
                    path=child_path,
                    redactions=redactions,
                )
        return redacted
    if isinstance(value, list):
        return [
            _redact_value(item, path=f"{path}[{index}]", redactions=redactions)
            for index, item in enumerate(value)
        ]
    if isinstance(value, str):
        redacted_value = _TOKEN_PATTERN.sub("<redacted-token>", value)
        redacted_value = _LOCAL_PATH_PATTERN.sub("<redacted-local-path>", redacted_value)
        if redacted_value != value:
            redactions.append({"path": path, "kind": "string_pattern"})
        return redacted_value
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in _SENSITIVE_KEYS or normalized.endswith("_token")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stdin_for_command(command: list[str], projection: Mapping[str, Any]) -> str | None:
    if command[:3] != ["gh", command[1], "comment"] and command[:3] != [
        "gh",
        "issue",
        "create",
    ]:
        return None
    comment = projection.get("comment")
    if not isinstance(comment, Mapping):
        return None
    body = comment.get("body")
    return body if isinstance(body, str) else None


def _stdin_for_generated_ticket_command(
    command: list[str],
    github_create: Mapping[str, Any],
    dedupe_projection: Mapping[str, Any] | None,
) -> str | None:
    if command[:3] == ["gh", "issue", "create"]:
        body = github_create.get("body")
        if isinstance(dedupe_projection, Mapping):
            projected_create = dedupe_projection.get("github_create")
            if isinstance(projected_create, Mapping):
                body = projected_create.get("body")
        return body if isinstance(body, str) else None
    if command[:3] == ["gh", "issue", "comment"] and isinstance(dedupe_projection, Mapping):
        comment = dedupe_projection.get("comment")
        if isinstance(comment, Mapping):
            body = comment.get("body")
            return body if isinstance(body, str) else None
    return None


def _run_gh_command(command: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


def _completed_process_result(
    command: list[str],
    completed: subprocess.CompletedProcess[str],
) -> dict[str, Any]:
    return {
        "command": list(command),
        "exit_code": completed.returncode,
        "stdout": completed.stdout or "",
        "stderr": completed.stderr or "",
    }


def _completed_process_detail(completed: subprocess.CompletedProcess[str]) -> str:
    stderr = (completed.stderr or "").strip()
    stdout = (completed.stdout or "").strip()
    return stderr or stdout or f"exit_code={completed.returncode}"


def _write_transport_receipt(
    result: GitHubHandoffTransportResult,
    receipt_path: Path | None,
) -> GitHubHandoffTransportResult:
    if receipt_path is None:
        return result
    resolved = receipt_path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = {**result.as_dict(), "receipt_path": str(resolved)}
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return GitHubHandoffTransportResult(
        ok=result.ok,
        dry_run=result.dry_run,
        applied=result.applied,
        target=result.target,
        commands=result.commands,
        command_results=result.command_results,
        preflight_results=result.preflight_results,
        receipt_path=str(resolved),
        errors=result.errors,
        schema=result.schema,
    )


def _write_ticket_source_fetch_receipt(
    result: GitHubTicketSourceFetchResult,
    receipt_path: Path | None,
) -> GitHubTicketSourceFetchResult:
    if receipt_path is None:
        return result
    resolved = receipt_path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = {**result.as_dict(), "receipt_path": str(resolved)}
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return GitHubTicketSourceFetchResult(
        ok=result.ok,
        dry_run=result.dry_run,
        executed=result.executed,
        repo=result.repo,
        command=result.command,
        command_result=result.command_result,
        ticket_source_path=result.ticket_source_path,
        ticket_source=result.ticket_source,
        receipt_path=str(resolved),
        errors=result.errors,
        schema=result.schema,
    )
