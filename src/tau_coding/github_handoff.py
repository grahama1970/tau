"""Apply-gated GitHub transport for Tau agent handoff projections."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CommandRunner = Callable[[list[str], str | None], subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class GitHubHandoffTransportResult:
    """Receipt for a dry-run or applied GitHub handoff transport."""

    ok: bool
    dry_run: bool
    applied: bool
    target: dict[str, Any] | None = None
    commands: tuple[list[str], ...] = ()
    command_results: tuple[dict[str, Any], ...] = ()
    receipt_path: str | None = None
    errors: tuple[str, ...] = ()
    schema: str = "tau.github_handoff_transport_receipt.v1"

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable transport receipt."""

        return {
            "schema": self.schema,
            "ok": self.ok,
            "dry_run": self.dry_run,
            "applied": self.applied,
            "target": self.target,
            "commands": [list(command) for command in self.commands],
            "command_results": list(self.command_results),
            "receipt_path": self.receipt_path,
            "errors": list(self.errors),
        }


def transport_handoff_projection_to_github(
    projection: Mapping[str, Any],
    *,
    apply: bool = False,
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
    command_results: list[dict[str, Any]] = []
    for command in commands:
        completed = command_runner(command, _stdin_for_command(command, projection))
        command_results.append(_completed_process_result(command, completed))
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            stdout = (completed.stdout or "").strip()
            detail = stderr or stdout or f"exit_code={completed.returncode}"
            result = GitHubHandoffTransportResult(
                ok=False,
                dry_run=False,
                applied=False,
                target=_target_dict(projection),
                commands=tuple(commands),
                command_results=tuple(command_results),
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
    )
    return _write_transport_receipt(result, receipt_path)


def transport_generated_ticket_to_github(
    *,
    repo: str,
    github_create: Mapping[str, Any],
    apply: bool = False,
    receipt_path: Path | None = None,
    runner: CommandRunner | None = None,
) -> GitHubHandoffTransportResult:
    """Render or apply a GitHub issue create command for one generated-ticket projection."""

    errors: list[str] = []
    commands = _generated_ticket_create_commands(repo, github_create, errors)
    target = {"repo": repo, "target": "new"}
    schema = "tau.github_generated_ticket_transport_receipt.v1"
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
    completed = command_runner(commands[0], str(github_create["body"]))
    command_results = (_completed_process_result(commands[0], completed),)
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
            command_results=command_results,
            errors=(f"GitHub command failed: {' '.join(commands[0])}: {detail}",),
            schema=schema,
        )
        return _write_transport_receipt(result, receipt_path)

    result = GitHubHandoffTransportResult(
        ok=True,
        dry_run=False,
        applied=True,
        target=target,
        commands=tuple(commands),
        command_results=command_results,
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
        runner=runner,
    )
    result = GitHubHandoffTransportResult(
        ok=transport.ok,
        dry_run=transport.dry_run,
        applied=transport.applied,
        target=transport.target,
        commands=transport.commands,
        command_results=transport.command_results,
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
    ticket_kind, ticket_number = _parse_ticket_ref(target_ref, errors)

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


def _stdin_for_command(command: list[str], projection: Mapping[str, Any]) -> str | None:
    if command[:3] != ["gh", command[1], "comment"]:
        return None
    comment = projection.get("comment")
    if not isinstance(comment, Mapping):
        return None
    body = comment.get("body")
    return body if isinstance(body, str) else None


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
        receipt_path=str(resolved),
        errors=result.errors,
        schema=result.schema,
    )
