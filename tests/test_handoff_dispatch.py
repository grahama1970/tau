import json
from pathlib import Path

from tau_coding.handoff_dispatch import (
    TAU_AGENT_HANDOFF_DISPATCH_RECEIPT_SCHEMA,
    dispatch_agent_handoff_once,
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
