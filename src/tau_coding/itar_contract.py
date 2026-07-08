"""Deterministic synthetic ITAR-style contract receipt."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.policy_profile import validate_data_boundary, validate_policy_profile

ITAR_CONTRACT_RECEIPT_SCHEMA = "tau.itar_contract_receipt.v1"

CONTROLLED_DATA_INDICATORS = {
    "design drawing": "Clause references design drawings.",
    "design drawings": "Clause references design drawings.",
    "test procedure": "Clause references test procedures.",
    "test procedures": "Clause references test procedures.",
    "manufacturing process": "Clause references manufacturing process notes.",
    "manufacturing process notes": "Clause references manufacturing process notes.",
    "technical data": "Clause references technical data.",
    "foreign-person access": "Clause references foreign-person access.",
    "foreign person access": "Clause references foreign-person access.",
    "external release": "Clause references external release.",
    "export control": "Clause references export control.",
    "controlled engineering": "Clause references controlled engineering information.",
}


def write_itar_contract_receipt(
    *,
    clause: Path,
    policy_profile: Path,
    data_boundary: Path,
    out: Path,
    contract_clause_id: str | None = None,
) -> dict[str, Any]:
    """Inspect a synthetic contract clause and write an ITAR-style receipt."""

    resolved_clause = clause.expanduser().resolve()
    resolved_policy = policy_profile.expanduser().resolve()
    resolved_boundary = data_boundary.expanduser().resolve()
    resolved_out = out.expanduser().resolve()

    source_text = resolved_clause.read_text(encoding="utf-8")
    policy_payload = _read_json_object(resolved_policy)
    boundary_payload = _read_json_object(resolved_boundary)
    policy_errors = validate_policy_profile(policy_payload)
    boundary_errors = validate_data_boundary(boundary_payload)
    reasons = _candidate_reasons(source_text)
    controlled_candidate = bool(reasons)
    boundary_is_itar = bool(
        boundary_payload.get("classification") == "ITAR" or boundary_payload.get("itar") is True
    )
    alerts: list[dict[str, Any]] = []
    if policy_errors:
        alerts.append(_alert("invalid_policy_profile", "Policy profile is invalid.", policy_errors))
    if boundary_errors:
        alerts.append(_alert("invalid_data_boundary", "Data boundary is invalid.", boundary_errors))
    if controlled_candidate and boundary_is_itar:
        alerts.append(
            {
                "severity": "BLOCK",
                "code": "human_export_control_review_required",
                "message": (
                    "Synthetic clause contains controlled technical data indicators; "
                    "human review required."
                ),
            }
        )

    ok = not alerts
    decision = (
        "approval_required"
        if controlled_candidate and boundary_is_itar
        else "insufficient_evidence"
        if policy_errors or boundary_errors
        else "allow"
    )
    receipt: dict[str, Any] = {
        "schema": ITAR_CONTRACT_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "decision": decision,
        "contract_clause_id": contract_clause_id or _detect_clause_id(source_text),
        "source_artifact": str(resolved_clause),
        "source_sha256": f"sha256:{_sha256(resolved_clause)}",
        "policy_profile": _source_payload(resolved_policy, policy_payload),
        "data_boundary": _source_payload(resolved_boundary, boundary_payload),
        "controlled_data_candidate": controlled_candidate,
        "candidate_reasons": reasons,
        "access_constraint": (
            "export_control_review_required"
            if controlled_candidate and boundary_is_itar
            else "none_detected"
        ),
        "required_human_role": (
            "export_control_officer" if controlled_candidate and boundary_is_itar else None
        ),
        "evidence": [
            {
                "kind": "source_clause",
                "path": str(resolved_clause),
                "sha256": f"sha256:{_sha256(resolved_clause)}",
            }
        ],
        "alerts": alerts,
        "alert_codes": [str(alert["code"]) for alert in alerts],
        "receipt_path": str(resolved_out),
        "checked_at": _utc_stamp(),
        "proof_scope": {
            "proves": [
                "Tau inspected a synthetic contract clause.",
                "Tau bound the source clause to a sha256 hash.",
                "Tau identified configured controlled-data indicators.",
                "Tau routed final authority to a human role when indicators appeared "
                "under an ITAR boundary.",
            ],
            "does_not_prove": [
                "ITAR compliance.",
                "Legal sufficiency.",
                "Correct USML classification.",
                "Authorization to process real controlled technical data.",
                "Human approval.",
                "Model semantic correctness.",
            ],
        },
    }
    resolved_out.parent.mkdir(parents=True, exist_ok=True)
    resolved_out.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def _candidate_reasons(source_text: str) -> list[str]:
    lowered = source_text.lower()
    reasons: list[str] = []
    seen: set[str] = set()
    for indicator, reason in CONTROLLED_DATA_INDICATORS.items():
        if indicator in lowered and reason not in seen:
            reasons.append(reason)
            seen.add(reason)
    return reasons


def _detect_clause_id(source_text: str) -> str:
    match = re.search(r"\b(SC-[0-9]+)\b", source_text)
    return match.group(1) if match else "UNKNOWN"


def _source_payload(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": str(path),
        "schema": payload.get("schema"),
        "sha256": f"sha256:{_sha256(path)}",
    }


def _alert(code: str, message: str, errors: list[str]) -> dict[str, Any]:
    return {
        "severity": "BLOCK",
        "code": code,
        "message": message,
        "errors": errors,
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
