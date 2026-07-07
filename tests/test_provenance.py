import json
from pathlib import Path

from tau_coding.provenance import (
    ACTOR_MANIFEST_SCHEMA,
    ENVIRONMENT_MANIFEST_SCHEMA,
    build_actor_manifest,
    build_environment_manifest,
    parse_actor_spec,
    validate_actor_manifest,
    validate_environment_manifest,
)


def test_actor_manifest_accepts_declared_human_and_agent(tmp_path: Path) -> None:
    out = tmp_path / "actor-manifest.json"

    manifest = build_actor_manifest(
        run_id="run-1",
        actors=[
            {
                "actor_id": "human:graham",
                "actor_type": "human",
                "roles": ["approver"],
                "trusted": True,
                "verified": False,
            },
            {
                "actor_id": "agent:coder",
                "actor_type": "agent",
                "roles": ["worker"],
                "trusted": False,
                "verified": False,
            },
        ],
        output_path=out,
    )

    assert manifest["schema"] == ACTOR_MANIFEST_SCHEMA
    assert manifest["ok"] is True
    assert manifest["status"] == "PASS"
    assert out.exists()
    assert json.loads(out.read_text(encoding="utf-8"))["actors"][1]["trusted"] is False
    assert "Human legal identity." in manifest["proof_scope"]["does_not_prove"]


def test_actor_manifest_blocks_invalid_actor_type() -> None:
    manifest = build_actor_manifest(
        run_id="run-1",
        actors=[
            {
                "actor_id": "agent:bad",
                "actor_type": "swarm",
                "roles": ["worker"],
                "trusted": False,
                "verified": False,
            }
        ],
    )

    assert manifest["ok"] is False
    assert manifest["status"] == "BLOCKED"
    assert "actors[0].actor_type" in manifest["errors"][0]


def test_actor_manifest_accepts_closed_eligibility_shape() -> None:
    manifest = build_actor_manifest(
        run_id="run-1",
        actors=[
            {
                "actor_id": "human:approver",
                "actor_type": "human",
                "roles": ["approver"],
                "trusted": True,
                "verified": True,
                "eligibility": {
                    "us_person": "verified",
                    "foreign_person": False,
                    "export_control_training_current": True,
                    "approved_for_boundary": ["ITAR"],
                },
            }
        ],
    )

    assert manifest["ok"] is True
    assert manifest["status"] == "PASS"
    assert manifest["actors"][0]["eligibility"]["approved_for_boundary"] == ["ITAR"]


def test_actor_manifest_blocks_invalid_eligibility_shape() -> None:
    manifest = build_actor_manifest(
        run_id="run-1",
        actors=[
            {
                "actor_id": "human:approver",
                "actor_type": "human",
                "roles": ["approver"],
                "trusted": True,
                "verified": True,
                "eligibility": {
                    "us_person": "maybe",
                    "foreign_person": "no",
                    "export_control_training_current": "yes",
                    "approved_for_boundary": ["ITAR", ""],
                },
            }
        ],
    )

    assert manifest["ok"] is False
    assert manifest["status"] == "BLOCKED"
    assert any("eligibility.us_person" in error for error in manifest["errors"])
    assert any("eligibility.foreign_person" in error for error in manifest["errors"])
    assert any(
        "eligibility.export_control_training_current" in error
        for error in manifest["errors"]
    )
    assert any("eligibility.approved_for_boundary" in error for error in manifest["errors"])


def test_parse_actor_spec_uses_agent_untrusted_default() -> None:
    actor = parse_actor_spec("coder:agent:worker,reviewer")

    assert actor["actor_id"] == "coder"
    assert actor["actor_type"] == "agent"
    assert actor["trusted"] is False
    assert validate_actor_manifest(
        {
            "schema": ACTOR_MANIFEST_SCHEMA,
            "run_id": "run-1",
            "actors": [actor],
        }
    ) == []


def test_environment_manifest_records_declared_controls(tmp_path: Path) -> None:
    out = tmp_path / "environment-manifest.json"

    manifest = build_environment_manifest(
        run_id="run-1",
        network_policy="deny",
        provider_access="denied",
        mounted_paths=["/tmp/tau-run"],
        secrets_visible=[],
        tool_versions={"tau": "0.1.0"},
        output_path=out,
    )

    assert manifest["schema"] == ENVIRONMENT_MANIFEST_SCHEMA
    assert manifest["ok"] is True
    assert manifest["network_policy"] == "deny"
    assert manifest["provider_access"] == "denied"
    assert manifest["tool_versions"]["tau"] == "0.1.0"
    assert out.exists()
    assert "Runtime sandbox enforcement." in manifest["proof_scope"]["does_not_prove"]


def test_environment_manifest_validates_policy_and_boundary_references(
    tmp_path: Path,
) -> None:
    policy_path = tmp_path / "policy-profile.json"
    boundary_path = tmp_path / "data-boundary.json"
    _write_json(policy_path, _valid_policy_profile())
    _write_json(boundary_path, _valid_data_boundary())

    manifest = build_environment_manifest(
        run_id="run-1",
        network_policy="deny",
        provider_access="denied",
        mounted_paths=[],
        secrets_visible=[],
        tool_versions={},
        policy_profile=str(policy_path),
        data_boundary=str(boundary_path),
    )

    assert manifest["ok"] is True
    assert manifest["status"] == "PASS"
    assert manifest["policy_profile_artifact"]["schema"] == "tau.policy_profile.v1"
    assert manifest["data_boundary_artifact"]["schema"] == "tau.data_boundary.v1"
    assert str(manifest["policy_profile_artifact"]["sha256"]).startswith("sha256:")


def test_environment_manifest_blocks_invalid_data_boundary_reference(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy-profile.json"
    boundary_path = tmp_path / "data-boundary.json"
    _write_json(policy_path, _valid_policy_profile())
    _write_json(boundary_path, {"schema": "tau.data_boundary.v1", "classification": "maybe"})

    manifest = build_environment_manifest(
        run_id="run-1",
        network_policy="deny",
        provider_access="denied",
        mounted_paths=[],
        secrets_visible=[],
        tool_versions={},
        policy_profile=str(policy_path),
        data_boundary=str(boundary_path),
    )

    assert manifest["ok"] is False
    assert manifest["status"] == "BLOCKED"
    assert any(error.startswith("data_boundary:") for error in manifest["errors"])


def test_environment_manifest_blocks_unknown_network_policy() -> None:
    errors = validate_environment_manifest(
        {
            "schema": ENVIRONMENT_MANIFEST_SCHEMA,
            "run_id": "run-1",
            "network_policy": "sometimes",
            "provider_access": "denied",
            "mounted_paths": [],
            "secrets_visible": [],
            "tool_versions": {},
        }
    )

    assert "network_policy must be one of" in errors[0]


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _valid_policy_profile() -> dict:
    return {
        "schema": "tau.policy_profile.v1",
        "profile_id": "test",
        "default_decision": "deny",
        "requires_data_boundary": True,
        "network": {"default": "deny", "allowed_domains": []},
        "providers": {"cloud_llm": "deny", "local_model": "allow_with_approval"},
        "research": {
            "external_search": "deny",
            "manual_sanitized_receipt": "allow_with_review",
        },
        "memory": {"read": "allow", "write": "approval_required"},
        "github": {"public_mutation": "deny", "dry_run_projection": "allow"},
        "filesystem": {"write_allowlist": [], "read_denylist": []},
    }


def _valid_data_boundary() -> dict:
    return {
        "schema": "tau.data_boundary.v1",
        "classification": "public",
        "export_controlled": False,
        "itar": False,
        "technical_data": False,
        "foreign_person_access": "allowed",
        "external_provider_allowed": False,
        "external_research_allowed": False,
        "public_repo_allowed": True,
        "notes": [],
    }
