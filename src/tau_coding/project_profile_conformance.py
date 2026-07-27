"""Live conformance receipt for authoritative project profile dispatch gates."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.project_profile import (
    PROJECT_PROFILE_SCHEMA,
    validate_project_profile,
)
from tau_coding.project_spine import (
    PROJECT_SPINE_SCHEMA,
    check_project_spine,
)
from tau_coding.skill_capability_registry import (
    DEFAULT_SKILL_CAPABILITY_REGISTRY,
    SKILL_CAPABILITY_REGISTRY_SCHEMA,
    validate_skill_capability_registry,
)

PROJECT_PROFILE_CONFORMANCE_SCHEMA = "tau.project_profile_conformance.v1"
PROJECT_PROFILE_DISPATCH_RECEIPT_SCHEMA = "tau.project_profile_dispatch_receipt.v1"
_PROJECT_ID = "tau-self-fix"
_REVISION_ID = "rev-002"
_GOAL_HASH = "sha256:goal-profile-conformance"


def write_project_profile_conformance(
    output: Path,
    *,
    allow_live_filesystem: bool,
) -> dict[str, Any]:
    """Exercise profile/spine/provider authority with real local artifacts."""

    if not allow_live_filesystem:
        raise RuntimeError("--allow-live-filesystem is required")
    resolved_output = output.expanduser().resolve()
    proof_dir = resolved_output.parent
    artifacts_dir = proof_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    profile = _profile()
    registry = _registry()
    spine = _spine()
    valid_paths = _write_case_artifacts(
        artifacts_dir=artifacts_dir,
        case_name="valid-dispatch",
        profile=profile,
        registry=registry,
        spine=spine,
    )
    valid_dispatch = _evaluate_dispatch(
        profile_path=valid_paths["profile"],
        registry_path=valid_paths["registry"],
        spine_path=valid_paths["spine"],
        requested_policy=_dispatch_policy(max_attempts_per_node=2),
        requested_capability="model_worker",
        requested_provider="scillm",
        receipt_path=artifacts_dir / "valid-dispatch-receipt.json",
    )

    parent_broadening_paths = _write_case_artifacts(
        artifacts_dir=artifacts_dir,
        case_name="parent-policy-broadening",
        profile=profile,
        registry=registry,
        spine=spine,
    )
    parent_broadening = _evaluate_dispatch(
        profile_path=parent_broadening_paths["profile"],
        registry_path=parent_broadening_paths["registry"],
        spine_path=parent_broadening_paths["spine"],
        requested_policy=_dispatch_policy(max_attempts_per_node=5),
        requested_capability="model_worker",
        requested_provider="scillm",
        receipt_path=artifacts_dir / "parent-policy-broadening-receipt.json",
    )

    stale_spine = _spine()
    stale_spine["active_work_queue"][0]["revision_id"] = "rev-001"
    stale_paths = _write_case_artifacts(
        artifacts_dir=artifacts_dir,
        case_name="stale-lineage",
        profile=profile,
        registry=registry,
        spine=stale_spine,
    )
    stale_lineage = _evaluate_dispatch(
        profile_path=stale_paths["profile"],
        registry_path=stale_paths["registry"],
        spine_path=stale_paths["spine"],
        requested_policy=_dispatch_policy(max_attempts_per_node=2),
        requested_capability="model_worker",
        requested_provider="scillm",
        receipt_path=artifacts_dir / "stale-lineage-receipt.json",
    )

    incompatible_paths = _write_case_artifacts(
        artifacts_dir=artifacts_dir,
        case_name="incompatible-provider",
        profile=profile,
        registry=registry,
        spine=spine,
    )
    incompatible_route = _evaluate_dispatch(
        profile_path=incompatible_paths["profile"],
        registry_path=incompatible_paths["registry"],
        spine_path=incompatible_paths["spine"],
        requested_policy=_dispatch_policy(max_attempts_per_node=2),
        requested_capability="model_worker",
        requested_provider="ask",
        receipt_path=artifacts_dir / "incompatible-provider-receipt.json",
    )

    dispatch_records_profile_spine_hashes = (
        valid_dispatch.get("profile_sha256") == _sha256_uri(valid_paths["profile"])
        and valid_dispatch.get("spine_sha256") == _sha256_uri(valid_paths["spine"])
        and valid_dispatch.get("registry_sha256") == _sha256_uri(valid_paths["registry"])
    )
    checks = {
        "valid_profile_permits_dispatch": valid_dispatch.get("status") == "PASS",
        "parent_policy_broadening_denied": parent_broadening.get("status") == "BLOCKED"
        and parent_broadening.get("block_code") == "PROFILE_POLICY_BROADENING_DENIED",
        "stale_lineage_denied_with_receipt": stale_lineage.get("status") == "BLOCKED"
        and stale_lineage.get("block_code") == "PROJECT_SPINE_BLOCKED"
        and stale_lineage.get("spine_receipt", {}).get("course_correction_count", 0) > 0,
        "incompatible_capability_provider_denied": incompatible_route.get("status")
        == "BLOCKED"
        and incompatible_route.get("block_code") == "CAPABILITY_PROVIDER_MISMATCH",
        "accepted_dispatch_records_profile_spine_hashes": dispatch_records_profile_spine_hashes,
    }
    failed_checks = [name for name, value in checks.items() if value is not True]
    payload = {
        "schema": PROJECT_PROFILE_CONFORMANCE_SCHEMA,
        "status": "PASS" if not failed_checks else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "output": str(resolved_output),
        "proof_dir": str(proof_dir),
        "artifacts_dir": str(artifacts_dir),
        "valid_dispatch_receipt": valid_dispatch["receipt_path"],
        "parent_policy_broadening_receipt": parent_broadening["receipt_path"],
        "stale_lineage_receipt": stale_lineage["receipt_path"],
        "incompatible_provider_receipt": incompatible_route["receipt_path"],
        "checks": checks,
        "failed_checks": failed_checks,
        "profile_spine_hashes": {
            "profile_sha256": valid_dispatch.get("profile_sha256"),
            "spine_sha256": valid_dispatch.get("spine_sha256"),
            "registry_sha256": valid_dispatch.get("registry_sha256"),
        },
        "proof_scope": {
            "proves": [
                "Tau created real project profile, project spine, registry, and dispatch "
                "receipt artifacts on disk.",
                "Tau denied dispatch when a child or parent policy attempted to broaden "
                "the project profile retry limit.",
                "Tau denied dispatch when the project spine contained stale lineage.",
                "Tau denied dispatch when a requested capability/provider route did not "
                "match the authoritative profile and registry.",
                "Tau accepted dispatch only with recorded profile, spine, and registry "
                "hashes.",
            ],
            "does_not_prove": [
                "Provider/model semantic quality.",
                "That a downstream provider call was made.",
                "Human approval for side effects.",
                "Future project profile authoring correctness outside this gate.",
            ],
        },
        "checked_at": _now(),
    }
    _write_json(resolved_output, payload)
    return payload


def _evaluate_dispatch(
    *,
    profile_path: Path,
    registry_path: Path,
    spine_path: Path,
    requested_policy: dict[str, Any],
    requested_capability: str,
    requested_provider: str,
    receipt_path: Path,
) -> dict[str, Any]:
    profile = _read_json(profile_path)
    registry = _read_json(registry_path)
    spine = _read_json(spine_path)
    profile_errors = validate_project_profile(profile, capability_registry=registry)
    registry_errors = validate_skill_capability_registry(registry)
    spine_receipt = check_project_spine(spine, spine_path=spine_path)
    block_code: str | None = None
    block_reasons: list[str] = []
    if profile_errors:
        block_code = "PROJECT_PROFILE_INVALID"
        block_reasons.extend(profile_errors)
    if registry_errors:
        block_code = block_code or "SKILL_CAPABILITY_REGISTRY_INVALID"
        block_reasons.extend(registry_errors)
    if spine_receipt.get("status") != "PASS":
        block_code = block_code or "PROJECT_SPINE_BLOCKED"
        block_reasons.extend(_spine_reasons(spine_receipt))
    profile_retry_limit = profile.get("retries", {}).get("max_attempts_per_node")
    requested_retry_limit = requested_policy.get("retries", {}).get("max_attempts_per_node")
    if (
        isinstance(profile_retry_limit, int)
        and isinstance(requested_retry_limit, int)
        and requested_retry_limit > profile_retry_limit
    ):
        block_code = block_code or "PROFILE_POLICY_BROADENING_DENIED"
        block_reasons.append(
            "requested retries.max_attempts_per_node broadens project profile policy"
        )
    profile_provider = profile.get("capability_providers", {}).get(requested_capability)
    registry_provider = (
        registry.get("capabilities", {}).get(requested_capability, {}).get("skill")
    )
    if requested_provider != profile_provider or requested_provider != registry_provider:
        block_code = block_code or "CAPABILITY_PROVIDER_MISMATCH"
        block_reasons.append(
            f"requested {requested_capability} provider {requested_provider!r} does not "
            "match profile and registry"
        )
    status = "PASS" if block_code is None else "BLOCKED"
    receipt = {
        "schema": PROJECT_PROFILE_DISPATCH_RECEIPT_SCHEMA,
        "status": status,
        "ok": status == "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "profile_path": str(profile_path.resolve()),
        "spine_path": str(spine_path.resolve()),
        "registry_path": str(registry_path.resolve()),
        "profile_sha256": _sha256_uri(profile_path),
        "spine_sha256": _sha256_uri(spine_path),
        "registry_sha256": _sha256_uri(registry_path),
        "requested_policy": requested_policy,
        "requested_capability": requested_capability,
        "requested_provider": requested_provider,
        "profile_provider": profile_provider,
        "registry_provider": registry_provider,
        "profile_errors": profile_errors,
        "registry_errors": registry_errors,
        "spine_receipt": spine_receipt,
        "block_code": block_code,
        "block_reasons": block_reasons,
        "dispatch_accepted": status == "PASS",
        "receipt_path": str(receipt_path.resolve()),
        "checked_at": _now(),
    }
    _write_json(receipt_path, receipt)
    return receipt


def _write_case_artifacts(
    *,
    artifacts_dir: Path,
    case_name: str,
    profile: dict[str, Any],
    registry: dict[str, Any],
    spine: dict[str, Any],
) -> dict[str, Path]:
    case_dir = artifacts_dir / case_name
    case_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "profile": case_dir / "project-profile.json",
        "registry": case_dir / "skill-capability-registry.json",
        "spine": case_dir / "project-spine.json",
    }
    _write_json(paths["profile"], profile)
    _write_json(paths["registry"], registry)
    _write_json(paths["spine"], spine)
    return paths


def _profile() -> dict[str, Any]:
    return {
        "schema": PROJECT_PROFILE_SCHEMA,
        "project_id": _PROJECT_ID,
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
            "action_capabilities": {
                "route_reviewer": "code_review",
            },
        },
        "capability_providers": {
            "debug_runtime_state": "debugger",
            "bounded_code_fix": "code-runner",
            "code_review": "review-code",
            "deep_research": "dogpile",
            "evidence_case": "create-evidence-case",
            "model_worker": "scillm",
        },
    }


def _registry() -> dict[str, Any]:
    registry = json.loads(json.dumps(DEFAULT_SKILL_CAPABILITY_REGISTRY))
    registry["schema"] = SKILL_CAPABILITY_REGISTRY_SCHEMA
    return registry


def _spine() -> dict[str, Any]:
    return {
        "schema": PROJECT_SPINE_SCHEMA,
        "project_id": _PROJECT_ID,
        "run_id": "profile-conformance-run",
        "dag_id": "profile-conformance-dag",
        "goal": {
            "goal_id": "goal-001",
            "active_revision_id": _REVISION_ID,
            "goal_hash": _GOAL_HASH,
        },
        "change_events": [{"event_id": "change-001", "status": "applied"}],
        "artifact_lineage_index": [
            {
                "artifact_id": "dispatch-work-order",
                "revision_id": _REVISION_ID,
                "depends_on_change_events": ["change-001"],
            }
        ],
        "active_work_queue": [
            {
                "work_id": "dispatch-001",
                "revision_id": _REVISION_ID,
                "status": "done",
                "artifact_id": "dispatch-work-order",
            }
        ],
        "work_lease_index": [],
        "accepted_evidence_index": [
            {
                "artifact_id": "dispatch-work-order",
                "revision_id": _REVISION_ID,
                "receipt_sha256": "sha256:dispatch-work-order",
            }
        ],
        "local_progress": {
            "reported_percent": 100,
            "derived_percent": 100,
            "source": "accepted_evidence_index",
        },
        "side_effects": [],
    }


def _dispatch_policy(*, max_attempts_per_node: int) -> dict[str, Any]:
    return {
        "retries": {
            "max_attempts_per_node": max_attempts_per_node,
        }
    }


def _spine_reasons(spine_receipt: dict[str, Any]) -> list[str]:
    reasons = [str(error) for error in spine_receipt.get("errors", [])]
    for defect in spine_receipt.get("defects", []):
        if isinstance(defect, dict):
            reasons.append(str(defect.get("message") or defect.get("code")))
    return reasons


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON root must be an object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256_uri(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
