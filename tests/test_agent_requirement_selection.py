"""Deterministic fixture tests for tau#308 agent-requirement transport selection."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from tau_coding.dag_runtime.agent_requirement import (
    AgentRequirementError,
    select_transport_profile,
    validate_agent_requirement,
    validate_selection_receipt,
)


def _requirement(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": "tau.agent_requirement.v1",
        "role": "frontend",
        "harness": "tau_native_agent_loop",
        "profile_preferences": ["frontend-primary"],
        "required_transport_capabilities": ["streaming", "tool_calling", "cancellation"],
        "domain_capabilities": ["typescript"],
        "workspace": {"mode": "isolated_worktree", "allowed_paths": ["apps/web/**"]},
        "required_evidence": ["code_patch_receipt", "test_run_receipt"],
        "fallback_policy": {"allowed": True, "prohibit_capability_downgrade": True},
    }
    base.update(overrides)
    return base


def _profile(profile_id: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": "scillm.transport_profile.v1",
        "id": profile_id,
        "label": profile_id,
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "auth_source": "oauth",
        "mode": "model_turn",
        "capabilities": ["streaming", "tool_calling", "cancellation", "structured_events"],
        "tags": ["frontend", "typescript"],
        "fallbacks": [],
    }
    base.update(overrides)
    return base


def _discovery(*profiles: dict[str, Any], readiness: dict[str, str] | None = None) -> dict[str, Any]:
    ready = readiness or {p["id"]: "transport_live_ready" for p in profiles}
    return {"profiles": list(profiles), "readiness": ready}


def test_valid_requirement_normalizes() -> None:
    normalized = validate_agent_requirement(_requirement())
    assert normalized["role"] == "frontend"
    assert normalized["required_transport_capabilities"] == [
        "cancellation",
        "streaming",
        "tool_calling",
    ]


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"schema": "tau.dag_contract.v1"}, "agent_requirement_schema_invalid"),
        ({"role": ""}, "agent_requirement_field_missing"),
        ({"harness": "scillm_agent_loop"}, "agent_requirement_harness_invalid"),
        ({"profile_preferences": []}, "agent_requirement_no_profile_preferences"),
        (
            {"required_transport_capabilities": ["agent_loop"]},
            "agent_requirement_tau_owned_capability",
        ),
        (
            {"required_transport_capabilities": ["quantum"]},
            "agent_requirement_unknown_capability",
        ),
        ({"workspace": {"mode": "anywhere"}}, "agent_requirement_workspace_mode_invalid"),
        (
            {"fallback_policy": {"allowed": True, "prohibit_capability_downgrade": False}},
            "agent_requirement_downgrade_not_permitted",
        ),
    ],
)
def test_invalid_requirements_fail_closed(overrides: dict[str, Any], code: str) -> None:
    with pytest.raises(AgentRequirementError) as excinfo:
        validate_agent_requirement(_requirement(**overrides))
    assert excinfo.value.code == code


def test_ordered_preference_selects_primary() -> None:
    discovery = _discovery(
        _profile("frontend-primary"),
        _profile("frontend-secondary"),
    )
    selection = select_transport_profile(
        requirement=_requirement(profile_preferences=["frontend-primary", "frontend-secondary"]),
        discovery=discovery,
    )
    assert selection.selected["profile_id"] == "frontend-primary"
    assert selection.fallback_order[0] == "frontend-primary"


def test_unavailable_primary_falls_back_when_permitted() -> None:
    discovery = _discovery(
        _profile("frontend-primary", fallbacks=["frontend-secondary"]),
        _profile("frontend-secondary"),
        readiness={
            "frontend-primary": "credential_ready",
            "frontend-secondary": "transport_live_ready",
        },
    )
    selection = select_transport_profile(requirement=_requirement(), discovery=discovery)
    assert selection.selected["profile_id"] == "frontend-secondary"
    rejected = {item["profile_id"]: item["rejection_codes"] for item in selection.rejected}
    assert any(code.startswith("PROFILE_NOT_LIVE_READY") for code in rejected["frontend-primary"])


def test_fallback_disallowed_does_not_expand_chain() -> None:
    discovery = _discovery(
        _profile("frontend-primary", fallbacks=["frontend-secondary"]),
        _profile("frontend-secondary"),
        readiness={
            "frontend-primary": "unavailable",
            "frontend-secondary": "transport_live_ready",
        },
    )
    requirement = _requirement(
        fallback_policy={"allowed": False, "prohibit_capability_downgrade": True}
    )
    with pytest.raises(AgentRequirementError) as excinfo:
        select_transport_profile(requirement=requirement, discovery=discovery)
    assert excinfo.value.code == "no_eligible_transport_profile"


def test_capability_downgrade_is_prohibited() -> None:
    discovery = _discovery(
        _profile("frontend-primary", fallbacks=["frontend-weak"]),
        _profile("frontend-weak", capabilities=["streaming"]),
        readiness={
            "frontend-primary": "degraded",
            "frontend-weak": "transport_live_ready",
        },
    )
    with pytest.raises(AgentRequirementError) as excinfo:
        select_transport_profile(requirement=_requirement(), discovery=discovery)
    assert excinfo.value.code == "no_eligible_transport_profile"
    assert "TRANSPORT_CAPABILITY_MISSING" in excinfo.value.detail


def test_no_eligible_profile_fails_closed() -> None:
    discovery = _discovery(_profile("other-role", tags=["backend"]))
    with pytest.raises(AgentRequirementError) as excinfo:
        select_transport_profile(
            requirement=_requirement(profile_preferences=["missing-profile"]),
            discovery=discovery,
        )
    assert excinfo.value.code == "no_eligible_transport_profile"
    assert "PROFILE_UNKNOWN" in excinfo.value.detail


def test_policy_data_boundary_rejection() -> None:
    discovery = _discovery(_profile("frontend-primary"))
    with pytest.raises(AgentRequirementError) as excinfo:
        select_transport_profile(
            requirement=_requirement(),
            discovery=discovery,
            policy_denied_profiles=["frontend-primary"],
        )
    assert "POLICY_DATA_BOUNDARY_REJECTED" in excinfo.value.detail


def test_tau_native_rejects_opaque_profile() -> None:
    discovery = _discovery(
        _profile("frontend-primary", mode="opaque_agent_compat", capabilities=["streaming"]),
    )
    requirement = _requirement(required_transport_capabilities=["streaming"])
    with pytest.raises(AgentRequirementError) as excinfo:
        select_transport_profile(requirement=requirement, discovery=discovery)
    assert "PROFILE_MODE_INCOMPATIBLE_WITH_TAU_NATIVE" in excinfo.value.detail


def test_opaque_harness_rejects_native_profile_and_accepts_opaque() -> None:
    native = _profile("native-profile")
    opaque = _profile(
        "opencode-compat",
        mode="opaque_agent_compat",
        capabilities=["streaming", "cancellation"],
    )
    requirement = _requirement(
        harness="opaque_agent_compat",
        profile_preferences=["native-profile", "opencode-compat"],
        required_transport_capabilities=["streaming", "cancellation"],
    )
    selection = select_transport_profile(
        requirement=requirement, discovery=_discovery(native, opaque)
    )
    assert selection.selected["profile_id"] == "opencode-compat"
    rejected = {item["profile_id"]: item["rejection_codes"] for item in selection.rejected}
    assert "PROFILE_MODE_NOT_OPAQUE_COMPAT" in rejected["native-profile"]


def test_replay_identical_inputs_identical_receipt() -> None:
    discovery = _discovery(
        _profile("frontend-primary", fallbacks=["frontend-secondary"]),
        _profile("frontend-secondary"),
    )
    binding = {
        "run_id": "run-1",
        "node_id": "node-frontend",
        "attempt_id": "attempt-1",
        "attempt": 1,
        "plan_sha256": "a" * 64,
        "goal_hash": "b" * 64,
        "policy_hash": "c" * 64,
        "data_boundary_hash": "d" * 64,
    }
    first = select_transport_profile(
        requirement=_requirement(), discovery=copy.deepcopy(discovery)
    ).receipt_payload(**binding)
    second = select_transport_profile(
        requirement=_requirement(), discovery=copy.deepcopy(discovery)
    ).receipt_payload(**binding)
    assert first == second
    validate_selection_receipt(first)


def _valid_receipt() -> dict[str, Any]:
    discovery = _discovery(_profile("frontend-primary"))
    return select_transport_profile(
        requirement=_requirement(), discovery=discovery
    ).receipt_payload(
        run_id="run-1",
        node_id="node-frontend",
        attempt_id="attempt-1",
        attempt=1,
        plan_sha256="a" * 64,
        goal_hash="b" * 64,
        policy_hash="c" * 64,
        data_boundary_hash="d" * 64,
    )


def test_receipt_validation_accepts_valid() -> None:
    validate_selection_receipt(_valid_receipt())


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda r: r.update(eligible_candidates=[]), "selection_receipt_missing_eligible_candidates"),
        (lambda r: r.pop("rejected_candidates"), "selection_receipt_missing_rejected_candidates"),
        (
            lambda r: r.update(rejected_candidates=[{"profile_id": "x", "rejection_codes": []}]),
            "selection_receipt_rejection_reason_missing",
        ),
        (lambda r: r.pop("goal_hash"), "agent_requirement_field_missing"),
        (
            lambda r: r["selected_profile"].update(readiness="credential_ready"),
            "selection_receipt_missing_live_readiness",
        ),
        (
            lambda r: r["selected_profile"].update(mode="opaque_agent_compat"),
            "selection_receipt_mode_incompatible_with_node",
        ),
        (
            lambda r: r["agent_requirement"].update(role="backend"),
            "selection_receipt_requirement_hash_mismatch",
        ),
    ],
)
def test_receipt_validation_fails_closed(mutate: Any, code: str) -> None:
    receipt = _valid_receipt()
    mutate(receipt)
    with pytest.raises(AgentRequirementError) as excinfo:
        validate_selection_receipt(receipt)
    assert excinfo.value.code == code
