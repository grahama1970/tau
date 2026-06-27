import json
from pathlib import Path

from tau_coding.human_goal_change import (
    TAU_HUMAN_GOAL_CHANGE_SCHEMA,
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


def test_trusted_human_goal_change_routes_to_goal_guardian() -> None:
    result = validate_human_goal_change_file(
        FIXTURES / "valid-human-goal-change.json",
        active_goal_hash="sha256:active-goal",
        trusted_human=True,
    )

    assert result.ok is True
    assert result.errors == ()
    assert result.next_agent == "goal-guardian"


def test_human_goal_change_refuses_untrusted_author() -> None:
    result = validate_human_goal_change_file(
        FIXTURES / "valid-human-goal-change.json",
        active_goal_hash="sha256:active-goal",
        trusted_human=False,
    )

    assert result.ok is False
    assert "human goal change requires trusted human author" in result.errors


def test_human_goal_change_refuses_non_human_previous_agent() -> None:
    result = validate_human_goal_change_file(
        FIXTURES / "invalid-human-goal-change-non-human.json",
        active_goal_hash="sha256:active-goal",
        trusted_human=True,
    )

    assert result.ok is False
    assert "human goal change requires previous_subagent=human" in result.errors
    assert "human goal change must route next_agent.name to goal-guardian" in result.errors


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
