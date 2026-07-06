"""Review-readiness validation for Tau compliance evidence packages."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

COMPLIANCE_PACKAGE_VALIDATION_RECEIPT_SCHEMA = "tau.compliance_package_validation_receipt.v1"

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
    for key, (relative_path, expected_schema, require_pass) in requirements.items():
        artifact_path = resolved_package / relative_path
        summary = _artifact_summary(artifact_path)
        artifacts[key] = summary
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
            _collect_hash(payload, "goal_hash", goal_hashes)
            _collect_hash(payload, "active_goal_hash", goal_hashes)
            _collect_hash(payload, "data_boundary_sha256", data_boundary_hashes)

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
                "Required artifacts, schemas, critical receipt statuses, non-claim language, and hash consistency were checked deterministically.",
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
