import json
from pathlib import Path

from tau_coding.generated_ticket import (
    TAU_AGENT_COMMON_SCHEMA,
    TAU_AGENT_HANDOFF_SCHEMA,
    TAU_GENERATED_TICKET_SCHEMA,
    derived_labels,
    validate_generated_ticket,
    validate_generated_ticket_file,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = ROOT / "experiments" / "goal-locked-subagents"
FIXTURES = CONTRACT_ROOT / "fixtures"
SCHEMAS = CONTRACT_ROOT / "schemas"


def test_minimal_schema_artifacts_have_tau_ids() -> None:
    common = json.loads((SCHEMAS / "tau.agent_common.v1.schema.json").read_text())
    handoff = json.loads((SCHEMAS / "tau.agent_handoff.v1.schema.json").read_text())
    generated = json.loads((SCHEMAS / "tau.generated_ticket.v1.schema.json").read_text())

    assert common["$id"] == TAU_AGENT_COMMON_SCHEMA
    assert handoff["$id"] == TAU_AGENT_HANDOFF_SCHEMA
    assert generated["$id"] == TAU_GENERATED_TICKET_SCHEMA
    assert generated["properties"]["schema"]["const"] == TAU_GENERATED_TICKET_SCHEMA


def test_valid_generated_ticket_derives_github_create_projection() -> None:
    result = validate_generated_ticket_file(
        FIXTURES / "valid-generated-ticket.json",
        active_goal_hash="sha256:active-goal",
    )

    assert result.ok is True
    assert result.errors == ()
    assert result.next_agent == "reviewer"
    assert result.github_create == {
        "kind": "issue",
        "title": "Review Tau generated-ticket contract evidence",
        "body": "Review the generated-ticket contract evidence.",
        "labels": ["agent-work", "next:reviewer", "executor:either"],
    }


def test_generated_ticket_does_not_require_agent_authored_labels() -> None:
    payload = json.loads((FIXTURES / "valid-generated-ticket.json").read_text())

    assert "labels" not in payload["ticket"]
    assert "labels" not in payload["next_agent"]
    assert "create" not in payload["github"]

    result = validate_generated_ticket(payload, active_goal_hash="sha256:active-goal")

    assert result.ok is True
    assert result.github_create is not None
    assert result.github_create["labels"] == ["agent-work", "next:reviewer", "executor:either"]


def test_generated_ticket_defaults_executor_to_either() -> None:
    assert derived_labels("reviewer", None) == (
        "agent-work",
        "next:reviewer",
        "executor:either",
    )


def test_generated_ticket_refuses_unknown_next_agent() -> None:
    result = validate_generated_ticket_file(
        FIXTURES / "invalid-generated-ticket-unknown-next-agent.json",
        active_goal_hash="sha256:active-goal",
    )

    assert result.ok is False
    assert result.github_create is None
    assert "next_agent.name must be one of" in "\n".join(result.errors)


def test_generated_ticket_refuses_goal_hash_change() -> None:
    payload = json.loads((FIXTURES / "valid-generated-ticket.json").read_text())
    payload["goal"]["goal_hash"] = "sha256:changed-goal"

    result = validate_generated_ticket(payload, active_goal_hash="sha256:active-goal")

    assert result.ok is False
    assert "generated ticket may not change goal.goal_hash" in result.errors


def test_generated_ticket_refuses_unauthorized_ticket_creator() -> None:
    payload = json.loads((FIXTURES / "valid-generated-ticket.json").read_text())
    payload["previous_subagent"] = "coder"

    result = validate_generated_ticket(payload, active_goal_hash="sha256:active-goal")

    assert result.ok is False
    assert "previous_subagent may not create tickets: coder" in result.errors
