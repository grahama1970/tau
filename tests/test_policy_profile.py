import json

from tau_coding.policy_profile import (
    DATA_BOUNDARY_SCHEMA,
    POLICY_PROFILE_SCHEMA,
    ZERO_TRUST_PREFLIGHT_RECEIPT_SCHEMA,
    validate_data_boundary,
    validate_policy_profile,
    zero_trust_preflight_receipt,
)


def test_policy_profile_accepts_default_deny_profile() -> None:
    assert validate_policy_profile(_policy_profile()) == []


def test_policy_profile_blocks_invalid_schema() -> None:
    profile = _policy_profile()
    profile["schema"] = "wrong"

    errors = validate_policy_profile(profile)

    assert f"schema must be {POLICY_PROFILE_SCHEMA}" in errors


def test_policy_profile_blocks_unknown_default_decision() -> None:
    profile = _policy_profile()
    profile["default_decision"] = "maybe"

    errors = validate_policy_profile(profile)

    assert "default_decision must be one of ['allow', 'deny']" in errors


def test_policy_profile_accepts_memory_gate_controls() -> None:
    profile = _policy_profile()
    profile["memory"].update(
        {
            "intent_required": True,
            "evidence_case_required_for": ["COMPLIANCE", "SUBAGENT"],
            "min_intent_confidence": 0.75,
            "clarify_blocks_dispatch": True,
            "deflect_blocks_dispatch": True,
        }
    )

    assert validate_policy_profile(profile) == []


def test_policy_profile_blocks_invalid_memory_gate_controls() -> None:
    profile = _policy_profile()
    profile["memory"].update(
        {
            "intent_required": "yes",
            "evidence_case_required_for": ["COMPLIANCE", ""],
            "min_intent_confidence": 1.25,
            "clarify_blocks_dispatch": "yes",
        }
    )

    errors = validate_policy_profile(profile)

    assert "memory.intent_required must be a boolean when present" in errors
    assert "memory.evidence_case_required_for must be a list of strings when present" in errors
    assert "memory.min_intent_confidence must be a number between 0 and 1" in errors
    assert "memory.clarify_blocks_dispatch must be a boolean when present" in errors


def test_data_boundary_accepts_itar_local_only() -> None:
    assert validate_data_boundary(_itar_boundary()) == []


def test_data_boundary_blocks_missing_classification() -> None:
    boundary = _itar_boundary()
    del boundary["classification"]

    errors = validate_data_boundary(boundary)

    assert f"classification must be one of {sorted(_classifications())}" in errors


def test_data_boundary_blocks_classified_not_allowed() -> None:
    boundary = _itar_boundary()
    boundary["classification"] = "classified-not-allowed"

    receipt = zero_trust_preflight_receipt(
        policy_profile=_policy_profile(),
        data_boundary=boundary,
    )

    assert receipt["schema"] == ZERO_TRUST_PREFLIGHT_RECEIPT_SCHEMA
    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["alert_codes"] == ["classified_not_allowed"]


def test_zero_trust_preflight_blocks_provider_dag_when_policy_denies_cloud() -> None:
    dag = {
        "schema": "tau.dag_contract.v1",
        "nodes": [
            {
                "id": "provider-task",
                "agent": "coder",
                "executor": "local",
                "provider": {"adapter": "generic-provider-dag-node"},
            }
        ],
    }

    receipt = zero_trust_preflight_receipt(
        policy_profile=_policy_profile(),
        data_boundary=_public_boundary(),
        dag_contract=dag,
    )

    assert receipt["ok"] is False
    assert "external_provider_denied" in receipt["alert_codes"]


def _policy_profile() -> dict:
    return {
        "schema": POLICY_PROFILE_SCHEMA,
        "profile_id": "itar-zero-trust-local-only",
        "default_decision": "deny",
        "requires_data_boundary": True,
        "network": {"default": "deny", "allowed_domains": []},
        "providers": {"cloud_llm": "deny", "local_model": "allow_with_approval"},
        "research": {"external_search": "deny", "manual_sanitized_receipt": "allow_with_review"},
        "memory": {"read": "allow", "write": "approval_required"},
        "github": {"public_mutation": "deny", "dry_run_projection": "allow"},
        "filesystem": {"write_allowlist": [], "read_denylist": []},
    }


def _itar_boundary() -> dict:
    return {
        "schema": DATA_BOUNDARY_SCHEMA,
        "classification": "ITAR",
        "export_controlled": True,
        "itar": True,
        "technical_data": True,
        "foreign_person_access": "prohibited",
        "external_provider_allowed": False,
        "external_research_allowed": False,
        "public_repo_allowed": False,
        "notes": [],
    }


def _public_boundary() -> dict:
    boundary = json.loads(json.dumps(_itar_boundary()))
    boundary.update(
        {
            "classification": "public",
            "export_controlled": False,
            "itar": False,
            "technical_data": False,
            "foreign_person_access": "allowed",
        }
    )
    return boundary


def _classifications() -> set[str]:
    return {"public", "internal", "CUI", "ITAR", "EAR", "classified-not-allowed"}
