import json
from pathlib import Path

from tau_coding.subagent_receipt import (
    TAU_SUBAGENT_RECEIPT_SCHEMA,
    validate_subagent_receipt,
    validate_subagent_receipt_file,
)

FIXTURES = (
    Path(__file__).resolve().parents[1]
    / "experiments"
    / "goal-locked-subagents"
    / "fixtures"
)
SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "experiments"
    / "goal-locked-subagents"
    / "schemas"
    / "tau.subagent_receipt.v1.schema.json"
)


def test_subagent_receipt_fixture_has_contract_schema() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    receipt = json.loads((FIXTURES / "valid-subagent-receipt.json").read_text(encoding="utf-8"))

    assert schema["$id"] == TAU_SUBAGENT_RECEIPT_SCHEMA
    assert schema["properties"]["schema"]["const"] == TAU_SUBAGENT_RECEIPT_SCHEMA
    assert receipt["schema"] == TAU_SUBAGENT_RECEIPT_SCHEMA


def test_valid_subagent_receipt_routes_to_next_subagent() -> None:
    result = validate_subagent_receipt_file(
        FIXTURES / "valid-subagent-receipt.json",
        active_goal_hash="sha256:active-goal",
    )

    assert result.ok is True
    assert result.errors == ()
    assert result.next_subagent == "reviewer"


def test_subagent_receipt_requires_common_envelope_fields() -> None:
    receipt = json.loads((FIXTURES / "valid-subagent-receipt.json").read_text(encoding="utf-8"))
    del receipt["context"]
    del receipt["result"]
    del receipt["rationale"]

    result = validate_subagent_receipt(receipt, active_goal_hash="sha256:active-goal")

    assert result.ok is False
    assert "receipt.context is required" in result.errors
    assert "receipt.result is required" in result.errors
    assert "receipt.rationale is required" in result.errors


def test_subagent_receipt_requires_next_subagent() -> None:
    result = validate_subagent_receipt_file(
        FIXTURES / "invalid-missing-next-subagent.json",
        active_goal_hash="sha256:active-goal",
    )

    assert result.ok is False
    assert result.next_subagent is None
    assert "next.subagent is required" in result.errors
    assert "next.subagent must be a non-empty string" in result.errors


def test_non_human_subagent_cannot_amend_goal_hash() -> None:
    result = validate_subagent_receipt_file(
        FIXTURES / "invalid-non-human-goal-amendment.json",
        active_goal_hash="sha256:active-goal",
    )

    assert result.ok is False
    assert "non-human subagent may not change goal.goal_hash" in result.errors
    assert "non-human subagent may not set immutable_goal_preserved=false" in result.errors


def test_human_actor_can_return_goal_amendment_receipt() -> None:
    receipt = json.loads(
        (FIXTURES / "invalid-non-human-goal-amendment.json").read_text(encoding="utf-8")
    )
    receipt["context"]["actor_type"] = "human"

    result = validate_subagent_receipt(receipt, active_goal_hash="sha256:active-goal")

    assert result.ok is True
    assert result.next_subagent == "human"
