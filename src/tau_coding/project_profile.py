"""Project-specific Tau orchestration profile validation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_PROFILE_SCHEMA = "tau.project_profile.v1"
PROJECT_PROFILE_VALIDATION_RECEIPT_SCHEMA = "tau.project_profile_validation_receipt.v1"

COURSE_CORRECTION_ACTIONS = {
    "send_reminder",
    "retry_node",
    "route_reviewer",
    "route_goal_guardian",
    "route_human",
    "block_run",
    "run_brave_search_then_retry",
    "retry_node_or_route_goal_guardian",
    "send_reminder_or_route_human",
}


def validate_project_profile(profile: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if profile.get("schema") != PROJECT_PROFILE_SCHEMA:
        errors.append(f"schema must be {PROJECT_PROFILE_SCHEMA}")
    if not _non_empty_str(profile.get("project_id")):
        errors.append("project_id must be a non-empty string")
    memory = profile.get("memory")
    if not isinstance(memory, dict):
        errors.append("memory must be an object")
    else:
        if not _non_empty_str(memory.get("scope")):
            errors.append("memory.scope must be a non-empty string")
        for key in ("intent_required", "evidence_case_required"):
            if not isinstance(memory.get(key), bool):
                errors.append(f"memory.{key} must be a boolean")
    retries = profile.get("retries")
    if not isinstance(retries, dict):
        errors.append("retries must be an object")
    else:
        max_attempts = retries.get("max_attempts_per_node")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            errors.append("retries.max_attempts_per_node must be a positive integer")
    herdr = profile.get("herdr")
    if not isinstance(herdr, dict):
        errors.append("herdr must be an object")
    else:
        for key in ("receipt_timeout_seconds", "stale_pane_seconds"):
            value = herdr.get(key)
            if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
                errors.append(f"herdr.{key} must be a positive number")
        for key in ("auth_required_action", "crashed_action", "interstitial_action"):
            _validate_action(herdr.get(key), f"herdr.{key}", errors)
    correction = profile.get("course_correction")
    if not isinstance(correction, dict):
        errors.append("course_correction must be an object")
    else:
        allowed = correction.get("allowed_actions")
        if not isinstance(allowed, list) or not allowed:
            errors.append("course_correction.allowed_actions must be a non-empty list")
        else:
            for item in allowed:
                _validate_action(item, "course_correction.allowed_actions[]", errors)
        after = correction.get("forbid_retry_same_context_after")
        if isinstance(after, bool) or not isinstance(after, int) or after < 1:
            errors.append("course_correction.forbid_retry_same_context_after must be a positive integer")
    return errors


def write_project_profile_validation_receipt(
    profile_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    resolved_profile = profile_path.expanduser().resolve()
    errors: list[str] = []
    profile = _read_json_object(resolved_profile, errors=errors, label="project_profile")
    if profile:
        errors.extend(validate_project_profile(profile))
    status = "PASS" if not errors else "BLOCKED"
    payload = {
        "schema": PROJECT_PROFILE_VALIDATION_RECEIPT_SCHEMA,
        "ok": not errors,
        "status": status,
        "mocked": False,
        "live": False,
        "provider_live": False,
        "profile_path": str(resolved_profile),
        "project_id": profile.get("project_id") if isinstance(profile, dict) else None,
        "alert_count": len(errors),
        "errors": errors,
        "policy_summary": _policy_summary(profile if profile else {}),
        "proof_scope": {
            "proves": [
                "Tau parsed a project profile artifact.",
                "Tau checked required project-specific orchestration policy fields.",
                "Tau did not mutate project routes, Memory, Herdr, providers, or DAG contracts.",
            ],
            "does_not_prove": [
                "The profile has been applied to a DAG run.",
                "The configured course-correction action has been executed.",
                "Provider/model semantic quality.",
                "Future route correctness.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    resolved_output = output_path.expanduser().resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _policy_summary(profile: dict[str, Any]) -> dict[str, Any]:
    memory = profile.get("memory") if isinstance(profile.get("memory"), dict) else {}
    retries = profile.get("retries") if isinstance(profile.get("retries"), dict) else {}
    herdr = profile.get("herdr") if isinstance(profile.get("herdr"), dict) else {}
    correction = (
        profile.get("course_correction")
        if isinstance(profile.get("course_correction"), dict)
        else {}
    )
    return {
        "memory_scope": memory.get("scope"),
        "memory_intent_required": memory.get("intent_required"),
        "evidence_case_required": memory.get("evidence_case_required"),
        "max_attempts_per_node": retries.get("max_attempts_per_node"),
        "herdr_receipt_timeout_seconds": herdr.get("receipt_timeout_seconds"),
        "herdr_stale_pane_seconds": herdr.get("stale_pane_seconds"),
        "course_correction_allowed_actions": correction.get("allowed_actions"),
    }


def _validate_action(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value:
        errors.append(f"{label} must be a non-empty string")
        return
    if value not in COURSE_CORRECTION_ACTIONS:
        errors.append(f"{label} must be one of: {', '.join(sorted(COURSE_CORRECTION_ACTIONS))}")


def _read_json_object(path: Path, *, errors: list[str], label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label} is unreadable: {path}: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"{label} must be a JSON object: {path}")
        return {}
    return payload


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
