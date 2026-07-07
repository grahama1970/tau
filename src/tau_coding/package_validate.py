"""Review-readiness validation for Tau compliance evidence packages."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

COMPLIANCE_PACKAGE_VALIDATION_RECEIPT_SCHEMA = "tau.compliance_package_validation_receipt.v1"

SUPPORTED_CODING_EVIDENCE_SCHEMAS = {
    "tau.code_patch_receipt.v1",
    "tau.code_runner_worker_receipt.v1",
    "tau.debugger_skill_adapter_receipt.v1",
    "tau.evidence_case_skill_adapter_receipt.v1",
    "tau.lsp_diagnostics_receipt.v1",
    "tau.lsp_symbol_receipt.v1",
    "tau.lsp_rename_receipt.v1",
    "tau.test_run_receipt.v1",
    "tau.review_findings.v1",
    "tau.review_code_skill_adapter_receipt.v1",
    "tau.commit_plan_receipt.v1",
    "tau.debug_session_receipt.v1",
    "tau.github_read_receipt.v1",
    "tau.omp_worker_doctor_receipt.v1",
    "tau.omp_worker_receipt.v1",
    "tau.omp_worker_launch_receipt.v1",
    "tau.research_skill_adapter_receipt.v1",
    "tau.scillm_worker_receipt.v1",
    "tau.scillm_worker_launch_receipt.v1",
    "tau.skill_composition_redteam_receipt.v1",
    "tau.course_correction.v1",
    "tau.orchestration_reliability_receipt.v1",
}

REQUIRED_ITAR_LOCAL_ONLY_FILES = {
    "data_boundary": ("data-boundary.json", "tau.data_boundary.v1", False),
    "policy_profile": ("policy-profile.json", "tau.policy_profile.v1", False),
    "zero_trust_preflight": (
        "zero-trust-preflight-receipt.json",
        "tau.zero_trust_preflight_receipt.v1",
        True,
    ),
    "memory_intent_gate": (
        "memory-intent-gate-receipt.json",
        "tau.memory_intent_gate_receipt.v1",
        True,
    ),
    "evidence_case_gate": (
        "evidence-case-gate-receipt.json",
        "tau.evidence_case_gate_receipt.v1",
        True,
    ),
    "evidence_validation": (
        "evidence-validation-receipt.json",
        "tau.evidence_validation_receipt.v1",
        True,
    ),
    "sandbox_run": ("sandbox-run-receipt.json", "tau.sandbox_run_receipt.v1", True),
    "actor_manifest": ("actor-access-manifest.json", "tau.actor_access_manifest.v1", False),
    "environment_manifest": ("environment-manifest.json", "tau.environment_manifest.v1", False),
    "signed_receipt_verification": (
        "signed-receipt-verification.json",
        "tau.signed_receipt_verification.v1",
        True,
    ),
    "itar_access_preflight": (
        "itar-access-preflight-receipt.json",
        "tau.itar_access_preflight_receipt.v1",
        True,
    ),
}


def write_compliance_package_validation_receipt(
    *,
    package_dir: Path,
    receipt_path: Path,
    policy: str = "itar-local-only",
) -> dict[str, Any]:
    """Validate whether a package is ready for review under a named policy."""

    resolved_package = package_dir.expanduser().resolve()
    resolved_receipt = receipt_path.expanduser().resolve()
    alerts: list[dict[str, Any]] = []
    artifacts: dict[str, dict[str, Any]] = {}

    if not resolved_package.is_dir():
        alerts.append(
            _alert(
                "package_dir_missing",
                "Compliance package directory does not exist.",
                path=str(resolved_package),
            )
        )
    requirements = REQUIRED_ITAR_LOCAL_ONLY_FILES if policy == "itar-local-only" else {}
    if not requirements:
        alerts.append(_alert("unknown_policy", f"Unknown package validation policy: {policy}"))

    goal_hashes: set[str] = set()
    data_boundary_hashes: set[str] = set()
    package_data_boundary_sha256: str | None = None
    for key, (relative_path, expected_schema, require_pass) in requirements.items():
        artifact_path = resolved_package / relative_path
        summary = _artifact_summary(artifact_path)
        artifacts[key] = summary
        if key == "data_boundary" and isinstance(summary.get("sha256"), str):
            package_data_boundary_sha256 = str(summary["sha256"])
        if not artifact_path.exists():
            alerts.append(
                _alert(
                    "missing_required_artifact",
                    f"Required package artifact is missing: {relative_path}",
                    artifact=relative_path,
                )
            )
            continue
        if relative_path.endswith(".json"):
            payload = _read_json_object(artifact_path, alerts=alerts, artifact=relative_path)
            schema = payload.get("schema") if isinstance(payload, Mapping) else None
            summary["schema"] = schema
            if schema != expected_schema:
                alerts.append(
                    _alert(
                        "artifact_schema_mismatch",
                        f"{relative_path} schema mismatch.",
                        artifact=relative_path,
                        expected_schema=expected_schema,
                        actual_schema=schema,
                    )
                )
            if require_pass and payload.get("status") not in {"PASS", "VALID"}:
                alerts.append(
                    _alert(
                        "critical_receipt_not_pass",
                        f"{relative_path} is not a passing critical receipt.",
                        artifact=relative_path,
                        status=payload.get("status"),
                    )
                )
            alerts.extend(_semantic_alerts(key, payload, artifact=relative_path))
            _collect_hash(payload, "goal_hash", goal_hashes)
            _collect_hash(payload, "active_goal_hash", goal_hashes)
            _collect_hash(payload, "data_boundary_sha256", data_boundary_hashes)

    _validate_coding_evidence_receipts(
        package_dir=resolved_package,
        artifacts=artifacts,
        alerts=alerts,
        goal_hashes=goal_hashes,
        data_boundary_hashes=data_boundary_hashes,
    )

    non_claims = resolved_package / "non-claims.md"
    artifacts["non_claims"] = _artifact_summary(non_claims)
    if not non_claims.exists():
        alerts.append(
            _alert(
                "missing_required_artifact",
                "Required package artifact is missing: non-claims.md",
                artifact="non-claims.md",
            )
        )
    else:
        text = non_claims.read_text(encoding="utf-8", errors="ignore").lower()
        if "does not prove" not in text and "not claim" not in text:
            alerts.append(
                _alert(
                    "non_claims_missing_boundary_language",
                    "non-claims.md must state what the package does not prove.",
                    artifact="non-claims.md",
                )
            )

    if len(goal_hashes) > 1:
        alerts.append(
            _alert(
                "goal_hash_mismatch",
                "Package artifacts cite inconsistent goal hashes.",
                goal_hashes=sorted(goal_hashes),
            )
        )
    if len(data_boundary_hashes) > 1:
        alerts.append(
            _alert(
                "data_boundary_hash_mismatch",
                "Package artifacts cite inconsistent data boundary hashes.",
                data_boundary_hashes=sorted(data_boundary_hashes),
            )
        )
    if (
        package_data_boundary_sha256 is not None
        and data_boundary_hashes
        and package_data_boundary_sha256 not in data_boundary_hashes
    ):
        alerts.append(
            _alert(
                "data_boundary_hash_does_not_match_artifact",
                "Receipt data_boundary_sha256 does not match packaged data-boundary.json.",
                expected=package_data_boundary_sha256,
                cited=sorted(data_boundary_hashes),
            )
        )

    review_ready = not alerts
    receipt: dict[str, Any] = {
        "schema": COMPLIANCE_PACKAGE_VALIDATION_RECEIPT_SCHEMA,
        "ok": review_ready,
        "status": "PASS" if review_ready else "BLOCKED",
        "review_ready": review_ready,
        "compliant": "NOT_CLAIMED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "policy": policy,
        "package_dir": str(resolved_package),
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "goal_hashes": sorted(goal_hashes),
        "data_boundary_hashes": sorted(data_boundary_hashes),
        "alerts": alerts,
        "alert_codes": [str(alert["code"]) for alert in alerts],
        "recommended_action": _recommended_action(alerts),
        "receipt_path": str(resolved_receipt),
        "checked_at": _utc_stamp(),
        "proof_scope": {
            "proves": [
                "Tau inspected a compliance evidence package for review readiness.",
                (
                    "Required artifacts, schemas, critical receipt statuses, "
                    "non-claim language, and hash consistency were checked deterministically."
                ),
                "Tau did not claim legal compliance.",
            ],
            "does_not_prove": [
                "ITAR compliance.",
                "Export-control legal sufficiency.",
                "Legal identity proof.",
                "Truth of the package contents.",
                "Provider/model semantic quality.",
            ],
        },
    }
    resolved_receipt.parent.mkdir(parents=True, exist_ok=True)
    resolved_receipt.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def _artifact_summary(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "sha256": f"sha256:{_file_sha256(path)}" if path.exists() else None,
    }


def _read_json_object(
    path: Path,
    *,
    alerts: list[dict[str, Any]],
    artifact: str,
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        alerts.append(
            _alert(
                "artifact_unreadable",
                f"{artifact} is not readable JSON.",
                artifact=artifact,
                error=str(exc),
            )
        )
        return {}
    if not isinstance(payload, dict):
        alerts.append(
            _alert(
                "artifact_not_object",
                f"{artifact} root must be a JSON object.",
                artifact=artifact,
            )
        )
        return {}
    return payload


def _collect_hash(payload: Mapping[str, Any], key: str, target: set[str]) -> None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        target.add(value)


def _validate_coding_evidence_receipts(
    *,
    package_dir: Path,
    artifacts: dict[str, dict[str, Any]],
    alerts: list[dict[str, Any]],
    goal_hashes: set[str],
    data_boundary_hashes: set[str],
) -> None:
    evidence_dir = package_dir / "coding-evidence-receipts"
    artifacts["coding_evidence_receipts"] = {
        "path": str(evidence_dir),
        "exists": evidence_dir.is_dir(),
        "receipt_count": 0,
    }
    if not evidence_dir.exists():
        return
    if not evidence_dir.is_dir():
        alerts.append(
            _alert(
                "coding_evidence_receipts_not_directory",
                "coding-evidence-receipts must be a directory when present.",
                artifact="coding-evidence-receipts",
            )
        )
        return
    receipt_paths = sorted(evidence_dir.glob("*.json"))
    artifacts["coding_evidence_receipts"]["receipt_count"] = len(receipt_paths)
    for receipt_path in receipt_paths:
        relative = receipt_path.relative_to(package_dir).as_posix()
        artifact_key = f"coding_evidence:{relative}"
        summary = _artifact_summary(receipt_path)
        artifacts[artifact_key] = summary
        payload = _read_json_object(receipt_path, alerts=alerts, artifact=relative)
        schema = payload.get("schema") if isinstance(payload, Mapping) else None
        summary["schema"] = schema
        if schema not in SUPPORTED_CODING_EVIDENCE_SCHEMAS:
            alerts.append(
                _alert(
                    "unsupported_coding_evidence_schema",
                    "Coding evidence receipt uses an unsupported schema.",
                    artifact=relative,
                    schema=schema,
                )
            )
        if payload.get("status") not in {"PASS", "VALID"} or payload.get("ok") is False:
            alerts.append(
                _alert(
                    "coding_evidence_receipt_not_pass",
                    "Coding evidence receipts in a review package must be passing receipts.",
                    artifact=relative,
                    status=payload.get("status"),
                    ok=payload.get("ok"),
                )
            )
        if payload.get("mocked") is True:
            alerts.append(
                _alert(
                    "coding_evidence_receipt_mocked",
                    "Mocked coding evidence is not review-ready package evidence.",
                    artifact=relative,
                )
            )
        _collect_hash(payload, "goal_hash", goal_hashes)
        _collect_hash(payload, "active_goal_hash", goal_hashes)
        _collect_hash(payload, "data_boundary_sha256", data_boundary_hashes)


def _semantic_alerts(
    key: str,
    payload: Mapping[str, Any],
    *,
    artifact: str,
) -> list[dict[str, Any]]:
    if key == "data_boundary":
        return _data_boundary_alerts(payload, artifact=artifact)
    if key == "policy_profile":
        return _policy_profile_alerts(payload, artifact=artifact)
    if key == "actor_manifest":
        return _actor_manifest_alerts(payload, artifact=artifact)
    if key == "signed_receipt_verification":
        return _signed_receipt_verification_alerts(payload, artifact=artifact)
    if key == "sandbox_run":
        return _sandbox_run_alerts(payload, artifact=artifact)
    return []


def _data_boundary_alerts(payload: Mapping[str, Any], *, artifact: str) -> list[dict[str, Any]]:
    checks = {
        "itar": True,
        "technical_data": True,
        "export_controlled": True,
        "external_provider_allowed": False,
        "public_repo_allowed": False,
    }
    alerts: list[dict[str, Any]] = []
    if payload.get("classification") != "ITAR":
        alerts.append(
            _alert(
                "data_boundary_not_itar",
                "ITAR local-only package requires data_boundary.classification to be ITAR.",
                artifact=artifact,
                actual=payload.get("classification"),
            )
        )
    if payload.get("foreign_person_access") != "prohibited":
        alerts.append(
            _alert(
                "foreign_person_access_not_prohibited",
                "ITAR local-only package requires foreign_person_access:'prohibited'.",
                artifact=artifact,
                actual=payload.get("foreign_person_access"),
            )
        )
    for field, expected in checks.items():
        if payload.get(field) is not expected:
            alerts.append(
                _alert(
                    "data_boundary_policy_mismatch",
                    f"ITAR local-only package requires data_boundary.{field}={expected!r}.",
                    artifact=artifact,
                    field=field,
                    expected=expected,
                    actual=payload.get(field),
                )
            )
    return alerts


def _policy_profile_alerts(payload: Mapping[str, Any], *, artifact: str) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if payload.get("default_decision") != "deny":
        alerts.append(
            _alert(
                "policy_default_not_deny",
                "ITAR local-only package requires policy_profile.default_decision:'deny'.",
                artifact=artifact,
                actual=payload.get("default_decision"),
            )
        )
    if payload.get("requires_data_boundary") is not True:
        alerts.append(
            _alert(
                "policy_does_not_require_data_boundary",
                "ITAR local-only package requires policy_profile.requires_data_boundary:true.",
                artifact=artifact,
                actual=payload.get("requires_data_boundary"),
            )
        )
    providers = payload.get("providers")
    provider_map = providers if isinstance(providers, Mapping) else {}
    if provider_map.get("cloud_llm") not in {"deny", "block", "approval_required"}:
        alerts.append(
            _alert(
                "policy_cloud_provider_not_denied",
                "ITAR local-only package must deny or gate cloud provider use.",
                artifact=artifact,
                actual=provider_map.get("cloud_llm"),
            )
        )
    github = payload.get("github")
    github_map = github if isinstance(github, Mapping) else {}
    if github_map.get("public_mutation") not in {"deny", "block", "approval_required"}:
        alerts.append(
            _alert(
                "policy_public_mutation_not_denied",
                "ITAR local-only package must deny or gate public GitHub mutation.",
                artifact=artifact,
                actual=github_map.get("public_mutation"),
            )
        )
    return alerts


def _actor_manifest_alerts(payload: Mapping[str, Any], *, artifact: str) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    eligibility = payload.get("eligibility")
    eligibility_map = eligibility if isinstance(eligibility, Mapping) else {}
    if payload.get("actor_type") != "human":
        alerts.append(
            _alert(
                "actor_manifest_actor_not_human",
                "ITAR local-only review package requires a verified human actor manifest.",
                artifact=artifact,
                actual=payload.get("actor_type"),
            )
        )
    if payload.get("trusted") is not True or payload.get("verified") is not True:
        alerts.append(
            _alert(
                "actor_manifest_not_verified",
                "ITAR local-only review package requires trusted:true and verified:true.",
                artifact=artifact,
                trusted=payload.get("trusted"),
                verified=payload.get("verified"),
            )
        )
    if eligibility_map.get("us_person") != "verified":
        alerts.append(
            _alert(
                "actor_us_person_not_verified",
                "ITAR local-only review package requires eligibility.us_person:'verified'.",
                artifact=artifact,
                actual=eligibility_map.get("us_person"),
            )
        )
    if eligibility_map.get("foreign_person") is not False:
        alerts.append(
            _alert(
                "actor_foreign_person_not_false",
                "ITAR local-only review package requires eligibility.foreign_person:false.",
                artifact=artifact,
                actual=eligibility_map.get("foreign_person"),
            )
        )
    if eligibility_map.get("export_control_training_current") is not True:
        alerts.append(
            _alert(
                "actor_export_training_not_current",
                "ITAR local-only review package requires current export-control training metadata.",
                artifact=artifact,
                actual=eligibility_map.get("export_control_training_current"),
            )
        )
    approved = eligibility_map.get("approved_for_boundary")
    if not isinstance(approved, list) or "ITAR" not in approved:
        alerts.append(
            _alert(
                "actor_boundary_not_approved",
                "ITAR local-only review package requires actor approval for ITAR boundary.",
                artifact=artifact,
                approved_for_boundary=approved,
            )
        )
    return alerts


def _sandbox_run_alerts(payload: Mapping[str, Any], *, artifact: str) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    policy = payload.get("policy")
    policy_map = policy if isinstance(policy, Mapping) else {}
    backend = payload.get("backend")
    backend_map = backend if isinstance(backend, Mapping) else {}

    if payload.get("command_executed") is True and not _accepted_sandbox_execution(payload):
        alerts.append(
            _alert(
                "sandbox_execution_not_review_ready",
                (
                    "Review packages cannot claim sandbox command execution without "
                    "a runtime execution receipt accepted by Tau."
                ),
                artifact=artifact,
            )
        )

    if policy_map.get("network") not in {None, "none"}:
        alerts.append(
            _alert(
                "sandbox_policy_not_review_ready",
                "Review package sandbox receipt must not allow network access.",
                artifact=artifact,
                field="policy.network",
                actual=policy_map.get("network"),
            )
        )
    for field in ("privileged", "docker_socket_mounted", "host_network"):
        if policy_map.get(field) is True:
            alerts.append(
                _alert(
                    "sandbox_policy_not_review_ready",
                    f"Review package sandbox receipt must not enable {field}.",
                    artifact=artifact,
                    field=f"policy.{field}",
                    actual=policy_map.get(field),
                )
            )
    image = backend_map.get("image")
    if isinstance(image, str) and "@sha256:" not in image:
        alerts.append(
            _alert(
                "sandbox_policy_not_review_ready",
                "Review package sandbox image must be pinned by digest when present.",
                artifact=artifact,
                field="backend.image",
                actual=image,
            )
        )
    return alerts


def _accepted_sandbox_execution(payload: Mapping[str, Any]) -> bool:
    execution = payload.get("execution")
    if not isinstance(execution, Mapping):
        return False
    if payload.get("status") != "PASS" or payload.get("live") is not True:
        return False
    if execution.get("command_executed") is not True or execution.get("exit_code") != 0:
        return False
    if execution.get("alerts"):
        return False
    return isinstance(execution.get("stdout_path"), str) and isinstance(
        execution.get("stderr_path"), str
    )


def _signed_receipt_verification_alerts(
    payload: Mapping[str, Any],
    *,
    artifact: str,
) -> list[dict[str, Any]]:
    verified_count = _first_int(
        payload,
        (
            "verified_count",
            "verified_receipt_count",
            "valid_signature_count",
            "signature_count",
        ),
    )
    if verified_count is None or verified_count < 1:
        return [
            _alert(
                "signed_receipt_verification_empty",
                "ITAR local-only review package requires at least one verified signed receipt.",
                artifact=artifact,
                verified_count=verified_count,
            )
        ]
    return []


def _first_int(payload: Mapping[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
    return None


def _recommended_action(alerts: list[dict[str, Any]]) -> dict[str, str]:
    if not alerts:
        return {
            "type": "continue",
            "next_agent": "reviewer",
            "reason": "Package is review-ready; reviewer must still assess the evidence.",
        }
    return {
        "type": "repair_package",
        "next_agent": "goal-guardian",
        "reason": "Repair missing, blocked, mismatched, or underdocumented package artifacts.",
    }


def _alert(code: str, message: str, **evidence: object) -> dict[str, Any]:
    return {"severity": "BLOCK", "code": code, "message": message, "evidence": evidence}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
