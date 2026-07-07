import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.project_profile import (
    PROJECT_PROFILE_SCHEMA,
    PROJECT_PROFILE_VALIDATION_RECEIPT_SCHEMA,
    validate_project_profile,
    write_project_profile_validation_receipt,
)


def test_project_profile_accepts_course_correction_and_herdr_policy() -> None:
    assert validate_project_profile(_profile()) == []


def test_project_profile_accepts_known_skill_providers() -> None:
    profile = _profile()

    errors = validate_project_profile(profile, capability_registry=_registry())

    assert errors == []


def test_project_profile_blocks_unknown_skill_provider() -> None:
    profile = _profile()
    profile["capability_providers"]["deep_research"] = "invented-researcher"

    errors = validate_project_profile(profile)

    assert "capability_providers.deep_research uses unknown skill provider" in errors


def test_project_profile_requires_registry_match() -> None:
    profile = _profile()
    registry = _registry()
    registry["capabilities"]["code_review"]["skill"] = "other-reviewer"

    errors = validate_project_profile(profile, capability_registry=registry)

    assert "capability_providers.code_review provider does not match registry" in errors


def test_project_profile_can_drive_course_correction_required_action() -> None:
    profile = _profile()
    profile["course_correction"]["action_capabilities"] = {
        "route_reviewer": "code_review",
        "run_brave_search_then_retry": "deep_research",
    }

    assert validate_project_profile(profile, capability_registry=_registry()) == []


def test_project_profile_blocks_action_capability_without_provider() -> None:
    profile = _profile()
    profile["course_correction"]["action_capabilities"] = {
        "route_reviewer": "missing_capability",
    }

    errors = validate_project_profile(profile, capability_registry=_registry())

    assert (
        "course_correction.action_capabilities.route_reviewer must reference "
        "capability_providers"
    ) in errors


def test_project_profile_blocks_under_specified_policy() -> None:
    profile = _profile()
    profile["memory"].pop("scope")
    profile["course_correction"]["allowed_actions"] = ["invent_unbounded_swarm"]

    errors = validate_project_profile(profile)

    assert "memory.scope must be a non-empty string" in errors
    assert any("invent_unbounded_swarm" not in error for error in errors)
    assert any("course_correction.allowed_actions[]" in error for error in errors)


def test_project_profile_validation_receipt_writes_policy_summary(tmp_path: Path) -> None:
    profile_path = tmp_path / "project-profile.json"
    profile_path.write_text(json.dumps(_profile()), encoding="utf-8")
    out = tmp_path / "receipt.json"

    payload = write_project_profile_validation_receipt(profile_path, out)

    assert payload["schema"] == PROJECT_PROFILE_VALIDATION_RECEIPT_SCHEMA
    assert payload["ok"] is True
    assert payload["status"] == "PASS"
    assert payload["policy_summary"] == {
        "memory_scope": "project:tau",
        "memory_intent_required": True,
        "evidence_case_required": True,
        "max_attempts_per_node": 2,
        "herdr_receipt_timeout_seconds": 300,
        "herdr_stale_pane_seconds": 180,
        "course_correction_allowed_actions": [
            "send_reminder",
            "retry_node",
            "route_reviewer",
            "route_goal_guardian",
            "route_human",
            "block_run",
        ],
        "capability_providers": _capability_providers(),
        "course_correction_action_capabilities": None,
    }
    assert json.loads(out.read_text(encoding="utf-8")) == payload


def test_cli_project_profile_validate_blocks_bad_profile(tmp_path: Path) -> None:
    profile = _profile()
    profile["herdr"]["receipt_timeout_seconds"] = 0
    profile_path = tmp_path / "project-profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    out = tmp_path / "receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "project-profile-validate",
            "--profile",
            str(profile_path),
            "--out",
            str(out),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload["schema"] == PROJECT_PROFILE_VALIDATION_RECEIPT_SCHEMA
    assert payload["status"] == "BLOCKED"
    assert "herdr.receipt_timeout_seconds must be a positive number" in payload["errors"]


def test_cli_project_profile_validate_accepts_registry(tmp_path: Path) -> None:
    profile_path = tmp_path / "project-profile.json"
    registry_path = tmp_path / "skill-capability-registry.json"
    out = tmp_path / "receipt.json"
    profile_path.write_text(json.dumps(_profile()), encoding="utf-8")
    registry_path.write_text(json.dumps(_registry()), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "project-profile-validate",
            "--profile",
            str(profile_path),
            "--out",
            str(out),
            "--registry",
            str(registry_path),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload["status"] == "PASS"
    assert payload["capability_registry_path"] == str(registry_path.resolve())


def _profile() -> dict:
    return {
        "schema": PROJECT_PROFILE_SCHEMA,
        "project_id": "tau-self-fix",
        "memory": {
            "scope": "project:tau",
            "intent_required": True,
            "evidence_case_required": True,
            "clarify_blocks_dispatch": True,
            "deflect_blocks_dispatch": True,
        },
        "retries": {
            "max_attempts_per_node": 2,
            "after_two_failures": "require_research_or_goal_guardian",
        },
        "herdr": {
            "receipt_timeout_seconds": 300,
            "stale_pane_seconds": 180,
            "auth_required_action": "route_human",
            "crashed_action": "retry_node",
            "interstitial_action": "route_human",
        },
        "course_correction": {
            "allowed_actions": [
                "send_reminder",
                "retry_node",
                "route_reviewer",
                "route_goal_guardian",
                "route_human",
                "block_run",
            ],
            "forbid_retry_same_context_after": 2,
        },
        "capability_providers": _capability_providers(),
    }


def _capability_providers() -> dict:
    return {
        "debug_runtime_state": "debugger",
        "bounded_code_fix": "code-runner",
        "code_review": "review-code",
        "deep_research": "dogpile",
        "evidence_case": "create-evidence-case",
        "model_worker": "scillm",
    }


def _registry() -> dict:
    return {
        "schema": "tau.skill_capability_registry.v1",
        "capabilities": {
            capability: {
                "skill": skill,
                "tau_receipt_schema": "tau.test_receipt.v1",
            }
            for capability, skill in _capability_providers().items()
        },
    }
