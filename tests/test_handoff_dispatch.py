import json
import sys
from pathlib import Path

from tau_coding.handoff_dispatch import (
    TAU_AGENT_HANDOFF_DISPATCH_RECEIPT_SCHEMA,
    dispatch_agent_handoff_command_once,
    dispatch_agent_handoff_once,
    load_agent_dispatch_command_spec,
    run_agent_handoff_command_loop,
    write_agent_handoff_command_loop_receipt,
    write_agent_handoff_command_dispatch_receipt,
    write_agent_handoff_dispatch_receipt,
)


def test_handoff_dispatch_consumes_selected_agent_response() -> None:
    start = _valid_handoff()
    reviewer = _valid_handoff()
    reviewer["previous_subagent"] = "reviewer"
    reviewer["next_agent"] = {
        "name": "human",
        "executor": "human",
        "reason": "Human decides the next live route.",
    }

    result = dispatch_agent_handoff_once(
        start,
        {"reviewer": reviewer},
        active_goal_hash="sha256:active-goal",
    )

    assert result.ok is True
    assert result.status == "COMPLETED"
    assert result.selected_agent == "reviewer"
    assert result.stop_reason == "response_consumed"
    assert result.mocked is True
    assert result.live is False
    assert result.response_projection is not None
    assert result.response_projection["next_agent"] == "human"


def test_handoff_dispatch_waits_for_missing_response() -> None:
    result = dispatch_agent_handoff_once(
        _valid_handoff(),
        {},
        active_goal_hash="sha256:active-goal",
    )

    assert result.ok is True
    assert result.status == "WAITING"
    assert result.selected_agent == "reviewer"
    assert result.stop_reason == "missing_agent_response"
    assert result.response_projection is None


def test_handoff_dispatch_waits_when_next_agent_is_human() -> None:
    start = _valid_handoff()
    start["next_agent"] = {
        "name": "human",
        "executor": "human",
        "reason": "Human route required.",
    }

    result = dispatch_agent_handoff_once(
        start,
        {},
        active_goal_hash="sha256:active-goal",
    )

    assert result.ok is True
    assert result.status == "WAITING"
    assert result.selected_agent == "human"
    assert result.stop_reason == "next_agent_is_human"


def test_handoff_dispatch_blocks_route_discontinuity() -> None:
    start = _valid_handoff()
    reviewer = _valid_handoff()
    reviewer["previous_subagent"] = "coder"

    result = dispatch_agent_handoff_once(
        start,
        {"reviewer": reviewer},
        active_goal_hash="sha256:active-goal",
    )

    assert result.ok is False
    assert result.status == "BLOCKED"
    assert result.stop_reason == "invalid_agent_response"
    assert "response.previous_subagent must equal selected_agent" in "\n".join(result.errors)


def test_handoff_dispatch_receipt_writes_projection_artifacts(tmp_path: Path) -> None:
    start = _valid_handoff()
    reviewer = _valid_handoff()
    reviewer["previous_subagent"] = "reviewer"
    receipt_dir = tmp_path / "dispatch"

    result = write_agent_handoff_dispatch_receipt(
        start,
        {"reviewer": reviewer},
        receipt_dir,
        active_goal_hash="sha256:active-goal",
    )
    receipt = json.loads((receipt_dir / "dispatch-receipt.json").read_text())

    assert result.ok is True
    assert receipt["schema"] == TAU_AGENT_HANDOFF_DISPATCH_RECEIPT_SCHEMA
    assert receipt["status"] == "COMPLETED"
    assert receipt["selected_agent"] == "reviewer"
    assert receipt["mocked"] is True
    assert receipt["live"] is False
    assert receipt["artifacts"] == [
        str(receipt_dir / "start-handoff.receipt.json"),
        str(receipt_dir / "reviewer-response.receipt.json"),
    ]
    assert (receipt_dir / "start-handoff.receipt.json").exists()
    assert (receipt_dir / "reviewer-response.receipt.json").exists()


def test_command_handoff_dispatch_consumes_stdout_response(tmp_path: Path) -> None:
    response = _valid_handoff()
    response["previous_subagent"] = "reviewer"
    response["next_agent"] = {
        "name": "human",
        "executor": "human",
        "reason": "Human decides the next route.",
    }
    response_path = tmp_path / "response.json"
    response_path.write_text(json.dumps(response), encoding="utf-8")

    result = dispatch_agent_handoff_command_once(
        _valid_handoff(),
        [
            sys.executable,
            "-c",
            f"from pathlib import Path; print(Path({str(response_path)!r}).read_text())",
        ],
        active_goal_hash="sha256:active-goal",
    )

    assert result.ok is True
    assert result.status == "COMPLETED"
    assert result.selected_agent == "reviewer"
    assert result.mocked is False
    assert result.live is True
    assert result.runner == "command"
    assert result.command_results[0]["exit_code"] == 0
    assert result.response_projection is not None
    assert result.response_projection["next_agent"] == "human"


def test_command_handoff_dispatch_blocks_nonzero_exit() -> None:
    result = dispatch_agent_handoff_command_once(
        _valid_handoff(),
        [sys.executable, "-c", "import sys; print('nope'); sys.exit(7)"],
        active_goal_hash="sha256:active-goal",
    )

    assert result.ok is False
    assert result.status == "BLOCKED"
    assert result.stop_reason == "command_failed"
    assert result.command_results[0]["exit_code"] == 7


def test_command_handoff_dispatch_blocks_malformed_json() -> None:
    result = dispatch_agent_handoff_command_once(
        _valid_handoff(),
        [sys.executable, "-c", "print('not json')"],
        active_goal_hash="sha256:active-goal",
    )

    assert result.ok is False
    assert result.status == "BLOCKED"
    assert result.stop_reason == "invalid_command_json"
    assert "command stdout was not JSON" in "\n".join(result.errors)


def test_command_handoff_dispatch_receipt_writes_command_results(tmp_path: Path) -> None:
    response = _valid_handoff()
    response["previous_subagent"] = "reviewer"
    response_path = tmp_path / "response.json"
    response_path.write_text(json.dumps(response), encoding="utf-8")
    receipt_dir = tmp_path / "command-dispatch"

    result = write_agent_handoff_command_dispatch_receipt(
        _valid_handoff(),
        [
            sys.executable,
            "-c",
            f"from pathlib import Path; print(Path({str(response_path)!r}).read_text())",
        ],
        receipt_dir,
        active_goal_hash="sha256:active-goal",
    )
    receipt = json.loads((receipt_dir / "dispatch-receipt.json").read_text())

    assert result.ok is True
    assert receipt["runner"] == "command"
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["command_results"][0]["exit_code"] == 0
    assert (receipt_dir / "start-handoff.receipt.json").exists()
    assert (receipt_dir / "reviewer-response.receipt.json").exists()


def test_command_handoff_dispatch_exposes_selected_agent_env(tmp_path: Path) -> None:
    response = _valid_handoff()
    response["previous_subagent"] = "reviewer"
    response["next_agent"] = {
        "name": "human",
        "executor": "human",
        "reason": "Human review is required.",
    }
    script = (
        "import json, os, sys; "
        "payload=json.load(sys.stdin); "
        f"response={json.dumps(response)!r}; "
        "data=json.loads(response); "
        "data['previous_subagent']=os.environ['TAU_HANDOFF_SELECTED_AGENT']; "
        "data['context']['artifacts']=payload['context']['artifacts']; "
        "print(json.dumps(data))"
    )

    result = dispatch_agent_handoff_command_once(
        _valid_handoff(),
        [sys.executable, "-c", script],
        active_goal_hash="sha256:active-goal",
    )

    assert result.ok is True
    assert result.live is True
    assert result.selected_agent == "reviewer"
    assert result.response_projection is not None
    assert result.response_projection["next_agent"] == "human"


def test_load_agent_dispatch_command_spec_from_registry(tmp_path: Path) -> None:
    agent_dir = tmp_path / "reviewer"
    agent_dir.mkdir()
    (agent_dir / "AGENTS.md").write_text("---\nid: reviewer\n---\n", encoding="utf-8")
    (agent_dir / "tau-dispatch-command.json").write_text(
        json.dumps({"command": [sys.executable, "-c", "print('{}')"], "timeout_s": 3}),
        encoding="utf-8",
    )

    spec = load_agent_dispatch_command_spec(tmp_path, "reviewer")

    assert spec["command"] == [sys.executable, "-c", "print('{}')"]
    assert spec["timeout_s"] == 3.0
    assert spec["cwd"] is None


def test_load_agent_dispatch_command_spec_from_overlay_requires_registry_entry(
    tmp_path: Path,
) -> None:
    agents_root = tmp_path / "agents"
    spec_root = tmp_path / "specs"
    agent_dir = agents_root / "project-or-harness-verifier"
    spec_dir = spec_root / "project-or-harness-verifier"
    agent_dir.mkdir(parents=True)
    spec_dir.mkdir(parents=True)
    (agent_dir / "AGENTS.md").write_text(
        "---\nid: project-or-harness-verifier\n---\n",
        encoding="utf-8",
    )
    (spec_dir / "tau-dispatch-command.json").write_text(
        json.dumps({"command": [sys.executable, "-c", "print('{}')"], "timeout_s": 3}),
        encoding="utf-8",
    )

    spec = load_agent_dispatch_command_spec(
        agents_root,
        "project-or-harness-verifier",
        command_spec_root=spec_root,
    )

    assert spec["command"] == [sys.executable, "-c", "print('{}')"]
    assert spec["timeout_s"] == 3.0


def test_load_agent_dispatch_command_spec_allows_builtin_agent_overlay(tmp_path: Path) -> None:
    agents_root = tmp_path / "agents"
    spec_root = tmp_path / "specs"
    spec_dir = spec_root / "goal-guardian"
    agents_root.mkdir()
    spec_dir.mkdir(parents=True)
    (spec_dir / "tau-dispatch-command.json").write_text(
        json.dumps({"command": [sys.executable, "-c", "print('{}')"], "timeout_s": 3}),
        encoding="utf-8",
    )

    spec = load_agent_dispatch_command_spec(
        agents_root,
        "goal-guardian",
        command_spec_root=spec_root,
    )

    assert spec["command"] == [sys.executable, "-c", "print('{}')"]


def test_committed_reviewer_overlay_command_spec_loads() -> None:
    root = Path(__file__).resolve().parents[1]

    spec = load_agent_dispatch_command_spec(
        root / "missing-agent-registry-root",
        "reviewer",
        command_spec_root=root / "experiments/goal-locked-subagents/agent-command-specs",
    )

    assert spec["command"][:4] == ["uv", "run", "tau", "handoff-agent-adapter"]
    assert "--next-agent" in spec["command"]
    assert "human" in spec["command"]
    assert spec["timeout_s"] == 30.0


def test_load_agent_dispatch_command_spec_fails_closed_when_missing(tmp_path: Path) -> None:
    agent_dir = tmp_path / "reviewer"
    agent_dir.mkdir()
    (agent_dir / "AGENTS.md").write_text("---\nid: reviewer\n---\n", encoding="utf-8")

    try:
        load_agent_dispatch_command_spec(tmp_path, "reviewer")
    except ValueError as exc:
        assert "agent dispatch command spec missing" in str(exc)
    else:
        raise AssertionError("missing command spec should fail")


def test_run_agent_handoff_command_loop_reaches_human(tmp_path: Path) -> None:
    start = _valid_handoff()
    start["previous_subagent"] = "human"
    start["next_agent"] = {
        "name": "goal-guardian",
        "executor": "local",
        "reason": "Check goal preservation first.",
    }
    guardian_response = _valid_handoff()
    guardian_response["previous_subagent"] = "goal-guardian"
    guardian_response["next_agent"] = {
        "name": "project-or-harness-verifier",
        "executor": "local",
        "reason": "Verifier should inspect the preserved-goal handoff.",
    }
    verifier_response = _valid_handoff()
    verifier_response["previous_subagent"] = "project-or-harness-verifier"
    verifier_response["next_agent"] = {
        "name": "human",
        "executor": "human",
        "reason": "Human decides the next route.",
    }
    agents_root = tmp_path / "agents"
    spec_root = tmp_path / "specs"
    verifier_dir = agents_root / "project-or-harness-verifier"
    guardian_spec_dir = spec_root / "goal-guardian"
    verifier_spec_dir = spec_root / "project-or-harness-verifier"
    verifier_dir.mkdir(parents=True)
    guardian_spec_dir.mkdir(parents=True)
    verifier_spec_dir.mkdir(parents=True)
    (verifier_dir / "AGENTS.md").write_text(
        "---\nid: project-or-harness-verifier\n---\n",
        encoding="utf-8",
    )
    (guardian_spec_dir / "tau-dispatch-command.json").write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
                    "-c",
                    f"print({json.dumps(json.dumps(guardian_response))})",
                ],
                "timeout_s": 5,
            }
        ),
        encoding="utf-8",
    )
    (verifier_spec_dir / "tau-dispatch-command.json").write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
                    "-c",
                    f"print({json.dumps(json.dumps(verifier_response))})",
                ],
                "timeout_s": 5,
            }
        ),
        encoding="utf-8",
    )

    result = run_agent_handoff_command_loop(
        start,
        agent_registry_root=agents_root,
        command_spec_root=spec_root,
        active_goal_hash="sha256:active-goal",
        max_steps=4,
    )

    assert result.ok is True
    assert result.status == "WAITING"
    assert result.step_count == 2
    assert result.terminal_agent == "human"
    assert result.stop_reason == "next_agent_is_human"
    assert [dispatch["selected_agent"] for dispatch in result.dispatches] == [
        "goal-guardian",
        "project-or-harness-verifier",
    ]
    assert all(dispatch["mocked"] is False for dispatch in result.dispatches)
    assert all(dispatch["live"] is True for dispatch in result.dispatches)


def test_run_agent_handoff_command_loop_appends_goal_guardian_ticket_source(
    tmp_path: Path,
) -> None:
    start = _valid_handoff()
    start["previous_subagent"] = "human"
    start["next_agent"] = {
        "name": "goal-guardian",
        "executor": "local",
        "reason": "Check goal preservation first.",
    }
    guardian_response = _valid_handoff()
    guardian_response["previous_subagent"] = "goal-guardian"
    guardian_response["next_agent"] = {
        "name": "human",
        "executor": "human",
        "reason": "Human decides the next route.",
    }
    ticket_source = tmp_path / "ticket-source.json"
    ticket_source.write_text('{"schema":"tau.goal_guardian_ticket_source.v1","tickets":[]}\n')
    agents_root = tmp_path / "agents"
    spec_root = tmp_path / "specs"
    guardian_spec_dir = spec_root / "goal-guardian"
    agents_root.mkdir()
    guardian_spec_dir.mkdir(parents=True)
    (guardian_spec_dir / "tau-dispatch-command.json").write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
                    "-c",
                    f"print({json.dumps(json.dumps(guardian_response))})",
                    "handoff-goal-guardian-adapter",
                ],
                "timeout_s": 5,
            }
        ),
        encoding="utf-8",
    )

    result = run_agent_handoff_command_loop(
        start,
        agent_registry_root=agents_root,
        command_spec_root=spec_root,
        active_goal_hash="sha256:active-goal",
        goal_guardian_ticket_source=ticket_source,
        max_steps=2,
    )
    command = result.dispatches[0]["command_results"][0]["command"]

    assert result.ok is True
    assert result.terminal_agent == "human"
    assert command[-2:] == ["--ticket-source", str(ticket_source.resolve())]


def test_run_agent_handoff_command_loop_blocks_stale_start_goal_before_dispatch(
    tmp_path: Path,
) -> None:
    start = _valid_handoff()
    start["goal"]["goal_hash"] = "sha256:stale-goal"
    agents_root = tmp_path / "agents"
    spec_root = tmp_path / "specs"
    reviewer_dir = spec_root / "reviewer"
    agents_root.mkdir()
    reviewer_dir.mkdir(parents=True)
    (reviewer_dir / "tau-dispatch-command.json").write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
                    "-c",
                    "raise SystemExit('selected command must not run for stale start handoff')",
                ],
                "timeout_s": 5,
            }
        ),
        encoding="utf-8",
    )

    result = run_agent_handoff_command_loop(
        start,
        agent_registry_root=agents_root,
        command_spec_root=spec_root,
        active_goal_hash="sha256:active-goal",
        max_steps=1,
    )

    assert result.ok is False
    assert result.status == "BLOCKED"
    assert result.step_count == 1
    assert result.terminal_agent is None
    assert result.stop_reason == "invalid_handoff"
    assert result.dispatches == ()
    assert "step[1]: agent handoff may not change goal.goal_hash" in "\n".join(
        result.errors
    )


def test_write_agent_handoff_command_loop_receipt_blocks_stale_start_without_artifacts(
    tmp_path: Path,
) -> None:
    start = _valid_handoff()
    start["goal"]["goal_hash"] = "sha256:stale-goal"
    receipt_dir = tmp_path / "loop-receipts"
    agents_root = tmp_path / "agents"
    spec_root = tmp_path / "specs"
    agents_root.mkdir()
    spec_root.mkdir()

    result = write_agent_handoff_command_loop_receipt(
        start,
        receipt_dir,
        agent_registry_root=agents_root,
        command_spec_root=spec_root,
        active_goal_hash="sha256:active-goal",
        max_steps=1,
    )
    receipt = json.loads((receipt_dir / "command-loop-receipt.json").read_text())

    assert result.ok is False
    assert result.status == "BLOCKED"
    assert result.stop_reason == "invalid_handoff"
    assert result.dispatches == ()
    assert result.artifacts == ()
    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["dispatches"] == []
    assert receipt["artifacts"] == []
    assert "step[1]: agent handoff may not change goal.goal_hash" in "\n".join(
        receipt["errors"]
    )


def _valid_handoff() -> dict:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {
            "repo": "grahama1970/chatgpt-lab",
            "target": "issue#17",
        },
        "goal": {
            "goal_id": "goal-tau-live-github-transport",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "previous_subagent": "webgpt-ticket-author",
        "context": {
            "summary": "Ticket author created a live GitHub issue.",
            "artifacts": ["/tmp/tau/generated-ticket.json"],
        },
        "result": {
            "status": "COMPLETED",
            "summary": "Issue is ready for one reviewer response.",
            "evidence": ["/tmp/tau/issue.json"],
        },
        "rationale": "Reviewer should inspect the generated-ticket evidence.",
        "next_agent": {
            "name": "reviewer",
            "executor": "either",
            "reason": "Reviewer validates the live transport proof.",
        },
        "required_evidence": ["reviewer returns tau.agent_handoff.v1"],
        "stop_condition": "Reviewer handoff is consumed once.",
    }
