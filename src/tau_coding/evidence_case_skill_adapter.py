"""Adapter from create-evidence-case artifacts into Tau evidence-case gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tau_coding.memory_evidence_gate import write_evidence_case_gate_receipt

EVIDENCE_CASE_SKILL_ADAPTER_RECEIPT_SCHEMA = "tau.evidence_case_skill_adapter_receipt.v1"
NORMALIZED_EVIDENCE_CASE_SCHEMA = "memory.evidence_case.v1"


def write_evidence_case_skill_adapter_receipt(
    *,
    case_path: Path,
    output_path: Path,
    repo_root: Path,
    expected_goal_hash: str | None = None,
    policy_profile: dict[str, Any] | None = None,
    data_boundary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_case = case_path.expanduser().resolve()
    resolved_output = output_path.expanduser().resolve()
    resolved_repo = repo_root.expanduser().resolve()
    errors: list[str] = []
    source = _read_json_object(resolved_case, errors=errors, label="evidence-case artifact")
    if source:
        _validate_source(source, expected_goal_hash=expected_goal_hash, errors=errors)
        _validate_policy_boundary_binding(
            source,
            policy_profile=policy_profile,
            data_boundary=data_boundary,
            errors=errors,
        )
    normalized = _normalize_evidence_case(
        source,
        source_path=resolved_case,
        repo_root=resolved_repo,
        policy_profile=policy_profile,
        data_boundary=data_boundary,
        errors=errors,
    )
    normalized_path = resolved_output.parent / "evidence-case.json"
    gate_receipt_path = resolved_output.parent / "evidence-case-gate-receipt.json"
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_path.write_text(
        json.dumps(normalized, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    gate_receipt = write_evidence_case_gate_receipt(
        evidence_case=normalized,
        evidence_case_path=normalized_path,
        dag_contract=_dag_contract(
            expected_goal_hash=expected_goal_hash,
            policy_profile=policy_profile,
            data_boundary=data_boundary,
            target=source.get("target") if isinstance(source.get("target"), dict) else None,
        ),
        receipt_path=gate_receipt_path,
    )
    if gate_receipt.get("ok") is not True:
        errors.append("evidence case gate receipt blocked")

    ok = not errors
    payload = {
        "schema": EVIDENCE_CASE_SKILL_ADAPTER_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "source_case_path": str(resolved_case),
        "source_case_sha256": _sha256_uri(resolved_case) if resolved_case.is_file() else None,
        "repo_root": str(resolved_repo),
        "goal_hash": source.get("goal_hash"),
        "expected_goal_hash": expected_goal_hash,
        "question": normalized.get("question"),
        "claim": normalized.get("claim"),
        "verdict": source.get("verdict", source.get("verdict_state")),
        "normalized_evidence_case_path": str(normalized_path),
        "normalized_evidence_case_sha256": _sha256_uri(normalized_path),
        "evidence_case_gate_receipt_path": str(gate_receipt_path),
        "evidence_case_gate_status": gate_receipt.get("status"),
        "evidence_case_gate_alert_codes": gate_receipt.get("alert_codes", []),
        "support_artifact_count": len(normalized.get("support_artifacts", [])),
        "errors": errors,
        "course_correction": _course_correction(errors),
        "proof_scope": {
            "proves": [
                "Tau ingested a create-evidence-case artifact.",
                "Tau normalized it into a separate memory.evidence_case.v1 artifact.",
                "Tau validated the normalized artifact with tau.evidence_case_gate_receipt.v1.",
            ],
            "does_not_prove": [
                "Evidence-case semantic completeness.",
                "Compliance sufficiency.",
                "Memory fact truth.",
                "Provider/model semantic quality.",
            ],
        },
    }
    resolved_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _validate_source(
    source: dict[str, Any],
    *,
    expected_goal_hash: str | None,
    errors: list[str],
) -> None:
    if not _question_or_claim(source):
        errors.append("question or claim is required")
    goal_hash = source.get("goal_hash")
    if expected_goal_hash and goal_hash != expected_goal_hash:
        errors.append("goal_hash mismatches expected_goal_hash")
    status = source.get("status")
    if isinstance(status, str) and status.upper() in {"BLOCKED", "ERROR", "FAILED"}:
        errors.append("create-evidence-case artifact status is blocked")


def _validate_policy_boundary_binding(
    source: dict[str, Any],
    *,
    policy_profile: dict[str, Any] | None,
    data_boundary: dict[str, Any] | None,
    errors: list[str],
) -> None:
    source_policy = source.get("policy_profile")
    if (
        policy_profile is not None
        and isinstance(source_policy, dict)
        and _policy_key(source_policy) != _policy_key(policy_profile)
    ):
        errors.append("policy_profile mismatches create-evidence-case artifact")
    source_boundary = source.get("data_boundary")
    if (
        data_boundary is not None
        and isinstance(source_boundary, dict)
        and _boundary_key(source_boundary) != _boundary_key(data_boundary)
    ):
        errors.append("data_boundary mismatches create-evidence-case artifact")


def _normalize_evidence_case(
    source: dict[str, Any],
    *,
    source_path: Path,
    repo_root: Path,
    policy_profile: dict[str, Any] | None,
    data_boundary: dict[str, Any] | None,
    errors: list[str],
) -> dict[str, Any]:
    support = _support_artifacts(source, repo_root=repo_root, errors=errors)
    return {
        "schema": NORMALIZED_EVIDENCE_CASE_SCHEMA,
        "source": "skill:create-evidence-case",
        "source_schema": source.get("schema"),
        "sha256": _sha256_uri(source_path) if source_path.is_file() else None,
        "case_sha256": _sha256_uri(source_path) if source_path.is_file() else None,
        "goal_hash": source.get("goal_hash"),
        "target": source.get("target") if isinstance(source.get("target"), dict) else None,
        "question": source.get("question", source.get("question_text")),
        "claim": source.get("claim"),
        "answer": source.get("answer"),
        "verdict": source.get("verdict", source.get("verdict_state")),
        "evidence_case": source.get("evidence_case"),
        "support_artifacts": support,
        "data_boundary": source.get("data_boundary", data_boundary),
        "policy_profile": source.get("policy_profile", policy_profile),
        "data_boundary_sha256": source.get("data_boundary_sha256"),
        "policy_profile_sha256": source.get("policy_profile_sha256"),
    }


def _support_artifacts(
    source: dict[str, Any],
    *,
    repo_root: Path,
    errors: list[str],
) -> list[dict[str, Any]]:
    values = source.get("support_artifacts", source.get("evidence_artifacts", []))
    if values is None:
        return []
    if not isinstance(values, list):
        errors.append("support_artifacts must be a list")
        return []
    support: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        path_value: str | None = None
        schema: str | None = None
        if isinstance(value, str):
            path_value = value
        elif isinstance(value, dict):
            raw = value.get("path")
            path_value = raw if isinstance(raw, str) else None
            raw_schema = value.get("schema")
            schema = raw_schema if isinstance(raw_schema, str) else None
        else:
            errors.append(f"support_artifacts[{index}] must be a string or object")
            continue
        if not path_value:
            errors.append(f"support_artifacts[{index}].path is required")
            continue
        path = Path(path_value).expanduser()
        resolved = path.resolve() if path.is_absolute() else (repo_root / path).resolve()
        if not _is_relative_to(resolved, repo_root):
            errors.append(f"support_artifacts[{index}] escapes repo root: {resolved}")
            continue
        if not resolved.is_file():
            errors.append(f"support_artifacts[{index}] is missing or not a file: {resolved}")
            continue
        support.append(
            {
                "path": str(resolved),
                "schema": schema,
                "sha256": _sha256_uri(resolved),
                "bytes": resolved.stat().st_size,
            }
        )
    return support


def _dag_contract(
    *,
    expected_goal_hash: str | None,
    policy_profile: dict[str, Any] | None,
    data_boundary: dict[str, Any] | None,
    target: dict[str, Any] | None,
) -> dict[str, Any]:
    contract: dict[str, Any] = {}
    if expected_goal_hash:
        contract["goal"] = {"goal_hash": expected_goal_hash}
    if target:
        contract["target"] = target
    if policy_profile is not None:
        contract["policy_profile"] = policy_profile
    if data_boundary is not None:
        contract["data_boundary"] = data_boundary
    return contract


def _course_correction(errors: list[str]) -> dict[str, Any] | None:
    if not errors:
        return None
    return {
        "schema": "tau.course_correction.v1",
        "trigger": "evidence_required",
        "required_next_action": "route_evidence_case",
        "allowed_next_routes": ["create-evidence-case", "reviewer", "human"],
        "forbidden_next_routes": ["dispatch_without_evidence_case_gate"],
        "required_evidence_before_retry": [
            "memory.evidence_case.v1",
            "tau.evidence_case_gate_receipt.v1",
        ],
    }


def _question_or_claim(source: dict[str, Any]) -> str | None:
    for field in ("question", "question_text", "claim"):
        value = source.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _policy_key(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": value.get("schema"),
        "profile_id": value.get("profile_id"),
        "default_decision": value.get("default_decision"),
    }


def _boundary_key(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": value.get("schema"),
        "boundary_id": value.get("boundary_id"),
        "classification": value.get("classification"),
        "export_controlled": value.get("export_controlled"),
        "itar": value.get("itar"),
        "technical_data": value.get("technical_data"),
    }


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


def _sha256_uri(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
