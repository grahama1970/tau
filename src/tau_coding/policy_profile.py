"""Zero-trust policy and data-boundary preflight receipts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

POLICY_PROFILE_SCHEMA = "tau.policy_profile.v1"
DATA_BOUNDARY_SCHEMA = "tau.data_boundary.v1"
ZERO_TRUST_PREFLIGHT_RECEIPT_SCHEMA = "tau.zero_trust_preflight_receipt.v1"

CLASSIFICATIONS = {
    "public",
    "internal",
    "CUI",
    "ITAR",
    "EAR",
    "classified-not-allowed",
}
DECISIONS = {"allow", "deny", "approval_required", "allow_with_approval", "allow_with_review"}


def validate_policy_profile(payload: Mapping[str, Any]) -> list[str]:
    """Return policy-profile validation errors."""

    errors: list[str] = []
    if payload.get("schema") != POLICY_PROFILE_SCHEMA:
        errors.append(f"schema must be {POLICY_PROFILE_SCHEMA}")
    _require_non_empty_string(payload, "profile_id", errors)
    if payload.get("default_decision") not in {"allow", "deny"}:
        errors.append("default_decision must be one of ['allow', 'deny']")
    if not isinstance(payload.get("requires_data_boundary"), bool):
        errors.append("requires_data_boundary must be a boolean")
    _validate_section_default(payload, "network", errors=errors)
    _validate_decision(payload, "providers", "cloud_llm", errors=errors)
    _validate_decision(payload, "providers", "local_model", errors=errors)
    _validate_decision(payload, "research", "external_search", errors=errors)
    _validate_decision(payload, "research", "manual_sanitized_receipt", errors=errors)
    _validate_decision(payload, "memory", "read", errors=errors)
    _validate_decision(payload, "memory", "write", errors=errors)
    _validate_memory_gate_policy(payload, errors=errors)
    _validate_decision(payload, "github", "public_mutation", errors=errors)
    _validate_decision(payload, "github", "dry_run_projection", errors=errors)
    filesystem = payload.get("filesystem")
    if not isinstance(filesystem, Mapping):
        errors.append("filesystem must be an object")
    else:
        if not _is_string_list(filesystem.get("write_allowlist")):
            errors.append("filesystem.write_allowlist must be a list of strings")
        if not _is_string_list(filesystem.get("read_denylist")):
            errors.append("filesystem.read_denylist must be a list of strings")
    return errors


def validate_data_boundary(payload: Mapping[str, Any]) -> list[str]:
    """Return data-boundary validation errors."""

    errors: list[str] = []
    if payload.get("schema") != DATA_BOUNDARY_SCHEMA:
        errors.append(f"schema must be {DATA_BOUNDARY_SCHEMA}")
    classification = payload.get("classification")
    if classification not in CLASSIFICATIONS:
        errors.append(f"classification must be one of {sorted(CLASSIFICATIONS)}")
    for key in (
        "export_controlled",
        "itar",
        "technical_data",
        "external_provider_allowed",
        "external_research_allowed",
        "public_repo_allowed",
    ):
        if not isinstance(payload.get(key), bool):
            errors.append(f"{key} must be a boolean")
    if payload.get("foreign_person_access") not in {"allowed", "restricted", "prohibited"}:
        errors.append(
            "foreign_person_access must be one of ['allowed', 'restricted', 'prohibited']"
        )
    if "notes" in payload and not _is_string_list(payload.get("notes")):
        errors.append("notes must be a list of strings when present")
    return errors


def zero_trust_preflight_receipt(
    *,
    policy_profile: Mapping[str, Any] | None,
    data_boundary: Mapping[str, Any] | None,
    dag_contract: Mapping[str, Any] | None = None,
    policy_profile_path: Path | None = None,
    data_boundary_path: Path | None = None,
    dag_contract_path: Path | None = None,
) -> dict[str, Any]:
    """Evaluate zero-trust policy/data-boundary gates without side effects."""

    alerts: list[dict[str, Any]] = []
    policy_errors: list[str] = []
    boundary_errors: list[str] = []
    if policy_profile is None:
        alerts.append(
            _alert(
                "missing_policy_profile",
                "Zero-trust preflight requires a policy profile.",
            )
        )
    else:
        policy_errors = validate_policy_profile(policy_profile)
        if policy_errors:
            code = (
                "invalid_policy_profile_schema"
                if policy_profile.get("schema") != POLICY_PROFILE_SCHEMA
                else "unsupported_default_decision"
                if policy_profile.get("default_decision") not in {"allow", "deny"}
                else "invalid_policy_profile"
            )
            alerts.append(
                _alert(
                    code,
                    "Zero-trust policy profile is invalid.",
                    errors=policy_errors,
                )
            )

    requires_boundary = bool(
        isinstance(policy_profile, Mapping) and policy_profile.get("requires_data_boundary") is True
    )
    if requires_boundary and data_boundary is None:
        alerts.append(_alert("missing_data_boundary", "Zero-trust policy requires data_boundary."))
    elif data_boundary is not None:
        boundary_errors = validate_data_boundary(data_boundary)
        if boundary_errors:
            code = (
                "invalid_data_boundary_schema"
                if data_boundary.get("schema") != DATA_BOUNDARY_SCHEMA
                else "missing_classification"
                if not data_boundary.get("classification")
                else "invalid_data_boundary"
            )
            alerts.append(
                _alert(
                    code,
                    "Zero-trust data boundary is invalid.",
                    errors=boundary_errors,
                )
            )
        elif data_boundary.get("classification") == "classified-not-allowed":
            alerts.append(
                _alert(
                    "classified_not_allowed",
                    "Tau refuses classified-not-allowed data boundaries.",
                )
            )

    if policy_profile is not None and data_boundary is not None and not boundary_errors:
        alerts.extend(_compatibility_alerts(policy_profile, data_boundary, dag_contract))

    ok = not alerts
    receipt = {
        "schema": ZERO_TRUST_PREFLIGHT_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "checked_at": _utc_stamp(),
        "policy_profile": _source_payload(
            policy_profile,
            path=policy_profile_path,
            sha256=_path_sha256(policy_profile_path),
        ),
        "data_boundary": _source_payload(
            data_boundary,
            path=data_boundary_path,
            sha256=_path_sha256(data_boundary_path),
        ),
        "dag_contract": _source_payload(
            dag_contract,
            path=dag_contract_path,
            sha256=_path_sha256(dag_contract_path),
        ),
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": {
            "proves": [
                "Tau inspected the policy profile and data boundary before DAG dispatch.",
                "Tau blocked execution when required classification metadata was missing "
                "or incompatible.",
            ],
            "does_not_prove": [
                "ITAR compliance.",
                "Export-control legal sufficiency.",
                "Runtime sandbox enforcement.",
                "Human identity verification.",
                "Provider/model semantic safety.",
                "Compliance package completeness.",
            ],
        },
    }
    return receipt


def write_zero_trust_preflight_receipt(
    *,
    policy_profile_path: Path,
    data_boundary_path: Path | None = None,
    dag_contract_path: Path | None = None,
    receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Load policy inputs, evaluate the preflight, and optionally write a receipt."""

    policy_payload = _read_json_object(policy_profile_path)
    boundary_payload = _read_json_object(data_boundary_path) if data_boundary_path else None
    dag_payload = _read_json_object(dag_contract_path) if dag_contract_path else None
    receipt = zero_trust_preflight_receipt(
        policy_profile=policy_payload,
        data_boundary=boundary_payload,
        dag_contract=dag_payload,
        policy_profile_path=policy_profile_path.expanduser().resolve(),
        data_boundary_path=(
            data_boundary_path.expanduser().resolve() if data_boundary_path else None
        ),
        dag_contract_path=dag_contract_path.expanduser().resolve() if dag_contract_path else None,
    )
    if receipt_path is not None:
        resolved_receipt = receipt_path.expanduser().resolve()
        resolved_receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt["receipt_path"] = str(resolved_receipt)
        resolved_receipt.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return receipt


def _compatibility_alerts(
    policy_profile: Mapping[str, Any],
    data_boundary: Mapping[str, Any],
    dag_contract: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    provider_policy = _section_value(policy_profile, "providers", "cloud_llm")
    if provider_policy == "deny" and _dag_requests_external_provider(dag_contract):
        alerts.append(
            _alert(
                "external_provider_denied",
                "DAG requests provider-backed execution while policy denies cloud LLM providers.",
            )
        )
    research_policy = _section_value(policy_profile, "research", "external_search")
    if research_policy == "deny" and data_boundary.get("external_research_allowed") is True:
        alerts.append(
            _alert(
                "external_research_denied",
                "Data boundary allows external research but policy denies it.",
            )
        )
    github_policy = _section_value(policy_profile, "github", "public_mutation")
    if github_policy == "deny" and data_boundary.get("public_repo_allowed") is True:
        alerts.append(
            _alert(
                "public_repo_denied",
                "Data boundary allows public repo activity but policy denies public "
                "GitHub mutation.",
            )
        )
    if _section_value(policy_profile, "memory", "write") == "approval_required":
        if _dag_requests_memory_write(dag_contract):
            alerts.append(
                _alert(
                    "memory_write_requires_approval",
                    "DAG requests Memory write while policy requires approval.",
                )
            )
    return alerts


def _dag_requests_external_provider(dag_contract: Mapping[str, Any] | None) -> bool:
    if not isinstance(dag_contract, Mapping):
        return False
    nodes = dag_contract.get("nodes")
    if not isinstance(nodes, list):
        return False
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        if isinstance(node.get("provider"), Mapping):
            return True
        executor = node.get("executor")
        if executor in {"codex", "opencode", "scillm", "provider"}:
            return True
    return False


def _dag_requests_memory_write(dag_contract: Mapping[str, Any] | None) -> bool:
    if not isinstance(dag_contract, Mapping):
        return False
    text = json.dumps(dag_contract, sort_keys=True).lower()
    return "memory_upsert" in text or "memory_write" in text


def _read_json_object(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    resolved = path.expanduser().resolve()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON root must be an object: {resolved}")
    return payload


def _validate_section_default(
    payload: Mapping[str, Any],
    section_name: str,
    *,
    errors: list[str],
) -> None:
    section = payload.get(section_name)
    if not isinstance(section, Mapping):
        errors.append(f"{section_name} must be an object")
        return
    if section.get("default") not in {"allow", "deny"}:
        errors.append(f"{section_name}.default must be one of ['allow', 'deny']")
    domains = section.get("allowed_domains", [])
    if not _is_string_list(domains):
        errors.append(f"{section_name}.allowed_domains must be a list of strings")


def _validate_decision(
    payload: Mapping[str, Any],
    section_name: str,
    key: str,
    *,
    errors: list[str],
) -> None:
    section = payload.get(section_name)
    if not isinstance(section, Mapping):
        errors.append(f"{section_name} must be an object")
        return
    if section.get(key) not in DECISIONS:
        errors.append(f"{section_name}.{key} must be one of {sorted(DECISIONS)}")


def _validate_memory_gate_policy(payload: Mapping[str, Any], *, errors: list[str]) -> None:
    memory = payload.get("memory")
    if not isinstance(memory, Mapping):
        return
    for key in ("intent_required", "clarify_blocks_dispatch", "deflect_blocks_dispatch"):
        if key in memory and not isinstance(memory.get(key), bool):
            errors.append(f"memory.{key} must be a boolean when present")
    if "min_intent_confidence" in memory:
        confidence = memory.get("min_intent_confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            errors.append("memory.min_intent_confidence must be a number between 0 and 1")
        elif confidence < 0 or confidence > 1:
            errors.append("memory.min_intent_confidence must be a number between 0 and 1")
    if "evidence_case_required_for" in memory and not _is_string_list(
        memory.get("evidence_case_required_for")
    ):
        errors.append("memory.evidence_case_required_for must be a list of strings when present")


def _section_value(payload: Mapping[str, Any], section_name: str, key: str) -> object:
    section = payload.get(section_name)
    return section.get(key) if isinstance(section, Mapping) else None


def _require_non_empty_string(
    payload: Mapping[str, Any],
    key: str,
    errors: list[str],
) -> None:
    if not isinstance(payload.get(key), str) or not payload[key].strip():
        errors.append(f"{key} must be a non-empty string")


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)


def _alert(code: str, message: str, *, errors: list[str] | None = None) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    if errors:
        evidence["errors"] = errors
    return {
        "severity": "BLOCK",
        "code": code,
        "message": message,
        "evidence": evidence,
    }


def _source_payload(
    payload: Mapping[str, Any] | None,
    *,
    path: Path | None,
    sha256: str | None,
) -> dict[str, Any] | None:
    if payload is None:
        return None
    result: dict[str, Any] = {
        "schema": payload.get("schema"),
    }
    if path is not None:
        result["path"] = str(path)
    if sha256 is not None:
        result["sha256"] = f"sha256:{sha256}"
    return result


def _path_sha256(path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return None
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
