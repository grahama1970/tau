"""Contract checks for adaptive parent reflection validation."""

from tau_coding.battle_adaptive_parent_reflection import REQUIRED_REQUEST_FIELDS


def test_parent_reflection_requires_spawn_contract_fields() -> None:
    assert "requested_action" in REQUIRED_REQUEST_FIELDS
    assert "requested_research_questions" in REQUIRED_REQUEST_FIELDS
    assert "requested_mutation_directions" in REQUIRED_REQUEST_FIELDS
    assert "requested_budget" in REQUIRED_REQUEST_FIELDS
