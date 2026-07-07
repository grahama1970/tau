"""Skill capability registry validation for Tau/agent-skills composition."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.project_profile import COURSE_CORRECTION_ACTIONS

SKILL_CAPABILITY_REGISTRY_SCHEMA = "tau.skill_capability_registry.v1"
SKILL_CAPABILITY_REGISTRY_VALIDATION_RECEIPT_SCHEMA = (
    "tau.skill_capability_registry_validation_receipt.v1"
)
DEFAULT_SKILLS_ROOT = Path("/home/graham/workspace/experiments/agent-skills/skills")

ALLOWED_REQUIRED_TRIGGERS = {
    *COURSE_CORRECTION_ACTIONS,
    "debugger_evidence_required",
    "two_failed_attempts",
    "missing_evidence",
    "review_required",
    "research_required",
    "evidence_case_required",
    "model_worker_required",
}

DEFAULT_SKILL_CAPABILITY_REGISTRY: dict[str, Any] = {
    "schema": SKILL_CAPABILITY_REGISTRY_SCHEMA,
    "capabilities": {
        "debug_runtime_state": {
            "skill": "debugger",
            "native_artifact_schema": "debugger.proof.v1",
            "tau_receipt_schema": "tau.debug_session_receipt.v1",
            "required_for_triggers": [
                "debugger_evidence_required",
                "two_failed_attempts",
            ],
        },
        "bounded_code_fix": {
            "skill": "code-runner",
            "native_artifact_schema": "code_runner.result.v1",
            "tau_receipt_schema": "tau.code_patch_receipt.v1",
            "required_for_triggers": ["retry_node"],
        },
        "code_review": {
            "skill": "review-code",
            "native_artifact_schema": "review_result.json",
            "tau_receipt_schema": "tau.review_findings.v1",
            "required_for_triggers": ["route_reviewer", "review_required"],
        },
        "deep_research": {
            "skill": "dogpile",
            "native_artifact_schema": "dogpile.report.v1",
            "pre_gate": "tau.research_query_safety_receipt.v1",
            "tau_receipt_schema": "tau.research_source_receipt.v1",
            "required_for_triggers": ["run_brave_search_then_retry", "research_required"],
        },
        "evidence_case": {
            "skill": "create-evidence-case",
            "native_artifact_schema": "create_evidence_case.result.v1",
            "tau_receipt_schema": "tau.evidence_case_gate_receipt.v1",
            "required_for_triggers": ["evidence_case_required", "missing_evidence"],
        },
        "model_worker": {
            "skill": "scillm",
            "native_artifact_schema": "scillm.worker_result.v1",
            "tau_receipt_schema": "tau.scillm_worker_receipt.v1",
            "required_for_triggers": ["model_worker_required"],
        },
    },
}


def validate_skill_capability_registry(
    registry: dict[str, Any],
    *,
    skills_root: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    if registry.get("schema") != SKILL_CAPABILITY_REGISTRY_SCHEMA:
        errors.append(f"schema must be {SKILL_CAPABILITY_REGISTRY_SCHEMA}")
    capabilities = registry.get("capabilities")
    if not isinstance(capabilities, dict) or not capabilities:
        errors.append("capabilities must be a non-empty object")
        return errors
    resolved_skills_root = resolve_skills_root(skills_root)
    for capability, entry in capabilities.items():
        label = f"capabilities.{capability}"
        if not isinstance(capability, str) or not capability.strip():
            errors.append("capability names must be non-empty strings")
            continue
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        skill = entry.get("skill")
        if not _non_empty_str(skill):
            errors.append(f"{label}.skill must be a non-empty string")
        else:
            _validate_skill_exists(str(skill), resolved_skills_root, label, errors)
        if not _non_empty_str(entry.get("tau_receipt_schema")):
            errors.append(f"{label}.tau_receipt_schema must be a non-empty string")
        native_schema = entry.get("native_artifact_schema")
        if native_schema is not None and not _non_empty_str(native_schema):
            errors.append(f"{label}.native_artifact_schema must be a string when provided")
        pre_gate = entry.get("pre_gate")
        if pre_gate is not None and not _non_empty_str(pre_gate):
            errors.append(f"{label}.pre_gate must be a string when provided")
        triggers = entry.get("required_for_triggers", [])
        if not isinstance(triggers, list):
            errors.append(f"{label}.required_for_triggers must be a list when provided")
        else:
            for trigger in triggers:
                if not _non_empty_str(trigger):
                    errors.append(f"{label}.required_for_triggers[] must be a non-empty string")
                elif trigger not in ALLOWED_REQUIRED_TRIGGERS:
                    errors.append(
                        f"{label}.required_for_triggers[] unknown trigger: {trigger}"
                    )
    return errors


def write_skill_capability_registry_validation_receipt(
    registry_path: Path,
    output_path: Path,
    *,
    skills_root: Path | None = None,
) -> dict[str, Any]:
    resolved_registry = registry_path.expanduser().resolve()
    resolved_output = output_path.expanduser().resolve()
    resolved_skills_root = resolve_skills_root(skills_root)
    errors: list[str] = []
    registry = _read_json_object(resolved_registry, errors=errors, label="skill registry")
    if registry:
        errors.extend(
            validate_skill_capability_registry(
                registry,
                skills_root=resolved_skills_root,
            )
        )
    status = "PASS" if not errors else "BLOCKED"
    payload = {
        "schema": SKILL_CAPABILITY_REGISTRY_VALIDATION_RECEIPT_SCHEMA,
        "ok": not errors,
        "status": status,
        "mocked": False,
        "live": False,
        "provider_live": False,
        "registry_path": str(resolved_registry),
        "registry_sha256": (
            f"sha256:{_sha256(resolved_registry)}" if resolved_registry.exists() else None
        ),
        "skills_root": str(resolved_skills_root),
        "capability_count": _capability_count(registry),
        "skill_names": _skill_names(registry),
        "alert_count": len(errors),
        "errors": errors,
        "proof_scope": {
            "proves": [
                "Tau parsed a skill capability registry artifact.",
                "Tau checked that declared skills exist in the configured skills root.",
                "Tau checked Tau receipt schema bindings and known course-correction triggers.",
                "Tau did not invoke skills, mutate DAGs, call providers, sync Memory, "
                "or change routes.",
            ],
            "does_not_prove": [
                "The skill output is semantically correct.",
                "Any skill was executed.",
                "The mapped Tau adapter has accepted a native skill artifact.",
                "Provider/model semantic quality.",
                "Future route correctness.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def write_default_skill_capability_registry(output_path: Path) -> dict[str, Any]:
    resolved_output = output_path.expanduser().resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(json.dumps(DEFAULT_SKILL_CAPABILITY_REGISTRY))
    resolved_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def resolve_skills_root(skills_root: Path | None = None) -> Path:
    if skills_root is not None:
        return skills_root.expanduser().resolve()
    env_root = os.environ.get("TAU_SKILLS_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return DEFAULT_SKILLS_ROOT


def _validate_skill_exists(
    skill: str,
    skills_root: Path,
    label: str,
    errors: list[str],
) -> None:
    skill_dir = skills_root / skill
    if not skill_dir.is_dir():
        errors.append(f"{label}.skill does not exist under skills_root: {skill}")
        return
    if not (skill_dir / "SKILL.md").is_file():
        errors.append(f"{label}.skill is missing SKILL.md: {skill}")


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


def _capability_count(registry: dict[str, Any]) -> int:
    capabilities = registry.get("capabilities")
    return len(capabilities) if isinstance(capabilities, dict) else 0


def _skill_names(registry: dict[str, Any]) -> list[str]:
    capabilities = registry.get("capabilities")
    if not isinstance(capabilities, dict):
        return []
    names = {
        str(entry["skill"])
        for entry in capabilities.values()
        if isinstance(entry, dict) and _non_empty_str(entry.get("skill"))
    }
    return sorted(names)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
