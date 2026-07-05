"""Human approval gates for Tau mutation and closure actions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

APPROVAL_PACKET_SCHEMA = "tau.human_approval_packet.v1"
APPROVAL_GATE_RECEIPT_SCHEMA = "tau.approval_gate_receipt.v1"
ALLOWED_ACTIONS = {
    "dag_expansion_apply",
    "github_apply",
    "github_ticket_closure",
    "herdr_cleanup_apply",
    "memory_upsert",
    "provider_branch_scheduling",
    "working_tree_mutation",
}
ALLOWED_AUTH_METHODS = {"github-comment", "local-signature", "manual"}


def evaluate_approval_gate(
    *,
    approval_packet: Path,
    requested_action: str,
    run_dir: Path,
    output: Path | None = None,
) -> dict[str, Any]:
    """Evaluate whether a gated action has explicit human approval."""

    resolved_run_dir = run_dir.expanduser().resolve()
    resolved_run_dir.mkdir(parents=True, exist_ok=True)
    resolved_packet = approval_packet.expanduser().resolve()
    output_path = (output.expanduser().resolve() if output else resolved_run_dir / "approval-gate-receipt.json")
    packet, load_errors = _load_packet(resolved_packet)
    validation_errors = _validate_packet(packet, requested_action=requested_action) if packet else []
    errors = load_errors + validation_errors
    approved = not errors
    receipt = {
        "schema": APPROVAL_GATE_RECEIPT_SCHEMA,
        "ok": approved,
        "status": "PASS" if approved else "BLOCKED",
        "mocked": False,
        "live": False,
        "requested_action": requested_action,
        "approved": approved,
        "approval_packet": str(resolved_packet),
        "approval_packet_sha256": _file_sha256(resolved_packet),
        "run_dir": str(resolved_run_dir),
        "errors": errors,
        "packet_summary": _packet_summary(packet),
        "proof_scope": {
            "proves": [
                "Tau can fail closed before mutation or closure without a valid human approval packet",
                "Tau can validate explicit human approval for a requested gated action",
                "Tau writes a durable approval-gate receipt before any gated mutation command",
            ],
            "does_not_prove": [
                "the gated mutation was executed",
                "GitHub ticket closure",
                "production repository mutation",
                "cryptographic signature validity beyond requiring a signature field",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(output_path, receipt)
    return receipt


def _load_packet(path: Path) -> tuple[dict[str, Any], list[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, [f"approval packet not found: {path}"]
    except json.JSONDecodeError as exc:
        return {}, [f"approval packet is not valid JSON: {exc}"]
    if not isinstance(payload, dict):
        return {}, ["approval packet must be a JSON object"]
    return payload, []


def _validate_packet(packet: dict[str, Any], *, requested_action: str) -> list[str]:
    errors = []
    if requested_action not in ALLOWED_ACTIONS:
        errors.append(f"requested_action must be one of {sorted(ALLOWED_ACTIONS)}")
    if packet.get("schema") != APPROVAL_PACKET_SCHEMA:
        errors.append(f"schema must be {APPROVAL_PACKET_SCHEMA}")
    if packet.get("approved") is not True:
        errors.append("approved must be true")
    actor = packet.get("actor")
    if not isinstance(actor, dict):
        errors.append("actor must be an object")
        actor = {}
    if not str(actor.get("id") or "").strip():
        errors.append("actor.id must be a non-empty string")
    if actor.get("auth_method") not in ALLOWED_AUTH_METHODS:
        errors.append(f"actor.auth_method must be one of {sorted(ALLOWED_AUTH_METHODS)}")
    action = packet.get("action")
    if action != requested_action:
        errors.append(f"action must match requested_action {requested_action}")
    if action not in ALLOWED_ACTIONS:
        errors.append(f"action must be one of {sorted(ALLOWED_ACTIONS)}")
    target = packet.get("target")
    if not isinstance(target, dict) or not str(target.get("id") or "").strip():
        errors.append("target.id must be a non-empty string")
    if not isinstance(packet.get("reason"), str) or not packet["reason"].strip():
        errors.append("reason must be a non-empty string")
    evidence = packet.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append("evidence must be a non-empty list")
    if not isinstance(packet.get("nonce"), str) or not packet["nonce"].strip():
        errors.append("nonce must be a non-empty string")
    if not isinstance(packet.get("signature"), str) or not packet["signature"].strip():
        errors.append("signature must be a non-empty string")
    expires_at = packet.get("expires_at")
    if expires_at is not None and not isinstance(expires_at, str):
        errors.append("expires_at must be a string when present")
    if isinstance(expires_at, str):
        expires_at_value = _parse_timestamp(expires_at)
        if expires_at_value is None:
            errors.append("expires_at must be an ISO-8601 timestamp when present")
        elif expires_at_value <= datetime.now(UTC):
            errors.append("approval packet expired")
    return errors


def _packet_summary(packet: dict[str, Any]) -> dict[str, Any] | None:
    if not packet:
        return None
    actor = packet.get("actor") if isinstance(packet.get("actor"), dict) else {}
    target = packet.get("target") if isinstance(packet.get("target"), dict) else {}
    return {
        "schema": packet.get("schema"),
        "approved": packet.get("approved"),
        "action": packet.get("action"),
        "actor_id": actor.get("id"),
        "actor_auth_method": actor.get("auth_method"),
        "human_id": actor.get("id"),
        "target_id": target.get("id"),
        "evidence_count": len(packet.get("evidence")) if isinstance(packet.get("evidence"), list) else 0,
        "nonce": packet.get("nonce"),
        "signature_present": bool(packet.get("signature")),
        "expires_at": packet.get("expires_at"),
    }


def _parse_timestamp(value: str) -> datetime | None:
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _file_sha256(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
