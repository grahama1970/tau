import json
from pathlib import Path

from tau_coding.generated_ticket import project_agent_handoff
from tau_coding.human_goal_change import (
    TAU_HUMAN_GOAL_CHANGE_BRIDGE_RECEIPT_SCHEMA,
    TAU_HUMAN_GOAL_CHANGE_SCHEMA,
    bridge_human_goal_change_to_handoff,
    validate_human_goal_change,
    validate_human_goal_change_file,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = ROOT / "experiments" / "goal-locked-subagents"
FIXTURES = CONTRACT_ROOT / "fixtures"
SCHEMAS = CONTRACT_ROOT / "schemas"


def test_human_goal_change_schema_artifact_has_tau_id() -> None:
    schema = json.loads((SCHEMAS / "tau.human_goal_change.v1.schema.json").read_text())

    assert schema["$id"] == TAU_HUMAN_GOAL_CHANGE_SCHEMA
    assert schema["properties"]["schema"]["const"] == TAU_HUMAN_GOAL_CHANGE_SCHEMA


def test_human_goal_change_bridge_receipt_schema_artifact_has_tau_id() -> None:
    schema = json.loads(
        (SCHEMAS / "tau.human_goal_change_bridge_receipt.v1.schema.json").read_text()
    )

    assert schema["$id"] == TAU_HUMAN_GOAL_CHANGE_BRIDGE_RECEIPT_SCHEMA
    assert schema["properties"]["schema"]["const"] == TAU_HUMAN_GOAL_CHANGE_BRIDGE_RECEIPT_SCHEMA


def test_goal_guardian_reconciliation_receipt_schema_artifact_has_tau_id() -> None:
    schema = json.loads(
        (SCHEMAS / "tau.goal_guardian_reconciliation_receipt.v1.schema.json").read_text()
    )

    assert schema["$id"] == "tau.goal_guardian_reconciliation_receipt.v1"
    assert (
        schema["properties"]["schema"]["const"]
        == "tau.goal_guardian_reconciliation_receipt.v1"
    )


def test_goal_guardian_ticket_source_schema_artifact_has_tau_id() -> None:
    schema = json.loads(
        (SCHEMAS / "tau.goal_guardian_ticket_source.v1.schema.json").read_text()
    )

    assert schema["$id"] == "tau.goal_guardian_ticket_source.v1"
    assert schema["properties"]["schema"]["const"] == "tau.goal_guardian_ticket_source.v1"


def test_trusted_human_goal_change_routes_to_goal_guardian() -> None:
    result = validate_human_goal_change_file(
        FIXTURES / "valid-human-goal-change.json",
        active_goal_hash="sha256:active-goal",
        trusted_human=True,
    )

    assert result.ok is True
    assert result.errors == ()
    assert result.next_agent == "goal-guardian"


def test_trusted_human_goal_change_bridge_builds_goal_guardian_start_handoff() -> None:
    source = FIXTURES / "valid-human-goal-change.json"
    payload = json.loads(source.read_text())

    handoff, errors = bridge_human_goal_change_to_handoff(
        payload,
        active_goal_hash="sha256:active-goal",
        trusted_human=True,
        source=str(source),
    )

    assert errors == ()
    assert handoff is not None
    assert handoff["schema"] == "tau.agent_handoff.v1"
    assert handoff["github"] == payload["github"]
    assert handoff["goal"] == payload["goal"]
    assert handoff["previous_subagent"] == "human"
    assert handoff["result"]["status"] == "GOAL_CHANGE_REQUESTED"
    assert str(source) in handoff["result"]["evidence"][0]
    assert handoff["next_agent"]["name"] == "goal-guardian"
    assert handoff["next_agent"]["executor"] == "local"
    assert "goal-guardian posts a reconciliation receipt" in handoff["required_evidence"][-1]
    assert "non-human agent continues" in handoff["stop_condition"]
    assert handoff["context"]["human_goal_change"]["new_goal"] == payload["new_goal"]

    projection = project_agent_handoff(handoff, active_goal_hash="sha256:active-goal")
    assert projection.ok is True
    assert projection.next_agent == "goal-guardian"


def test_human_goal_change_refuses_untrusted_author() -> None:
    result = validate_human_goal_change_file(
        FIXTURES / "valid-human-goal-change.json",
        active_goal_hash="sha256:active-goal",
        trusted_human=False,
    )

    assert result.ok is False
    assert "human goal change requires trusted human author" in result.errors


def test_human_goal_change_bridge_refuses_untrusted_author() -> None:
    payload = json.loads((FIXTURES / "valid-human-goal-change.json").read_text())

    handoff, errors = bridge_human_goal_change_to_handoff(
        payload,
        active_goal_hash="sha256:active-goal",
        trusted_human=False,
        source="valid-human-goal-change.json",
    )

    assert handoff is None
    assert "human goal change requires trusted human author" in errors


def test_human_goal_change_refuses_non_human_previous_agent() -> None:
    result = validate_human_goal_change_file(
        FIXTURES / "invalid-human-goal-change-non-human.json",
        active_goal_hash="sha256:active-goal",
        trusted_human=True,
    )

    assert result.ok is False
    assert "human goal change requires previous_subagent=human" in result.errors
    assert "human goal change must route next_agent.name to goal-guardian" in result.errors


def test_human_goal_change_bridge_refuses_non_human_previous_agent() -> None:
    payload = json.loads((FIXTURES / "invalid-human-goal-change-non-human.json").read_text())

    handoff, errors = bridge_human_goal_change_to_handoff(
        payload,
        active_goal_hash="sha256:active-goal",
        trusted_human=True,
        source="invalid-human-goal-change-non-human.json",
    )

    assert handoff is None
    assert "human goal change requires previous_subagent=human" in errors


def test_human_goal_change_refuses_stale_goal_hash() -> None:
    payload = json.loads((FIXTURES / "valid-human-goal-change.json").read_text())
    payload["goal"]["goal_hash"] = "sha256:stale-goal"

    result = validate_human_goal_change(
        payload,
        active_goal_hash="sha256:active-goal",
        trusted_human=True,
    )

    assert result.ok is False
    assert "human goal change must reference the current goal hash" in result.errors


def test_human_goal_change_bridge_refuses_stale_goal_hash() -> None:
    payload = json.loads((FIXTURES / "valid-human-goal-change.json").read_text())
    payload["goal"]["goal_hash"] = "sha256:stale-goal"

    handoff, errors = bridge_human_goal_change_to_handoff(
        payload,
        active_goal_hash="sha256:active-goal",
        trusted_human=True,
        source="valid-human-goal-change.json",
    )

    assert handoff is None
    assert "human goal change must reference the current goal hash" in errors
