"""Actor and environment provenance manifests for Tau runs."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.policy_profile import validate_data_boundary, validate_policy_profile

ACTOR_MANIFEST_SCHEMA = "tau.actor_manifest.v1"
ENVIRONMENT_MANIFEST_SCHEMA = "tau.environment_manifest.v1"

ACTOR_TYPES = {"agent", "human", "harness", "validator"}
NETWORK_POLICIES = {"deny", "allowlisted", "allow", "unknown"}
PROVIDER_ACCESS = {"denied", "allowed", "unknown"}
US_PERSON_VALUES = {"verified", "not_verified", "unknown"}


def build_actor_manifest(
    *,
    run_id: str,
    actors: Sequence[Mapping[str, Any]],
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Build and optionally write a Tau actor manifest."""

    manifest = {
        "schema": ACTOR_MANIFEST_SCHEMA,
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "created_at": _utc_stamp(),
        "run_id": run_id,
        "actors": [_normalize_actor(actor) for actor in actors],
        "proof_scope": {
            "proves": [
                "Tau recorded the declared actors for this run.",
                "Tau checked actor manifest shape and closed vocabulary fields.",
            ],
            "does_not_prove": [
                "Human legal identity.",
                "US-person or export-control eligibility.",
                "That an agent is trustworthy.",
                "That the declared actors performed the claimed work.",
            ],
        },
    }
    errors = validate_actor_manifest(manifest)
    if errors:
        manifest["ok"] = False
        manifest["status"] = "BLOCKED"
        manifest["errors"] = errors
    if output_path is not None:
        _write_json(output_path, manifest)
    return manifest


def build_environment_manifest(
    *,
    run_id: str,
    network_policy: str,
    provider_access: str,
    mounted_paths: Sequence[str] | None = None,
    secrets_visible: Sequence[str] | None = None,
    tool_versions: Mapping[str, str] | None = None,
    policy_profile: str | None = None,
    data_boundary: str | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Build and optionally write a Tau environment manifest."""

    merged_tool_versions = dict(tool_versions or {})
    merged_tool_versions.setdefault("python", sys.version.split()[0])
    git_version = _command_version(["git", "--version"])
    if git_version is not None:
        merged_tool_versions.setdefault("git", git_version)
    uv_version = _command_version(["uv", "--version"])
    if uv_version is not None:
        merged_tool_versions.setdefault("uv", uv_version)

    manifest = {
        "schema": ENVIRONMENT_MANIFEST_SCHEMA,
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "created_at": _utc_stamp(),
        "run_id": run_id,
        "host_id": platform.node() or "unknown",
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "network_policy": network_policy,
        "provider_access": provider_access,
        "mounted_paths": list(mounted_paths or []),
        "secrets_visible": list(secrets_visible or []),
        "tool_versions": merged_tool_versions,
        "policy_profile": policy_profile,
        "policy_profile_artifact": _optional_json_reference(policy_profile),
        "data_boundary": data_boundary,
        "data_boundary_artifact": _optional_json_reference(data_boundary),
        "proof_scope": {
            "proves": [
                "Tau recorded declared environment controls for this run.",
                "Tau checked environment manifest shape and closed vocabulary fields.",
            ],
            "does_not_prove": [
                "Runtime sandbox enforcement.",
                "Network egress enforcement.",
                "Secret absence outside the declared list.",
                "Provider/model semantic safety.",
                "ITAR compliance or legal sufficiency.",
            ],
        },
    }
    errors = validate_environment_manifest(manifest)
    if errors:
        manifest["ok"] = False
        manifest["status"] = "BLOCKED"
        manifest["errors"] = errors
    if output_path is not None:
        _write_json(output_path, manifest)
    return manifest


def validate_actor_manifest(payload: Mapping[str, Any]) -> list[str]:
    """Return actor manifest validation errors."""

    errors: list[str] = []
    if payload.get("schema") != ACTOR_MANIFEST_SCHEMA:
        errors.append(f"schema must be {ACTOR_MANIFEST_SCHEMA}")
    if not _non_empty_string(payload.get("run_id")):
        errors.append("run_id must be a non-empty string")
    actors = payload.get("actors")
    if not isinstance(actors, list) or not actors:
        errors.append("actors must be a non-empty list")
    elif all(isinstance(actor, Mapping) for actor in actors):
        for index, actor in enumerate(actors):
            errors.extend(_validate_actor(actor, prefix=f"actors[{index}]"))
    else:
        errors.append("actors entries must be objects")
    return errors


def validate_environment_manifest(payload: Mapping[str, Any]) -> list[str]:
    """Return environment manifest validation errors."""

    errors: list[str] = []
    if payload.get("schema") != ENVIRONMENT_MANIFEST_SCHEMA:
        errors.append(f"schema must be {ENVIRONMENT_MANIFEST_SCHEMA}")
    if not _non_empty_string(payload.get("run_id")):
        errors.append("run_id must be a non-empty string")
    if payload.get("network_policy") not in NETWORK_POLICIES:
        errors.append(f"network_policy must be one of {sorted(NETWORK_POLICIES)}")
    if payload.get("provider_access") not in PROVIDER_ACCESS:
        errors.append(f"provider_access must be one of {sorted(PROVIDER_ACCESS)}")
    if not _string_list(payload.get("mounted_paths")):
        errors.append("mounted_paths must be a list of strings")
    if not _string_list(payload.get("secrets_visible")):
        errors.append("secrets_visible must be a list of strings")
    tool_versions = payload.get("tool_versions")
    if not isinstance(tool_versions, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in tool_versions.items()
    ):
        errors.append("tool_versions must be an object of string values")
    errors.extend(
        _validate_optional_json_reference(
            payload.get("policy_profile"),
            field="policy_profile",
            validator=validate_policy_profile,
        )
    )
    errors.extend(
        _validate_optional_json_reference(
            payload.get("data_boundary"),
            field="data_boundary",
            validator=validate_data_boundary,
        )
    )
    return errors


def parse_actor_spec(spec: str) -> dict[str, Any]:
    """Parse CLI actor specs in actor_id:actor_type:role1,role2 form."""

    parts = spec.split(":", 2)
    if len(parts) != 3:
        raise RuntimeError("actor specs must use actor_id:actor_type:role1,role2")
    actor_id, actor_type, roles_raw = parts
    roles = [role.strip() for role in roles_raw.split(",") if role.strip()]
    return {
        "actor_id": actor_id,
        "actor_type": actor_type,
        "roles": roles,
        "trusted": False if actor_type == "agent" else True,
        "verified": False,
    }


def _normalize_actor(actor: Mapping[str, Any]) -> dict[str, Any]:
    roles = actor.get("roles")
    normalized = {
        "actor_id": actor.get("actor_id"),
        "actor_type": actor.get("actor_type"),
        "roles": list(roles) if isinstance(roles, list) else roles,
        "trusted": bool(actor.get("trusted", False)),
        "verified": bool(actor.get("verified", False)),
    }
    if "eligibility" in actor:
        normalized["eligibility"] = actor.get("eligibility")
    return normalized


def _validate_actor(actor: Mapping[str, Any], *, prefix: str) -> list[str]:
    errors: list[str] = []
    if not _non_empty_string(actor.get("actor_id")):
        errors.append(f"{prefix}.actor_id must be a non-empty string")
    if actor.get("actor_type") not in ACTOR_TYPES:
        errors.append(f"{prefix}.actor_type must be one of {sorted(ACTOR_TYPES)}")
    if not _string_list(actor.get("roles")) or not actor.get("roles"):
        errors.append(f"{prefix}.roles must be a non-empty list of strings")
    if not isinstance(actor.get("trusted"), bool):
        errors.append(f"{prefix}.trusted must be a boolean")
    if not isinstance(actor.get("verified"), bool):
        errors.append(f"{prefix}.verified must be a boolean")
    if "eligibility" in actor:
        errors.extend(_validate_actor_eligibility(actor.get("eligibility"), prefix=prefix))
    return errors


def _validate_actor_eligibility(value: Any, *, prefix: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return [f"{prefix}.eligibility must be an object"]
    if value.get("us_person") not in US_PERSON_VALUES:
        errors.append(
            f"{prefix}.eligibility.us_person must be one of {sorted(US_PERSON_VALUES)}"
        )
    if not isinstance(value.get("foreign_person"), bool):
        errors.append(f"{prefix}.eligibility.foreign_person must be a boolean")
    if not isinstance(value.get("export_control_training_current"), bool):
        errors.append(
            f"{prefix}.eligibility.export_control_training_current must be a boolean"
        )
    approved = value.get("approved_for_boundary")
    if not isinstance(approved, list) or not all(
        isinstance(item, str) and item.strip() for item in approved
    ):
        errors.append(
            f"{prefix}.eligibility.approved_for_boundary must be a list of non-empty strings"
        )
    return errors


def _command_version(args: list[str]) -> str | None:
    try:
        result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or result.stderr.strip() or None


def _optional_json_reference(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    path = Path(value).expanduser().resolve()
    payload = _read_json_object_or_none(path)
    artifact = {
        "path": str(path),
        "exists": path.exists() and path.is_file(),
        "sha256": f"sha256:{_sha256(path)}" if path.exists() and path.is_file() else None,
    }
    if isinstance(payload, Mapping) and isinstance(payload.get("schema"), str):
        artifact["schema"] = payload["schema"]
    return artifact


def _validate_optional_json_reference(value: Any, *, field: str, validator: Any) -> list[str]:
    if value is None:
        return []
    if not _non_empty_string(value):
        return [f"{field} must be a non-empty path string when present"]
    path = Path(value).expanduser().resolve()
    if not path.exists() or not path.is_file():
        return [f"{field} path must exist and be a file: {path}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{field} path must contain valid JSON: {path}: {exc}"]
    except OSError as exc:
        return [f"{field} path could not be read: {path}: {exc}"]
    if not isinstance(payload, Mapping):
        return [f"{field} path must contain a JSON object: {path}"]
    return [f"{field}: {error}" for error in validator(payload)]


def _read_json_object_or_none(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    path.expanduser().resolve().write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)
