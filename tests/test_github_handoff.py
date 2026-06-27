import subprocess

from tau_coding.github_handoff import (
    transport_command_loop_terminal_to_github,
    transport_generated_ticket_to_github,
    transport_handoff_projection_to_github,
)


def test_github_handoff_transport_dry_run_renders_comment_and_label_commands() -> None:
    projection = _valid_projection()

    result = transport_handoff_projection_to_github(projection, apply=False)

    assert result.ok is True
    assert result.dry_run is True
    assert result.applied is False
    assert result.commands == (
        [
            "gh",
            "issue",
            "comment",
            "123",
            "--repo",
            "grahama1970/chatgpt-lab",
            "--body-file",
            "-",
        ],
        [
            "gh",
            "issue",
            "edit",
            "123",
            "--repo",
            "grahama1970/chatgpt-lab",
            "--add-label",
            "agent-work,next:reviewer,executor:either",
            "--remove-label",
            "agent-active,agent-blocked",
        ],
    )


def test_github_handoff_transport_apply_uses_runner_with_comment_stdin() -> None:
    projection = _valid_projection()
    calls: list[tuple[list[str], str | None]] = []

    def runner(command: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        calls.append((command, stdin))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = transport_handoff_projection_to_github(projection, apply=True, runner=runner)

    assert result.ok is True
    assert result.dry_run is False
    assert result.applied is True
    assert len(calls) == 2
    assert calls[0][0][:3] == ["gh", "issue", "comment"]
    assert calls[0][1] == "## Tau Agent Handoff\n"
    assert calls[1][0][:3] == ["gh", "issue", "edit"]
    assert calls[1][1] is None
    assert result.command_results == (
        {"command": calls[0][0], "exit_code": 0, "stdout": "", "stderr": ""},
        {"command": calls[1][0], "exit_code": 0, "stdout": "", "stderr": ""},
    )


def test_github_handoff_transport_new_target_renders_issue_create() -> None:
    projection = _valid_projection()
    projection["target"]["target"] = "new"
    projection["next_agent"] = "human"

    result = transport_handoff_projection_to_github(projection, apply=False)

    assert result.ok is True
    assert result.dry_run is True
    assert result.applied is False
    assert result.target == {"repo": "grahama1970/chatgpt-lab", "target": "new"}
    assert result.commands == (
        [
            "gh",
            "issue",
            "create",
            "--repo",
            "grahama1970/chatgpt-lab",
            "--title",
            "Tau handoff: human",
            "--body-file",
            "-",
            "--label",
            "agent-work,next:reviewer,executor:either",
        ],
    )


def test_github_handoff_transport_new_target_apply_uses_body_stdin_and_auth_preflight() -> None:
    projection = _valid_projection()
    projection["target"]["target"] = "new"
    projection["next_agent"] = "human"
    calls: list[tuple[list[str], str | None]] = []

    def runner(command: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        calls.append((command, stdin))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = transport_handoff_projection_to_github(
        projection,
        apply=True,
        require_preflight=True,
        runner=runner,
    )

    assert result.ok is True
    assert result.dry_run is False
    assert result.applied is True
    assert len(calls) == 2
    assert calls[0][0] == ["gh", "auth", "status", "--hostname", "github.com"]
    assert calls[0][1] is None
    assert calls[1][0][:3] == ["gh", "issue", "create"]
    assert calls[1][1] == "## Tau Agent Handoff\n"
    assert len(result.preflight_results) == 1
    assert len(result.command_results) == 1


def test_github_handoff_transport_records_runner_failure() -> None:
    projection = _valid_projection()

    def runner(command: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        del stdin
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="denied")

    result = transport_handoff_projection_to_github(projection, apply=True, runner=runner)

    assert result.ok is False
    assert result.dry_run is False
    assert result.applied is False
    assert "GitHub command failed" in result.errors[0]
    assert "denied" in result.errors[0]


def test_generated_ticket_transport_dry_run_renders_issue_create_command() -> None:
    result = transport_generated_ticket_to_github(
        repo="grahama1970/tau",
        github_create=_valid_github_create(),
        apply=False,
    )

    assert result.ok is True
    assert result.dry_run is True
    assert result.applied is False
    assert result.target == {"repo": "grahama1970/tau", "target": "new"}
    assert result.commands == (
        [
            "gh",
            "issue",
            "create",
            "--repo",
            "grahama1970/tau",
            "--title",
            "Review Tau generated-ticket contract evidence",
            "--body-file",
            "-",
            "--label",
            "agent-work,next:reviewer,executor:either",
        ],
    )


def test_generated_ticket_transport_apply_uses_runner_with_body_stdin() -> None:
    calls: list[tuple[list[str], str | None]] = []

    def runner(command: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        calls.append((command, stdin))
        return subprocess.CompletedProcess(command, 0, stdout="https://github.com/x/y/issues/1\n")

    result = transport_generated_ticket_to_github(
        repo="grahama1970/tau",
        github_create=_valid_github_create(),
        apply=True,
        runner=runner,
    )

    assert result.ok is True
    assert result.dry_run is False
    assert result.applied is True
    assert len(calls) == 1
    assert calls[0][0][:3] == ["gh", "issue", "create"]
    assert calls[0][1] == "Review the generated-ticket contract evidence."
    assert result.command_results == (
        {
            "command": calls[0][0],
            "exit_code": 0,
            "stdout": "https://github.com/x/y/issues/1\n",
            "stderr": "",
        },
    )


def test_generated_ticket_transport_refuses_pull_request_create() -> None:
    github_create = _valid_github_create()
    github_create["kind"] = "pull_request"

    result = transport_generated_ticket_to_github(
        repo="grahama1970/tau",
        github_create=github_create,
        apply=False,
    )

    assert result.ok is False
    assert result.applied is False
    assert "supports kind='issue' only" in "\n".join(result.errors)


def test_command_loop_terminal_transport_dry_run_uses_last_response_projection() -> None:
    receipt = _valid_command_loop_receipt()

    result = transport_command_loop_terminal_to_github(receipt)

    assert result.ok is True
    assert result.schema == "tau.github_command_loop_terminal_transport_receipt.v1"
    assert result.dry_run is True
    assert result.applied is False
    assert result.target == {"repo": "grahama1970/chatgpt-lab", "target": "issue#123"}
    assert result.commands == (
        [
            "gh",
            "issue",
            "comment",
            "123",
            "--repo",
            "grahama1970/chatgpt-lab",
            "--body-file",
            "-",
        ],
        [
            "gh",
            "issue",
            "edit",
            "123",
            "--repo",
            "grahama1970/chatgpt-lab",
            "--add-label",
            "agent-work,next:human,executor:human",
        ],
    )


def test_command_loop_terminal_transport_refuses_non_human_terminal() -> None:
    receipt = _valid_command_loop_receipt()
    receipt["terminal_agent"] = "project-or-harness-verifier"

    result = transport_command_loop_terminal_to_github(receipt)

    assert result.ok is False
    assert "terminal_agent must be human" in "\n".join(result.errors)


def test_command_loop_terminal_transport_apply_uses_runner() -> None:
    receipt = _valid_command_loop_receipt()
    calls: list[tuple[list[str], str | None]] = []

    def runner(command: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        calls.append((command, stdin))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = transport_command_loop_terminal_to_github(receipt, apply=True, runner=runner)

    assert result.ok is True
    assert result.schema == "tau.github_command_loop_terminal_transport_receipt.v1"
    assert result.dry_run is False
    assert result.applied is True
    assert len(calls) == 4
    assert calls[0][0] == ["gh", "auth", "status", "--hostname", "github.com"]
    assert calls[0][1] is None
    assert calls[1][0] == [
        "gh",
        "issue",
        "view",
        "123",
        "--repo",
        "grahama1970/chatgpt-lab",
        "--json",
        "number",
    ]
    assert calls[1][1] is None
    assert calls[2][0][:3] == ["gh", "issue", "comment"]
    assert calls[2][1] == "## Tau Agent Handoff\n"
    assert calls[3][0][:3] == ["gh", "issue", "edit"]
    assert calls[3][1] is None
    assert len(result.preflight_results) == 2
    assert len(result.command_results) == 2


def test_command_loop_terminal_transport_apply_refuses_when_preflight_fails() -> None:
    receipt = _valid_command_loop_receipt()
    calls: list[tuple[list[str], str | None]] = []

    def runner(command: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        calls.append((command, stdin))
        if command[:3] == ["gh", "issue", "view"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = transport_command_loop_terminal_to_github(receipt, apply=True, runner=runner)

    assert result.ok is False
    assert result.schema == "tau.github_command_loop_terminal_transport_receipt.v1"
    assert result.dry_run is False
    assert result.applied is False
    assert len(calls) == 2
    assert calls[0][0][:3] == ["gh", "auth", "status"]
    assert calls[1][0][:3] == ["gh", "issue", "view"]
    assert result.command_results == ()
    assert len(result.preflight_results) == 2
    assert "GitHub preflight failed" in result.errors[0]
    assert "not found" in result.errors[0]


def _valid_projection() -> dict:
    return {
        "schema": "tau.agent_handoff_projection_receipt.v1",
        "ok": True,
        "dry_run": True,
        "next_agent": "reviewer",
        "target": {
            "repo": "grahama1970/chatgpt-lab",
            "target": "issue#123",
        },
        "labels": {
            "add": ["agent-work", "next:reviewer", "executor:either"],
            "remove": ["agent-active", "agent-blocked"],
        },
        "comment": {"body": "## Tau Agent Handoff\n"},
        "errors": [],
    }


def _valid_command_loop_receipt() -> dict:
    return {
        "schema": "tau.agent_handoff_command_loop_receipt.v1",
        "ok": True,
        "status": "WAITING",
        "step_count": 2,
        "terminal_agent": "human",
        "stop_reason": "next_agent_is_human",
        "mocked": False,
        "live": True,
        "runner": "agent-registry-command-loop",
        "dispatches": [
            {
                "selected_agent": "goal-guardian",
                "response_projection": {
                    **_valid_projection(),
                    "next_agent": "project-or-harness-verifier",
                },
            },
            {
                "selected_agent": "project-or-harness-verifier",
                "response_projection": {
                    **_valid_projection(),
                    "next_agent": "human",
                    "labels": {
                        "add": ["agent-work", "next:human", "executor:human"],
                        "remove": [],
                    },
                },
            },
        ],
        "errors": [],
    }


def _valid_github_create() -> dict:
    return {
        "kind": "issue",
        "title": "Review Tau generated-ticket contract evidence",
        "body": "Review the generated-ticket contract evidence.",
        "labels": ["agent-work", "next:reviewer", "executor:either"],
    }
