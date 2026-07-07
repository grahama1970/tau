"""Project-specific Tau orchestration profile validation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_PROFILE_SCHEMA = "tau.project_profile.v1"
PROJECT_PROFILE_VALIDATION_RECEIPT_SCHEMA = "tau.project_profile_validation_receipt.v1"
SKILL_CAPABILITY_REGISTRY_SCHEMA = "tau.skill_capability_registry.v1"

DEFAULT_CAPABILITY_PROVIDERS = {
    "debug_runtime_state": "debugger",
    "bounded_code_fix": "code-runner",
    "code_review": "review-code",
    "deep_research": "dogpile",
    "evidence_case": "create-evidence-case",
    "model_worker": "scillm",
}

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


def validate_project_profile(
    profile: dict[str, Any],
    *,
    capability_registry: dict[str, Any] | None = None,
) -> list[str]:
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
            errors.append(
                "course_correction.forbid_retry_same_context_after must be a positive integer"
            )
        _validate_action_capabilities(
            correction.get("action_capabilities"),
            profile.get("capability_providers"),
            errors=errors,
        )
    _validate_capability_providers(
        profile.get("capability_providers"),
        capability_registry=capability_registry,
        errors=errors,
    )
    return errors


def write_project_profile_validation_receipt(
    profile_path: Path,
    output_path: Path,
    capability_registry_path: Path | None = None,
) -> dict[str, Any]:
    resolved_profile = profile_path.expanduser().resolve()
    resolved_registry = (
        capability_registry_path.expanduser().resolve() if capability_registry_path else None
    )
    errors: list[str] = []
    profile = _read_json_object(resolved_profile, errors=errors, label="project_profile")
    registry = (
        _read_json_object(resolved_registry, errors=errors, label="skill_capability_registry")
        if resolved_registry is not None
        else None
    )
    if profile:
        errors.extend(validate_project_profile(profile, capability_registry=registry))
    status = "PASS" if not errors else "BLOCKED"
    payload = {
        "schema": PROJECT_PROFILE_VALIDATION_RECEIPT_SCHEMA,
        "ok": not errors,
        "status": status,
        "mocked": False,
        "live": False,
        "provider_live": False,
        "profile_path": str(resolved_profile),
        "capability_registry_path": str(resolved_registry) if resolved_registry else None,
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
    resolved_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
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
        "capability_providers": profile.get("capability_providers"),
        "course_correction_action_capabilities": correction.get("action_capabilities"),
    }


def _validate_capability_providers(
    value: Any,
    *,
    capability_registry: dict[str, Any] | None,
    errors: list[str],
) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or not value:
        errors.append("capability_providers must be a non-empty object when present")
        return
    registry_capabilities = _registry_capabilities(capability_registry, errors=errors)
    for capability, provider in value.items():
        if not _non_empty_str(capability):
            errors.append("capability_providers keys must be non-empty strings")
            continue
        if not _non_empty_str(provider):
            errors.append(f"capability_providers.{capability} must be a non-empty string")
            continue
        default_provider = DEFAULT_CAPABILITY_PROVIDERS.get(capability)
        if default_provider is None and capability_registry is None:
            errors.append(f"capability_providers.{capability} is not a known capability")
            continue
        if capability_registry is None:
            if provider not in DEFAULT_CAPABILITY_PROVIDERS.values():
                errors.append(f"capability_providers.{capability} uses unknown skill provider")
            elif default_provider is not None and provider != default_provider:
                errors.append(
                    f"capability_providers.{capability} must use provider {default_provider}"
                )
            continue
        registry_entry = registry_capabilities.get(capability)
        if registry_entry is None:
            errors.append(f"capability_providers.{capability} is missing from registry")
            continue
        registry_provider = (
            registry_entry.get("skill") if isinstance(registry_entry, dict) else None
        )
        if registry_provider != provider:
            errors.append(
                f"capability_providers.{capability} provider does not match registry"
            )


def _validate_action_capabilities(
    value: Any,
    capability_providers: Any,
    *,
    errors: list[str],
) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        errors.append("course_correction.action_capabilities must be an object when present")
        return
    providers = capability_providers if isinstance(capability_providers, dict) else {}
    for action, capability in value.items():
        _validate_action(action, "course_correction.action_capabilities action", errors)
        if not _non_empty_str(capability):
            errors.append(
                f"course_correction.action_capabilities.{action} must name a capability"
            )
        elif capability not in providers:
            errors.append(
                f"course_correction.action_capabilities.{action} "
                "must reference capability_providers"
            )


def _registry_capabilities(
    capability_registry: dict[str, Any] | None,
    *,
    errors: list[str],
) -> dict[str, Any]:
    if capability_registry is None:
        return {}
    if capability_registry.get("schema") != SKILL_CAPABILITY_REGISTRY_SCHEMA:
        errors.append(f"capability_registry schema must be {SKILL_CAPABILITY_REGISTRY_SCHEMA}")
        return {}
    capabilities = capability_registry.get("capabilities")
    if not isinstance(capabilities, dict):
        errors.append("capability_registry.capabilities must be an object")
        return {}
    return capabilities


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
