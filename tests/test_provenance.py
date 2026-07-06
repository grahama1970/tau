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
