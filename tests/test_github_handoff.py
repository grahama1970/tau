import json
import subprocess
from pathlib import Path

from tau_coding.github_handoff import (
    fetch_goal_guardian_ticket_source_from_github,
    transport_command_loop_terminal_to_github,
    transport_generated_ticket_to_github,
    transport_goal_guardian_reconciliation_to_github,
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


def test_goal_guardian_reconciliation_transport_dry_run_renders_comment_and_labels() -> None:
    receipt = _valid_reconciliation_receipt()

    result = transport_goal_guardian_reconciliation_to_github(receipt)

    assert result.ok is True
    assert result.schema == "tau.github_goal_guardian_reconciliation_transport_receipt.v1"
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
            "agent-work,next:human,executor:human,goal-change",
            "--remove-label",
            "next:goal-guardian,agent-active",
        ],
    )


def test_goal_guardian_reconciliation_transport_apply_uses_preflight_and_body() -> None:
    receipt = _valid_reconciliation_receipt()
    calls: list[tuple[list[str], str | None]] = []

    def runner(command: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        calls.append((command, stdin))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = transport_goal_guardian_reconciliation_to_github(
        receipt,
        apply=True,
        runner=runner,
    )

    assert result.ok is True
    assert result.dry_run is False
    assert result.applied is True
    assert len(calls) == 4
    assert calls[0][0] == ["gh", "auth", "status", "--hostname", "github.com"]
    assert calls[1][0][:3] == ["gh", "issue", "view"]
    assert calls[2][0][:3] == ["gh", "issue", "comment"]
    assert "Tau Goal-Guardian Reconciliation" in (calls[2][1] or "")
    assert "tau-goal-guardian-reconciliation:v1" in (calls[2][1] or "")
    assert calls[3][0][:3] == ["gh", "issue", "edit"]


def test_goal_guardian_reconciliation_transport_refuses_non_human_next_agent() -> None:
    receipt = _valid_reconciliation_receipt()
    receipt["next_agent"] = "coder"

    result = transport_goal_guardian_reconciliation_to_github(receipt)

    assert result.ok is False
    assert result.applied is False
    assert "next_agent must be human" in "\n".join(result.errors)


def test_goal_guardian_ticket_source_github_fetch_dry_run_renders_issue_list() -> None:
    result = fetch_goal_guardian_ticket_source_from_github(
        repo="grahama1970/chatgpt-lab",
        output_path=Path("ticket-source.json"),
        execute=False,
        state="open",
        limit=25,
    )

    assert result.ok is True
    assert result.dry_run is True
    assert result.executed is False
    assert result.command == [
        "gh",
        "issue",
        "list",
        "--repo",
        "grahama1970/chatgpt-lab",
        "--state",
        "open",
        "--limit",
        "25",
        "--json",
        "number,title,state,url,labels",
    ]
    assert result.ticket_source is None
    assert result.ticket_source_path is None


def test_goal_guardian_ticket_source_github_fetch_execute_writes_ticket_source(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "goal-guardian-ticket-source.json"
    receipt_path = tmp_path / "fetch-receipt.json"
    gh_stdout = json.dumps(
        [
            {
                "number": 7,
                "title": "Route goal update",
                "state": "OPEN",
                "url": "https://github.com/grahama1970/chatgpt-lab/issues/7",
                "labels": [{"name": "agent-work"}, {"name": "ticket:goal"}],
            },
            {
                "number": 8,
                "title": "Closed stale task",
                "state": "CLOSED",
                "url": "https://github.com/grahama1970/chatgpt-lab/issues/8",
                "labels": ["agent-done"],
            },
        ]
    )

    def runner(command: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        assert stdin is None
        return subprocess.CompletedProcess(command, 0, stdout=gh_stdout, stderr="")

    result = fetch_goal_guardian_ticket_source_from_github(
        repo="grahama1970/chatgpt-lab",
        output_path=output_path,
        execute=True,
        state="all",
        limit=2,
        receipt_path=receipt_path,
        runner=runner,
    )
    source = json.loads(output_path.read_text())
    receipt = json.loads(receipt_path.read_text())

    assert result.ok is True
    assert result.dry_run is False
    assert result.executed is True
    assert source["schema"] == "tau.goal_guardian_ticket_source.v1"
    assert source["tickets"] == [
        {
            "id": "issue#7",
            "kind": "issue",
            "number": 7,
            "status": "open",
            "title": "Route goal update",
            "url": "https://github.com/grahama1970/chatgpt-lab/issues/7",
            "labels": ["agent-work", "ticket:goal"],
        },
        {
            "id": "issue#8",
            "kind": "issue",
            "number": 8,
            "status": "closed",
            "title": "Closed stale task",
            "url": "https://github.com/grahama1970/chatgpt-lab/issues/8",
            "labels": ["agent-done"],
        },
    ]
    assert result.ticket_source == source
    assert receipt == result.as_dict()


def test_goal_guardian_ticket_source_github_fetch_fail_closed_on_runner_error() -> None:
    def runner(command: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        del stdin
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="api denied")

    result = fetch_goal_guardian_ticket_source_from_github(
        repo="grahama1970/chatgpt-lab",
        output_path=Path("ticket-source.json"),
        execute=True,
        runner=runner,
    )

    assert result.ok is False
    assert result.dry_run is False
    assert result.executed is True
    assert result.ticket_source is None
    assert "GitHub issue list failed" in result.errors[0]
    assert "api denied" in result.errors[0]


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


def _valid_reconciliation_receipt() -> dict:
    return {
        "schema": "tau.goal_guardian_reconciliation_receipt.v1",
        "ok": True,
        "dry_run": True,
        "github": {"repo": "grahama1970/chatgpt-lab", "target": "issue#123"},
        "goal": {
            "goal_id": "goal-tau-orchestration-001",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "decision": "REQUIRES_HUMAN_GOAL_VERSION",
        "new_goal": {
            "text": "Build Tau's goal-locked GitHub ticket harness one slice at a time.",
            "success_criteria": ["New goal capsule is written."],
            "constraints": ["Only humans can amend immutable goals."],
            "non_goals": [],
        },
        "source_schema": "tau.human_goal_change.v1",
        "source": "experiments/goal-locked-subagents/fixtures/valid-human-goal-change.json",
        "source_artifacts": [],
        "open_ticket_reconciliation": {
            "status": "classified",
            "reason": "Classified tickets from authoritative local ticket source.",
            "source": "experiments/goal-locked-subagents/fixtures/goal-guardian-ticket-source.json",
            "source_schema": "tau.goal_guardian_ticket_source.v1",
            "counts": {"keep": 1, "close": 1, "migrate": 1, "regenerate": 1},
            "keep": [{"id": "issue#101", "title": "Keep current proof artifact review"}],
            "close": [{"id": "issue#104", "title": "Close superseded branch"}],
            "migrate": [{"id": "issue#102", "title": "Migrate goal capsule documentation"}],
            "regenerate": [{"id": "issue#103", "title": "Regenerate stale implementation ticket"}],
        },
        "next_agent": "human",
        "errors": [],
    }
