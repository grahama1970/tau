import subprocess

from tau_coding.github_handoff import transport_handoff_projection_to_github


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


def test_github_handoff_transport_refuses_new_target() -> None:
    projection = _valid_projection()
    projection["target"]["target"] = "new"

    result = transport_handoff_projection_to_github(projection, apply=False)

    assert result.ok is False
    assert result.applied is False
    assert "target.target must be issue#<number> or pr#<number>" in result.errors


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
