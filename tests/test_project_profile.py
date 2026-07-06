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
    }
