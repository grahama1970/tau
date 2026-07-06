"""Declared actor/access preflight for ITAR-shaped Tau boundaries."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.policy_profile import validate_data_boundary

ACTOR_ACCESS_MANIFEST_SCHEMA = "tau.actor_access_manifest.v1"
ITAR_ACCESS_PREFLIGHT_RECEIPT_SCHEMA = "tau.itar_access_preflight_receipt.v1"
APPROVAL_PACKET_SCHEMA = "tau.human_approval_packet.v1"


def write_itar_access_preflight_receipt(
    *,
    actor_manifest_path: Path,
    data_boundary_path: Path,
    receipt_path: Path,
    approval_packet_path: Path | None = None,
    required_boundary: str = "ITAR",
) -> dict[str, Any]:
    """Check declared actor access metadata before high-stakes controlled work."""

    resolved_actor = actor_manifest_path.expanduser().resolve()
    resolved_boundary = data_boundary_path.expanduser().resolve()
    resolved_approval = approval_packet_path.expanduser().resolve() if approval_packet_path else None
    resolved_receipt = receipt_path.expanduser().resolve()

    errors: list[str] = []
    actor = _read_json_object(resolved_actor, errors=errors, label="actor_manifest")
    boundary = _read_json_object(resolved_boundary, errors=errors, label="data_boundary")
    approval = (
        _read_json_object(resolved_approval, errors=errors, label="approval_packet")
        if resolved_approval is not None
        else None
    )

    alerts = _evaluate_access(
        actor=actor,
        boundary=boundary,
        approval=approval,
        required_boundary=required_boundary,
        initial_errors=errors,
    )
    ok = not alerts
    receipt: dict[str, Any] = {
        "schema": ITAR_ACCESS_PREFLIGHT_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "required_boundary": required_boundary,
        "actor_manifest": _source_payload(resolved_actor, actor),
        "data_boundary": _source_payload(resolved_boundary, boundary),
        "approval_packet": (
            _source_payload(resolved_approval, approval)
            if resolved_approval is not None
            else None
        ),
        "actor": _actor_summary(actor),
        "alerts": alerts,
        "alert_codes": [str(alert["code"]) for alert in alerts],
        "recommended_action": _recommended_action(alerts),
        "receipt_path": str(resolved_receipt),
        "checked_at": _utc_stamp(),
        "proof_scope": {
            "proves": [
                "Tau inspected declared actor/access metadata before controlled-boundary work.",
                "Tau checked data-boundary, actor verification, U.S.-person declaration, foreign-person access, and approval actor consistency deterministically.",
                "No provider, GitHub, Memory, research, browser, or filesystem mutation was executed.",
            ],
            "does_not_prove": [
                "ITAR compliance.",
                "Export-control legal sufficiency.",
                "Legal identity proof.",
                "U.S.-person status truth beyond declared verified metadata.",
                "Human identity non-repudiation.",
                "Runtime sandbox enforcement.",
            ],
        },
    }
    resolved_receipt.parent.mkdir(parents=True, exist_ok=True)
    resolved_receipt.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def _evaluate_access(
    *,
    actor: Mapping[str, Any],
    boundary: Mapping[str, Any],
    approval: Mapping[str, Any] | None,
    required_boundary: str,
    initial_errors: list[str],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for error in initial_errors:
        alerts.append(_alert("input_unreadable", error))
    actor_errors = _validate_actor_manifest(actor)
    for error in actor_errors:
        alerts.append(_alert("invalid_actor_manifest", error))
    boundary_errors = validate_data_boundary(boundary) if boundary else ["data_boundary missing"]
    for error in boundary_errors:
        alerts.append(_alert("invalid_data_boundary", error))
    controlled = _boundary_matches(boundary, required_boundary)
    if not controlled:
        return alerts

    eligibility = actor.get("eligibility")
    eligibility_map = eligibility if isinstance(eligibility, Mapping) else {}
    actor_id = str(actor.get("actor_id") or "")
    actor_type = str(actor.get("actor_type") or "")
    if actor.get("verified") is not True:
        alerts.append(_alert("actor_not_verified", "Actor is not marked verified.", actor_id=actor_id))
    if actor.get("trusted") is not True:
        alerts.append(_alert("actor_not_trusted", "Actor is not marked trusted.", actor_id=actor_id))
    if eligibility_map.get("us_person") != "verified":
        alerts.append(
            _alert(
                "us_person_not_verified",
                f"{required_boundary} boundary requires declared verified U.S.-person eligibility.",
                actor_id=actor_id,
            )
        )
    if eligibility_map.get("foreign_person") is True:
        alerts.append(
            _alert(
                "foreign_person_actor_blocked",
                "Data boundary prohibits foreign-person access.",
                actor_id=actor_id,
            )
        )
    if eligibility_map.get("export_control_training_current") is not True:
        alerts.append(
            _alert(
                "export_training_not_current",
                "Controlled-boundary work requires current export-control training metadata.",
                actor_id=actor_id,
            )
        )
    approved_boundaries = eligibility_map.get("approved_for_boundary")
    if not isinstance(approved_boundaries, list) or required_boundary not in approved_boundaries:
        alerts.append(
            _alert(
                "actor_boundary_not_approved",
                f"Actor is not approved for boundary {required_boundary}.",
                actor_id=actor_id,
            )
        )
    roles = actor.get("roles")
    if isinstance(roles, list) and "approver" in roles and actor_type != "human":
        alerts.append(
            _alert(
                "agent_as_approver_rejected",
                "Only verified human actors may be declared as approvers.",
                actor_id=actor_id,
            )
        )
    if approval is not None:
        alerts.extend(_approval_alerts(approval, actor_id=actor_id, actor_type=actor_type))
    return alerts


def _validate_actor_manifest(actor: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if actor.get("schema") != ACTOR_ACCESS_MANIFEST_SCHEMA:
        errors.append(f"schema must be {ACTOR_ACCESS_MANIFEST_SCHEMA}")
    if not _non_empty_string(actor.get("actor_id")):
        errors.append("actor_id must be a non-empty string")
    if actor.get("actor_type") not in {"human", "service", "agent"}:
        errors.append("actor_type must be one of ['human', 'service', 'agent']")
    if not isinstance(actor.get("roles"), list) or not all(
        isinstance(item, str) and item.strip() for item in actor.get("roles", [])
    ):
        errors.append("roles must be a list of non-empty strings")
    if not isinstance(actor.get("trusted"), bool):
        errors.append("trusted must be a boolean")
    if not isinstance(actor.get("verified"), bool):
        errors.append("verified must be a boolean")
    eligibility = actor.get("eligibility")
    if not isinstance(eligibility, Mapping):
        errors.append("eligibility must be an object")
        return errors
    if eligibility.get("us_person") not in {"verified", "not_verified", "unknown"}:
        errors.append("eligibility.us_person must be one of ['verified', 'not_verified', 'unknown']")
    if not isinstance(eligibility.get("foreign_person"), bool):
        errors.append("eligibility.foreign_person must be a boolean")
    if not isinstance(eligibility.get("export_control_training_current"), bool):
        errors.append("eligibility.export_control_training_current must be a boolean")
    approved = eligibility.get("approved_for_boundary")
    if not isinstance(approved, list) or not all(
        isinstance(item, str) and item.strip() for item in approved
    ):
        errors.append("eligibility.approved_for_boundary must be a list of non-empty strings")
    return errors


def _approval_alerts(
    approval: Mapping[str, Any],
    *,
    actor_id: str,
    actor_type: str,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if approval.get("schema") != APPROVAL_PACKET_SCHEMA:
        alerts.append(_alert("invalid_approval_packet", f"schema must be {APPROVAL_PACKET_SCHEMA}"))
    actor = approval.get("actor")
    approval_actor = actor if isinstance(actor, Mapping) else {}
    approval_actor_id = approval_actor.get("id")
    if approval_actor_id != actor_id:
        alerts.append(
            _alert(
                "approval_actor_mismatch",
                "Approval packet actor must match the verified actor manifest.",
                actor_id=actor_id,
                approval_actor_id=approval_actor_id,
            )
        )
    if actor_type != "human":
        alerts.append(
            _alert(
                "approval_actor_not_human",
                "Controlled-boundary approval must come from a verified human actor.",
                actor_id=actor_id,
            )
        )
    if approval.get("approved") is not True:
        alerts.append(_alert("approval_not_approved", "Approval packet approved must be true."))
    return alerts


def _boundary_matches(boundary: Mapping[str, Any], required_boundary: str) -> bool:
    if required_boundary == "ITAR":
        return bool(boundary.get("classification") == "ITAR" or boundary.get("itar") is True)
    return boundary.get("classification") == required_boundary


def _actor_summary(actor: Mapping[str, Any]) -> dict[str, Any]:
    eligibility = actor.get("eligibility")
    eligibility_map = eligibility if isinstance(eligibility, Mapping) else {}
    return {
        "actor_id": actor.get("actor_id"),
        "actor_type": actor.get("actor_type"),
        "roles": actor.get("roles") if isinstance(actor.get("roles"), list) else [],
        "trusted": actor.get("trusted"),
        "verified": actor.get("verified"),
        "us_person": eligibility_map.get("us_person"),
        "foreign_person": eligibility_map.get("foreign_person"),
        "approved_for_boundary": eligibility_map.get("approved_for_boundary"),
    }


def _recommended_action(alerts: list[dict[str, Any]]) -> dict[str, str]:
    if not alerts:
        return {
            "type": "continue",
            "next_agent": "orchestrator",
            "reason": "Declared actor/access metadata passed the controlled-boundary preflight.",
        }
    return {
        "type": "repair_actor_access",
        "next_agent": "human",
        "reason": (
            "Provide verified actor/access metadata and a matching human approval packet "
            "before controlled-boundary work proceeds."
        ),
    }


def _read_json_object(path: Path, *, errors: list[str], label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label} is unreadable: {path}: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"{label} root must be a JSON object: {path}")
        return {}
    return payload


def _source_payload(path: Path, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": f"sha256:{_file_sha256(path)}",
        "schema": payload.get("schema") if isinstance(payload, Mapping) else None,
    }


def _alert(code: str, message: str, **evidence: object) -> dict[str, Any]:
    return {"severity": "BLOCK", "code": code, "message": message, "evidence": evidence}


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
