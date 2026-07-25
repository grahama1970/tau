"""Durable permission request and reply receipts for Tau operator gates."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.approval_gate import ALLOWED_ACTIONS

PERMISSION_REQUEST_RECEIPT_SCHEMA = "tau.permission_request_receipt.v1"
PERMISSION_REPLY_RECEIPT_SCHEMA = "tau.permission_reply_receipt.v1"
ALLOWED_PERMISSION_REPLIES = ("once", "always", "reject")


def write_permission_request_receipt(
    *,
    action: str,
    resources: list[str],
    source_node: str,
    run_dir: Path,
    output: Path | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    mode: str | None = None,
    proposed_save_rule: str | None = None,
    denied: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Record a pending or denied permission request without executing a mutation."""

    resolved_run_dir = run_dir.expanduser().resolve()
    resolved_run_dir.mkdir(parents=True, exist_ok=True)
    normalized_resources = [item.strip() for item in resources if item.strip()]
    errors = _permission_request_errors(
        action=action,
        resources=normalized_resources,
        source_node=source_node,
        request_id=request_id,
        proposed_save_rule=proposed_save_rule,
    )
    resolved_request_id = request_id or _default_request_id(
        action=action,
        resources=normalized_resources,
        source_node=source_node,
        session_id=session_id,
    )
    status = "BLOCKED" if denied or errors else "PENDING"
    ok = not denied and not errors
    output_path = (
        output.expanduser().resolve()
        if output is not None
        else resolved_run_dir / f"permission-request-{resolved_request_id}.json"
    )
    receipt = {
        "schema": PERMISSION_REQUEST_RECEIPT_SCHEMA,
        "ok": ok,
        "status": status,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "request_id": resolved_request_id,
        "session_id": session_id,
        "mode": mode,
        "action": action,
        "resources": normalized_resources,
        "source_node": source_node,
        "allowed_replies": list(ALLOWED_PERMISSION_REPLIES) if status == "PENDING" else [],
        "proposed_save_rule": proposed_save_rule,
        "decision": "DENIED" if denied or errors else "ASK",
        "reason": reason or (
            "permission denied before mutation"
            if denied
            else "permission request is pending human reply"
        ),
        "run_dir": str(resolved_run_dir),
        "receipt_path": str(output_path),
        "errors": errors,
        "proof_scope": {
            "proves": [
                "Tau records a durable permission request before a gated mutation",
                "Tau records denied permission attempts as fail-closed receipts",
                (
                    "Tau exposes the exact request id, action, resources, source node, "
                    "and reply choices"
                ),
            ],
            "does_not_prove": [
                "the requested mutation was executed",
                "a human approved the request",
                "permission queue UI rendering",
                "provider or model behavior",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(output_path, receipt)
    return receipt


def write_permission_reply_receipt(
    *,
    request_receipt: Path,
    reply: str,
    output: Path | None = None,
    actor_id: str | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """Record a durable human reply to a pending permission request receipt."""

    resolved_request = request_receipt.expanduser().resolve()
    request_payload, load_errors = _load_json_object(resolved_request, label="request receipt")
    normalized_reply = reply.strip().lower()
    errors = load_errors + _permission_reply_errors(
        request_payload=request_payload,
        reply=normalized_reply,
    )
    request_id = (
        str(request_payload.get("request_id"))
        if isinstance(request_payload.get("request_id"), str)
        else "unknown-request"
    )
    output_path = (
        output.expanduser().resolve()
        if output is not None
        else resolved_request.parent / f"permission-reply-{request_id}.json"
    )
    accepted = normalized_reply in {"once", "always"} and not errors
    receipt = {
        "schema": PERMISSION_REPLY_RECEIPT_SCHEMA,
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "request_id": request_id,
        "reply": normalized_reply,
        "accepted": accepted,
        "actor_id": actor_id,
        "scope": scope,
        "request_receipt": str(resolved_request),
        "request_receipt_sha256": _file_sha256(resolved_request),
        "action": request_payload.get("action") if request_payload else None,
        "resources": request_payload.get("resources") if request_payload else [],
        "source_node": request_payload.get("source_node") if request_payload else None,
        "save_rule": _reply_save_rule(
            request_payload=request_payload,
            reply=normalized_reply,
            scope=scope,
        ),
        "errors": errors,
        "proof_scope": {
            "proves": [
                "Tau records a durable permission reply linked to a request receipt",
                "Tau distinguishes once, always, and reject replies",
                "Tau preserves request receipt provenance with a SHA-256 digest",
            ],
            "does_not_prove": [
                "the requested mutation was executed",
                "permission state was applied to a running process",
                "permission queue UI rendering",
                "provider or model behavior",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(output_path, receipt)
    return receipt


def _permission_request_errors(
    *,
    action: str,
    resources: list[str],
    source_node: str,
    request_id: str | None,
    proposed_save_rule: str | None,
) -> list[str]:
    errors: list[str] = []
    if action not in ALLOWED_ACTIONS:
        errors.append(f"action must be one of {sorted(ALLOWED_ACTIONS)}")
    if not resources:
        errors.append("at least one --resource is required")
    if not source_node.strip():
        errors.append("--source-node must be a non-empty string")
    if request_id is not None and not request_id.strip():
        errors.append("--request-id must be a non-empty string when present")
    if proposed_save_rule is not None and not proposed_save_rule.strip():
        errors.append("--save-rule must be a non-empty string when present")
    return errors


def _permission_reply_errors(
    *,
    request_payload: dict[str, Any],
    reply: str,
) -> list[str]:
    errors: list[str] = []
    if reply not in ALLOWED_PERMISSION_REPLIES:
        errors.append(f"reply must be one of {list(ALLOWED_PERMISSION_REPLIES)}")
    if not request_payload:
        return errors
    if request_payload.get("schema") != PERMISSION_REQUEST_RECEIPT_SCHEMA:
        errors.append(f"request receipt schema must be {PERMISSION_REQUEST_RECEIPT_SCHEMA}")
    if request_payload.get("status") != "PENDING":
        errors.append("request receipt status must be PENDING")
    allowed = request_payload.get("allowed_replies")
    if not isinstance(allowed, list) or reply not in allowed:
        errors.append("reply must be allowed by request receipt allowed_replies")
    if not isinstance(request_payload.get("request_id"), str) or not request_payload[
        "request_id"
    ].strip():
        errors.append("request receipt request_id must be a non-empty string")
    return errors


def _reply_save_rule(
    *,
    request_payload: dict[str, Any],
    reply: str,
    scope: str | None,
) -> dict[str, Any] | None:
    if reply != "always" or not request_payload:
        return None
    return {
        "scope": scope or request_payload.get("proposed_save_rule") or "session",
        "action": request_payload.get("action"),
        "resources": request_payload.get("resources", []),
        "source_node": request_payload.get("source_node"),
    }


def _default_request_id(
    *,
    action: str,
    resources: list[str],
    source_node: str,
    session_id: str | None,
) -> str:
    payload = {
        "action": action,
        "resources": resources,
        "session_id": session_id,
        "source_node": source_node,
        "timestamp": _utc_stamp(),
    }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"perm-{hashlib.sha256(data).hexdigest()[:16]}"


def _load_json_object(path: Path, *, label: str) -> tuple[dict[str, Any], list[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, [f"{label} not found: {path}"]
    except json.JSONDecodeError as exc:
        return {}, [f"{label} is not valid JSON: {exc}"]
    if not isinstance(payload, dict):
        return {}, [f"{label} must be a JSON object"]
    return payload, []


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
